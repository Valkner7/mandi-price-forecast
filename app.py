import os
import concurrent.futures
from dotenv import load_dotenv

load_dotenv()
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import db

BASE_DIR = Path(__file__).resolve().parent
STATIC_DASHBOARD_DIR = BASE_DIR / "static" / "dashboard"

app = FastAPI(
    title="Mandi Setu API",
    version="1.0.0",
    description=(
        "Backend for Mandi Setu — daily-price forecasting, trend tracking, "
        "and voice/WhatsApp advisory for Punjab's mandis. Powers "
        "MandiDarpan (dashboard), Mandi Sameep (nearby mandis), Mandi "
        "Rujhan (trend board), and Mandi Bol (voice advisory)."
    ),
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Mount Static Folder (for Leaflet CSS, JS, and Images)
# (removed dead /static mount - unused by current React/Vite frontend)

# Root Route to serve dashboard UI
# methods=["GET", "HEAD"]: this Starlette version does not auto-add HEAD
# support to GET routes, so health-check/uptime monitors that send HEAD
# requests (e.g. Render's own readiness probe) were getting a 405 here
# even though the service was healthy and GET worked fine.
@app.api_route("/", methods=["GET", "HEAD"])
async def read_index():
    index_path = BASE_DIR / "static" / "dashboard" / "index.html"
    if not index_path.exists():
        return JSONResponse(
            {"error": "Dashboard build not found. Run `npm run build` in frontend/ and copy the output to static/dashboard."},
            status_code=503,
        )
    return FileResponse(str(index_path))


# The built dashboard's index.html references its JS/CSS bundle and icons
# with root-relative paths (e.g. /assets/index-XXXX.js, /favicon.svg), since
# that's what Vite emits by default. Serving index.html at "/" above without
# also serving these exact paths at root left the page loading successfully
# but blank, with every asset request 404ing (the /dashboard mount below
# only covers /dashboard/assets/..., not /assets/...). This makes / actually
# render, not just the /dashboard mount.
@app.get("/favicon.svg")
async def read_favicon():
    favicon_path = BASE_DIR / "static" / "dashboard" / "favicon.svg"
    if not favicon_path.exists():
        raise HTTPException(status_code=404, detail="favicon.svg not found")
    return FileResponse(str(favicon_path))


@app.get("/icons.svg")
async def read_icons():
    icons_path = BASE_DIR / "static" / "dashboard" / "icons.svg"
    if not icons_path.exists():
        raise HTTPException(status_code=404, detail="icons.svg not found")
    return FileResponse(str(icons_path))

# The dashboard (static/dashboard) is served from the same origin as the
# API, so CORS is only needed if you ever point a separately-hosted
# frontend at this API. Left permissive since every endpoint here is
# read-only market data — nothing sensitive to protect with a stricter
# allow-list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Serves the built React/Vite dashboard (source in frontend/, compiled
# output committed to static/dashboard/) at /dashboard — it calls /meta,
# /predict, /history and /trends on this same app. If static/dashboard
# doesn't exist yet (e.g. a fresh clone before a build has been added),
# skip the mount instead of failing to start.
if STATIC_DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(STATIC_DASHBOARD_DIR), html=True), name="dashboard")
    _dashboard_assets_dir = STATIC_DASHBOARD_DIR / "assets"
    if _dashboard_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_dashboard_assets_dir)), name="dashboard-assets-root")

# --- Price alerts (Tier 1 #4) --------------------------------------------
# Twilio credentials for sending PROACTIVE outbound WhatsApp messages (as
# opposed to /whatsapp above, which only replies to an inbound message).
# Get these from the Twilio Console; TWILIO_WHATSAPP_FROM is your sandbox
# number in the form "whatsapp:+14155238886".
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

# Shared secret so /check-alerts (which sends real WhatsApp messages and
# will be hit by a public external cron service) can't be triggered by
# anyone who finds the URL. Set this in your cron service's URL as
# ?secret=... . If left unset, the endpoint is open — fine for local
# testing, NOT fine once deployed.
ALERTS_CRON_SECRET = os.getenv("ALERTS_CRON_SECRET")

