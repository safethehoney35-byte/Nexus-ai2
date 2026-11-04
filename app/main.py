import asyncio
import random
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

app = FastAPI()

# Serving static assets from directory
app.mount("/static", StaticFiles(directory="static"), name="static")

class StealthRouteRequest(BaseModel):
    url: str

# Production stealth profiles (Fully matching system fingerprints)
STEALTH_METRIC_PROFILES = [
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

def compile_camouflage_driver(vendor, renderer):
    return f"""
    (() => {{
        const hardwareContextProxy = (target, thisArg, argumentsList) => {{
            if (argumentsList === 37445) return "{vendor}";
            if (argumentsList === 37446) return "{renderer}";
            return Reflect.apply(target, thisArg, argumentsList);
        }};
        if (window.WebGLRenderingContext) window.WebGLRenderingContext.prototype.getParameter = new Proxy(window.WebGLRenderingContext.prototype.getParameter, {{ apply: hardwareContextProxy }});
        if (window.WebGL2RenderingContext) window.WebGL2RenderingContext.prototype.getParameter = new Proxy(window.WebGL2RenderingContext.prototype.getParameter, {{ apply: hardwareContextProxy }});
        
        let orientationAlpha = {random.uniform(15.0, 60.0)}, orientationBeta = {random.uniform(-3.0, 3.0)}, orientationGamma = {random.uniform(-3.0, 3.0)};
        setInterval(() => {{
            orientationAlpha += (Math.random() - 0.5) * 0.1;
            orientationBeta += (Math.random() - 0.5) * 0.1;
            orientationGamma += (Math.random() - 0.5) * 0.1;
            window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {{
                alpha: orientationAlpha, beta: orientationBeta, gamma: orientationGamma, absolute: false
            }}));
        }}, 50);
    })();
    """

async def execute_behavioral_tunnel(page):
    try:
        dom_blocks = await page.locator("p, h1, img").count()
        delay_factor = min(0.6 + (dom_blocks * 0.04) + random.uniform(0.3, 2.0), 4.9)
        await asyncio.sleep(delay_factor)
        return delay_factor
    except Exception:
        fallback_pause = random.uniform(1.8, 3.4)
        await asyncio.sleep(fallback_pause)
        return fallback_pause

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/api/run")
async def process_stealth_pipeline(request: StealthRouteRequest):
    async with async_playwright() as p:
        allocated_profile = random.choice(STEALTH_METRIC_PROFILES)
        target_w = allocated_profile["w"] + random.randint(-4, 4)
        target_h = allocated_profile["h"] + random.randint(-8, 8)
        
        browser = await p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-gpu", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        
        context = await browser.new_context(
            user_agent=allocated_profile["ua"],
            viewport={"width": target_w, "height": target_h},
            device_scale_factor=allocated_profile["scale"],
            is_mobile=True, has_touch=True, timezone_id=allocated_profile["tz"]
        )
        
        camouflage_payload = compile_camouflage_driver(allocated_profile["vendor"], allocated_profile["renderer"])
        await context.add_init_script(camouflage_payload)
        
        page = await context.new_page()
        await stealth_async(page)
        
        try:
            response = await page.goto(request.url, wait_until="commit", timeout=30000)
            tunnel_delay = await execute_behavioral_tunnel(page)
            extracted_title = await page.title()
            
            return {
                "status": "success",
                "title": extracted_title,
                "http_code": response.status if response else 200,
                "profile_used": {
                    "os": allocated_profile["os"],
                    "gpu": allocated_profile["renderer"],
                    "resolution": f"{target_w}x{target_h}",
                    "delay_spent": f"{tunnel_delay:.2f}s"
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            await browser.close()
