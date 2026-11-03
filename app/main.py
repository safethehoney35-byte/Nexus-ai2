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
state = {"status":"idle","phase":"Ready","action":"Waiting for instructions","task":"",
         "current_url":"","current_title":"","collected":0,"analysed":0,"target":MAX_SOURCES,
         "plan":None,"sources":[],"report":"","error":"","elapsed":0}
events, ws_clients, ws_layers = [], set(), {}
agent_task = None
seen_urls = set()

async def broadcast(data):
    dead=[]
    for ws in list(ws_clients):
        try: await ws.send_json(data)
        except Exception: dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws); ws_layers.pop(ws,None)

async def emit(message, category="INFO"):
    event={"time":time.strftime("%H:%M:%S"),"category":category,"message":str(message)}
    events.append(event)
    if len(events)>150: del events[:-150]
    await broadcast({"type":"event","event":event})

async def update(**kwargs):
    state.update(kwargs)
    await broadcast({"type":"state","state":state,"events":events[-60:]})

def validate_public_url(url):
    try:
        p=urlparse((url or "").strip())
        if p.scheme not in ("http","https") or not p.hostname or p.username or p.password: return None
        host=p.hostname.lower()
        if host in ("localhost","localhost.localdomain"): return None
        try:
            ip=ipaddress.ip_address(host)
            if not ip.is_global: return None
        except ValueError: pass
        return urlunparse(p)
    except Exception: return None

def parse_json(text):
    text=re.sub(r"^```(?:json)?\s*|\s*```$","",text.strip())
    a,b=text.find("{"),text.rfind("}")
    if a<0 or b<a: raise ValueError("AI returned invalid JSON")
    return json.loads(text[a:b+1])

async def ask_groq(system,prompt,max_tokens=1800):
    if not client: raise RuntimeError("GROQ_API_KEY is missing in Render environment variables")
    r=await client.chat.completions.create(model=GROQ_MODEL,
        messages=[{"role":"system","content":system},{"role":"user","content":prompt}],
        temperature=0.2,max_tokens=max_tokens)
    return r.choices[0].message.content or ""

async def create_plan(task,start_url):
    await update(phase="Planning",action="Understanding your instruction")
    await emit("Reading and interpreting your instruction","PLANNER")
    system="""Return ONLY JSON:
{"intent":"open|search|research|interact","target_site":"named site or empty","start_url":"known URL or empty","search_query":"specific query or empty","goal":"short goal","target_sources":8}
Preserve named websites. 'Open X' means open X, not search for alternatives. If asked to find something on a named site, open it first. Never invent URLs. Do not plan bypassing CAPTCHA, paywalls, login gates or access restrictions. Maximum target_sources 12."""
    plan=parse_json(await ask_groq(system,f"USER TASK:\n{task}\nSTART URL:\n{start_url or 'Not supplied'}",1000))
    plan["target_sources"]=min(max(int(plan.get("target_sources",8)),1),MAX_SOURCES)
    plan["start_url"]=start_url or (validate_public_url(plan.get("start_url","")) if plan.get("start_url") else "")
    plan["search_query"]=str(plan.get("search_query") or "").strip()
    plan["intent"]=str(plan.get("intent") or "research").lower()
    state["target"]=plan["target_sources"]
    await update(phase="Plan ready",plan=plan,action="Research plan created")
    await emit("Research plan created","PLANNER")
    return plan

async def search_web(query):
    await update(phase="Searching",action=f"Searching: {query}")
    await emit(f"Searching the web for: {query}","SEARCH")
    url="https://html.duckduckgo.com/html/?q="+quote_plus(query)
    async with httpx.AsyncClient(timeout=12,follow_redirects=True,
        headers={"User-Agent":"Mozilla/5.0 NEXUSResearch/0.3"}) as h:
        r=await h.get(url); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser"); out=[]
    for item in soup.select(".result"):
        a=item.select_one(".result__a")
        sn=item.select_one(".result__snippet")
        if not a: continue
        u=validate_public_url(a.get("href",""))
        if u: out.append({"title":a.get_text(" ",strip=True),"url":u,
                          "snippet":sn.get_text(" ",strip=True) if sn else ""})
    await emit(f"Found {len(out)} potential results","RESULTS")
    return out

async def extract_page(page):
    return await page.evaluate("""() => ({
      title:document.title||"",
      description:document.querySelector('meta[name="description"]')?.content||"",
      text:(document.body?.innerText||"").slice(0,9000),
      links:Array.from(document.querySelectorAll("a")).filter(a=>{
        const r=a.getBoundingClientRect(); return r.width>0&&r.height>0&&a.href.startsWith("http");
      }).slice(0,50).map(a=>({text:(a.innerText||"").trim(),href:a.href}))
    })""")

