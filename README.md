# Mandi Setu — Punjab Mandi Price Forecasting

Multilingual voice + WhatsApp price advisory, market-wide trend dashboard, and 7-day price forecasting for Punjab crops. Built for Smart India Hackathon.

## What's in this repo
- `app.py` — FastAPI backend: forecasting, voice/WhatsApp/SMS advisory, price alerts, mandi comparison, anomaly detection, trend dashboard
- `static/dashboard/` — standalone web dashboard (plain HTML/CSS/JS, no build step) at `/dashboard`
- `voice_extraction.py` — multilingual (EN/HI/PA) crop & mandi extraction from free-text/voice questions
- `clean_mandi_prices.csv` — `date, crop, mandi, price` (21,017 rows, 42 crops, 22 mandis, Jun 2023–present)
- `raw_agmarknet/` — raw source data pulled from Agmarknet, feeding the cleaned CSV
- `fetch_daily_mandi_data.py` — pulls from the data.gov.in API automatically (daily, via GitHub Actions)
- `update_mandi_prices.py` — merges a manually-downloaded raw Agmarknet export CSV (see `raw_agmarknet/`) into `clean_mandi_prices.csv`; run this by hand after a manual download, not on a schedule
- `.github/workflows/update-mandi-data.yml` — runs the above daily via GitHub Actions
- `.github/workflows/keep-alive.yml` — pings the deployed service every 10 min so Render's free tier doesn't sleep
- `mandi_price_forecasting__1_.ipynb` — EDA, gaps/outliers, naive baseline vs. ETS model evaluation
- `PROJECT_STATUS.md`, `problem_statement_and_task_breakdown.md`, `execution_checklist_with_schedule.md` — planning docs
- `test_scenarios.py`, `diagnose_gemini.py`, `diagnose_tts.py` — test/debug scripts

**Data note:** only **Potato** (9,233 rows), **Onion** (8,443 rows), and **Tomato** (3,200 rows) currently have enough history to forecast reliably. Every other crop has too few records and will return a `422` from `/predict`. Demo with Potato/Onion/Tomato.

## 1. Install
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## 2. Set environment variables
```bash
# Required for text/voice advisory generation
export GEMINI_API_KEY=your-key-here          # or GOOGLE_API_KEY
export GEMINI_MODEL=gemini-3.7-flash          # optional override if the default 404s

# Required for outbound WhatsApp price alerts (see "Price alerts" below)
export TWILIO_ACCOUNT_SID=ACxxxxxxxx...
export TWILIO_AUTH_TOKEN=...
export TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # your Twilio sandbox number

# Protects /check-alerts from being triggered by anyone who finds the URL
export ALERTS_CRON_SECRET=some-long-random-string

# Optional, local rehearsal only — does NOT work reliably once deployed on
# Render's free tier (see "Price alerts" below for why)
export ENABLE_INTERNAL_ALERT_SCHEDULER=true
export ALERT_CHECK_INTERVAL_SECONDS=600
```

## 3. Run the API
```bash
uvicorn app:app --reload
```
Open:
- `http://127.0.0.1:8000/` — health check
- `http://127.0.0.1:8000/docs` — interactive API docs (Swagger UI)
- `http://127.0.0.1:8000/dashboard/` — the price-forecast web dashboard
- `http://127.0.0.1:8000/voice-test` — mic + typed-text demo page (English, Hindi, Punjabi)
- `http://127.0.0.1:8000/trends-dashboard` — "Mandi Rate Board" market-wide trend view

## 4. Run the notebook (optional)
```bash
jupyter notebook mandi_price_forecasting__1_.ipynb
```
Compares a naive baseline (P(t+1) = P(t)) against Exponential Smoothing (ETS) on a held-out window — the model only beats "no change" about 28% of the time, documented honestly rather than hidden.

## Endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/predict?crop=X&mandi=Y` | GET | Latest price, trend, 7-day forecast, confidence note, compact anomaly flag (JSON) |
| `/anomalies?crop=X&mandi=Y&z_threshold=2.5` | GET | Full history of statistically unusual day-over-day price moves for this crop-mandi pair |
| `/compare?crop=X&mandis=A,B,C` | GET | Ranks 2–5 named mandis by price for the same crop; flags when compared mandis' latest records aren't from the same date |
| `/compare-advisory?crop=X&mandis=A,B,C&question=...&language=en\|hi\|pa` | GET | `/compare` + generated advisory weighing the price spread against (unknown) travel cost |
| `/trends?crops=Potato,Onion,Tomato` | GET | Market-wide view: which crop-mandi pairs are rising/falling/stable, grouped by crop, sorted by size of move |
| `/trends-dashboard` | GET | Rate-board style HTML page rendering `/trends` — for a mandi board/policymaker audience |
| `/meta` | GET | Every crop and mandi name in the dataset, for populating a frontend's dropdowns |
| `/history?crop=X&mandi=Y&days=45` | GET | Recent actual price history (for charting) |
| `/dashboard/` | GET | The price-forecast web dashboard (static files, see below) |
| `/advisory?crop=X&mandi=Y&question=...&language=en\|hi\|pa` | GET | Forecast + generated advisory text (JSON) |
| `/voice-advisory?question=...&language=en\|hi\|pa` | POST | Crop/mandi auto-extracted from the question; returns spoken advisory (audio/mpeg) |
| `/voice-test` | GET | Browser demo page (mic + typed text, all 3 languages) |
| `/sms` | POST | Twilio SMS webhook — texts a crop+mandi (EN/HI/PA), gets a price + advisory back via SMS |
| `/whatsapp` | POST | Twilio WhatsApp webhook — same as `/sms` but over WhatsApp (works via Twilio's free Sandbox, no DLT registration needed) |
| `/check-alerts?secret=...` | GET/POST | Checks all active price alerts and sends any that triggered — meant to be called by an external scheduler, not a person |

Example: `http://127.0.0.1:8000/predict?crop=Potato&mandi=Rayya`

## Web dashboard (`/dashboard`)
A standalone frontend at `static/dashboard/` — plain HTML/CSS/JS, no build step, served by FastAPI's `StaticFiles` mount. It calls `/meta`, `/predict`, `/history`, and `/trends` on this same backend to show:
- Current price, trend, and 7-day forecast for a selected crop/mandi, with a price-history chart
- Top gainers / top losers across the whole market
- Alerts panel — biggest projected moves
- A price table for the selected crop across every mandi with enough history

Uses [Chart.js](https://www.chartjs.org/) from a CDN — no local dependency.

## Trend dashboard (`/trends-dashboard`)
A second, simpler market-wide view — a "Mandi Rate Board" styled page — aimed at mandi boards/policymakers rather than one farmer with one question. Reuses `/predict` across every viable crop-mandi pair; no new forecasting logic.

## Price-spike / anomaly detection
`/predict` and `/anomalies` flag day-over-day price moves that are statistical outliers (z-score on % change) relative to that specific crop-mandi's own volatility — not a fixed rupee threshold. Framed as **worth a second look**, never as a claim about cause — could reflect distress-selling, middleman activity, or a data-entry irregularity. Computed on the *raw, actually-reported* prices, not the forward-filled series used for forecasting, to avoid synthetic zero-change days from skewing the statistics.

## Mandi comparison (`/compare`, `/compare-advisory`)
Answers the actual decision a farmer faces — sell here, or is it worth traveling to another mandi. Explicitly flags when the compared mandis' most recent price records aren't from the same date (their data can go stale independently), so a price gap isn't presented as same-day when it isn't. Doesn't know real travel distance/cost — says so explicitly rather than overclaiming.

## Price alerts (WhatsApp only)
A farmer messages the WhatsApp sandbox number:
- `alert me potato rayya 850` → sets an alert, default trigger is "crosses ₹850 in either direction"
- `my alerts` → lists active alerts
- `stop alerts` → cancels them

Storage is a single gitignored `subscriptions.json` file — fine for a demo, worth a real database before scaling. `check_all_alerts()` groups subscriptions by crop+mandi (predicts each pair once regardless of how many farmers are watching it) and uses a merge-on-save strategy: it reloads the subscriptions file fresh right before saving and applies only the specific alerts that fired, so a farmer creating/cancelling an alert while a check cycle is mid-flight doesn't get silently lost.

**Deployment reality:** Render's free tier sleeps after ~15 min idle and only wakes on an incoming request — a background thread inside the app sleeps too. `.github/workflows/keep-alive.yml` handles this by pinging `/` every 10 minutes, which keeps the service awake so a separate cron hitting `/check-alerts` doesn't hit a cold-started/sleeping app. `ENABLE_INTERNAL_ALERT_SCHEDULER` exists for **local rehearsal only**.

## SMS / WhatsApp setup (Twilio)
1. Get a Twilio account. For WhatsApp, use Twilio's free **WhatsApp Sandbox** (no DLT registration needed, works in minutes) — SMS to Indian numbers requires DLT registration, a real regulatory hurdle.
2. In the Twilio console, set the number's "A message comes in" webhook to `POST https://<your-public-host>/whatsapp` (or `/sms`) — use `ngrok http 8000` for a public URL while testing locally.
3. Message it something like `Potato Rayya` — works in English, Hindi, or Punjabi.

## Automated data updates
`.github/workflows/update-mandi-data.yml` runs `fetch_daily_mandi_data.py` daily (18:00 UTC, after Agmarknet typically posts the day's prices) to pull fresh data and refresh `clean_mandi_prices.csv`. Can also be triggered manually from the Actions tab before a demo. `update_mandi_prices.py` is separate and manual — run it yourself after downloading a raw export by hand (see above).

## Known limitations
- Subscriptions reset on every Render redeploy (local JSON file, not a database) — fine for a demo.
- The forecasting model (ETS) only beats a naive "no change" baseline ~28% of the time on held-out data — documented honestly rather than hidden, and surfaced to users via the `confidence` field on `/predict`.
- Most crops don't have enough history to forecast reliably yet (see "Data note" above).
