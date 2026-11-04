from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

APP_VERSION = "1.0.0"
ACCESS_KEY = os.getenv("NEXUS_ACCESS_KEY", "").strip()
SCAN_INTERVAL = 2.0
MAX_ELEMENTS = 80
LOG_LIMIT = 120
HOST_VALIDATION_CACHE: dict[str, tuple[float, bool]] = {}

DETECTION_ONLY = {
    "watchAds": '[data-testid="lv-membership-plan-option-wrapper-btn"]',
    "continueButton": '[data-testid="action-wall-continue-action-btn"]',
    "adCounter": ".carousel-items-counter p",
    "adLabel": ".lv-card__ad-indicator p",
    "skipAdContainer": '[data-testid="lv-lib-carousel-skip-btn"]',
    "successHeading": '[data-testid="success-title"]',
    "contentReady": ".success-wrapper h2",
    "openHereButton": ".success-wrapper button",
}
BLOCKED_TERMS = re.compile(
    r"\b(ad|ads|advert|advertisement|reward|claim|skip ad|watch ads|"
    r"membership|payment|pay now|purchase|checkout|subscribe|"
    r"delete|remove account|transfer|withdraw|payout)\b", re.I
)

state: dict[str, Any] = {
    "status": "STOPPED", "url": "", "title": "", "message": "Ready",
    "started_at": None, "last_scan": None, "elements": [],
    "detections": [], "activity": [], "errors": [], "session_id": None,
}
playwright = None
browser = None
context = None
page = None
monitor_task: asyncio.Task | None = None
start_task: asyncio.Task | None = None
operation_lock = asyncio.Lock()
log = logging.getLogger("nexus")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def add_log(message: str, level: str = "INFO") -> None:
    item = {"time": time.strftime("%H:%M:%S"), "level": level, "message": str(message)[:500]}
    state["activity"].append(item)
    state["activity"] = state["activity"][-LOG_LIMIT:]
    if level == "ERROR":
        state["errors"].append(item)
        state["errors"] = state["errors"][-LOG_LIMIT:]
    getattr(log, "error" if level == "ERROR" else "info")(message)


def require_key(x_nexus_key: str | None) -> None:
    if not ACCESS_KEY:
        raise HTTPException(503, "NEXUS_ACCESS_KEY is not configured on the server.")
    if not x_nexus_key or x_nexus_key != ACCESS_KEY:
        raise HTTPException(401, "Access key missing or incorrect.")