async def open_page(page,url):
    u=validate_public_url(url)
    if not u: raise ValueError("Invalid public HTTP/HTTPS URL")
    await emit(f"Opening website: {u}","NAVIGATION")
    try: await page.goto(u,wait_until="domcontentloaded",timeout=15000)
    except Exception as e:
        await emit(f"Navigation warning: {str(e)[:130]}","WARNING")
        if page.url in ("","about:blank"): raise
    title=await page.title()
    await update(current_url=page.url,current_title=title,action=f"Viewing {title or page.url}")
    await emit(f"Page opened: {title or page.url}","SUCCESS")

async def find_search_box(page):
    for sel in ['input[type="search"]','input[name="q"]','input[placeholder*="search" i]',
                'input[aria-label*="search" i]','textarea[placeholder*="search" i]']:
        try:
            loc=page.locator(sel).first
            if await loc.is_visible(timeout=350): return loc
        except Exception: pass
    return None

async def interact_search(page,query):
    await emit("Inspecting page for a visible search box","BROWSER")
    box=await find_search_box(page)
    if not box:
        await emit("No supported visible search box found","WARNING"); return False
    try:
        await box.click(timeout=2000); await emit("Clicked search box","CLICK")
        await box.fill(query,timeout=2500); await emit(f"Typed: {query}","TYPE")
        await box.press("Enter",timeout=2500); await emit("Submitted website search","CLICK")
        try: await page.wait_for_load_state("domcontentloaded",timeout=6000)
        except Exception: pass
        await update(current_url=page.url,current_title=await page.title())
        return True
    except Exception as e:
        await emit(f"Search interaction failed: {str(e)[:130]}","ERROR"); return False

async def click_relevant_link(page,keywords):
    terms=[x.lower() for x in keywords.split() if len(x)>2]
    links=page.locator("a")
    try: count=min(await links.count(),60)
    except Exception: return False
    for i in range(count):
        link=links.nth(i)
        try:
            if not await link.is_visible(timeout=200): continue
            label=(await link.inner_text(timeout=350)).strip()
            if not label or sum(t in label.lower() for t in terms)<1: continue
            href=await link.get_attribute("href")
            if not validate_public_url(urljoin(page.url,href or "")): continue
            await emit(f"Found relevant link: {label[:80]}","BROWSER")
            await link.click(timeout=3500); await emit(f"Clicked: {label[:80]}","CLICK")
            try: await page.wait_for_load_state("domcontentloaded",timeout=6000)
            except Exception: pass
            await update(current_url=page.url,current_title=await page.title())
            return True
        except Exception: pass
    return False

async def save_source(data):
    u=validate_public_url(data.get("url",""))
    if not u or u in seen_urls: return False
    seen_urls.add(u); data.update(url=u,id=len(state["sources"])+1,status="Collected")
    state["sources"].append(data)
    await update(collected=len(state["sources"]))
    await emit(f"Source {len(state['sources'])}/{state['target']} collected: {data.get('title','Untitled')[:90]}","COLLECTION")
    return True

async def analyse_batch(batch):
    await update(phase="Analysis",action=f"Analysing {len(batch)} sources")
    await emit(f"Analysing batch of {len(batch)} sources","ANALYST")
    compact=[{"title":s.get("title",""),"url":s.get("url",""),"text":s.get("text","")[:3500]} for s in batch]
    result=await ask_groq("Analyse only supplied source text. Do not invent facts. Note uncertainty and conflicts.",json.dumps(compact))
    for s in batch: s["status"]="Analysed"
    state["analysed"]+=len(batch)
    await update(analysed=state["analysed"])
    await emit(f"Finished analysing {len(batch)} sources","SUCCESS")
    return result

async def analysis_worker(queue):
    results=[]
    while True:
        first=await queue.get()
        if first is None: break
        batch=[first]; stop=False
        while len(batch)<BATCH_SIZE:
            try:
                item=await asyncio.wait_for(queue.get(),timeout=.8)
                if item is None: stop=True; break
                batch.append(item)
            except asyncio.TimeoutError: break
        try: results.append(await analyse_batch(batch))
        except Exception as e: await emit(f"Analysis error: {str(e)[:130]}","ERROR")
        if stop: break
    return results

async def final_report(task,analyses):
    await update(phase="Final report",action="Preparing final report")
    sources=[{"title":s.get("title"),"url":s.get("url"),"status":s.get("status")} for s in state["sources"]]
    prompt=f"TASK:\n{task}\nANALYSES:\n{json.dumps(analyses)}\nSOURCES:\n{json.dumps(sources)}\nWrite direct answer, findings, evidence, uncertainty and source URLs. Never invent facts."
    report=await ask_groq("You are NEXUS AI's careful research report writer.",prompt,2500)
    state["report"]=report; await emit("Final report completed","SUCCESS")

