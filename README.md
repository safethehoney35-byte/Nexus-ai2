# NEXUS AI Browser Monitor 1.0

FastAPI + Playwright single-session browser monitor. The canonical backend is
`app/main.py`; the dashboard is `static/index.html`.

## Features
- Compact URL / Start / Stop dashboard
- Browser state, detected controls, activity and error logs
- Explicit approval before eligible ordinary button/link clicks
- Read-only detection of Linkvertise/ad/reward selectors
- `NEXUS_ACCESS_KEY` protection for browser-control and data APIs
- Legacy `/automation/nexus-bypass` and `/ws` routes retained for compatibility

## Safety boundaries
Ad viewing, skipping ads, reward claiming/completion, payment, account deletion,
transfers and withdrawals are not automated. Ad/reward selectors are detection-only.
The approval endpoint re-checks the live element before an eligible ordinary click.

## Render deployment
1. Push this repository structure to GitHub.
2. Keep the existing Render Docker service.
3. Ensure Render Environment contains `NEXUS_ACCESS_KEY` with a long unique secret.
4. Deploy the latest commit.
5. Check `/health`, then open the dashboard and enter the same access key.

The Dockerfile starts `uvicorn app.main:app` and uses Render's `$PORT`.
Do not change the start command to `main:app`.

## Local run
```bash
pip install -r requirements.txt
playwright install chromium
export NEXUS_ACCESS_KEY='your-long-random-secret'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Endpoints
- `GET /` and `GET /automation/nexus-bypass` — dashboard
- `GET /health` — public health check
- `GET /api/status`, `GET /api/data` — access-key protected
- `POST /api/start`, `/api/stop`, `/api/approve`, `/api/reject` — protected
- `WS /ws` — legacy heartbeat/scan compatibility

Send `X-Nexus-Key: <NEXUS_ACCESS_KEY>` with protected API requests.