# Render's free tier puts the service to sleep after ~15 min idle and only
# wakes it on an incoming HTTP request — a Python thread sleeping in the
# background is asleep too and will NOT fire on schedule. This in-process
# scheduler is therefore only reliable for local rehearsal, not the
# deployed demo. For the deployed app, point a free external scheduler
# (cron-job.org, UptimeRobot, or a scheduled GitHub Action) at
# GET /check-alerts?secret=... every 5-10 minutes instead — see README.
ENABLE_INTERNAL_ALERT_SCHEDULER = os.getenv("ENABLE_INTERNAL_ALERT_SCHEDULER", "false").lower() == "true"
ALERT_CHECK_INTERVAL_SECONDS = int(os.getenv("ALERT_CHECK_INTERVAL_SECONDS", "600"))
# Subscriptions live in Turso (see db.py) rather than a local file, so this
# module doesn't need a path or a lock for them anymore — just the Twilio/
# scheduler config above. db.py reads TURSO_DATABASE_URL and
# TURSO_AUTH_TOKEN from the environment directly (both required; get them
# from the Turso dashboard for whichever database you created).


@app.middleware("http")
async def log_request_timing(request, call_next):
    """Step 19: log latency for every real request, not just the manual
    scenario script — useful to point at live during a demo Q&A."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    print(f"[{request.method}] {request.url.path} -> {response.status_code} ({duration_ms:.0f}ms)")
    return response


@app.get("/sw.js")
def service_worker():
    """Step 15: offline price cache. Frontend-only — no new backend logic,
    just a service worker that caches GET /predict lookups (pure, cacheable,
    already returns everything needed) and the /voice-test shell itself, so
    a farmer who already checked a crop/mandi can see that price again with
    no signal. Served with no-cache so browsers always pick up SW updates.

    Source lives in static/sw.js (extracted from an inline string here so it
    gets normal JS tooling/syntax highlighting); this route just serves it
    with the headers a service worker needs (no-cache, correct MIME type)."""
    sw_path = BASE_DIR / "static" / "sw.js"
    if not sw_path.exists():
        raise HTTPException(status_code=404, detail="sw.js not found")
    return FileResponse(
        str(sw_path),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/voice-test", response_class=HTMLResponse)
def voice_test():
    """Farmer-facing one-question voice UI. HTML lives in
    templates/voice_test.html (extracted from an inline string here so it
    gets normal HTML/CSS/JS tooling); this route just reads and serves it.
    No server-side templating/variables are involved — it's static markup,
    so a plain file read is enough (no Jinja2 needed)."""
    voice_test_path = BASE_DIR / "templates" / "voice_test.html"
    if not voice_test_path.exists():
        raise HTTPException(status_code=404, detail="voice_test.html not found")
    return HTMLResponse(voice_test_path.read_text(encoding="utf-8"))


@app.get("/trends-dashboard", response_class=HTMLResponse)
def trends_dashboard():
    """Rate-board style page for mandi boards/policymakers — a market-wide
    view, distinct from /voice-test's one-farmer-one-question voice UI.
    Fetches /trends client-side and renders it; no server-side templating
    needed for a page this simple. HTML lives in templates/trends_dashboard.html
    (extracted from an inline string here so it gets normal tooling)."""
    trends_dashboard_path = BASE_DIR / "templates" / "trends_dashboard.html"
    if not trends_dashboard_path.exists():
        raise HTTPException(status_code=404, detail="trends_dashboard.html not found")
    return HTMLResponse(trends_dashboard_path.read_text(encoding="utf-8"))


# --- Router registration -----------------------------------------------
# Imported here, at the very end of the file, on purpose: routers/voice.py
# and routers/alerts.py both do `from app import <names>` for functions
# they need (_build_prediction, generate_advisory, FORECAST_CONFIDENCE_NOTE,
# etc.). Those names are all defined above this point in the file, so by
# the time these imports execute, app's (partially-initialized) module
# object already has them. Importing these routers any earlier would raise
# an ImportError from failing to find those attributes. routers/voice.py
# also imports alert-command helpers from routers/alerts.py directly.
#
# routers/predict.py is imported FIRST, ahead of routers/voice.py and
# routers/alerts.py, because both of those already do
# `from app import (_build_prediction, generate_advisory, ...)` /
# `from app import (..., FORECAST_CONFIDENCE_NOTE, _build_prediction)` —
# names that used to be defined directly in app.py and now live in
# routers/predict.py instead. Re-exporting them here (the `from
# routers.predict import ...` below binds those names onto app's own
# module object) lets those two already-committed router files keep
# working completely unmodified.
from routers.predict import (  # noqa: E402
    router as predict_router,
    _build_prediction,
    generate_advisory,
    FORECAST_CONFIDENCE_NOTE,
    _warm_trends_cache,
)
app.include_router(predict_router)

_TRENDS_WARMUP_TIMEOUT_SECONDS = 45


def _warm_trends_cache_startup() -> None:
    """Bounded, non-fatal wrapper around _warm_trends_cache(), registered
    as the actual startup hook below instead of _warm_trends_cache itself.

    On 2026-09-21, this hook (unbounded, synchronous) caused a Render
    deploy to hang and eventually time out ("Timed Out" after 19m05s) --
    _warm_trends_cache() falls back to per-pair ETS fitting for any
    crop-mandi pair the trained LightGBM model doesn't cover, and each
    fallback fit is three separate full numerical optimizations
    (fit_ets() in routers/predict.py) with nothing to bound how long that
    can take. A retry with no code changes succeeded, which pointed at
    startup-timing flakiness rather than a real code regression -- but
    that same unbounded shape is exactly what caused the earlier Turso
    incident (see _init_subscriptions_db_startup below), so it gets the
    same treatment here: run the real work in a background thread with a
    hard timeout, and never let a slow or hung warmup take the whole
    app's startup down with it. /trends will just compute on-demand,
    uncached, on its first real request if the warmup didn't finish in
    time -- slower for that one request, not a startup failure."""
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="trends-warmup"
    )
    future = executor.submit(_warm_trends_cache)
    try:
        future.result(timeout=_TRENDS_WARMUP_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        print(
            f"[STARTUP] WARNING: /trends cache warmup did not finish within "
            f"{_TRENDS_WARMUP_TIMEOUT_SECONDS}s -- continuing startup "
            f"without it; /trends will compute on-demand instead of served "
            f"from cache until it's naturally populated by a real request."
        )
    except Exception as exc:
        print(
            f"[STARTUP] WARNING: /trends cache warmup failed -- continuing "
            f"startup without it: {exc}"
        )
    finally:
        # wait=False: same reasoning as db.py's init_db() -- never block
        # here regardless of outcome, so a hung warmup thread is abandoned
        # rather than defeating the timeout by waiting for it anyway.
        executor.shutdown(wait=False)


# APIRouter has no .on_event of its own, so this startup hook (pre-warms
# the /trends cache) is registered directly on the app instance here
# instead of via a decorator in routers/predict.py — same pattern as the
# alerts scheduler below.
app.on_event("startup")(_warm_trends_cache_startup)

from routers.voice import router as voice_router  # noqa: E402
app.include_router(voice_router)

from routers.alerts import (  # noqa: E402
    router as alerts_router,
    _maybe_start_internal_alert_scheduler,
)
app.include_router(alerts_router)


def _init_subscriptions_db_startup() -> None:
    """Non-fatal wrapper around db.init_db(), registered as the actual
    startup hook below instead of db.init_db itself.

    On 2026-09-17, wiring db.init_db() directly into startup caused a
    Turso connection problem to hang the ENTIRE app's startup (predictions,
    dashboard, voice -- all of it), not just alerts, until Render's own
    ~15 minute deploy timeout killed it. That's disproportionate: nothing
    except the alerts feature actually depends on the subscriptions
    database. So failures here are now caught and logged, not raised --
    the rest of the app starts normally either way. If Turso really is
    unreachable, alerts-related endpoints will fail individually when
    someone actually tries to use them (a normal 5xx on that one request),
    which is the right amount of blast radius for a storage problem in one
    feature. db.py's own init_db() still enforces a hard timeout on the
    connection attempt itself (see db.py) -- this wrapper is what stops a
    failure, fast or slow, from taking the whole process down with it."""
    try:
        db.init_db()
    except Exception as exc:
        print(
            f"[STARTUP] WARNING: subscriptions database (Turso) init failed "
            f"-- alerts feature will be degraded until this is fixed: {exc}"
        )


# Creates the subscriptions table in Turso if it doesn't exist yet (see
# db.py). Registered before the scheduler startup hook below, so the table
# is guaranteed to exist before anything might try to check alerts against
# it, whether that's the optional in-process scheduler or the very first
# /check-alerts request -- unless it fails, in which case see the wrapper
# function's docstring just above for why that's now a warning, not a
# crash.
app.on_event("startup")(_init_subscriptions_db_startup)
# APIRouter has no .on_event of its own, so this startup hook (moved out of
# app.py along with the rest of the alerts code) is registered directly on
# the app instance here instead of via a decorator in routers/alerts.py.
app.on_event("startup")(_maybe_start_internal_alert_scheduler)