async def validate_public_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Only valid http:// and https:// URLs are supported.")
        if parsed.username or parsed.password:
            raise ValueError("URLs containing embedded credentials are not allowed.")
        host = parsed.hostname.rstrip(".").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        cache_key = f"{host}:{port}"
        cached = HOST_VALIDATION_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < 60:
            if cached[1]:
                return value
            raise ValueError("The URL resolves to a private or non-public address.")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise ValueError("Local/private network addresses are not allowed.")
        try:
            ip = ipaddress.ip_address(host)
            addresses = [ip]
        except ValueError:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
                timeout=4.0,
            )
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not addresses:
            raise ValueError("The hostname did not resolve.")
        is_public = all(addr.is_global for addr in addresses)
        HOST_VALIDATION_CACHE[cache_key] = (time.monotonic(), is_public)
        if not is_public:
            raise ValueError("The URL resolves to a private or non-public address.")
        return value
    except asyncio.TimeoutError:
        raise ValueError("DNS lookup timed out. Check the hostname and try again.")
    except (socket.gaierror, OSError):
        raise ValueError("The hostname could not be resolved.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"URL validation failed: {type(exc).__name__}")


async def route_guard(route) -> None:
    request_url = route.request.url
    try:
        await validate_public_url(request_url)
        await route.continue_()
    except Exception:
        add_log(f"Blocked a request to a non-public or invalid destination: {urlparse(request_url).hostname}", "WARN")
        await route.abort()


async def scan_page() -> None:
    if page is None or page.is_closed():
        return
    try:
        result = await page.evaluate("""(limit) => {
          const nodes = [...document.querySelectorAll(
            'button, a[href], input, select, textarea, [role="button"]'
          )].slice(0, limit);
          return nodes.map((el, index) => {
            if (!el.dataset.nexusId) el.dataset.nexusId = 'nx-' + crypto.randomUUID();
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const visible = style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
            const text = (el.innerText || el.value || el.getAttribute('aria-label')
              || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 150);
            const tag = el.tagName.toLowerCase();
            return {
              id: el.dataset.nexusId, tag, text: text || '(unlabelled element)',
              type: (el.getAttribute('type') || '').toLowerCase(),
              href: tag === 'a' ? el.href : '',
              visible, disabled: !!el.disabled,
              aria: el.getAttribute('aria-label') || ''
            };
          });
        }""", MAX_ELEMENTS)
        for item in result:
            descriptor = " ".join([item.get("text", ""), item.get("aria", ""), item.get("href", "")])
            item["sensitive"] = bool(BLOCKED_TERMS.search(descriptor))
            item["clickable"] = (
                item["visible"] and not item["disabled"] and not item["sensitive"]
                and ((item["tag"] == "button" and item["type"] == "button")
                     or item["tag"] == "a")
            )
        detections = []
        for name, selector in DETECTION_ONLY.items():
            try:
                count = await page.locator(selector).count()
                sample = ""
                if count:
                    sample = (await page.locator(selector).first.inner_text(timeout=500)).strip()[:100]
                detections.append({"name": name, "selector": selector, "count": count, "sample": sample,
                                   "detection_only": True})
            except Exception:
                detections.append({"name": name, "selector": selector, "count": 0, "sample": "",
                                   "detection_only": True})
        state["elements"] = result
        state["detections"] = detections
        state["last_scan"] = time.time()
        try:
            state["title"] = await page.title()
        except Exception:
            pass
    except Exception as exc:
        add_log(f"DOM scan failed: {type(exc).__name__}: {str(exc)[:180]}", "ERROR")


async def monitor_loop() -> None:
    while state["status"] == "RUNNING":
        await scan_page()
        await asyncio.sleep(SCAN_INTERVAL)


async def cleanup_browser() -> None:
    global playwright, browser, context, page, monitor_task
    current_monitor = monitor_task
    monitor_task = None
    if current_monitor and current_monitor is not asyncio.current_task():
        current_monitor.cancel()
        try:
            await asyncio.wait_for(current_monitor, 1.5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    for name, obj in (("page", page), ("context", context), ("browser", browser), ("playwright", playwright)):
        if obj is not None:
            try:
                closer = getattr(obj, "close", None)
                if closer:
                    await asyncio.wait_for(closer(), 2.0)
                elif name == "playwright":
                    await asyncio.wait_for(obj.stop(), 2.0)
            except Exception as exc:
                add_log(f"{name} cleanup: {type(exc).__name__}", "WARN")
    page = context = browser = playwright = None


async def start_worker(url: str, session_id: str) -> None:
    global playwright, browser, context, page, monitor_task
    try:
        await validate_public_url(url)
        playwright = await async_playwright().start()
        browser = await asyncio.wait_for(
            playwright.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--no-zygote",
            ]),
            timeout=18.0,
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
            accept_downloads=False,
        )
        await context.route("**/*", route_guard)
        page = await context.new_page()
        page.set_default_timeout(2500)
        page.set_default_navigation_timeout(20000)
        add_log("Chromium launched. Opening the requested page.")
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if state["session_id"] != session_id or state["status"] != "STARTING":
            return
        state.update(status="RUNNING", message="Browser is running", started_at=time.time())
        add_log(f"Page loaded: {url}")
        await scan_page()
        monitor_task = asyncio.create_task(monitor_loop())
    except asyncio.CancelledError:
        add_log("Start operation cancelled.")
        raise
    except Exception as exc:
        state.update(status="ERROR", message=f"{type(exc).__name__}: {str(exc)[:220]}")
        add_log(state["message"], "ERROR")
        await cleanup_browser()


class StartRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ElementRequest(BaseModel):
    element_id: str = Field(min_length=4, max_length=100)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await cleanup_browser()


app = FastAPI(title="NEXUS AI Browser Monitor", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def home():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "healthy", "app": "NEXUS AI", "version": APP_VERSION,
            "browser_status": state["status"]}


@app.get("/api/status")
async def get_status(x_nexus_key: str | None = Header(default=None)):
    require_key(x_nexus_key)
    return {k: v for k, v in state.items() if k not in ("activity", "errors", "elements", "detections")}


