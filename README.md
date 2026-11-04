# NEXUS AI v0.6 — dashboard repair + selector diagnostics

A mobile-friendly FastAPI dashboard with working health, status, start/stop, and WebSocket endpoints, plus a read-only Linkvertise DOM selector diagnostic.

## What's repaired
- `/health` returns a health response for deployment checks.
- `/api/status` returns a consistent state object used by the dashboard.
- `/api/start` and `/api/stop` now exist and return honest task lifecycle states.
- `/ws` accepts dashboard connections and streams state/events; the dashboard reconnects if disconnected.
- `/automation/nexus-bypass` serves the dashboard from `static/index.html`.
- Removed the old fake three-ad timer and false “success”/“clicked” logs. The backend does not claim to have browsed or completed ads.

## Selector Diagnostic Mode
1. Open the dashboard and choose **Actions & Diagnostics**.
2. In the browser/inspector where you are inspecting a page, copy its DOM snapshot (for example, `document.documentElement.outerHTML`).
3. Paste that snapshot into **Linkvertise Selector Diagnostic** and choose **RUN SELECTOR CHECKS**.
4. The dashboard parses the supplied snapshot locally with `DOMParser` and reports matches for Watch Ads, Continue, ad counter, Ad label, Skip Ad presence, success heading, content-ready heading, and Open here.

The diagnostic is read-only. It does not navigate to Linkvertise, click advertisements, skip ads, simulate completion, or forge reward/access state. Selectors for stages not present in the pasted snapshot will correctly show NOT FOUND.

## Current limitations
- The research task endpoint is a connection/lifecycle placeholder, not a full research agent. It deliberately reports that no research provider/browser task was run instead of fabricating collected sources or screenshots.
- The selector checker tests a pasted snapshot, not the live DOM in another browser tab. Dynamic page state can change after the snapshot is copied.
- No Groq key or browser automation provider is used by this build.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/automation/nexus-bypass`.

## Deploy on Render
Use the included `render.yaml` or Dockerfile. The health check path is `/health`. Set the service start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
