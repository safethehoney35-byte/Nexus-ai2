# NEXUS AI v0.5 — browser research agent

A mobile-friendly dashboard + FastAPI + Playwright + Groq starter. The dashboard and API run together as one Render service.

## What this build can do
- Accept a task and optional starting URL.
- Open public HTTP/HTTPS pages and search the public web.
- Extract page text, links, video links and image URLs separately.
- Use a small allow-list of DOM-based browser actions: click one uniquely matched visible button/link, scroll, wait and inspect.
- Verify action outcomes and show green/amber/red activity logs.
- Stream a remote Chromium screenshot preview while the Browser tab is open.
- Stop a running task and expose `/health` for Render health checks.

## Important safety and reliability limits
- Clicks are attempted only when a visible button/link resolves uniquely by accessible name. Ambiguous matches are not clicked.
- Sensitive controls (for example purchase, payment, account, posting or deletion controls) are skipped.
- NEXUS does not enter passwords, submit general forms, make purchases, bypass CAPTCHAs/paywalls/logins, or evade site access restrictions.
- Use only sites you are allowed to access and respect their terms.
- Screenshots are a preview, not an interactive remote desktop. Some sites block automation or image hotlinking.
- Render free instances may sleep and have limited CPU/RAM; long tasks may be interrupted.
- Groq API usage may incur limits or costs according to your provider account.

## Deploy on Render
1. Push the project files to a GitHub repository.
2. In Render, create a Docker Web Service or use the included `render.yaml` Blueprint.
3. Set `GROQ_API_KEY` as a Render environment variable. Never put the key in frontend code or commit it to GitHub.
4. Deploy and wait until the service is Live.
5. Open the Render service URL. `/health` should return a healthy status.

The default model is set by `GROQ_MODEL` in the environment; the current fallback is `openai/gpt-oss-120b`.

## Run locally
```bash
python -m venv .venv
pip install -r requirements.txt
playwright install chromium
# Set GROQ_API_KEY in your environment
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000
