import asyncio
import ipaddress
import json
import secrets
import socket
import time

from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
)


# ============================================================
# NEXUS AI CONFIGURATION
# ============================================================

APP_NAME = "Nexus AI"
APP_VERSION = "0.7.0"

MAX_APPROVAL_AGE = 30

browser_engine = None
browser: Optional[Browser] = None
browser_context: Optional[BrowserContext] = None
page: Optional[Page] = None

active_url = None
browser_running = False

connected_clients = set()
pending_approvals = {}


# ============================================================
# PREDEFINED SELECTOR LIBRARY
# ============================================================

SELECTOR_LIBRARY = [
    {
        "name": "Watch Ads option",
        "selector": '[data-testid="lv-membership-plan-option-wrapper-btn"]',
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Continue button",
        "selector": '[data-testid="action-wall-continue-action-btn"]',
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Ad counter",
        "selector": ".carousel-items-counter p",
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Ad label",
        "selector": ".lv-card__ad-indicator p",
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Skip Ad control",
        "selector": '[data-testid="lv-lib-carousel-skip-btn"]',
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Success heading",
        "selector": '[data-testid="success-title"]',
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Content-ready heading",
        "selector": ".success-wrapper h2",
        "category": "ad_reward_detection",
        "detection_only": True,
    },
    {
        "name": "Open here button",
        "selector": ".success-wrapper button",
        "category": "ad_reward_detection",
        "detection_only": True,
    },
]


# ============================================================
# APP LIFECYCLE
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

    await stop_browser()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


# ============================================================
# REQUEST MODELS
# ============================================================

class StartRequest(BaseModel):
    url: Optional[str] = None


class NavigateRequest(BaseModel):
    url: str


class InspectRequest(BaseModel):
    selector: str


class PrepareActionRequest(BaseModel):
    selector: str


class ExecuteActionRequest(BaseModel):
    approval_token: str


# ============================================================
# WEBSOCKET MANAGER
# ============================================================

async def broadcast(event_type, data):

    message = {
        "type": event_type,
        "timestamp": time.time(),
        "data": data,
    }

    disconnected = set()

    for client in connected_clients:
        try:
            await client.send_json(message)

        except Exception:
            disconnected.add(client)

    for client in disconnected:
        connected_clients.discard(client)


# ============================================================
# URL VALIDATION
# ============================================================

async def validate_public_url(url: str):

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="Only HTTP and HTTPS URLs are allowed.",
        )

    hostname = parsed.hostname

    if not hostname:
        raise HTTPException(
            status_code=400,
            detail="Invalid URL hostname.",
        )

    if hostname.lower() == "localhost":
        raise HTTPException(
            status_code=403,
            detail="Local addresses are blocked.",
        )

    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )

        for address in addresses:

            ip = ipaddress.ip_address(address[4][0])

            if not ip.is_global:
                raise HTTPException(
                    status_code=403,
                    detail="Private or reserved network addresses are blocked.",
                )

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Unable to validate destination address.",
        )

    return url


# ============================================================
# BROWSER REQUEST FILTER
# ============================================================

async def filter_browser_request(route):

    request_url = route.request.url

    try:
        await validate_public_url(request_url)
        await route.continue_()

    except Exception:
        await route.abort()


# ============================================================
# BROWSER MANAGEMENT
# ============================================================

async def start_browser():

    global browser_engine
    global browser
    global browser_context
    global page
    global browser_running

    if browser_running and page:
        return

    try:

        browser_engine = await async_playwright().start()

        browser = await browser_engine.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        browser_context = await browser.new_context(
            viewport={
                "width": 1366,
                "height": 768,
            },
        )

        await browser_context.route(
            "**/*",
            filter_browser_request,
        )

        page = await browser_context.new_page()

        await page.expose_binding(
            "nexusNotify",
            handle_dom_notification,
        )

        browser_running = True

        await broadcast(
            "browser_started",
            {
                "status": "running",
                "engine": "Chromium",
            },
        )

        return True

    except Exception as error:

        browser_running = False

        await broadcast(
            "error",
            {
                "message": str(error),
            },
        )

        raise


async def stop_browser():

    global browser_engine
    global browser
    global browser_context
    global page
    global browser_running
    global active_url

    browser_running = False
    active_url = None

    pending_approvals.clear()

    try:
        if browser_context:
            await browser_context.close()

    except Exception:
        pass

    try:
        if browser:
            await browser.close()

    except Exception:
        pass

    try:
        if browser_engine:
            await browser_engine.stop()

    except Exception:
        pass

    browser_engine = None
    browser = None
    browser_context = None
    page = None

    await broadcast(
        "browser_stopped",
        {
            "status": "stopped",
        },
    )


