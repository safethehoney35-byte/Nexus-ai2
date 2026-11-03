import os
import re
import json
import time
import asyncio
import hashlib
import ipaddress
from urllib.parse import quote_plus, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from groq import AsyncGroq
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

app = FastAPI(title="NEXUS AI")
app.mount("/static", StaticFiles(directory="static"), name="static")

client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

MAX_QUERIES = 5
MAX_RESULTS_PER_QUERY = 5
MAX_PAGE_CHARS = 5000
MAX_TASK_SECONDS = 240
BATCH_SIZE = 4

state = {
    "status": "Idle",
    "phase": "Waiting",
    "task": "",
    "task_id": None,
    "current_action": "Waiting for a task",
    "current_url": "",
    "collected": 0,
    "analysed": 0,
    "target": 0,
    "started_at": None,
    "last_error": None,
    "report": "",
    "plan": [],
}

events = []
connections = {}
agent_task = None
stop_event = asyncio.Event()
browser = None
page = None


class StartRequest(BaseModel):
    task: str
    start_url: str = ""


def log(message, kind="info"):
    item = {
        "time": time.strftime("%H:%M:%S"),
        "message": str(message)[:500],
        "kind": kind,
    }
    events.append(item)

    if len(events) > 150:
        del events[:-150]

    return item


async def broadcast(data):
    dead = []

    for ws in list(connections):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)

    for ws in dead:
        connections.pop(ws, None)


async def update_state(**kwargs):
    state.update(kwargs)
    await broadcast({"type": "state", "data": state})


async def emit(message, kind="info"):
    item = log(message, kind)
    await broadcast({"type": "event", "data": item})


def validate_public_url(url):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        host = parsed.hostname

        if not host:
            return False

        if host.lower() in ("localhost", "localhost.localdomain"):
            return False

        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            pass

        return True

    except Exception:
        return False


async def groq_text(system, user, max_tokens=1200):
    if not client:
        raise RuntimeError("GROQ_API_KEY is missing in Render environment variables.")

    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )

    return response.choices[0].message.content or ""


def extract_json(text):
    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("AI did not return valid JSON.")

    return json.loads(text[start:end + 1])


async def create_plan(task, start_url):
    await update_state(
        phase="Planning",
        current_action="Groq is creating the research plan",
    )

    await emit("Planner started.")

    prompt = f"""
Create a bounded public-web research plan.

User task:
{task}

Starting URL:
{start_url or "None"}

Return JSON only, in this exact structure:
{{
  "queries": ["query 1", "query 2"],
  "target": 12,
  "reason": "short explanation"
}}

Rules:
- Maximum 5 distinct search queries.
- Maximum 5 useful results per query.
- Target must be between 1 and 20.
- Queries must directly relate to the task.
- Do not include login, payment, private data or restricted-access tasks.
- Do not invent URLs.
"""

    raw = await groq_text(
        "You are a careful web research planner. Return valid JSON only.",
        prompt,
        500,
    )

    plan = extract_json(raw)

    queries = plan.get("queries", [])
    queries = [
        str(q).strip()[:180]
        for q in queries
        if str(q).strip()
    ][:MAX_QUERIES]

    if not queries:
        raise ValueError("The research planner returned no search queries.")

    target = int(plan.get("target", 12))
    target = max(1, min(target, MAX_QUERIES * MAX_RESULTS_PER_QUERY))

    return {
        "queries": queries,
        "target": target,
        "reason": str(plan.get("reason", ""))[:300],
    }


async def extract_page(page):
    return await page.evaluate(
        """() => {
            const title = document.title || "";
            const text = document.body
                ? document.body.innerText.slice(0, 5000)
                : "";

            const links = Array.from(document.querySelectorAll("a[href]"))
                .filter(a => {
                    const r = a.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                })
                .map(a => ({
                    title: (a.innerText || a.getAttribute("aria-label") || "")
                        .trim().slice(0, 180),
                    url: a.href
                }))
                .filter(a =>
                    a.title &&
                    a.url.startsWith("http")
                )
                .slice(0, 40);

            return {
                title,
                text,
                links,
                url: location.href
            };
        }"""
    )


