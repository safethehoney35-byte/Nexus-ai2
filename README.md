# NEXUS AI — browser-agent starter

A mobile-friendly dashboard + FastAPI + Playwright + Groq starter. The dashboard and API run together as one Render service, so you do **not** need Vercel for the first version.

## What this starter can do
- Accept a starting URL and a plain-language research task.
- Run a visible-to-the-server Chromium browser using Playwright.
- Show live browser screenshots and activity logs in the dashboard.
- Let Groq choose from a small, allow-listed set of browser tools.
- Check each action against the resulting URL/page state.
- Keep a task/action ID and basic integrity hash in the event log.
- Stop a running task.

## Important limitations
- "Live browser" is streamed as screenshots from the remote browser, not a full interactive remote desktop.
- This is a starter, not a fully autonomous production agent.
- The inspector is a deterministic Python check plus Groq's task reasoning; neither guarantees correctness.
- The browser is restricted to ordinary browsing/search/research actions. It will not submit forms, purchase items, enter passwords, bypass logins/CAPTCHAs, or perform sensitive account actions.
- Use only websites you are allowed to access. Respect site terms and robots/access restrictions.
- Render free instances may sleep and have limited resources. Browser automation may need a paid instance for reliable longer runs.

## Deploy from an Android phone

### 1. Get the project into GitHub
1. Download `nexus-ai-starter.zip`.
2. Open GitHub in your mobile browser and create a **new empty repository** named `nexus-ai`.
3. Open the repository, choose **Add file → Upload files** (or the upload icon/menu).
4. Upload the ZIP only if GitHub offers file upload for it; otherwise extract the ZIP using your phone's Files app and upload the extracted files/folders. GitHub's web UI can be awkward on mobile, so use the browser's desktop-site option if needed.
5. Commit the files to the `main` branch.

### 2. Deploy on Render
1. Sign in to Render and choose **New → Blueprint** (or **New → Web Service**).
2. Connect GitHub and select `nexus-ai`.
3. If using Blueprint, Render reads `render.yaml`. If using Web Service, select **Docker** as the runtime.
4. Add environment variable `GROQ_API_KEY` with your own key. Keep it secret; never put it in frontend files or GitHub.
5. Deploy and wait for the service to become Live.
6. Open the Render URL. The dashboard is served by the same service.

### 3. Get a Groq API key
Create a key in the Groq Console and add it to Render's environment variables as `GROQ_API_KEY`. Do not share it in chat or commit it to GitHub.

## Run locally (optional)
```bash
python -m venv .venv
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Add GROQ_API_KEY to .env
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

## Current tools
- `open_url`: navigate to an HTTP/HTTPS page
- `click_text`: click a visible link/button by its text
- `fill_search`: fill a visible search input
- `press_enter`: submit a search query by pressing Enter in the search field
- `scroll_page`: scroll the page
- `inspect_page`: read URL, title, visible text and links

The agent asks for approval before any action outside this limited tool set. Add more tools only with explicit validation and safeguards.
