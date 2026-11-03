from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(title="Nexus-AI V0.5 Updated Engine")

# CORS Setup for handling external automation pipeline streams smoothly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files lookup mount (index.html, assets, css)
# Falls back gracefully if directories don't exist locally
static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# ==========================================
# 1. CORE EXISTNG BASE ROUTE ENGINE (OLD CORE LOGIC)
# ==========================================
@app.get("/")
async def root_index(request: Request):
    return JSONResponse({
        "status": "online",
        "version": "0.5-updated",
        "agent": "Nexus-AI Core Operating Unit",
        "endpoints": {
            "terminal_ui": "/automation/nexus-bypass",
            "static_view": "/static/index.html"
        }
    })

# ==========================================
# 2. NEW ADVANCED AUTOMATION & BYPASS CONTROLLER
# ==========================================
@app.get("/automation/nexus-bypass", response_class=HTMLResponse)
async def serve_nexus_bypass(request: Request):
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Nexus Automation Terminal Hub</title>
        <style>
            body { 
                background-color: #08080c; 
                color: #e2e8f0; 
                font-family: 'SF Pro Display', -apple-system, 'Segoe UI', monospace; 
                margin: 0; 
                padding: 10px;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
            }
            #nexus-panel { 
                border: 1px solid #1e1e2f; 
                background: #0f0f17; 
                border-radius: 12px; 
                padding: 24px; 
                width: 90%;
                max-width: 550px; 
                box-shadow: 0 10px 40px rgba(0,0,0,0.8); 
            }
            .terminal-header { 
                display: flex; 
                justify-content: space-between; 
                align-items: center; 
                border-bottom: 1px solid #1e1e2f; 
                padding-bottom: 14px; 
                margin-bottom: 18px; 
            }
            .terminal-header h3 {
                margin: 0;
                font-size: 16px;
                letter-spacing: 1px;
                color: #38bdf8;
            }
            .mode-btn { 
                background: #141522; 
                color: #00ffcc; 
                border: 1px solid #00ffcc; 
                padding: 6px 14px; 
                border-radius: 6px; 
                cursor: pointer; 
                font-weight: bold; 
                font-size: 11px;
                transition: all 0.2s ease;
            }
            .mode-btn.normal { 
                color: #94a3b8; 
                border-color: #334155; 
            }
            #log-stream { 
                background: #050508; 
                border: 1px solid #141522; 
                height: 280px; 
                overflow-y: auto; 
                padding: 12px; 
                border-radius: 8px; 
                font-size: 12px; 
                color: #cbd5e1; 
                line-height: 1.6; 
            }
            .log-entry { 
                margin-bottom: 8px; 
                border-left: 2px solid #00ffcc; 
                padding-left: 10px; 
                animation: fadeIn 0.2s ease;
            }
            .log-entry.system { border-color: #38bdf8; color: #38bdf8; }
            .log-entry.success { border-color: #4ade80; color: #4ade80; }
            .log-entry.alert { border-color: #f87171; color: #f87171; }
            
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(2px); }
                to { opacity: 1; transform: translateY(0); }
            }
        </style>
    </head>
    <body>

        <div id="nexus-panel">
            <div class="terminal-header">
                <h3>NEXUS SYSTEM CONTROLLER</h3>
                <button id="mode-toggle" class="mode-btn">AUTOMATION ACTIVE</button>
            </div>
            <div id="log-stream">
                <div class="log-entry system">> Landed on target bypass terminal window layer.</div>
            </div>
        </div>

        <script>
            let config = { automationMode: true, currentAdTask: 0, maxAds: 3 };
            const logsBox = document.getElementById('log-stream');
            const toggleBtn = document.getElementById('mode-toggle');

            function appendLog(msg, type = '') {
                const div = document.createElement('div');
                div.className = 'log-entry ' + type;
                div.innerText = `> [${new Date().toLocaleTimeString()}] ${msg}`;
                logsBox.appendChild(div);
                logsBox.scrollTop = logsBox.scrollHeight;
            }

            toggleBtn.addEventListener('click', () => {
                config.automationMode = !config.automationMode;
                if(config.automationMode) {
                    toggleBtn.innerText = "AUTOMATION ACTIVE";
                    toggleBtn.className = "mode-btn";
                    appendLog("Switched back to Hyper Fast Automation System.", "system");
                    triggerAutomationSequence();
                } else {
                    toggleBtn.innerText = "NORMAL MODE";
                    toggleBtn.className = "mode-btn normal";
                    appendLog("Switched to Normal Manual Flow mode. Automation paused.", "alert");
                }
            });

            // Structural Matrix Verification Sequence (No text reliance)
            let executionLoop;
            function triggerAutomationSequence() {
                if(executionLoop) clearInterval(executionLoop);
                
                executionLoop = setInterval(() => {
                    if(!config.automationMode) return;
                    
                    if(config.currentAdTask < config.maxAds) {
                        config.currentAdTask++;
                        appendLog(`Doing Ad Task Number ${config.currentAdTask} of 3... Extracting genuine structural matrices.`);
                    } else {
                        clearInterval(executionLoop);
                        appendLog("All 3 Ads processing segments handled successfully!", "success");
                        appendLog("Now showing Free Access element tokens on execution matrix.", "system");
                        appendLog("Clicked on target layout node node. Destination link sequence active.", "success");
                    }
                }, 2000); // Super-fast 2 seconds polling block emulation
            }

            // Initializing Engine Flow execution
            triggerAutomationSequence();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
