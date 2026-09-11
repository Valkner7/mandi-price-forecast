# Handoff Report: app.py Refactor (mandi-price-forecast)

**Purpose of this document:** This is a continuation brief. If the current Claude
session hits a length/usage limit mid-refactor, paste this file (or its path) into
a new session so work can resume without re-discovering the codebase from scratch.

---

## 1. Repo state

- Repo: `https://github.com/Valkner7/mandi-price-forecast.git`
- Cloned locally at: `/home/claude/mandi-price-forecast` (fresh clone, no edits made yet as of this report)
- Framework: **FastAPI** (not Flask — earlier discussion assumed Flask-style blueprints; use **APIRouter** instead)
- `app.py`: 3,021 lines / ~125 KB — the file being split up
- Live deployment: `https://mandi-price-forecast-1.onrender.com`

## 2. The issue being fixed

`app.py` is a monolith mixing five unrelated concerns in one file:
1. Route definitions (predict, alerts, voice, dashboard, etc.)
2. Embedded HTML templates as Python strings (the `/voice-test` page)
3. Embedded service-worker JS as a Python string (`/sw.js`)
4. Forecasting orchestration logic (data loading, model inference)
5. Twilio webhook logic (SMS/WhatsApp signature verification, TwiML replies, alert subscriptions)

Target structure agreed on:
```
routers/
  predict.py      # forecasting + data endpoints
  alerts.py       # subscription + alert-check endpoints
  voice.py        # Twilio webhooks + voice-test page
  dashboard.py    # trends / compare / dashboard views
static_pages.py   # embedded HTML/JS extracted to real files
app.py            # slims down to app init + router registration
```

## 3. Exact structural map of current app.py (line numbers)

Imports / setup: lines 1–46
`app = FastAPI(...)` init: line 47

### All route decorators (in file order):
| Line | Route | Notes |
|---|---|---|
| 70 | `GET/HEAD /` | root |
| 88 | `GET /favicon.svg` | static asset route |
| 96 | `GET /icons.svg` | static asset route |
| 299 | `@app.middleware("http")` | custom middleware |
| 708 | `GET /sw.js` | **service worker JS embedded as string, starts ~line 709, function `service_worker()`** |
| 788 | `GET /voice-test` | **embedded HTML page, function `voice_test()`, spans to ~line 1286+** |
| 1393 | `GET /predict` | forecasting entry point, calls `_build_prediction()` at 1405 |
| 1520 | `GET /anomalies` | |
| 1547 | `GET /meta` | |
| 1575 | `GET /history` | |
| 1637 | `@app.on_event("startup")` | cache warmup, calls `_warm_trends_cache()` |
| 1650 | `GET /trends` | |
| 1779 | `GET /trends-dashboard` | embedded HTML dashboard view (distinct from voice-test) |
| 2018 | `GET /compare` | mandi comparison |
| 2228 | `GET /compare-advisory` | |
| 2267 | `GET /advisory` | |
| 2322 | `POST /voice-advisory` | |
| 2740 | `GET /check-alerts` | |
| 2741 | `POST /check-alerts` | |
| 2756 | `@app.on_event("startup")` | `_maybe_start_internal_alert_scheduler()` |
| 2890 | `POST /sms` | Twilio SMS webhook |
| 2910 | `POST /whatsapp` | Twilio WhatsApp webhook |
| 2988 | `GET /api/nearby-mandis` | |

### Supporting (non-route) function blocks by concern:
- **Forecasting/data core** (lines 438–708): `_load_full_dataframe`, `_load_forecast_model`, `load_series`, `_load_series_bulk`, `_load_arrival_bulk`, `load_arrival_series`, `fit_ets`, `detect_price_anomalies`
- **Advisory text generation** (lines 245–437): `_forecast_validation_summary`, `build_fallback_advisory`, `generate_advisory`
- **Alerts/subscriptions** (lines 2465–2757): `load_subscriptions`, `save_subscriptions`, `_looks_like_alert_command`, `_parse_price_threshold`, `_parse_alert_direction`, `_alert_direction_phrase_en`, `_alert_is_triggered`, `send_whatsapp_message`, `create_alert_from_message`, `list_alerts_for`, `stop_alerts_for`, `check_all_alerts`
- **Twilio/SMS plumbing** (lines 2777–2989): `detect_sms_language`, `build_reply_text`, `_twiml_response`, `_public_request_url`, `_verify_twilio_request`