# ============================================================
# LIVE DOM MONITORING
# ============================================================

async def handle_dom_notification(source, payload):

    await broadcast(
        "dom_changed",
        payload,
    )


DOM_OBSERVER_SCRIPT = """
(() => {

    if (window.__nexusObserverInstalled) {
        return;
    }

    window.__nexusObserverInstalled = true;

    let timer = null;

    const notify = () => {

        clearTimeout(timer);

        timer = setTimeout(() => {

            if (typeof window.nexusNotify === "function") {

                window.nexusNotify({
                    event: "DOM_UPDATED",
                    url: location.href,
                    title: document.title,
                    timestamp: Date.now()
                });

            }

        }, 150);

    };

    const observer = new MutationObserver(() => {
        notify();
    });

    observer.observe(document.documentElement, {

        childList: true,
        subtree: true,
        attributes: true,
        characterData: true

    });

})();
"""


async def install_dom_observer():

    if not page:
        return

    try:

        await page.evaluate(
            DOM_OBSERVER_SCRIPT
        )

    except Exception:
        pass


# ============================================================
# CLICKABILITY DETECTION
# ============================================================

CLICKABILITY_SCRIPT = """
(selector) => {

    const elements = Array.from(
        document.querySelectorAll(selector)
    );

    return elements.map((element, index) => {

        const style = getComputedStyle(element);

        const rect = element.getBoundingClientRect();

        const visible =
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            Number(style.opacity) > 0 &&
            rect.width > 0 &&
            rect.height > 0;

        const disabled =
            element.disabled === true ||
            element.getAttribute("aria-disabled") === "true";

        let unobstructed = false;

        if (visible) {

            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;

            const topElement = document.elementFromPoint(x, y);

            unobstructed =
                topElement === element ||
                element.contains(topElement);

        }

        const clickable =
            visible &&
            !disabled &&
            unobstructed;

        return {

            index,

            tag: element.tagName.toLowerCase(),

            text: (element.innerText || element.textContent || "")
                .trim()
                .slice(0, 300),

            id: element.id || "",

            className:
                typeof element.className === "string"
                    ? element.className
                    : "",

            visible,

            disabled,

            unobstructed,

            clickable,

            status: clickable
                ? "NOW CLICKABLE"
                : !visible
                    ? "HIDDEN"
                    : disabled
                        ? "DISABLED"
                        : "NOT CLICKABLE",

            attributes: Array.from(element.attributes).reduce(
                (result, attribute) => {

                    result[attribute.name] = attribute.value;

                    return result;

                },
                {}
            )

        };

    });

}
"""


async def scan_selector(selector):

    if not page:
        raise HTTPException(
            status_code=400,
            detail="Browser is not running.",
        )

    try:

        return await page.evaluate(
            CLICKABILITY_SCRIPT,
            selector,
        )

    except Exception as error:

        return {
            "error": str(error),
            "selector": selector,
        }


# ============================================================
# COMPLETE SELECTOR DIAGNOSTICS
# ============================================================

async def run_selector_diagnostics():

    if not page:
        raise HTTPException(
            status_code=400,
            detail="Browser is not running.",
        )

    results = []

    for item in SELECTOR_LIBRARY:

        selector = item["selector"]

        elements = await scan_selector(selector)

        if isinstance(elements, dict):

            results.append({
                **item,
                "found": False,
                "error": elements.get("error"),
            })

            continue

        results.append({

            **item,

            "found": len(elements) > 0,

            "count": len(elements),

            "elements": elements,

            "status": (
                "FOUND"
                if elements
                else "NOT FOUND"
            ),

        })

    return results


# ============================================================
# TECHNICAL INSPECTOR
# ============================================================

async def inspect_element(selector):

    if not page:
        raise HTTPException(
            status_code=400,
            detail="Browser is not running.",
        )

    script = """
    (selector) => {

        const element = document.querySelector(selector);

        if (!element) {
            return null;
        }

        const style = getComputedStyle(element);

        const rect = element.getBoundingClientRect();

        return {

            selector,

            tag: element.tagName.toLowerCase(),

            text: (
                element.innerText ||
                element.textContent ||
                ""
            ).trim().slice(0, 1000),

            id: element.id,

            className: element.className,

            attributes: Array.from(element.attributes).reduce(
                (result, attribute) => {

                    result[attribute.name] = attribute.value;

                    return result;

                },
                {}
            ),

            visible:
                style.display !== "none" &&
                style.visibility !== "hidden" &&
                rect.width > 0 &&
                rect.height > 0,

            disabled:
                element.disabled === true,

            dimensions: {

                width: rect.width,

                height: rect.height,

                x: rect.x,

                y: rect.y

            },

            inspectedAt: Date.now()

        };

    }
    """

    try:

        result = await page.evaluate(
            script,
            selector,
        )

        if result is None:

            raise HTTPException(
                status_code=404,
                detail="Element not found.",
            )

        return result

    except HTTPException:
        raise

    except Exception as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )


# ============================================================
# ACTION SAFETY
# ============================================================

BLOCKED_ACTION_KEYWORDS = [

    "ad",
    "skip",
    "reward",
    "continue",
    "membership",
    "success",
    "payment",
    "checkout",
    "purchase",
    "subscribe",
    "delete",
    "remove",
    "transfer",
    "password",
    "security",
    "login",
    "logout",

]


def is_detection_only_selector(selector):

    normalized = selector.lower()

    for item in SELECTOR_LIBRARY:

        if item["selector"] == selector:

            return item["detection_only"]

    detection_terms = [

        "linkvertise",
        "lv-",
        "carousel",
        "success-wrapper",
        "success-title",

    ]

    return any(
        term in normalized
        for term in detection_terms
    )


# ============================================================
# APPROVAL PREPARATION
# ============================================================

async def prepare_safe_action(selector):

    if not page:
        raise HTTPException(
            status_code=400,
            detail="Browser is not running.",
        )

    if is_detection_only_selector(selector):

        raise HTTPException(
            status_code=403,
            detail="This selector is detection-only.",
        )

    details = await inspect_element(selector)

    if details["tag"] != "button":

        raise HTTPException(
            status_code=403,
            detail="Only ordinary HTML buttons are supported.",
        )

    if details["disabled"] or not details["visible"]:

        raise HTTPException(
            status_code=400,
            detail="Button is not available.",
        )

    button_type = details["attributes"].get(
        "type",
        "submit",
    ).lower()

    if button_type != "button":

        raise HTTPException(
            status_code=403,
            detail="Submit buttons are not supported.",
        )

    text_content = details["text"].lower()

    if any(
        keyword in text_content
        for keyword in BLOCKED_ACTION_KEYWORDS
    ):

        raise HTTPException(
            status_code=403,
            detail="This action is restricted.",
        )

    token = secrets.token_urlsafe(32)

    pending_approvals[token] = {

        "selector": selector,

        "tag": details["tag"],

        "text": details["text"],

        "created": time.time(),

        "url": page.url,

    }

    return {

        "approval_required": True,

        "approval_token": token,

        "action": "Click ordinary button",

        "target": details,

        "risk": "Low-risk heuristic; inspect before approving.",

        "expires_in": MAX_APPROVAL_AGE,

    }


# ============================================================
# APPROVED ACTION EXECUTION
# ============================================================

async def execute_approved_action(token):

    if token not in pending_approvals:

        raise HTTPException(
            status_code=404,
            detail="Approval token not found or already used.",
        )

    approval = pending_approvals.pop(token)

    if time.time() - approval["created"] > MAX_APPROVAL_AGE:

        raise HTTPException(
            status_code=403,
            detail="Approval expired. Prepare the action again.",
        )

    if not page:

        raise HTTPException(
            status_code=400,
            detail="Browser is not running.",
        )

    if page.url != approval["url"]:

        raise HTTPException(
            status_code=403,
            detail="Page changed. Approval revoked.",
        )

    current = await inspect_element(
        approval["selector"]
    )

    if (
        current["tag"] != approval["tag"]
        or current["text"] != approval["text"]
        or not current["visible"]
        or current["disabled"]
    ):

        raise HTTPException(
            status_code=403,
            detail="Target changed. Approval revoked.",
        )

    # Re-check detection-only status immediately before action.
    if is_detection_only_selector(approval["selector"]):

        raise HTTPException(
            status_code=403,
            detail="Detection-only selector.",
        )

    try:

        await page.locator(
            approval["selector"]
        ).click(
            timeout=3000,
        )

        await broadcast(
            "action_completed",
            {
                "selector": approval["selector"],
                "result": "completed",
            },
        )

        await asyncio.sleep(0.2)

        await broadcast(
            "post_action_scan",
            {
                "url": page.url,
                "title": await page.title(),
            },
        )

        return {
            "success": True,
            "message": "Approved action completed.",
        }
    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=f"Action failed: {str(error)}",
        )
