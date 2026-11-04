import asyncio
import random
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

app = FastAPI()

# Dashboard frontend linking parameters
app.mount("/static", StaticFiles(directory="static"), name="static")

class MissionRequest(BaseModel):
    url: str

# Armour inventory data matrix
ARMOUR_SUITS = [
    {
        "os": "ios",
        "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "vendor": "Apple Inc.", "renderer": "Apple GPU", "scale": 3.0, "w": 393, "h": 852, "tz": "America/New_York"
    },
    {
        "os": "android",
        "ua": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        "vendor": "Google Inc. (Qualcomm)", "renderer": "Adreno (TM) 750", "scale": 2.6, "w": 412, "h": 915, "tz": "America/Chicago"
    }
]

def inject_stealth_gear(vendor, renderer):
    return f"""
    (() => {{
        const glProxy = (target, thisArg, args) => {{
            if (args === 37445) return "{vendor}";
            if (args === 37446) return "{renderer}";
            return Reflect.apply(target, thisArg, args);
        }};
        if (window.WebGLRenderingContext) window.WebGLRenderingContext.prototype.getParameter = new Proxy(window.WebGLRenderingContext.prototype.getParameter, {{ apply: glProxy }});
        if (window.WebGL2RenderingContext) window.WebGL2RenderingContext.prototype.getParameter = new Proxy(window.WebGL2RenderingContext.prototype.getParameter, {{ apply: glProxy }});
        
        let a = {random.uniform(20, 45)}, b = {random.uniform(-1, 1)}, c = {random.uniform(-1, 1)};
        setInterval(() => {{
            a += (Math.random() - 0.5) * 0.1; b += (Math.random() - 0.5) * 0.1; c += (Math.random() - 0.5) * 0.1;
            window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {{ alpha: a, beta: b, gamma: c, absolute: false }}));
        }}, 50);
    })();
    """

async def timing_tunnel(page):
    try:
        elements = await page.locator("p, article, img").count()
        delay = min(0.5 + (elements * 0.03) + random.uniform(0.5, 2.0), 4.8)
        await asyncio.sleep(delay)
        return delay
    except:
        delay = random.uniform(1.5, 3.0)
        await asyncio.sleep(delay)
        return delay

@app.post("/api/run")
async def start_stealth_mission(request: MissionRequest):
    async with async_playwright() as p:
        suit = random.choice(ARMOUR_SUITS)
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        
        context = await browser.new_context(
            user_agent=suit["ua"],
            viewport={"width": suit["w"], "height": suit["h"]},
            device_scale_factor=suit["scale"],
            is_mobile=True, has_touch=True, timezone_id=suit["tz"]
        )
        
        await context.add_init_script(inject_stealth_gear(suit["vendor"], suit["renderer"]))
        page = await context.new_page()
        await stealth_async(page)
        
        try:
            response = await page.goto(request.url, wait_until="commit", timeout=30000)
            time_paused = await timing_tunnel(page)
            page_title = await page.title()
            
            return {
                "status": "success",
                "title": page_title,
                "http_code": response.status if response else 200,
                "equipped_suit": {
                    "os": suit["os"],
                    "gpu": suit["renderer"],
                    "delay_spent": f"{time_paused:.2f}s",
                    "resolution": f"{suit['w']}x{suit['h']}"
                }
            }
        except Exception as e:
            return {"status": "failed", "error": str(e)}
        finally:
            await browser.close()