@app.get("/api/data")
async def get_data(x_nexus_key: str | None = Header(default=None)):
    require_key(x_nexus_key)
    return {"status": state["status"], "url": state["url"], "title": state["title"],
            "message": state["message"], "elements": state["elements"],
            "detections": state["detections"], "activity": state["activity"][-50:],
            "errors": state["errors"][-30:]}


@app.post("/api/start")
async def start(req: StartRequest, x_nexus_key: str | None = Header(default=None)):
    global start_task
    require_key(x_nexus_key)
    try:
        await validate_public_url(req.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    async with operation_lock:
        if state["status"] in ("STARTING", "RUNNING", "STOPPING"):
            raise HTTPException(409, f"Browser is currently {state['status'].lower()}.")
        state.update(status="STARTING", url=req.url, title="", message="Starting browser...",
                     elements=[], detections=[], started_at=None, last_scan=None,
                     session_id=str(uuid.uuid4()))
        sid = state["session_id"]
        add_log(f"Start requested for {req.url}")
        start_task = asyncio.create_task(start_worker(req.url, sid))
    return {"ok": True, "status": "STARTING", "message": "Start request accepted."}


@app.post("/api/stop")
async def stop(x_nexus_key: str | None = Header(default=None)):
    global start_task
    require_key(x_nexus_key)
    async with operation_lock:
        if state["status"] == "STOPPED":
            return {"ok": True, "status": "STOPPED"}
        state.update(status="STOPPING", message="Stopping browser...")
        add_log("Stop requested.")
        pending_start = start_task
        if pending_start and not pending_start.done():
            pending_start.cancel()
            try:
                await asyncio.wait_for(pending_start, 2.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await cleanup_browser()
        start_task = None
        state.update(status="STOPPED", message="Browser stopped.", elements=[], detections=[])
        add_log("Browser stopped and resources released.")
    return {"ok": True, "status": "STOPPED"}


@app.post("/api/approve")
async def approve(req: ElementRequest, x_nexus_key: str | None = Header(default=None)):
    require_key(x_nexus_key)
    if state["status"] != "RUNNING" or page is None or page.is_closed():
        raise HTTPException(409, "Browser is not running.")
    element = next((x for x in state["elements"] if x["id"] == req.element_id), None)
    if not element:
        raise HTTPException(404, "Element is no longer in the latest scan. Scan again.")
    if not element.get("clickable") or element.get("sensitive"):
        raise HTTPException(403, "This element is not eligible for manual approval.")
    try:
        locator = page.locator(f'[data-nexus-id="{req.element_id}"]')
        if await locator.count() != 1 or not await locator.is_visible() or not await locator.is_enabled():
            raise HTTPException(409, "Element changed or is no longer available.")
        # Re-check the live element's label immediately before clicking.
        live = await locator.evaluate("""el => ({
          tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(),
          text: (el.innerText || el.value || el.getAttribute('aria-label') || '').slice(0,150),
          href: el.href || ''
        })""")
        if BLOCKED_TERMS.search(" ".join(str(v) for v in live.values())):
            raise HTTPException(403, "Safety filter blocked this element.")
        if not ((live["tag"] == "button" and live["type"] == "button") or live["tag"] == "a"):
            raise HTTPException(403, "Only ordinary buttons and links can be approved.")
        await locator.click(timeout=2500)
        add_log(f"Approved and clicked: {element['text'][:100]}")
        await asyncio.sleep(0.25)
        await scan_page()
        return {"ok": True, "message": "Approved click completed."}
    except HTTPException:
        raise
    except PlaywrightTimeoutError:
        raise HTTPException(408, "Click timed out; the page may have changed.")
    except Exception as exc:
        add_log(f"Approved click failed: {type(exc).__name__}: {str(exc)[:160]}", "ERROR")
        raise HTTPException(500, "Click failed. Check the error log.")


@app.post("/api/reject")
async def reject(req: ElementRequest, x_nexus_key: str | None = Header(default=None)):
    require_key(x_nexus_key)
    element = next((x for x in state["elements"] if x["id"] == req.element_id), None)
    add_log(f"Click declined: {element['text'][:100] if element else req.element_id}")
    return {"ok": True, "message": "No action taken."}
