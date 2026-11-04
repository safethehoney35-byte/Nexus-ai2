from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Nexus AI", version="0.6.0")
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

state: dict[str, Any] = {
    "status": "idle", "phase": "Ready", "action": "Waiting for a task.",
    "collected": 0, "analysed": 0, "target": 12, "current_url": "",
    "current_title": "", "sources": [], "images": [], "actions": [],
    "plan": [], "report": "", "network": {"checks": [], "note": "Not run."},
    "elapsed": 0,
}
events: list[dict[str, str]] = []
clients: set[WebSocket] = set()
task_runner: asyncio.Task | None = None
started_at: float | None = None


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def log(message: str, status: str = "info") -> dict[str, str]:
    event = {"time": stamp(), "message": message, "status": status}
    events.append(event)
    del events[:-120]
    return event


async def broadcast(payload: dict[str, Any]) -> None:
    stale = []
    for ws in list(clients):
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            stale.append(ws)
    for ws in stale:
        clients.discard(ws)


async def publish(message: str, status: str = "info") -> None:
    event = log(message, status)
    await broadcast({"type": "event", "event": event})
    await broadcast({"type": "state", "state": state, "events": events[-40:]})


class StartRequest(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    start_url: str = Field(default="", max_length=2048)


class SelectorCheckRequest(BaseModel):
    html: str = Field(min_length=1, max_length=1_000_000)
    selectors: list[str] = Field(default_factory=list, max_length=30)


async def run_placeholder_research(task_text: str, start_url: str) -> None:
    """Honest task lifecycle until a real research provider is configured."""
    global started_at
    started_at = time.monotonic()
    state.update(status="running", phase="Task accepted", action="Validating task", report="")
    await publish("Task accepted. No browser actions have been performed.", "success")
    if start_url:
        parsed = urlparse(start_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            state.update(status="failed", phase="Invalid URL", action="Provide a valid public HTTP(S) URL.")
            await publish("Starting URL must be a valid HTTP or HTTPS URL.", "failed")
            return
        state["current_url"] = start_url
    state.update(status="idle", phase="Ready for research integration", action="Backend connection is working; research provider is not configured.")
    state["report"] = (
        "NEXUS backend is connected. This build repairs the dashboard/API lifecycle and adds "
        "selector diagnostics. A live research/browser provider is not configured, so no web "
        "research or browser interaction was run."
    )
    state["elapsed"] = round(time.monotonic() - (started_at or time.monotonic()), 2)
    await publish("Task lifecycle check completed. Research engine is not configured; no simulated results were created.", "warning")


@app.get("/", response_class=JSONResponse)
async def root_index():
    return {"status": "online", "version": app.version, "agent": "Nexus AI", "dashboard": "/automation/nexus-bypass", "health": "/health"}


@app.get("/health")
async def health():
    return {"ok": True, "status": "healthy", "version": app.version}


@app.get("/automation/nexus-bypass", response_class=HTMLResponse)
async def dashboard():
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("Dashboard file missing", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/api/status")
async def api_status():
    if started_at and state["status"] == "running":
        state["elapsed"] = round(time.monotonic() - started_at, 2)
    return {"state": state, "events": events[-40:]}


@app.post("/api/start")
async def api_start(payload: StartRequest):
    global task_runner
    if task_runner and not task_runner.done():
        return JSONResponse({"error": "A task is already running."}, status_code=409)
    task_runner = asyncio.create_task(run_placeholder_research(payload.task.strip(), payload.start_url.strip()))
    return {"ok": True, "message": "Task accepted", "status": "running"}


@app.post("/api/stop")
async def api_stop():
    global task_runner
    if task_runner and not task_runner.done():
        task_runner.cancel()
        try:
            await task_runner
        except asyncio.CancelledError:
            pass
    state.update(status="stopped", phase="Stopped", action="Task stopped by user.")
    await publish("Task stopped by user.", "warning")
    return {"ok": True, "status": "stopped"}


@app.post("/api/diagnostics/selectors")
async def selector_diagnostics(payload: SelectorCheckRequest):
    # Parse in the user's browser with DOMParser; this endpoint intentionally
    # accepts only a static HTML snapshot and never visits/clicks a third-party site.
    return {"ok": True, "message": "Send the HTML snapshot to the dashboard's local DOMParser diagnostic.", "selector_count": len(payload.selectors)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps({"type": "state", "state": state, "events": events[-40:]}))
    try:
        while True:
            message = await ws.receive_text()
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        clients.discard(ws)
    except Exception:
        clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
