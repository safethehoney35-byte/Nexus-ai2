import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent

state = {
    "running": False,
    "url": "",
    "title": "",
    "message": "Stopped",
    "results": [],
    "last_scan": None,
    "error": None,
}
playwright = None
browser = None
context = None
page = None
monitor_task = None
selectors = []

# These selectors are detection-only and must never be clicked.
DETECTION_ONLY_SELECTORS = {
    '[data-testid="lv-membership-plan-option-wrapper-btn"]',
    '[data-testid="action-wall-continue-action-btn"]',
    '.carousel-items-counter p',
    '.lv-card__ad-indicator p',
    '[data-testid="lv-lib-carousel-skip-btn"]',
    '[data-testid="success-title"]',
    '.success-wrapper h2',
    '.success-wrapper button',
}
BLOCKED_WORDS = {
    "ad", "ads", "advert", "advertisement", "skip", "reward", "claim",
    "membership", "payment", "pay", "purchase", "checkout", "subscribe",
    "login", "log in", "sign in", "delete", "remove", "transfer", "withdraw",
}


class StartRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    selectors: list[str] = Field(min_length=1, max_length=30)


class ClickRequest(BaseModel):
    selector: str
    index: int = Field(default=0, ge=0, le=100)


def validate_public_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Enter a valid public http:// or https:// URL.")
    if parsed.username or parsed.password:
        raise HTTPException(400, "URLs containing usernames or passwords are not allowed.")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise HTTPException(400, "Private/local links are blocked for safety.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except Exception:
        raise HTTPException(400, "Could not resolve this website address.")
    if not addresses:
        raise HTTPException(400, "Website address could not be resolved.")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise HTTPException(400, "Private or internal network addresses are blocked.")
    return value.strip()


def is_detection_only(selector: str) -> bool:
    s = selector.strip()
    return s in DETECTION_ONLY_SELECTORS or any(word in s.lower() for word in ("lv-card__ad", "lv-lib-carousel", "membership-plan", "action-wall", "success-wrapper"))


async def scan_now():
    global page
    if not page or page.is_closed():
        state["results"] = []
        return
    found = []
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            visible_count = 0
            samples = []
            for index in range(min(count, 20)):
                item = locator.nth(index)
                try:
                    visible = await item.is_visible()
                    enabled = await item.is_enabled() if visible else False
                    if visible:
                        visible_count += 1
                    text = (await item.inner_text(timeout=250)).strip().replace("\n", " ")[:100]
                    tag = await item.evaluate("el => el.tagName.toLowerCase()")
                    samples.append({"index": index, "tag": tag, "text": text, "visible": visible, "enabled": enabled})
                except Exception:
                    samples.append({"index": index, "tag": "?", "text": "", "visible": False, "enabled": False})
            found.append({
                "selector": selector,
                "detected": count > 0,
                "count": count,
                "visible_count": visible_count,
                "detection_only": is_detection_only(selector),
                "matches": samples,
            })
        except Exception as exc:
            found.append({"selector": selector, "detected": False, "count": 0, "visible_count": 0, "detection_only": is_detection_only(selector), "matches": [], "error": str(exc)[:160]})
    state["results"] = found
    state["last_scan"] = asyncio.get_running_loop().time()


async def monitor_page():
    while state["running"] and page and not page.is_closed():
        try:
            await scan_now()
        except Exception as exc:
            state["error"] = str(exc)[:250]
        await asyncio.sleep(0.5)


async def stop_browser():
    global playwright, browser, context, page, monitor_task, selectors
    state["running"] = False
    if monitor_task and monitor_task is not asyncio.current_task():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    monitor_task = None
    for obj in (context, browser, playwright):
        if obj:
            try:
                await (obj.close() if obj is not playwright else obj.stop())
            except Exception:
                pass
    playwright = browser = context = page = None
    selectors = []
    state.update({"running": False, "message": "Stopped", "results": [], "error": None})


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await stop_browser()


app = FastAPI(title="Nexus AI Minimal", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def home():
    return FileResponse(BASE_DIR / "minimal.html")


@app.get("/health")
async def health():
    return {"ok": True, "app": "Nexus AI Minimal"}


@app.get("/api/status")
async def get_status():
    return state


@app.post("/api/start")
async def start(req: StartRequest):
    global playwright, browser, context, page, monitor_task, selectors
    await stop_browser()
    url = validate_public_url(req.url)
    clean = []
    for item in req.selectors:
        selector = item.strip()
        if selector and selector not in clean:
            if len(selector) > 300:
                raise HTTPException(400, "A selector is too long.")
            clean.append(selector)
    if not clean:
        raise HTTPException(400, "Add at least one CSS selector.")
    selectors = clean
    state.update({"url": url, "message": "Starting…", "error": None, "results": []})
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        # Re-check every browser request, including redirects, against private network destinations.
        async def guard_route(route):
            try:
                validate_public_url(route.request.url)
                await route.continue_()
            except Exception:
                await route.abort()
        await page.route("**/*", guard_route)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        state.update({"running": True, "url": page.url, "title": await page.title(), "message": "Running", "error": None})
        await scan_now()
        monitor_task = asyncio.create_task(monitor_page())
        return {"success": True, "status": "running", "url": page.url}
    except HTTPException:
        await stop_browser()
        raise
    except Exception as exc:
        state.update({"running": False, "message": "Error", "error": str(exc)[:250]})
        await stop_browser()
        state["message"] = "Error"
        state["error"] = str(exc)[:250]
        raise HTTPException(502, f"Could not start browser: {str(exc)[:180]}")


@app.post("/api/stop")
async def stop():
    await stop_browser()
    return {"success": True, "status": "stopped"}


@app.post("/api/click")
async def approve_click(req: ClickRequest):
    if not state["running"] or not page or page.is_closed():
        raise HTTPException(409, "Browser is not running.")
    if req.selector not in selectors:
        raise HTTPException(400, "That selector is not in the current detection list.")
    if is_detection_only(req.selector):
        raise HTTPException(403, "This selector is detection-only and cannot be clicked.")
    try:
        locator = page.locator(req.selector).nth(req.index)
        if not await locator.count():
            raise HTTPException(404, "Element was not found.")
        if not await locator.is_visible() or not await locator.is_enabled():
            raise HTTPException(409, "Element is hidden or disabled.")
        tag = (await locator.evaluate("el => el.tagName.toLowerCase()"))
        typ = (await locator.get_attribute("type") or "").lower()
        label = ((await locator.inner_text(timeout=500)) + " " + (await locator.get_attribute("aria-label") or "")).lower()
        if tag != "button" or typ != "button":
            raise HTTPException(403, "For safety, approval-click supports only ordinary button type=button elements.")
        if any(word in label for word in BLOCKED_WORDS):
            raise HTTPException(403, "This button looks sensitive or ad/reward-related, so clicking is blocked.")
        await locator.click(timeout=2000)
        await asyncio.sleep(0.2)
        await scan_now()
        return {"success": True, "message": "Approved button clicked and elements re-scanned."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Click failed: {str(exc)[:160]}")