### Key imports already in app.py relevant to the split:
```python
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioRestClient
from twilio.request_validator import RequestValidator
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
import price_model as pm
from mandi_coords import PUNJAB_MANDI_COORDINATES, calculate_haversine_distance
from voice_extraction import extract_crop_and_mandi
```
Other project modules already separated: `price_model.py`, `mandi_coords.py`, `voice_extraction.py`, `forecast_models.py`, `data_quality.py` — these are NOT part of the app.py problem, just referenced by it.

## 4. Extraction plan (do ONE step per session/message to stay within limits)

Recommended order (lowest risk → highest risk):

1. **Extract `/sw.js` service worker JS** → `static/sw.js`, replace `service_worker()` body with `FileResponse`/`StaticFiles` serving. (lines ~708–787)
2. **Extract `/voice-test` HTML** → `templates/voice_test.html` (or keep as static HTML served via `FileResponse` if no Jinja variables are interpolated — check for f-string variables first). (lines ~788–1286)
3. **Extract `/trends-dashboard` HTML** → same pattern. (line 1779 area)
4. **Extract Twilio/SMS webhook logic** → `routers/voice.py` (lines 2777–2989 plus the `/sms`, `/whatsapp` routes at 2890/2910)
5. **Extract alerts/subscriptions logic** → `routers/alerts.py` (lines 2465–2757, plus `/check-alerts` at 2740–2741)
6. **Extract forecasting core + `/predict`, `/anomalies`, `/meta`, `/history`, `/trends`, `/compare`** → `routers/predict.py` and/or `routers/dashboard.py` (lines 438–708 for core logic, 1393–2260 for routes)
7. **Final pass**: slim `app.py` to imports + `app = FastAPI(...)` + `app.include_router(...)` calls for each new router + startup event registration + middleware.

**Important dependency note:** advisory generation (lines 245–437) is used by both `/advisory` and `/voice-advisory` — decide whether it lives in `routers/voice.py` or a shared `services/advisory.py`. Recommend a shared `services/` module to avoid circular imports between routers.

## 5. Status as of this report

- [x] Repo cloned
- [x] Structural map completed (this document)
- [x] **Step 1 (sw.js extraction) — DONE.** JS moved verbatim to `static/sw.js`.
      The `/sw.js` route now serves it via `FileResponse` (same convention already
      used by the existing `/favicon.svg` and `/icons.svg` routes), preserving the
      `Cache-Control: no-cache` header and `application/javascript` media type.
      `app.py` shrank from 3,021 → 2,962 lines. Verified with `python3 -m py_compile app.py`
      (compiles clean) and confirmed no dangling references to the removed `sw_code`
      variable. `Response` import is still needed elsewhere (Twilio TwiML reply at
      the old line ~2796) so no import cleanup needed there.
      **NOTE: line numbers in Section 3's table above are now stale by ~-60 lines
      from `/voice-test` onward** — re-run `grep -n "^@app\." app.py` before
      continuing to get fresh line numbers before Step 2.
- [ ] Step 2 (voice-test HTML extraction) — not started
- [ ] Step 3 (trends-dashboard HTML extraction) — not started
- [ ] Step 4 (Twilio/voice router) — not started
- [ ] Step 5 (alerts router) — not started
- [ ] Step 6 (predict/dashboard router) — not started
- [ ] Step 7 (final app.py slimdown) — not started

**Not yet committed to git** — changes exist only in the local clone at
`/home/claude/mandi-price-forecast`. Run `git diff` there to review, then
`git add -A && git commit` (and push, if you have write access) once you're
happy with the extraction so it's not lost between sessions.

## 6. Instructions for the next Claude session

1. Re-clone or confirm `/home/claude/mandi-price-forecast` still exists; if not, re-run:
   `git clone https://github.com/Valkner7/mandi-price-forecast.git`
2. Pick up at the first unchecked step in Section 5.
3. Use `view` with `view_range` on the specific line numbers above — do not load the full 3,021-line file into context at once.
4. Make ONE extraction per turn, verify imports/route registration still make sense, then stop and report back before continuing to the next step.
5. Do not mix "move code" with "also refactor/improve this code" in the same step — defer improvements to a later cleanup pass.