async def analyse_batch(batch, task):
    if not batch:
        return ""

    await update_state(
        phase="Analysing",
        current_action=f"Analysing a batch of {len(batch)} sources",
    )

    compact = []

    for item in batch:
        compact.append({
            "title": item["title"],
            "url": item["url"],
            "content": item["content"][:2500],
        })

    prompt = f"""
Research objective:
{task}

Analyse these public web sources.

Return:
- Important findings
- Relevant facts
- Agreements or disagreements between sources
- Missing information
- Source URLs

Do not invent facts.
Clearly distinguish source claims from conclusions.

Sources:
{json.dumps(compact, ensure_ascii=False)}
"""

    return await groq_text(
        "You are a careful research analyst. Use only the supplied evidence.",
        prompt,
        1000,
    )


async def analysis_worker(queue, task, results):
    batch = []

    while True:
        item = await queue.get()

        try:
            if item is None:
                if batch:
                    result = await analyse_batch(batch, task)
                    results.append(result)

                    state["analysed"] += len(batch)
                    await emit(
                        f"Final batch analysed: {len(batch)} sources."
                    )
                    await broadcast({"type": "state", "data": state})

                break

            batch.append(item)

            if len(batch) >= BATCH_SIZE:
                result = await analyse_batch(batch, task)
                results.append(result)

                state["analysed"] += len(batch)
                await emit(
                    f"Analysis batch completed: {len(batch)} sources.",
                    "success",
                )
                batch = []

                await broadcast({"type": "state", "data": state})

        finally:
            queue.task_done()


async def collect_research(task, plan, start_url):
    global browser, page

    queue = asyncio.Queue(maxsize=8)
    analyses = []

    worker = asyncio.create_task(
        analysis_worker(queue, task, analyses)
    )

    collected_urls = set()
    collected_count = 0

    try:
        for query in plan["queries"]:
            if stop_event.is_set():
                break

            if collected_count >= plan["target"]:
                break

            await update_state(
                phase="Collecting",
                current_action=f"Searching: {query}",
            )

            await emit(f"Searching the web: {query}")

            search_url = (
                "https://www.bing.com/search?q="
                + quote_plus(query)
            )

            try:
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
            except PlaywrightTimeout:
                await emit("Search page took too long to load.", "warning")
            except Exception as exc:
                await emit(f"Search navigation issue: {exc}", "warning")
                continue

            await page.wait_for_timeout(1200)

            try:
                data = await extract_page(page)
            except Exception as exc:
                await emit(f"Could not read search results: {exc}", "error")
                continue

            candidates = []

            for link in data["links"]:
                url = link["url"]
                host = (urlparse(url).hostname or "").lower()

                if not validate_public_url(url):
                    continue

                if "bing.com" in host:
                    continue

                if url in collected_urls:
                    continue

                candidates.append(link)

            candidates = candidates[:MAX_RESULTS_PER_QUERY]

            for candidate in candidates:
                if stop_event.is_set():
                    break

                if collected_count >= plan["target"]:
                    break

                url = candidate["url"]

                await update_state(
                    current_action=f"Reading: {candidate['title'][:80]}",
                    current_url=url,
                )

                try:
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )

                    await page.wait_for_timeout(500)

                    content = await extract_page(page)

                    text = content["text"].strip()

                    if len(text) < 100:
                        continue

                    record = {
                        "title": content["title"] or candidate["title"],
                        "url": content["url"],
                        "content": text[:MAX_PAGE_CHARS],
                    }

                    if record["url"] in collected_urls:
                        continue

                    collected_urls.add(record["url"])
                    collected_count += 1

                    state["collected"] = collected_count

                    await queue.put(record)

                    await emit(
                        f"Collected source {collected_count}: {record['title']}",
                        "success",
                    )

                    await broadcast({"type": "state", "data": state})

                except PlaywrightTimeout:
                    await emit("Source timed out; moving to the next one.", "warning")

                except Exception as exc:
                    await emit(f"Source skipped: {str(exc)[:180]}", "warning")

        await queue.put(None)
        await worker

        return analyses

    except asyncio.CancelledError:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        raise


