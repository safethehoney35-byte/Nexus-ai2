# NEXUS AI Browser Monitor 1.0

A lightweight, single-session Playwright browser monitor with:
- URL input and Start/Stop state machine
- Visible control detection
- Explicit Yes/No approval before eligible ordinary clicks
- Activity and error logs
- Read-only Linkvertise/ad/reward selector diagnostics
- API access key protection

## Safety boundaries

Ad-view, skip-ad, reward, payment, account deletion, transfer, and withdrawal-related controls are detection-only or blocked. NEXUS does not automate ad views or reward completion. Only visible ordinary `button[type=button]` elements and links can be offered for approval. Form-submit buttons are not clickable through the approval endpoint.

## Render deployment

1. Push the project files to your GitHub repository (replace the old app files only after making a backup).
2. In Render, use the existing Docker service and deploy the updated commit.
3. Set `NEXUS_ACCESS_KEY` in Render Environment to a long, unique secret. `render.yaml` can generate one for a new Blueprint service; for an existing service, verify the variable exists.
4. Keep the service on one instance. The app intentionally runs one Chromium session to reduce memory use.
5. Confirm `https://YOUR-SERVICE/health` returns healthy.
6. Open the service URL. Enter the same `NEXUS_ACCESS_KEY` configured in Render.

No additional Render start command is required: Dockerfile runs `uvicorn main:app` and uses Render's `$PORT`.

## Local run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export NEXUS_ACCESS_KEY='replace-with-a-long-random-secret'
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

## API

- `GET /health` — public health check
- `GET /api/status` — authenticated status
- `GET /api/data` — authenticated elements and logs
- `POST /api/start` — `{"url":"https://example.com"}`
- `POST /api/stop`
- `POST /api/approve` — `{"element_id":"..."}`
- `POST /api/reject` — `{"element_id":"..."}`

Authenticated requests require `X-Nexus-Key: <NEXUS_ACCESS_KEY>`.

## Operational notes

- The browser is launched only when Start is requested and closed on Stop or application shutdown.
- Start returns `STARTING`; the background worker transitions to `RUNNING` or `ERROR`.
- Stop transitions through `STOPPING` and then confirms `STOPPED`.
- URL validation rejects localhost and non-public IP destinations. Network requests are checked as well.
- Render Free can cold-start after inactivity and has limited memory. Browser launch time depends on Render availability and target-site response; no fixed startup time is promised.
- This is a single-process, single-session app. Do not scale it to multiple instances without introducing shared session coordination.
