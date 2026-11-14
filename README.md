# NEXUS Replay Engine 2.3.1

NEXUS is a single-session FastAPI + Playwright browser replay service.

## Recorder formats

- Accepts NEXUS Recorder V3 `actions[]` exports.
- Also accepts `NEXUS-UNIQUE-RECORDER` v2.0.0 `events[]` exports and converts click events to the engine's click-action shape.
- The current replay engine supports click actions only. Other event types are reported as skipped.
- V2 event timestamps are used to preserve the recorded inter-click gaps. Target fingerprints, attributes, ancestor context, CSS paths, and page URLs are carried into the normalized action.

The importer does not make a workflow safe merely because it can parse it. The documented baseline policy still blocks ad/reward, payment, financial-transfer, and destructive controls. In particular, use a normal demo site for replay testing rather than an advertising/reward gate.

## Recommended test sites

1. `https://demo.playwright.dev/todomvc/` — typing, adding items, toggling and deleting demo todo items.
2. `https://the-internet.herokuapp.com/` — buttons, checkboxes, dropdowns, dynamic loading and alerts.
3. `https://www.saucedemo.com/` — a demo shopping workflow using its documented test account.

## Runtime behavior

- Docker starts `uvicorn main:app`; `main.py` and `static/index.html` are the active application files.
- The UI polls `/api/data` every 1.4 seconds for status updates. This is dashboard refresh, not the replay scanner.
- Replay target lookup retries every 250 ms, up to `NEXUS_ELEMENT_WAIT_SECONDS` (default 20 seconds, bounded 3–120 seconds).
- Navigation waits for `domcontentloaded` and does not wait for all images, fonts, or third-party resources.
- The service uses one in-memory browser session. Keep one Render instance; multiple instances do not share the browser state.

## Render deployment

1. Back up the current repository.
2. Replace the deployed project with this cleaned project.
3. Set `NEXUS_ACCESS_KEY` in Render Environment.
4. Keep the service at one instance.
5. Deploy and confirm `/health` reports version `2.3.1`.

## Local run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export NEXUS_ACCESS_KEY='replace-with-a-long-random-secret'
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## API

- `GET /health` — public health check
- `GET /api/data` — authenticated runtime state
- `POST /api/start` — `{"url":"https://example.com"}`
- `POST /api/inspect` — inspect normalized click actions
- `POST /api/replay` — replay normalized click actions
- `POST /api/stop` — stop the browser and replay

Authenticated requests require `X-Nexus-Key: <NEXUS_ACCESS_KEY>`.