async def final_report(task, analyses):
    await update_state(
        phase="Final report",
        current_action="Combining research findings",
    )

    if not analyses:
        return "No usable sources were collected. Try a more specific research task."

    prompt = f"""
Research task:
{task}

The following are analyses of collected public web sources:

{json.dumps(analyses, ensure_ascii=False)}

Create a useful final research report with:
1. Executive summary
2. Main findings
3. Important details
4. Conflicting or uncertain information
5. Limitations
6. Source URLs

Do not invent facts or claim that unverified information is certain.
"""

    return await groq_text(
        "You are the final report writer for NEXUS AI. Be accurate and transparent.",
        prompt,
        1800,
    )


async def run_agent(request):
    global browser, page

    task = request.task.strip()
    start_url = request.start_url.strip()

    if not task:
        raise ValueError("Please enter a research task.")

    if start_url and not validate_public_url(start_url):
        raise ValueError("Please provide a valid public HTTP or HTTPS starting URL.")

    stop_event.clear()

    await update_state(
        status="Running",
        phase="Starting",
        task=task,
        task_id=hashlib.sha256(
            f"{time.time()}-{task}".encode()
        ).hexdigest()[:12],
        current_url="",
        current_action="Starting browser",
        collected=0,
        analysed=0,
        target=0,
        started_at=time.time(),
        last_error=None,
        report="",
        plan=[],
    )

    await emit("NEXUS research session started.", "success")

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            context = await browser.new_context(
                viewport={"width": 1365, "height": 850},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
                ),
            )

            page = await context.new_page()
            page.set_default_timeout(10000)

            plan = await create_plan(task, start_url)

            state["plan"] = plan["queries"]
            state["target"] = plan["target"]

            await emit(
                f"Plan ready: {len(plan['queries'])} queries, "
                f"target {plan['target']} sources.",
                "success",
            )

            await broadcast({"type": "state", "data": state})

            analyses = await asyncio.wait_for(
                collect_research(task, plan, start_url),
                timeout=MAX_TASK_SECONDS,
            )

            if stop_event.is_set():
                return

            report = await final_report(task, analyses)

            await update_state(
                status="Completed",
                phase="Finished",
                current_action="Research completed",
                report=report,
            )

            await emit("Final research report is ready.", "success")

            await broadcast({"type": "state", "data": state})

    except asyncio.CancelledError:
        await update_state(
            status="Stopped",
            phase="Stopped",
            current_action="Task stopped by user",
        )
        await emit("Research stopped.", "warning")
        raise

    except Exception as exc:
        state["last_error"] = str(exc)[:500]

        await update_state(
            status="Error",
            phase="Error",
            current_action="Research encountered an error",
        )

        await emit(f"ERROR: {str(exc)[:300]}", "error")

    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass

        browser = None
        page = None


@app.get("/")
async def dashboard():
    return FileResponse("static/index.html")
   
@app.get("/health")
async def health():
    return {"status": "healthy", "app": "NEXUS AI"}


@app.get("/api/status")
async def get_status():
    return {
        "state": state,
        "events": events[-50:],
    }


@app.post("/api/start")
async def start_task(request: StartRequest):
    global agent_task

    if agent_task and not agent_task.done():
        return {"error": "A research task is already running."}

    if not request.task.strip():
        return {"error": "Please enter a task."}

    agent_task = asyncio.create_task(run_agent(request))

    return {"message": "Research started."}


@app.post("/api/stop")
async def stop_task():
    global agent_task

    stop_event.set()

    if agent_task and not agent_task.done():
        agent_task.cancel()

    return {"message": "Stop requested."}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connections[ws] = 1

    try:
        await ws.send_json({
            "type": "initial",
            "state": state,
            "events": events[-50:],
        })

        while True:
            message = await ws.receive_json()

            if message.get("type") == "layer":
                connections[ws] = int(message.get("layer", 1))

    except WebSocketDisconnect:
        connections.pop(ws, None)

    except Exception:
        connections.pop(ws, None)


async def screenshot_loop():
    while True:
        await asyncio.sleep(2)

        if not page or not browser:
            continue

        if not any(layer == 3 for layer in connections.values()):
            continue

        try:
            screenshot = await page.screenshot(
                type="jpeg",
                quality=45,
                timeout=3000,
            )

            import base64

            encoded = base64.b64encode(screenshot).decode("ascii")

            await broadcast({
                "type": "screenshot",
                "data": encoded,
            })

        except Exception:
            pass


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(screenshot_loop())
