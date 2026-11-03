import os, re, json, time, asyncio, ipaddress, base64
from urllib.parse import urlparse, urlunparse, quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup
from groq import AsyncGroq
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright

APP_NAME = "NEXUS AI"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None
app = FastAPI(title=APP_NAME)

MAX_SOURCES, BATCH_SIZE = 12, 4
state = {
    "status": "idle", "phase": "Ready", "action": "Waiting for instructions", "task": "",
    "current_url": "", "current_title": "", "collected": 0, "analysed": 0,
    "target": MAX_SOURCES, "plan": None, "sources": [], "images": [], "actions": [],
    "report": "", "error": "", "elapsed": 0, "network": {}
}
events, ws_clients, ws_layers = [], set(), {}
agent_task = None
seen_urls = set()

async def broadcast(data):
    dead = []
    for ws in list(ws_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)
        ws_layers.pop(ws, None)

async def emit(message, category="INFO", status=None, details=None):
    event = {"time": time.strftime("%H:%M:%S"), "category": category,
             "message": str(message), "status": status or category.lower(),
             "details": details or ""}
    events.append(event)
    if len(events) > 180:
        del events[:-180]
    state["actions"].append(event)
    if len(state["actions"]) > 180:
        del state["actions"][:-180]
    await broadcast({"type": "event", "event": event})

async def update(**kwargs):
    state.update(kwargs)
    await broadcast({"type": "state", "state": state, "events": events[-70:]})

def validate_public_url(url):
    try:
        p = urlparse((url or "").strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return None
        host = p.hostname.lower()
        if host in ("localhost", "localhost.localdomain"):
            return None
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return None
        except ValueError:
            pass
        return urlunparse(p)
    except Exception:
        return None

def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("AI returned invalid JSON")
    return json.loads(text[a:b+1])

async def ask_groq(system, prompt, max_tokens=1800):
    if not client:
        raise RuntimeError("GROQ_API_KEY is missing in Render environment variables")
    r = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=max_tokens
    )
    return r.choices[0].message.content or ""

async def create_plan(task, start_url):
    await update(phase="Planning", action="Understanding your instruction")
    await emit("Understanding task and selecting a research strategy", "PLANNER", "running")
    system = """Return ONLY valid JSON:
{"intent":"open|search|research|interact","target_site":"named site or empty","start_url":"known URL or empty","search_query":"specific query or empty","goal":"short goal","target_sources":8}
Preserve named websites. 'Open X' means open X, not search alternatives. If a starting URL is supplied, use it. Never invent URLs. Do not bypass CAPTCHA, paywalls, login gates or access restrictions. Maximum target_sources 12."""
    plan = parse_json(await ask_groq(system, f"USER TASK:\n{task}\nSTART URL:\n{start_url or 'Not supplied'}", 1000))
    plan["target_sources"] = min(max(int(plan.get("target_sources", 8)), 1), MAX_SOURCES)
    plan["start_url"] = start_url or (validate_public_url(plan.get("start_url", "")) if plan.get("start_url") else "")
    plan["search_query"] = str(plan.get("search_query") or "").strip()
    plan["intent"] = str(plan.get("intent") or "research").lower()
    state["target"] = plan["target_sources"]
    await update(phase="Plan ready", plan=plan, action="Research plan created")
    await emit("Research plan created", "PLANNER", "success")
    return plan