async def screenshot_worker(page):
    while True:
        await asyncio.sleep(3)
        if not any(v==3 for v in ws_layers.values()): continue
        try:
            if page.is_closed(): continue
            img=await page.screenshot(type="jpeg",quality=45,timeout=5000)
            await broadcast({"type":"screenshot","image":base64.b64encode(img).decode("ascii")})
        except Exception: pass

async def run_agent(task,start_url):
    global seen_urls
    seen_urls=set()
    state.update(sources=[],report="",analysed=0,collected=0,error="",current_url="",current_title="")
    started=time.time(); browser=None; shot=None; worker=None; queue=None
    await update(status="running",phase="Starting",task=task,action="Starting NEXUS AI",plan=None)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
            context=await browser.new_context(viewport={"width":1365,"height":850})
            async def block_heavy(route):
                if route.request.resource_type in ("image","font","media"): await route.abort()
                else: await route.continue_()
            await context.route("**/*",block_heavy)
            page=await context.new_page(); page.set_default_timeout(4000)
            shot=asyncio.create_task(screenshot_worker(page))
            plan=await create_plan(task,start_url)
            queue=asyncio.Queue(); worker=asyncio.create_task(analysis_worker(queue))
            url=plan.get("start_url") or ""; query=plan.get("search_query","")
            if url:
                await open_page(page,url)
                if query: await interact_search(page,query)
                data=await extract_page(page)
                if await save_source({"title":data["title"],"url":page.url,"text":data["text"],"description":data["description"]}):
                    await queue.put(state["sources"][-1])
            elif query and plan["intent"] in ("search","research"):
                results=await search_web(query)
                for result in results:
                    if len(state["sources"])>=state["target"]: break
                    await open_page(page,result["url"]); data=await extract_page(page)
                    saved=await save_source({"title":data["title"] or result["title"],"url":page.url,
                        "text":data["text"],"description":data["description"] or result["snippet"]})
                    if saved: await queue.put(state["sources"][-1])
            elif plan["intent"]=="open":
                await emit("Open-only task needs a starting URL. Add it in the URL field; NEXUS will not guess a domain.","WARNING")
            else:
                raise ValueError("Could not identify a website or search query. Add a starting URL or clarify the task.")
            await queue.put(None)
            analyses=await worker; worker=None
            if state["sources"]: await final_report(task,analyses)
            else: state["report"]="No sources collected. Check activity and try again with a starting URL."
            await update(status="completed",phase="Completed",action="Research finished")
            await emit("NEXUS task completed","SUCCESS")
    except asyncio.CancelledError:
        if worker and not worker.done(): worker.cancel()
        await update(status="stopped",phase="Stopped",action="Task stopped")
        await emit("Research stopped by user","WARNING")
        raise
    except Exception as e:
        if worker and not worker.done(): worker.cancel()
        state["error"]=str(e); await update(status="error",phase="Error",action="Research failed")
        await emit(f"Error: {str(e)[:200]}","ERROR")
    finally:
        if shot: shot.cancel()
        if browser:
            try: await browser.close()
            except Exception: pass
        await update(elapsed=round(time.time()-started,1))

class StartRequest(BaseModel):
    task: str
    start_url: str = ""

@app.get("/")
async def dashboard(): return FileResponse("static/index.html")

@app.head("/")
async def dashboard_head(): return JSONResponse(content=None,status_code=200)

@app.get("/health")
async def health(): return {"status":"healthy","app":APP_NAME}

@app.get("/api/status")
async def get_status(): return JSONResponse({"state":state,"events":events[-60:]})

@app.post("/api/start")
async def start_agent(req:StartRequest):
    global agent_task
    if agent_task and not agent_task.done():
        return JSONResponse({"error":"A task is already running"},status_code=409)
    if not req.task.strip():
        return JSONResponse({"error":"Enter a task first"},status_code=400)
    url=""
    if req.start_url.strip():
        url=validate_public_url(req.start_url)
        if not url: return JSONResponse({"error":"Enter a valid public HTTP/HTTPS URL"},status_code=400)
    events.clear()
    agent_task=asyncio.create_task(run_agent(req.task.strip(),url))
    return {"success":True,"message":"NEXUS started"}

@app.post("/api/stop")
async def stop_agent():
    if agent_task and not agent_task.done():
        agent_task.cancel()
        return {"success":True,"message":"Stopping NEXUS"}
    return {"success":False,"message":"No active task"}

@app.websocket("/ws")
async def websocket_endpoint(ws:WebSocket):
    await ws.accept(); ws_clients.add(ws); ws_layers[ws]=1
    try:
        await ws.send_json({"type":"state","state":state,"events":events[-60:]})
        while True:
            msg=await ws.receive_json()
            if msg.get("type")=="layer":
                try: ws_layers[ws]=int(msg.get("layer",1))
                except (TypeError,ValueError): ws_layers[ws]=1
    except WebSocketDisconnect: pass
    except Exception: pass
    finally:
        ws_clients.discard(ws); ws_layers.pop(ws,None)
