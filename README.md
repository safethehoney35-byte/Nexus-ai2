# NEXUS AI v0.7 — Linkvertise element monitor

A FastAPI + Playwright browser-monitoring dashboard based on the supplied Nexus project.

## What it does

- Accepts a Linkvertise URL and opens it in a controlled Chromium session.
- Watches DOM changes and checks only the configured Linkvertise selectors (not arbitrary page text).
- Identifies the **Watch Ads** membership option by its visible text, while excluding **Free Access**.
- When Watch Ads is detected, prepares a time-limited approval request in the dashboard. Nothing is selected until the user presses **Approve**; **Reject** cancels it.
- After the approved Watch Ads selection, monitors the ad counter (`1/3`, `2/3`, `3/3`), ad indicator, Skip Ad control and success headings.
- Ad CTA and Skip Ad are detection-only. The user must interact with those controls manually. The app does not automate ad viewing, clicking ads, skipping ads, bypassing access requirements or manufacturing views/rewards.
- Shows activity and error logs in the dashboard. DOM scans are debounced to avoid excessive requests.

## Selector set

| Element | Selector | Behavior |
|---|---|---|
| Watch Ads option | `[data-testid="lv-membership-plan-option-wrapper-btn"]` | Text-filtered; approval required; Free Access excluded |
| Continue button | `[data-testid="action-wall-continue-action-btn"]` | Detection only |
| Ad counter | `.carousel-items-counter p` | Detection only |
| Ad label | `.lv-card__ad-indicator p` | Detection only |
| Skip Ad | `[data-testid="lv-lib-carousel-skip-btn"]` | Detection only; manual interaction |
| Success heading | `[data-testid="success-title"]` | Detection only |
| Content-ready heading | `.success-wrapper h2` | Detection only |
| Open here button | `.success-wrapper button` | Detection only |

Selectors can change when Linkvertise changes its website. Use the diagnostics view to confirm current matches. A detected `3/3` counter or Skip Ad control is not proof that Linkvertise credited a view or granted access; only the platform can confirm that.

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/automation/nexus-bypass>.

## Deploy on Render

The included `render.yaml` starts the FastAPI app. For a persistent browser environment, use a deployment that supports installing/running Playwright Chromium and its OS dependencies. Set the start command to:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Safety and operational notes

- Approval tokens expire after 30 seconds and are bound to the current page URL and inspected target.
- Only the specifically text-matched Watch Ads option can be approved by this Linkvertise flow.
- Ad CTA, Skip Ad, reward, payment, account and success controls are not automated.
- Keep the dashboard private; do not expose an unauthenticated browser-control service to the public internet.