async def measure_network():
    """A lightweight check to distinguish basic connectivity from slow page rendering."""
    checks = []
    async with httpx.AsyncClient(timeout=8, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 NEXUS-AI/0.4"}) as h:
        for url in ("https://www.google.com/generate_204", "https://html.duckduckgo.com/"):
            t = time.perf_counter()
            try:
                r = await h.get(url)
                checks.append({"host": urlparse(url).netloc, "ok": r.status_code < 500,
                               "status": r.status_code, "ms": round((time.perf_counter()-t)*1000)})
            except Exception as e:
                checks.append({"host": urlparse(url).netloc, "ok": False,
                               "error": str(e)[:100], "ms": round((time.perf_counter()-t)*1000)})
    state["network"] = {"checks": checks, "note": "Server-side connectivity check; not a measurement of your phone's Wi-Fi speed."}
    await update(network=state["network"])
    for c in checks:
        await emit(f"Connectivity {c['host']}: {'OK' if c['ok'] else 'FAILED'} ({c['ms']} ms)",
                   "NETWORK", "success" if c["ok"] else "warning")
    return checks

async def search_web(query):
    await update(phase="Searching", action=f"Searching the web: {query}")
    await emit(f"Searching public web results for: {query}", "SEARCH", "running")
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    t = time.perf_counter()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122 Safari/537.36"}) as h:
        r = await h.get(url)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a")
        sn = item.select_one(".result__snippet")
        if not a:
            continue
        raw = a.get("href", "")
        # DuckDuckGo may wrap destination URLs in its redirect parameter.
        if raw.startswith("//"):
            raw = "https:" + raw
        parsed = urlparse(raw)
        if "duckduckgo.com" in (parsed.hostname or "") and parsed.query:
            from urllib.parse import parse_qs
            raw = parse_qs(parsed.query).get("uddg", [raw])[0]
        u = validate_public_url(raw)
        if u:
            out.append({"title": a.get_text(" ", strip=True), "url": u,
                        "snippet": sn.get_text(" ", strip=True) if sn else ""})
    await emit(f"Search returned {len(out)} usable links in {round((time.perf_counter()-t)*1000)} ms",
               "RESULTS", "success" if out else "failed")
    return out

async def extract_page(page):
    return await page.evaluate("""() => {
      const visible = el => { const r=el.getBoundingClientRect(); return r.width>0 && r.height>0; };
      const links = Array.from(document.querySelectorAll('a')).filter(a => visible(a) && a.href.startsWith('http'));
      const images = Array.from(document.querySelectorAll('img')).filter(img => {
        const s=img.currentSrc || img.src || img.getAttribute('data-src') || '';
        return s.startsWith('http') && !s.startsWith('data:');
      }).slice(0,40).map(img => ({
        src: img.currentSrc || img.src || img.getAttribute('data-src'),
        alt: img.alt || '',
        width: img.naturalWidth || img.width || 0,
        height: img.naturalHeight || img.height || 0
      }));
      const videos = links.filter(a => /\\/watch\\?v=|youtu\\.be\\//.test(a.href)).slice(0,30).map(a => ({
        title:(a.innerText || a.getAttribute('title') || '').trim(),
        url:a.href
      })).filter(v => v.title || v.url);
      return {
        title:document.title||'',
        description:document.querySelector('meta[name="description"]')?.content||'',
        text:(document.body?.innerText||'').slice(0,10000),
        links:links.slice(0,70).map(a=>({text:(a.innerText||a.getAttribute('aria-label')||'').trim(),href:a.href})),
        images, videos
      };
    }""")

async def open_page(page, url):
    u = validate_public_url(url)
    if not u:
        raise ValueError("Invalid public HTTP/HTTPS URL")
    await emit(f"Opening {u}", "NAVIGATION", "running")
    started = time.perf_counter()
    try:
        # DOM content is enough to begin text extraction; don't wait for images/ads.
        await page.goto(u, wait_until="domcontentloaded", timeout=18000)
    except Exception as e:
        await emit(f"Navigation warning: {str(e)[:150]}", "NAVIGATION", "warning")
        if page.url in ("", "about:blank"):
            raise
    try:
        title = await page.title()
    except Exception:
        title = ""
    elapsed = round((time.perf_counter()-started)*1000)
    await update(current_url=page.url, current_title=title,
                 action=f"Opened {title or page.url} · {elapsed} ms")
    await emit(f"Page DOM opened in {elapsed} ms: {title or page.url}", "NAVIGATION", "success")
    return elapsed

async def find_search_box(page):
    # Site-specific selectors first, then generic accessible search inputs.
    selectors = [
        "ytd-searchbox input#search", "input#search", "input[name='search_query']",
        "form[role='search'] input", "input[type='search']", "input[name='q']",
        "input[placeholder*='search' i]", "input[aria-label*='search' i]",
        "textarea[placeholder*='search' i]", "[role='searchbox']"
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=450):
                return loc
        except Exception:
            continue
    return None

async def wait_for_site_results(page, query):
    host = (urlparse(page.url).hostname or "").lower()
    selectors = []
    if "youtube.com" in host:
        selectors = ["ytd-video-renderer", "ytd-rich-item-renderer", "a#video-title"]
    elif "google." in host:
        selectors = ["#search", "div.MjjYud", "a h3"]
    elif "bing.com" in host:
        selectors = ["#b_results", "li.b_algo"]
    else:
        selectors = ["main", "[role='main']", "article", ".search-results", "[class*='result' i]"]
    for sel in selectors:
        try:
            await page.locator(sel).first.wait_for(state="attached", timeout=2500)
            return True
        except Exception:
            pass
    # SPA sites often update without a full navigation event.
    try:
        await page.wait_for_function(
            "() => (document.body?.innerText || '').length > 120", timeout=1800
        )
    except Exception:
        pass
    return False

async def interact_search(page, query):
    await emit("Looking for the website's own search field (not the browser address bar)", "BROWSER", "running")
    box = await find_search_box(page)
    if not box:
        await emit("No supported visible website search field was found", "SEARCH", "failed")
        return False
    try:
        await box.click(timeout=2500)
        await emit("Clicked website search field", "CLICK", "success")
        await box.fill(query, timeout=3000)
        await emit(f"Entered search text: {query}", "TYPE", "success")
        await box.press("Enter", timeout=2500)
        await emit("Submitted website search", "CLICK", "success")
        # YouTube is a client-rendered SPA: wait for result cards, not networkidle.
        found = await wait_for_site_results(page, query)
        await update(current_url=page.url, current_title=await page.title())
        await emit("Search results detected" if found else "Search submitted, but result elements were not detected",
                   "RESULTS", "success" if found else "warning")
        return found
    except Exception as e:
        await emit(f"Website search failed: {str(e)[:150]}", "SEARCH", "failed")
        return False

async def save_source(data):
    u = validate_public_url(data.get("url", ""))
    if not u or u in seen_urls:
        return False
    seen_urls.add(u)
    data.update(url=u, id=len(state["sources"])+1, status="Collected")
    state["sources"].append(data)
    await update(collected=len(state["sources"]))
    await emit(f"Collected source {len(state['sources'])}/{state['target']}: {data.get('title','Untitled')[:90]}",
               "COLLECTION", "success")
    return True

async def save_images(images, page_url):
    known = {x.get("url") for x in state["images"]}
    for item in images or []:
        raw = item.get("src", "")
        u = validate_public_url(urljoin(page_url, raw))
        if not u or u in known:
            continue
        # Keep image metadata separate from text research; do not block the research pipeline.
        state["images"].append({"url": u, "alt": item.get("alt", ""),
                                "width": item.get("width", 0), "height": item.get("height", 0),
                                "status": "URL extracted"})
        known.add(u)
        if len(state["images"]) >= 60:
            break
    await update(images=state["images"])

async def analyse_batch(batch):
    await update(phase="Analysis", action=f"Analysing {len(batch)} sources")
    await emit(f"Analysing batch of {len(batch)} source(s)", "ANALYST", "running")
    compact = [{"title": s.get("title", ""), "url": s.get("url", ""),
                "text": s.get("text", "")[:3000]} for s in batch]
    result = await ask_groq(
        "Analyse only supplied source text. Do not invent facts. State when evidence is missing.",
        json.dumps(compact), 1800
    )
    for s in batch:
        s["status"] = "Analysed"
    state["analysed"] += len(batch)
    await update(analysed=state["analysed"], sources=state["sources"])
    await emit(f"Successfully analysed {len(batch)} source(s)", "ANALYST", "success")
    return result

async def analysis_worker(queue):
    results = []
    while True:
        first = await queue.get()
        if first is None:
            break
        batch, stop = [first], False
        while len(batch) < BATCH_SIZE:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=.8)
                if item is None:
                    stop = True
                    break
                batch.append(item)
            except asyncio.TimeoutError:
                break
        try:
            results.append(await analyse_batch(batch))
        except Exception as e:
            await emit(f"Analysis failed: {str(e)[:150]}", "ANALYST", "failed")
        if stop:
            break
    return results

async def final_report(task, analyses):
    await update(phase="Final report", action="Preparing evidence-based report")
    sources = [{"title": s.get("title"), "url": s.get("url"), "status": s.get("status")}
               for s in state["sources"]]
    prompt = (
        f"TASK:\n{task}\nANALYSES:\n{json.dumps(analyses)}\nSOURCES:\n{json.dumps(sources)}\n"
        "Write a useful answer with findings, evidence limitations and clickable full source URLs. "
        "Only claim what supplied text supports. Mention incomplete collection honestly."
    )
    report = await ask_groq("You are NEXUS AI's careful research report writer.", prompt, 2500)
    state["report"] = report
    await update(report=report)
    await emit("Evidence-based report generated", "REPORT", "success")

async def screenshot_worker(page):
    while True:
        await asyncio.sleep(3)
        if not any(v == 3 for v in ws_layers.values()):
            continue
        try:
            if page.is_closed():
                continue
            img = await page.screenshot(type="jpeg", quality=42, timeout=4500)
            await broadcast({"type": "screenshot", "image": base64.b64encode(img).decode("ascii")})
        except Exception:
            pass

async def run_agent(task, start_url):
    global seen_urls
    seen_urls = set()
    state.update(sources=[], images=[], actions=[], report="", analysed=0, collected=0,
                 error="", current_url="", current_title="", network={})
    events.clear()
    started = time.time()
    browser = shot = worker = queue = None
    await update(status="running", phase="Starting", task=task, action="Starting NEXUS AI",
                 plan=None, target=MAX_SOURCES)
    try:
        checks = await measure_network()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking"])
            context = await browser.new_context(
                viewport={"width": 1365, "height": 850},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
            async def route_resources(route):
                # Text-first mode: block heavy assets, but still parse image URLs from DOM.
                # Keep scripts/stylesheets enabled because modern search pages need them.
                if route.request.resource_type in ("image", "font", "media"):
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", route_resources)
            page = await context.new_page()
            page.set_default_timeout(5000)
            shot = asyncio.create_task(screenshot_worker(page))
            plan = await create_plan(task, start_url)
            queue = asyncio.Queue()
            worker = asyncio.create_task(analysis_worker(queue))
            url, query = plan.get("start_url") or "", plan.get("search_query", "")

            if url:
                await open_page(page, url)
                if query:
                    found = await interact_search(page, query)
                    if not found:
                        await emit("Search may not have returned usable results; extracting current page text anyway",
                                   "WARNING", "warning")
                data = await extract_page(page)
                await save_images(data.get("images"), page.url)
                title = data["title"] or plan.get("target_site") or page.url
                text_content = data["text"]
                if data.get("videos"):
                    video_lines = "\n".join(
                        f"VIDEO: {v.get('title') or 'Untitled'} | {v['url']}" for v in data["videos"]
                    )
                    text_content += "\n\nVIDEO LINKS:\n" + video_lines
                saved = await save_source({"title": title, "url": page.url,
                    "text": text_content, "description": data["description"],
                    "links": data.get("links", [])[:30], "videos": data.get("videos", [])})
                if saved:
                    await queue.put(state["sources"][-1])
                # YouTube's page can be SPA-driven; capture video cards even when thumbnails are blocked.
                if "youtube.com" in (urlparse(page.url).hostname or ""):
                    await emit(f"Extracted {len(data.get('videos', []))} video link(s) and {len(data.get('images', []))} image URL(s)",
                               "VISUAL", "success" if data.get("videos") else "warning")
            elif query and plan["intent"] in ("search", "research"):
                results = await search_web(query)
                for result in results:
                    if len(state["sources"]) >= state["target"]:
                        break
                    try:
                        await open_page(page, result["url"])
                        data = await extract_page(page)
                        await save_images(data.get("images"), page.url)
                        saved = await save_source({"title": data["title"] or result["title"],
                            "url": page.url, "text": data["text"],
                            "description": data["description"] or result["snippet"],
                            "links": data.get("links", [])[:30]})
                        if saved:
                            await queue.put(state["sources"][-1])
                    except Exception as e:
                        await emit(f"Could not collect {result['url']}: {str(e)[:100]}",
                                   "NAVIGATION", "failed")
                        continue
            elif plan["intent"] == "open":
                await emit("Open-only task needs a starting URL; NEXUS will not guess a domain",
                           "NAVIGATION", "failed")
            else:
                raise ValueError("Could not identify a website or search query. Add a starting URL or clarify the task.")

            await queue.put(None)
            analyses = await worker
            worker = None
            if state["sources"]:
                await final_report(task, analyses)
            else:
                state["report"] = "No sources were collected. Check the red/amber actions and retry with a starting URL or a more specific search."
                await update(report=state["report"])
            if state["collected"] < state["target"]:
                await emit(f"Collection incomplete: {state['collected']} of {state['target']} target sources collected",
                           "VERIFICATION", "warning")
            await update(status="completed", phase="Completed",
                action=f"Finished · {state['collected']} sources collected · {state['analysed']} analysed")
            await emit("Task execution ended; see verification for actual completed work", "VERIFICATION",
                       "success" if state["collected"] > 0 else "failed")
    except asyncio.CancelledError:
        if worker and not worker.done():
            worker.cancel()
        await update(status="stopped", phase="Stopped", action="Task stopped by user")
        await emit("Research stopped by user", "VERIFICATION", "warning")
        raise
    except Exception as e:
        if worker and not worker.done():
            worker.cancel()
        state["error"] = str(e)
        await update(status="error", phase="Error", action="Research failed")
        await emit(f"Task failed: {str(e)[:200]}", "ERROR", "failed")
    finally:
        if shot:
            shot.cancel()
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        await update(elapsed=round(time.time()-started, 1))

class StartRequest(BaseModel):
    task: str
    start_url: str = ""

@app.get("/")
async def dashboard():
    return FileResponse("static/index.html")

@app.head("/")
async def dashboard_head():
    return JSONResponse(content=None, status_code=200)

@app.get("/health")
async def health():
    return {"status": "healthy", "app": APP_NAME, "version": "0.4"}

@app.get("/api/status")
async def get_status():
    return JSONResponse({"state": state, "events": events[-70:]})

@app.post("/api/start")
async def start_agent(req: StartRequest):
    global agent_task
    if agent_task and not agent_task.done():
        return JSONResponse({"error": "A task is already running"}, status_code=409)
    if not req.task.strip():
        return JSONResponse({"error": "Enter a task first"}, status_code=400)
    url = ""
    if req.start_url.strip():
        url = validate_public_url(req.start_url)
        if not url:
            return JSONResponse({"error": "Enter a valid public HTTP/HTTPS URL"}, status_code=400)
    agent_task = asyncio.create_task(run_agent(req.task.strip(), url))
    return {"success": True, "message": "NEXUS started", "version": "0.4"}

@app.post("/api/stop")
async def stop_agent():
    if agent_task and not agent_task.done():
        agent_task.cancel()
        return {"success": True, "message": "Stopping NEXUS"}
    return {"success": False, "message": "No active task"}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    ws_layers[ws] = 1
    try:
        await ws.send_json({"type": "state", "state": state, "events": events[-70:]})
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "layer":
                try:
                    ws_layers[ws] = int(msg.get("layer", 1))
                except (TypeError, ValueError):
                    ws_layers[ws] = 1
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_clients.discard(ws)
        ws_layers.pop(ws, None)
