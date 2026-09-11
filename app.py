import os
from dotenv import load_dotenv

load_dotenv()
import time
import json
import re
import threading
import uuid
import hmac
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from mandi_coords import PUNJAB_MANDI_COORDINATES, calculate_haversine_distance

from datetime import datetime, timezone
import concurrent.futures
import numpy as np
import pandas as pd
from fastapi import HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from google import genai
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from voice_extraction import extract_crop_and_mandi
from io import BytesIO
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from gtts import gTTS
import price_model as pm


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "clean_mandi_prices.csv"
SUBSCRIPTIONS_PATH = BASE_DIR / "subscriptions.json"
STATIC_DASHBOARD_DIR = BASE_DIR / "static" / "dashboard"
FORECAST_MODEL_PATH = BASE_DIR / "models" / "lgbm_price_model.joblib"
FORECAST_MODEL_META_PATH = BASE_DIR / "models" / "lgbm_price_model_meta.json"

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
# Override with `export GEMINI_MODEL=...` if this model name ever 404s —
# verify the current valid model name in Google AI Studio before your demo.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# If Gemini is slow or overloaded (e.g. 503 UNAVAILABLE under high demand),
# don't leave the farmer staring at a spinner for 30-60s while the SDK
# retries in the background. Give up after this many seconds and fall back
# to a plain, template-built advisory using the same trusted forecast data.
# Kept below 10s (rather than exactly 10) so the plain-text endpoints
# (/advisory, /compare-advisory, SMS/WhatsApp replies) that use this value
# directly still return well within a 10s frontend budget once predict(),
# JSON serialization, and network round-trip are added on top.
ADVISORY_TIMEOUT_SECONDS = float(os.getenv("ADVISORY_TIMEOUT_SECONDS", "8"))

# /voice-advisory is the one endpoint where Gemini generation is followed by
# a second slow step (text-to-speech), so it can't just reuse
# ADVISORY_TIMEOUT_SECONDS for Gemini alone — that would leave TTS free to
# run unbounded on top, and the two stages could sum to well over 10s. This
# is the hard ceiling for that *entire* round trip as experienced by the
# frontend; Gemini and TTS dynamically split it (see voice_advisory()) so
# together they can't blow past it by more than a small, bounded margin.
VOICE_ADVISORY_BUDGET_SECONDS = float(os.getenv("VOICE_ADVISORY_BUDGET_SECONDS", "10"))

# Always keep at least this many seconds of the shared budget free for TTS,
# regardless of how long Gemini is allowed to run — a 2-3 sentence advisory
# typically synthesizes in well under this, but it needs *some* floor so a
# near-exhausted budget doesn't get handed to gTTS as an unreasonably short
# (or zero) timeout.
MIN_TTS_RESERVE_SECONDS = 2.5

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

_subscriptions_lock = threading.Lock()

# Basic, non-LLM translations used only when Gemini is unavailable. These are
# template phrases, not reviewed by a native speaker — good enough to keep
# the app useful during an outage, but worth a native speaker's once-over
# before you rely on them in front of judges.
_FALLBACK_TREND_WORDS = {
    "en": {"rising": "rising", "falling": "falling", "stable": "stable"},
    "hi": {"rising": "बढ़ता हुआ", "falling": "गिरता हुआ", "stable": "स्थिर"},
    "pa": {"rising": "ਵਧ ਰਿਹਾ", "falling": "ਘੱਟ ਰਿਹਾ", "stable": "ਸਥਿਰ"},
}

_FALLBACK_TEMPLATES = {
    "en": (
        "{crop} in {mandi} was ₹{latest_price} per quintal as of {latest_date}. "
        "The trend looks {trend}, with a forecast of about ₹{forecast_last} in "
        "{horizon} days. This is an estimate, not a guaranteed price.{data_note} "
        "{confidence_note} "
        "(Our advisory assistant is busy right now, so this is the plain "
        "forecast data instead of a full written recommendation.)"
    ),
    "hi": (
        "{mandi} में {crop} की कीमत {latest_date} तक ₹{latest_price} प्रति क्विंटल थी। "
        "रुझान {trend} है, अगले {horizon} दिनों में लगभग ₹{forecast_last} होने का अनुमान है। "
        "यह एक अनुमान है, गारंटी नहीं।{data_note} "
        "{confidence_note} "
        "(सलाहकार सेवा अभी व्यस्त है, इसलिए यह सीधा पूर्वानुमान डेटा है।)"
    ),
    "pa": (
        "{mandi} ਵਿੱਚ {crop} ਦੀ ਕੀਮਤ {latest_date} ਤੱਕ ₹{latest_price} ਪ੍ਰਤੀ ਕੁਇੰਟਲ ਸੀ। "
        "ਰੁਝਾਨ {trend} ਹੈ, ਅਗਲੇ {horizon} ਦਿਨਾਂ ਵਿੱਚ ਲਗਭਗ ₹{forecast_last} ਹੋਣ ਦੀ ਉਮੀਦ ਹੈ। "
        "ਇਹ ਇੱਕ ਅਨੁਮਾਨ ਹੈ, ਗਾਰੰਟੀ ਨਹੀਂ।{data_note} "
        "{confidence_note} "
        "(ਸਲਾਹ ਸੇਵਾ ਇਸ ਵੇਲੇ ਰੁੱਝੀ ਹੋਈ ਹੈ, ਇਸ ਲਈ ਇਹ ਸਿੱਧਾ ਪੂਰਵ ਅਨੁਮਾਨ ਡਾਟਾ ਹੈ।)"
    ),
}

_FALLBACK_DATA_NOTE = {
    "en": " Note: this price data is not from today.",
    "hi": " ध्यान दें: यह मूल्य डेटा आज का नहीं है।",
    "pa": " ਧਿਆਨ ਦਿਓ: ਇਹ ਕੀਮਤ ਡਾਟਾ ਅੱਜ ਦਾ ਨਹੀਂ ਹੈ।",
}

# Honest confidence framing: on this dataset, the forecasting model beats a
# simple "no change" (naive persistence) baseline on only a minority of
# tested crop-mandi combinations on a held-out window — short-horizon mandi
# prices behave close to a random walk. This is surfaced directly in the
# product (not just the pitch deck) so the app never overstates its own
# precision. The actual figures (combinations tested, win rate vs. naive)
# live in models/lgbm_price_model_meta.json, written by
# train_forecast_model.py's backtest_vs_naive() on every retrain — see
# _forecast_validation_summary() below, which reads that artifact directly
# rather than hardcoding a number that would go stale after the next retrain.
FORECAST_CONFIDENCE_NOTE = {
    "en": "This is a directional estimate, not a precise prediction — on this "
          "kind of data, forecasts improve on simply expecting no price change "
          "only some of the time.",
    "hi": "यह एक दिशात्मक अनुमान है, सटीक भविष्यवाणी नहीं — इस तरह के डेटा में, "
          "पूर्वानुमान हमेशा कीमत में कोई बदलाव न मानने से बेहतर नहीं होते।",
    "pa": "ਇਹ ਇੱਕ ਦਿਸ਼ਾਤਮਕ ਅਨੁਮਾਨ ਹੈ, ਸਟੀਕ ਭਵਿੱਖਬਾਣੀ ਨਹੀਂ — ਇਸ ਤਰ੍ਹਾਂ ਦੇ ਡਾਟੇ ਵਿੱਚ, "
          "ਅਨੁਮਾਨ ਹਮੇਸ਼ਾ ਕੀਮਤ ਵਿੱਚ ਕੋਈ ਬਦਲਾਅ ਨਾ ਮੰਨਣ ਨਾਲੋਂ ਬਿਹਤਰ ਨਹੀਂ ਹੁੰਦੇ।",
}


def _forecast_validation_summary(meta: dict | None) -> str:
    """Human-readable validation claim for /predict's confidence block,
    built from the real backtest numbers baked into the trained model
    artifact (models/lgbm_price_model_meta.json) rather than a hardcoded
    string that references a notebook nobody committed. Falls back to an
    honest "not available" note if no artifact is loaded (e.g. the ETS
    fallback path, where no LightGBM meta exists at all).
    """
    if not meta:
        return (
            "No trained global model artifact is loaded for this response "
            "(ETS fallback in use) — no backtest numbers apply here."
        )

    combos = meta.get("crop_mandi_combinations_tested")
    win_rate = meta.get("crop_mandi_win_rate_vs_naive")
    backtest_days = meta.get("backtest_days")

    if combos is None or win_rate is None:
        return (
            "Trained model artifact loaded, but it doesn't include backtest "
            "metadata (older artifact format) — see train_forecast_model.py "
            "for how validation is computed."
        )

    return (
        f"{combos} crop-mandi combinations, {backtest_days}-day held-out "
        f"test window (see train_forecast_model.py's backtest_vs_naive; "
        f"beat naive persistence in {win_rate:.1%} of combinations — "
        f"models/lgbm_price_model_meta.json has the full numbers)."
    )


def build_fallback_advisory(forecast_data: dict, language_code: str) -> str:
    """Plain, non-LLM advisory built directly from trusted forecast data.
    Used only when the Gemini call times out or fails, so the farmer still
    gets real numbers instead of nothing."""
    template = _FALLBACK_TEMPLATES.get(language_code, _FALLBACK_TEMPLATES["en"])
    trend_words = _FALLBACK_TREND_WORDS.get(language_code, _FALLBACK_TREND_WORDS["en"])
    return template.format(
        crop=forecast_data["crop"],
        mandi=forecast_data["mandi"],
        latest_price=forecast_data["latest_price"],
        latest_date=forecast_data["latest_date"],
        trend=trend_words.get(forecast_data["trend"], forecast_data["trend"]),
        forecast_last=forecast_data["forecast"][-1]["price"],
        horizon=forecast_data["forecast_horizon_days"],
        data_note=_FALLBACK_DATA_NOTE.get(language_code, _FALLBACK_DATA_NOTE["en"])
        if forecast_data.get("data_note")
        else "",
        confidence_note=FORECAST_CONFIDENCE_NOTE.get(language_code, FORECAST_CONFIDENCE_NOTE["en"]),
    )


@app.middleware("http")
async def log_request_timing(request, call_next):
    """Step 19: log latency for every real request, not just the manual
    scenario script — useful to point at live during a demo Q&A."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    print(f"[{request.method}] {request.url.path} -> {response.status_code} ({duration_ms:.0f}ms)")
    return response

LANGUAGES = {
    "en": {
        "name": "English",
        "script_rule": "Write only in English.",
    },
    "hi": {
        "name": "Hindi",
        "script_rule": "Write only in Hindi using Devanagari script. Do not use English or Roman Hindi.",
    },
    "pa": {
        "name": "Punjabi",
        "script_rule": "Write only in Punjabi using Gurmukhi script. Do not use English or Roman Punjabi.",
    },
}
def generate_advisory(
    forecast_data: dict,
    farmer_question: str,
    language_code: str,
    timeout_seconds: float | None = None,
) -> tuple[str, bool]:
    """Turn trusted forecast data into a short advisory in one chosen language.

    Returns (advisory_text, used_fallback). used_fallback is True when Gemini
    was too slow or unavailable and we fell back to a plain, template-built
    advisory instead of raising and leaving the caller with nothing.

    timeout_seconds overrides the module-level ADVISORY_TIMEOUT_SECONDS for
    this call. voice_advisory() uses this to hand Gemini a shorter deadline
    than the default, reserving the rest of its shared request budget for
    the text-to-speech step that follows (see VOICE_ADVISORY_BUDGET_SECONDS)."""

    if language_code not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported language. Use en for English, hi for Hindi, or pa for Punjabi.",
        )

    # The Google SDK supports either of these standard environment-variable names.
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        raise HTTPException(
            status_code=500,
            detail="A Gemini API key is missing. Set GEMINI_API_KEY or GOOGLE_API_KEY in the terminal before starting the server.",
        )

    language = LANGUAGES[language_code]
    client = genai.Client(api_key=gemini_api_key)

    instructions = f"""
You are a mandi-price advisory assistant for farmers in India.

Reply language: {language["name"]}
Language requirement: {language["script_rule"]}

Strict rules:
1. Use only the price forecast provided below.
2. Never invent prices, dates, crops, mandis, weather, or market facts.
3. Give a useful recommendation in only 2 or 3 short sentences.
4. State that the result is an estimate, not a guaranteed price.
5. Do not give medical, legal, emergency, or financial-investment advice.
6. If the question is unrelated to the supplied crop and mandi, politely say that you can only answer about this forecast.
7. If a "Data recency note" is provided below, briefly mention that the price data isn't from today — do not imply the price is current if it isn't.
8. A "Confidence note" is provided below — briefly and simply reflect that this is a directional estimate rather than a precise, guaranteed forecast. Don't use technical terms like "baseline" or "held-out" — just convey the honest limitation in plain language.
"""

    forecast_summary = f"""
Crop: {forecast_data["crop"]}
Mandi: {forecast_data["mandi"]}
Latest date: {forecast_data["latest_date"]}
Latest price: ₹{forecast_data["latest_price"]} {forecast_data["unit"]}
Forecast trend: {forecast_data["trend"]}
Forecast for the next {forecast_data["forecast_horizon_days"]} days:
{forecast_data["forecast"]}
{"Data recency note: " + forecast_data["data_note"] if forecast_data.get("data_note") else ""}
Confidence note: {forecast_data.get("confidence", {}).get("note", FORECAST_CONFIDENCE_NOTE["en"])}
"""

    def call_gemini():
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"""
      {instructions}

      Farmer question:

     {farmer_question}

      Trusted forecast data:

      {forecast_summary}

      Write the advisory now.
      """,
        )

    # Run the call in a worker thread so we can enforce a hard wall-clock
    # timeout — the Gemini SDK's own retry-on-503 behavior can otherwise
    # take 30-60+ seconds before giving up, which is much longer than a
    # farmer will wait for a spoken answer.
    # Not used as a context manager on purpose: exiting a `with` block calls
    # shutdown(wait=True), which would block until the slow call finishes
    # anyway and silently erase the timeout. shutdown(wait=False) lets us
    # return immediately; the abandoned call finishes quietly in the
    # background and its result is simply discarded.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call_gemini)
    effective_timeout = ADVISORY_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        response = future.result(timeout=effective_timeout)
        executor.shutdown(wait=False)
        return response.text.strip(), False
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False)
        print(f"GEMINI TIMEOUT: no response within {effective_timeout}s")
        return build_fallback_advisory(forecast_data, language_code), True
    except Exception as error:
        executor.shutdown(wait=False)
        print("GEMINI ERROR:", error)
        return build_fallback_advisory(forecast_data, language_code), True
# In-memory cache for the dataset CSV, keyed off the file's mtime. Every
# /predict call used to re-read and re-parse the full CSV from disk (12.7k+
# rows), and /compare + check_all_alerts multiply that cost per mandi/group
# checked in a single request. Caching the parsed DataFrame avoids the
# repeat disk read + parse on every call. Keyed on mtime (not just cached
# forever) so a redeployed or edited CSV — e.g. during dataset-strengthening
# work — is picked up automatically without needing a server restart.
_dataframe_cache: dict = {"mtime": None, "df": None}


def _load_full_dataframe() -> pd.DataFrame:
    mtime = DATA_PATH.stat().st_mtime
    if _dataframe_cache["df"] is None or _dataframe_cache["mtime"] != mtime:
        _dataframe_cache["df"] = pd.read_csv(DATA_PATH, parse_dates=["date"])
        _dataframe_cache["mtime"] = mtime
    return _dataframe_cache["df"]


# In-memory cache for the trained LightGBM forecast artifact, keyed off the
# model file's mtime — same pattern as _load_full_dataframe() above. The
# artifact is trained OFFLINE by train_forecast_model.py (never inside a
# request); this just loads the already-trained model into memory once and
# reuses it, instead of the old fit_ets() pattern of fitting a fresh model
# on every single /predict call (see handoff notes §3b for the measured
# latency cost of that).
_forecast_model_cache: dict = {"mtime": None, "model": None, "meta": None}


def _load_forecast_model():
    """Returns (model, meta) for the global LightGBM model, or (None, None)
    if no trained artifact exists yet (e.g. first deploy before the
    training workflow has run once) — callers should fall back to fit_ets()
    in that case, not error out."""
    if not FORECAST_MODEL_PATH.exists() or not FORECAST_MODEL_META_PATH.exists():
        return None, None

    mtime = FORECAST_MODEL_PATH.stat().st_mtime
    if _forecast_model_cache["model"] is None or _forecast_model_cache["mtime"] != mtime:
        try:
            model, meta = pm.load_artifact(FORECAST_MODEL_PATH, FORECAST_MODEL_META_PATH)
        except Exception as error:
            print("FORECAST MODEL LOAD ERROR:", error)
            return None, None
        _forecast_model_cache["model"] = model
        _forecast_model_cache["meta"] = meta
        _forecast_model_cache["mtime"] = mtime

    return _forecast_model_cache["model"], _forecast_model_cache["meta"]


def load_series(crop: str, mandi: str) -> pd.Series:
    # Swagger UI or voice/text input can include accidental surrounding spaces.
    # Normalise them before matching against the dataset.
    crop = crop.strip()
    mandi = mandi.strip()

    df = _load_full_dataframe()
    mask = (
        df["crop"].astype(str).str.casefold().eq(crop.casefold())
        & df["mandi"].astype(str).str.casefold().eq(mandi.casefold())
    )
    data = df.loc[mask, ["date", "price"]].dropna().sort_values("date")
    if data.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for crop='{crop}' and mandi='{mandi}'.",
        )

    MIN_POINTS = 30  # below this, ETS forecasts aren't trustworthy
    if len(data) < MIN_POINTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Only {len(data)} price records found for crop='{crop}' and "
                f"mandi='{mandi}' — not enough history for a reliable forecast "
                f"(need at least {MIN_POINTS}). Try Potato or Onion, which have "
                f"the deepest history in this dataset."
            ),
        )

    # If multiple records occur on the same date, use their mean price.
    series = data.groupby("date")["price"].mean().sort_index()

    # Keep the raw, actually-reported series (before any fill) around for
    # anomaly detection — see detect_price_anomalies() for why forecasting
    # and anomaly detection need different versions of this data.
    raw_series = series.copy()

    # Forecasting needs a regular daily frequency. Missing days are filled
    # with the most recent observed mandi price; raw gaps are still reported
    # separately in the notebook. Resample to the daily grid BEFORE
    # ffilling so we can record whether the most recent day is a real
    # report or a forward-filled repeat — the forecasting model uses this
    # as its is_observed_today feature (see price_model.py).
    daily = series.resample("D").last()
    is_observed_today = bool(daily.notna().iloc[-1]) if len(daily) else True
    series = daily.ffill()
    series.attrs["raw"] = raw_series
    series.attrs["is_observed_today"] = is_observed_today
    return series


def _load_series_bulk(crop_names: list[str], min_points: int = 30) -> tuple[dict, dict]:
    """Bulk equivalent of load_series() for /trends: scans the full
    dataframe ONCE for all requested crops and builds a price series for
    every (crop, mandi) pair found, instead of load_series()'s pattern of
    re-masking the entire dataframe from scratch for every single pair
    (239 separate full-table scans in /trends' case — the other major
    cost identified in profiling, alongside the per-call LightGBM
    overhead that forecast_recursive_batch() addresses).

    Applies the same per-pair logic as load_series(): mean price per
    date (for same-date duplicates), resampled onto a daily frequency and
    forward-filled, with pairs below `min_points` daily records excluded
    (matching load_series()'s MIN_POINTS=30 threshold) rather than raising
    — callers just won't see that pair, mirroring /trends' existing
    "skip pairs that can't forecast" behavior.

    Returns ({(crop, mandi): price_series}, {(crop, mandi): is_observed_today}),
    one entry per pair with enough history. Crop/mandi names are stripped,
    matching load_series(). The second dict mirrors load_series()'s
    series.attrs["is_observed_today"] per pair, for callers (forecast_recursive_batch)
    that need to know whether each pair's most recent day is a real report.
    """
    df = _load_full_dataframe()
    crop_set = {c.strip().casefold() for c in crop_names}
    sub = df[df["crop"].astype(str).str.casefold().isin(crop_set)]
    sub = sub.dropna(subset=["price"])

    series_map: dict = {}
    is_observed_map: dict = {}
    for (crop_val, mandi_val), group in sub.groupby(["crop", "mandi"], sort=False):
        series = group.groupby("date")["price"].mean().sort_index()
        if len(series) < min_points:
            continue
        daily = series.resample("D").last()
        key = (str(crop_val).strip(), str(mandi_val).strip())
        is_observed_map[key] = bool(daily.notna().iloc[-1]) if len(daily) else True
        series_map[key] = daily.ffill()
    return series_map, is_observed_map


def _load_arrival_bulk(crop_names: list[str], price_index_by_pair: dict) -> dict:
    """Bulk equivalent of load_arrival_series() for /trends — one full-
    dataframe scan for all requested crops instead of one scan per pair.
    Mirrors load_arrival_series()'s contract: summed (not averaged) per
    day, reindexed onto each pair's own price index, not forward-filled.
    Pairs with no arrival data (or no arrival_qty column at all) get an
    all-NaN series, exactly like load_arrival_series() would return.
    """
    df = _load_full_dataframe()
    if "arrival_qty" not in df.columns:
        return {key: pd.Series(np.nan, index=idx) for key, idx in price_index_by_pair.items()}

    crop_set = {c.strip().casefold() for c in crop_names}
    sub = df[df["crop"].astype(str).str.casefold().isin(crop_set)].copy()
    sub["arrival_qty"] = pd.to_numeric(sub["arrival_qty"], errors="coerce")

    arrival_map: dict = {}
    for (crop_val, mandi_val), group in sub.groupby(["crop", "mandi"], sort=False):
        key = (str(crop_val).strip(), str(mandi_val).strip())
        if key not in price_index_by_pair:
            continue
        arrival = group.groupby("date")["arrival_qty"].sum(min_count=1).sort_index()
        arrival_map[key] = arrival.reindex(price_index_by_pair[key])

    # Any pair with no arrival rows at all (e.g. filtered out above) still
    # needs an all-NaN entry so callers never need to special-case absence.
    for key, idx in price_index_by_pair.items():
        if key not in arrival_map:
            arrival_map[key] = pd.Series(np.nan, index=idx)
    return arrival_map


def load_arrival_series(crop: str, mandi: str, price_index: pd.DatetimeIndex) -> pd.Series:
    """Mirrors price_model.build_panel's arrival handling: summed per day
    (not averaged, since arrival is a volume), reindexed onto the same
    daily index as the price series, and NOT forward-filled (a gap day
    with no reported arrival stays NaN rather than fabricating a repeat
    trading day). Returns an all-NaN series if this crop/mandi has no
    arrival_qty column or no arrival data at all, so callers never need
    to special-case its absence — matches build_panel()'s contract.
    """
    df = _load_full_dataframe()
    if "arrival_qty" not in df.columns:
        return pd.Series(np.nan, index=price_index)

    crop = crop.strip()
    mandi = mandi.strip()
    mask = (
        df["crop"].astype(str).str.casefold().eq(crop.casefold())
        & df["mandi"].astype(str).str.casefold().eq(mandi.casefold())
    )
    data = df.loc[mask, ["date", "arrival_qty"]].copy()
    data["arrival_qty"] = pd.to_numeric(data["arrival_qty"], errors="coerce")

    arrival = data.groupby("date")["arrival_qty"].sum(min_count=1).sort_index()
    return arrival.reindex(price_index)


def fit_ets(series: pd.Series):
    """Fit a robust Exponential Smoothing model and return fitted model + name."""
    candidates = [
        ("ETS_add_damped", dict(trend="add", damped_trend=True)),
        ("ETS_add", dict(trend="add", damped_trend=False)),
        ("ETS_level", dict(trend=None, damped_trend=False)),
    ]
    best_model, best_name, best_aic = None, None, np.inf

    for name, kwargs in candidates:
        try:
            model = ExponentialSmoothing(
                series,
                seasonal=None,
                initialization_method="estimated",
                **kwargs,
            ).fit(optimized=True)
            aic = getattr(model, "aic", np.inf)
            if np.isfinite(aic) and aic < best_aic:
                best_model, best_name, best_aic = model, name, aic
        except Exception:
            continue

    if best_model is None:
        # Simple fallback that is still within the Exponential Smoothing family.
        best_name = "ETS_level"
        best_model = ExponentialSmoothing(
            series,
            trend=None,
            seasonal=None,
            initialization_method="estimated",
        ).fit(optimized=True)

    return best_model, best_name


# --- Price-spike / anomaly detection (Tier 2 #5) ---------------------------
# Ties directly to the project's own problem statement about middlemen who
# "may not act in the farmer's interest": an unusually large day-over-day
# jump or drop can be a signal worth a second look (distress-selling,
# middleman activity) — but can just as easily be a data-entry irregularity
# in the source data, so this is deliberately framed as "worth checking",
# not an assertion about cause.
def detect_price_anomalies(series: pd.Series, z_threshold: float = 2.5) -> list[dict]:
    """Flag day-over-day price changes that are statistical outliers
    relative to that crop-mandi's own volatility (z-score on % change).
    Pure stdlib/pandas/numpy — no new dependency, reuses the same series
    load_series() already builds for forecasting.

    Deliberately computed on the RAW, actually-reported prices (via
    series.attrs["raw"], set by load_series()) rather than the daily
    forward-filled series used for forecasting. Forward-filling introduces
    a large share of synthetic zero-change days (measured ~53% for a
    typical crop-mandi pair here), which artificially deflates the
    standard deviation and shifts which real price moves cross the
    z-threshold. Falls back to the passed-in series if raw data isn't
    attached, so this still works if called with a plain series."""
    raw_series = series.attrs.get("raw", series)
    pct_change = raw_series.pct_change().dropna()
    if len(pct_change) < 10:
        return []  # not enough history for a meaningful z-score

    mean = pct_change.mean()
    std = pct_change.std()
    if not np.isfinite(std) or std == 0:
        return []

    z_scores = (pct_change - mean) / std
    anomalies = []
    for date, z in z_scores.items():
        if abs(z) >= z_threshold:
            anomalies.append({
                "date": date.date().isoformat(),
                "price": round(float(raw_series.loc[date]), 2),
                "pct_change": round(float(pct_change.loc[date]) * 100, 1),
                "z_score": round(float(z), 2),
                "direction": "spike" if z > 0 else "drop",
            })
    return anomalies

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


@app.get("/predict")
def predict(
    crop: str = Query(..., description="Crop name, e.g. Potato"),
    mandi: str = Query(..., description="Mandi name, e.g. Rayya"),
):
    result = _build_prediction(crop=crop, mandi=mandi)
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _build_prediction(crop: str, mandi: str) -> dict:
    """Core prediction logic, returning a plain dict.

    Used both by the /predict HTTP route (which wraps this in a
    JSONResponse) and by internal callers like the voice advisory
    pipeline, which need the raw dict — not an HTTP response object —
    so they can subscript it directly (e.g. forecast_data["crop"]).
    """
    series = load_series(crop, mandi)
    horizon = 7

    # Global LightGBM model first (fast, cached, trained offline — see
    # price_model.py / train_forecast_model.py). Falls back to the
    # original per-series ETS path if no artifact has been trained yet,
    # this crop/mandi has too little history, or prediction throws for
    # any other reason — same forecast contract either way, with a
    # transparent model_note added to the response so the fallback is
    # never silent.
    model_note = None
    lgbm_model, lgbm_meta = _load_forecast_model()
    forecast_values = None
    model_name = None

    if lgbm_model is not None:
        try:
            arrival_series = load_arrival_series(crop, mandi, series.index)
            forecast_values = pm.forecast_recursive(
                lgbm_model, lgbm_meta, series, crop, mandi, horizon=horizon,
                arrival_series=arrival_series,
                is_observed_today=series.attrs.get("is_observed_today", True),
            )
            model_name = "LightGBM_global"
        except Exception as error:
            print("LIGHTGBM PREDICT ERROR, falling back to ETS:", error)
            forecast_values = None

    if forecast_values is None:
        model, model_name = fit_ets(series)
        forecast = model.forecast(horizon)
        forecast_values = [float(x) for x in forecast.values]
        model_note = (
            "Using the per-series ETS fallback model for this forecast "
            + ("(no trained global model artifact found)."
               if lgbm_model is None
               else "(the global model couldn't produce a prediction for this crop/mandi).")
        )

    latest_price = float(series.iloc[-1])
    delta = float(forecast_values[-1] - latest_price)

    # Build forecast dates explicitly from the last known date rather than
    # relying on forecast.items() to carry a proper DatetimeIndex — keeps
    # date output correct even if the fitted model's index loses its freq.
    last_date = series.index[-1]
    forecast_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1), periods=horizon, freq="D"
    )

    # 1% threshold avoids calling tiny changes a meaningful trend.
    threshold = max(1.0, abs(latest_price) * 0.01)
    if delta > threshold:
        trend = "rising"
    elif delta < -threshold:
        trend = "falling"
    else:
        trend = "stable"

    days_stale = (pd.Timestamp.now().normalize() - last_date).days

    # Compact anomaly summary (full detail lives at /anomalies) — surfaces
    # whether the most recent price itself was an outlier move, and how many
    # such moves happened in the last 30 days, without bloating /predict's
    # main payload with the full history of flagged dates.
    all_anomalies = detect_price_anomalies(series)
    recent_cutoff = last_date - pd.Timedelta(days=30)
    recent_anomalies = [a for a in all_anomalies if pd.Timestamp(a["date"]) >= recent_cutoff]
    latest_is_anomaly = bool(all_anomalies and all_anomalies[-1]["date"] == last_date.date().isoformat())

    result = {
        "crop": crop,
        "mandi": mandi,
        "latest_date": last_date.date().isoformat(),
        "latest_price": round(latest_price, 2),
        "trend": trend,
        "forecast_horizon_days": horizon,
        "forecast": [
            {"date": dt.date().isoformat(), "price": round(val, 2)}
            for dt, val in zip(forecast_dates, forecast_values)
        ],
        "model": model_name,
        "unit": "INR per quintal",
        "confidence": {
            "note": FORECAST_CONFIDENCE_NOTE["en"],
            "validated_on": _forecast_validation_summary(lgbm_meta),
        },
        "anomaly_flag": {
            "latest_price_is_anomaly": latest_is_anomaly,
            "anomalies_last_30_days": len(recent_anomalies),
            "most_recent_anomaly": all_anomalies[-1] if all_anomalies else None,
            "note": "Unusually large day-over-day price move(s) detected in this crop-mandi's "
            "own history — worth a second look (possible distress-selling or middleman "
            "activity), not a determination of cause. See /anomalies for full history.",
        },
    }
    if days_stale > 90:
        result["data_note"] = (
            f"Most recent available record is from {result['latest_date']} "
            f"({days_stale} days ago). Forecast is projected forward from that date, "
            f"not from today."
        )
    if model_note:
        result["model_note"] = model_note
    return result


@app.get("/anomalies")
def anomalies(
    crop: str = Query(..., description="Crop name, e.g. Potato"),
    mandi: str = Query(..., description="Mandi name, e.g. Rayya"),
    z_threshold: float = Query(
        2.5, ge=1.0, le=6.0,
        description="How unusual a day-over-day move must be (in standard deviations) to be flagged. Lower = more sensitive.",
    ),
):
    """Full anomaly history for a crop-mandi pair — every day-over-day
    price move statistically unusual for that specific series, not just
    the compact summary embedded in /predict."""
    series = load_series(crop, mandi)
    detected = detect_price_anomalies(series, z_threshold=z_threshold)
    return {
        "crop": crop,
        "mandi": mandi,
        "z_threshold": z_threshold,
        "anomaly_count": len(detected),
        "anomalies": detected,
        "note": "Flags unusually large day-over-day price moves (statistical outliers relative "
        "to this crop-mandi's own volatility) as worth a closer look — could reflect "
        "distress-selling, middleman activity, or a data-entry irregularity in the source "
        "data. This is not a determination of cause.",
    }


@app.get("/meta")
def meta(
    crop: str | None = Query(
        None,
        description="Optional crop name — if given, the returned `mandis` list "
        "is narrowed to mandis with enough history to forecast this crop "
        "(same criteria /predict uses), instead of every mandi in the dataset.",
    ),
):
    """Crop and mandi names present in the dataset, so a frontend can
    populate its dropdowns from real data instead of a hardcoded list.
    Without `crop`, doesn't filter by data sufficiency — /predict is still
    the source of truth for whether a given crop-mandi pair has enough
    history to forecast (see its 422 response). With `crop`, `mandis` is
    pre-filtered to pairs /predict can actually serve."""
    df = _load_full_dataframe()
    crops = sorted(df["crop"].dropna().astype(str).str.strip().unique().tolist())
    if crop:
        mandi_names = _viable_mandis_for_crop(crop)
    else:
        mandi_names = sorted(df["mandi"].dropna().astype(str).str.strip().unique().tolist())
    return {
        "crops": crops,
        "mandis": mandi_names,
        "reliable_crops": ["Potato", "Onion", "Tomato"],
    }


@app.get("/history")
def history(
    crop: str = Query(..., description="Crop name, e.g. Potato"),
    mandi: str = Query(..., description="Mandi name, e.g. Rayya"),
    days: int = Query(
        45, ge=7, le=365,
        description="How many most recent days of price history to return.",
    ),
):
    """Recent daily price history for a crop-mandi pair — for charting the
    actual price trend client-side, alongside /predict's forecast. Reuses
    load_series() like every other endpoint here, so it raises the same
    404/422 as /predict for an unknown or too-short series."""
    series = load_series(crop, mandi)
    recent = series.tail(days)
    return {
        "crop": crop,
        "mandi": mandi,
        "unit": "INR per quintal",
        "points": [
            {"date": dt.date().isoformat(), "price": round(float(price), 2)}
            for dt, price in recent.items()
        ],
    }


# --- Trend dashboard (Tier 2 #6) -------------------------------------------
# A separate, data-dense view (not the farmer-facing voice UI) showing which
# crop-mandi pairs are trending up/down across the whole dataset — useful to
# mandi boards/policymakers scanning the market, not just one farmer with one
# question. Deliberately reuses predict() per crop-mandi pair rather than any
# new forecasting logic, matching every other feature in this file.
_RELIABLE_TREND_CROPS = ["Potato", "Onion", "Tomato"]  # only crops with enough history to forecast


def _viable_mandis_for_crop(crop: str, min_points: int = 30) -> list[str]:
    """Every mandi with enough price history for predict() to succeed on
    this crop, in first-seen order from the dataset (stable, readable
    ordering rather than alphabetical)."""
    df = _load_full_dataframe()
    sub = df[df["crop"].astype(str).str.casefold() == crop.casefold()]
    counts = sub.groupby("mandi").size()
    viable_names = set(counts[counts >= min_points].index)
    # Preserve first-seen order/casing from the CSV rather than the sorted
    # index groupby returns, so results read naturally.
    seen = []
    for name in sub["mandi"]:
        if name in viable_names and name not in seen:
            seen.append(name)
    return seen


# Short-TTL cache for /trends: the underlying data only changes once a day
# (via the daily fetch + retrain GitHub Action), so re-computing 239
# forecasts on every dashboard load/refresh within the same few minutes is
# pure waste. Keyed on the `crops` query string so different callers of
# this endpoint don't collide. This is a defensive extra layer on top of
# the real fix below (batching); even a fully-optimized /trends benefits
# from not recomputing identical results for a dashboard that polls it.
_TRENDS_CACHE_TTL_SECONDS = 600  # 10 minutes
_trends_cache: dict = {}

@app.on_event("startup")
def _warm_trends_cache():
    """Pre-compute /trends for the default crop set on startup, so the
    first real dashboard visitor doesn't pay the cold-cache cost (see
    _TRENDS_CACHE_TTL_SECONDS above). Uses the same default crops the
    route itself defaults to, so it fills the exact cache key a fresh
    page load will hit."""
    try:
        trends(crops=",".join(_RELIABLE_TREND_CROPS))
    except Exception as exc:
        print(f"[TRENDS] cache warm-up failed: {exc}")


@app.get("/trends")
def trends(
    crops: str = Query(
        ",".join(_RELIABLE_TREND_CROPS),
        description="Comma-separated crop names. Defaults to the crops with enough "
        "history to forecast reliably (Potato, Onion, Tomato).",
    ),
):
    """Which crop-mandi pairs are trending up/down right now, across the
    whole dataset — not one farmer's one question. Built for a mandi board
    or policymaker scanning the market at a glance, so it's grouped by crop
    and sorted by size of move (biggest gainers/losers first) rather than
    alphabetically.

    Performance note: this used to call predict() once per crop-mandi pair
    (239 of them at last count), each of which (a) re-scanned the full
    dataframe from scratch via load_series()/load_arrival_series(), and
    (b) issued 7 separate single-row LightGBM.predict() calls for its
    7-day recursive forecast — ~1,673 single-row predict() calls and 239
    full-table scans per /trends request, measured at 13-23s wall clock.
    This version bulk-loads the dataframe once for all requested crops
    (_load_series_bulk/_load_arrival_bulk) and forecasts every pair in a
    single batched pass (forecast_recursive_batch — one predict() call per
    horizon day, across ALL pairs at once, instead of one per pair per
    day). Falls back to the original per-pair ETS path only for pairs the
    batched LightGBM path can't cover (no trained artifact at all), same
    honesty contract as /predict.
    """
    crop_names = [c.strip() for c in crops.split(",") if c.strip()]
    if not crop_names:
        raise HTTPException(status_code=400, detail="Provide at least one crop name.")

    cache_key = ",".join(sorted(c.casefold() for c in crop_names))
    cached = _trends_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_response = cached
        if time.time() - cached_at < _TRENDS_CACHE_TTL_SECONDS:
            return cached_response

    horizon = 7
    lgbm_model, lgbm_meta = _load_forecast_model()

    series_map, is_observed_map = _load_series_bulk(crop_names)

    batch_forecasts: dict = {}
    if lgbm_model is not None and series_map:
        try:
            price_index_by_pair = {key: series.index for key, series in series_map.items()}
            arrival_map = _load_arrival_bulk(crop_names, price_index_by_pair)
            batch_forecasts = pm.forecast_recursive_batch(
                lgbm_model, lgbm_meta, series_map, horizon=horizon,
                arrival_map=arrival_map,
                is_observed_map=is_observed_map,
            )
        except Exception as error:
            print("LIGHTGBM BATCH PREDICT ERROR, falling back to per-pair ETS:", error)
            batch_forecasts = {}

    results_by_crop = {}
    rising = falling = stable = 0

    for crop in crop_names:
        mandi_names = _viable_mandis_for_crop(crop)
        rows = []
        for mandi_name in mandi_names:
            key = (crop.strip(), mandi_name.strip())
            series = series_map.get(key)
            if series is None:
                continue  # shouldn't happen given the min-points filter, but don't let one bad pair fail the whole dashboard

            latest_price = float(series.iloc[-1])
            forecast_values = batch_forecasts.get(key)
            if forecast_values is None:
                # No trained LightGBM artifact at all, or the batch call
                # failed outright — fall back to the original per-series
                # ETS path for this pair only, same as /predict would.
                model, _ = fit_ets(series)
                forecast = model.forecast(horizon)
                forecast_values = [float(x) for x in forecast.values]

            forecast_last = forecast_values[-1]
            delta = forecast_last - latest_price
            threshold = max(1.0, abs(latest_price) * 0.01)
            if delta > threshold:
                trend = "rising"
            elif delta < -threshold:
                trend = "falling"
            else:
                trend = "stable"

            pct_change = round((delta / latest_price) * 100, 1) if latest_price else 0.0
            rows.append({
                "mandi": mandi_name,
                "latest_price": round(latest_price, 2),
                "trend": trend,
                "forecast_price": round(forecast_last, 2),
                "pct_change": pct_change,
                "forecast_horizon_days": horizon,
            })
            if trend == "rising":
                rising += 1
            elif trend == "falling":
                falling += 1
            else:
                stable += 1

        # Biggest movers first, in either direction — that's what a
        # policymaker scanning the board actually wants to see first.
        rows.sort(key=lambda r: abs(r["pct_change"]), reverse=True)
        if rows:
            results_by_crop[crop] = rows

    if not results_by_crop:
        raise HTTPException(
            status_code=404,
            detail=f"No crop-mandi pairs with enough history to forecast for: {', '.join(crop_names)}.",
        )

    response = {
        "crops": results_by_crop,
        "summary": {"rising": rising, "falling": falling, "stable": stable},
        "unit": "INR per quintal",
        "note": "Grouped by crop, sorted by size of forecast move (biggest movers first). "
        "Same 7-day forecast and honesty caveats as /predict — see its 'confidence' field.",
    }
    _trends_cache[cache_key] = (time.time(), response)
    return response


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


@app.get("/compare")
def compare_mandis(
    crop: str = Query(..., description="Crop name, e.g. Potato"),
    mandis: str = Query(
        ...,
        description="Comma-separated mandi names to compare, e.g. 'Rayya,Rajpura,Ludhiana' (2-5 mandis)",
    ),
):
    """Answer the actual decision a farmer faces: not just 'what's the price
    here', but 'should I sell here or travel to another mandi'. Runs the
    exact same predict() used everywhere else across each named mandi and
    returns a ranked comparison — no new forecasting logic, just reuse.
    """
    mandi_names = [m.strip() for m in mandis.split(",") if m.strip()]
    # Case-insensitive de-dup while preserving first-seen casing/order.
    seen = set()
    deduped = []
    for name in mandi_names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(name)
    mandi_names = deduped

    if len(mandi_names) < 2:
        raise HTTPException(
            status_code=400,
            detail="Provide at least 2 mandi names to compare, e.g. mandis=Rayya,Rajpura.",
        )
    if len(mandi_names) > 5:
        raise HTTPException(
            status_code=400,
            detail="Please compare at most 5 mandis at a time.",
        )

    results = []
    errors = []
    for mandi_name in mandi_names:
        try:
            results.append(_build_prediction(crop=crop, mandi=mandi_name))
        except HTTPException as error:
            errors.append({"mandi": mandi_name, "detail": error.detail})

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No usable data for crop='{crop}' at any of the requested mandis. "
            + "; ".join(f"{e['mandi']}: {e['detail']}" for e in errors),
        )

    # Rank by latest price, highest first — that's the mandi worth selling at
    # today, all else (distance, transport cost) held constant. Ties broken
    # by forecast direction (rising beats stable beats falling) since that
    # favors the mandi likely to still be good in a few days.
    trend_rank = {"rising": 0, "stable": 1, "falling": 2}
    ranked = sorted(
        results,
        key=lambda r: (-r["latest_price"], trend_rank.get(r["trend"], 1)),
    )

    best, worst = ranked[0], ranked[-1]
    spread = round(best["latest_price"] - worst["latest_price"], 2)
    spread_pct = round((spread / worst["latest_price"]) * 100, 1) if worst["latest_price"] else 0.0

    for i, r in enumerate(ranked):
        r["rank"] = i + 1

    # --- Staleness/date-alignment check -----------------------------------
    # Bug: this endpoint used to rank purely on latest_price without checking
    # whether the compared mandis' "latest" records are actually from the
    # same date. Two mandis' data can go stale independently (see README —
    # some mandis' history lags by months), so a naive price comparison can
    # silently pit today's price at one mandi against a months-old price at
    # another and present the gap as if it were current. We now surface the
    # date spread explicitly and add a top-level warning whenever mandis
    # being compared aren't reporting from the same date, plus roll up any
    # per-mandi staleness notes (>90 days old) that /predict already flags.
    dates = [pd.Timestamp(r["latest_date"]) for r in ranked]
    date_spread_days = int((max(dates) - min(dates)).days)
    dates_aligned = date_spread_days == 0
    stale_mandis = [
        {"mandi": r["mandi"], "latest_date": r["latest_date"], "note": r["data_note"]}
        for r in ranked
        if r.get("data_note")
    ]

    summary = (
        f"{crop} is highest at {best['mandi']} (₹{best['latest_price']}/quintal, "
        f"as of {best['latest_date']}) and lowest at {worst['mandi']} "
        f"(₹{worst['latest_price']}/quintal, as of {worst['latest_date']}) — a spread of "
        f"₹{spread} ({spread_pct}%). This does not account for travel cost or time, "
        f"which can easily outweigh a small spread."
    )
    if not dates_aligned:
        summary += (
            f" Note: these mandis' most recent records are {date_spread_days} day(s) apart, "
            "not from the same date — treat the spread above as directional, not a same-day comparison."
        )

    response = {
        "crop": crop,
        "compared_mandis": [r["mandi"] for r in ranked],
        "results": ranked,
        "best_mandi": {
            "mandi": best["mandi"],
            "latest_price": best["latest_price"],
            "trend": best["trend"],
            "latest_date": best["latest_date"],
        },
        "price_spread": spread,
        "price_spread_pct": spread_pct,
        "dates_aligned": dates_aligned,
        "date_spread_days": date_spread_days,
        "summary": summary,
        "unit": "INR per quintal",
    }
    if stale_mandis:
        response["stale_data_warning"] = stale_mandis
    if errors:
        response["skipped"] = errors
    return response


def generate_compare_advisory(
    comparison_data: dict,
    farmer_question: str,
    language_code: str,
) -> tuple[str, bool]:
    """Same pattern as generate_advisory(), but reasoning across several
    mandis for the same crop instead of one — 'is it worth traveling'
    rather than 'what will the price be'. Reuses the same Gemini timeout
    and plain-fallback strategy so it fails the same safe way in a demo."""

    if language_code not in LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported language. Use en for English, hi for Hindi, or pa for Punjabi.",
        )

    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        raise HTTPException(
            status_code=500,
            detail="A Gemini API key is missing. Set GEMINI_API_KEY or GOOGLE_API_KEY in the terminal before starting the server.",
        )

    language = LANGUAGES[language_code]
    client = genai.Client(api_key=gemini_api_key)

    instructions = f"""
You are a mandi-price advisory assistant for farmers in India, comparing prices for
one crop across a few mandis so the farmer can decide whether it's worth selling
locally or traveling to a better-priced mandi.

Reply language: {language["name"]}
Language requirement: {language["script_rule"]}

Strict rules:
1. Use only the comparison data provided below. Never invent prices, distances, or travel times.
2. You do not know travel distance or transport cost between these mandis — say so plainly,
   and frame the price difference as something the farmer should weigh against their own
   travel cost and time, not as a definite "go there" instruction.
3. Give a useful recommendation in only 2 or 3 short sentences.
4. State that the result is an estimate, not a guaranteed price.
5. Do not give medical, legal, emergency, or financial-investment advice.
6. If the question is unrelated to the supplied crop and mandis, politely say that you can
   only answer about this comparison.
"""

    comparison_summary = f"""
Crop: {comparison_data["crop"]}
Mandis compared (ranked highest price first): {comparison_data["compared_mandis"]}
Full ranked results: {comparison_data["results"]}
Price spread: ₹{comparison_data["price_spread"]} ({comparison_data["price_spread_pct"]}%)
"""

    def call_gemini():
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"""
      {instructions}

      Farmer question:

     {farmer_question}

      Trusted comparison data:

      {comparison_summary}

      Write the advisory now.
      """,
        )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call_gemini)
    try:
        response = future.result(timeout=ADVISORY_TIMEOUT_SECONDS)
        executor.shutdown(wait=False)
        return response.text.strip(), False
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False)
        print(f"GEMINI TIMEOUT: no response within {ADVISORY_TIMEOUT_SECONDS}s")
        return comparison_data["summary"], True
    except Exception as error:
        executor.shutdown(wait=False)
        print("GEMINI ERROR:", error)
        return comparison_data["summary"], True


@app.get("/compare-advisory")
def compare_advisory(
    crop: str = Query(..., description="Crop name, e.g. Potato"),
    mandis: str = Query(..., description="Comma-separated mandi names, e.g. 'Rayya,Rajpura'"),
    question: str = Query(..., description="Farmer question in the selected language"),
    language: str = Query("en", description="en = English, hi = Hindi, pa = Punjabi"),
):
    comparison_data = compare_mandis(crop=crop, mandis=mandis)
    # generate_compare_advisory() already falls back to comparison_data["summary"]
    # on a Gemini timeout or API error. It still raises HTTPException for a
    # real config problem (missing API key) — catch that here too, same as
    # /advisory and /voice-advisory, and reuse its own fallback summary text
    # instead of a bare 500. A bad `language` value (400) still propagates.
    try:
        advisory_text, used_fallback = generate_compare_advisory(
            comparison_data=comparison_data,
            farmer_question=question,
            language_code=language,
        )
    except HTTPException as exc:
        if exc.status_code != 500:
            raise
        advisory_text = comparison_data["summary"]
        used_fallback = True
    return {
        "crop": crop,
        "compared_mandis": comparison_data["compared_mandis"],
        "language": LANGUAGES[language]["name"],
        "question": question,
        "best_mandi": comparison_data["best_mandi"],
        "price_spread": comparison_data["price_spread"],
        "price_spread_pct": comparison_data["price_spread_pct"],
        "advisory": advisory_text,
        "advisory_source": "fallback" if used_fallback else "gemini",
        "disclaimer": "Forecasts are estimates and do not guarantee future mandi prices. "
        "Travel cost and time are not factored in.",
    }


@app.get("/advisory")
def advisory(
    crop: str = Query(..., description="Example: Potato"),
    mandi: str = Query(..., description="Example: Rayya"),
    question: str = Query(..., description="Farmer question in the selected language"),
    language: str = Query(
        "en",
        description="en = English, hi = Hindi, pa = Punjabi",
    ),
):
    # First obtain factual data from your existing forecasting model.
    forecast_data = _build_prediction(crop=crop, mandi=mandi)

    # Then ask the LLM only to explain those facts. A missing API key
    # (status 500 config problem) falls back to a plain data reply instead
    # of a bare 500, matching /sms, /whatsapp, and /voice-advisory. A bad
    # `language` value (400) is the caller's mistake and still propagates.
    try:
        advisory_text, used_fallback = generate_advisory(
            forecast_data=forecast_data,
            farmer_question=question,
            language_code=language,
        )
    except HTTPException as exc:
        if exc.status_code != 500:
            raise
        advisory_text = (
            f"{crop} at {mandi}: Rs {forecast_data['latest_price']}/quintal, "
            f"trend {forecast_data['trend']}."
        )
        used_fallback = True

    return {
        "crop": crop,
        "mandi": mandi,
        "language": LANGUAGES[language]["name"],
        "question": question,
        "forecast_trend": forecast_data["trend"],
        "advisory": advisory_text,
        "advisory_source": "fallback" if used_fallback else "gemini",
        "confidence": forecast_data.get("confidence"),
        "disclaimer": "Forecasts are estimates and do not guarantee future mandi prices.",
    }
def _ascii_safe_header_value(value: str, fallback: str = "unknown") -> str:
    """HTTP header values must be Latin-1 encodable. A handful of crop names
    in the dataset embed non-Latin script (e.g. "Pea Pod/Pea Cod/हरी मटर"),
    which would otherwise raise UnicodeEncodeError when set on a response
    header and crash the whole request. Strip anything outside Latin-1
    rather than reject it outright, so the response still succeeds."""
    if not value:
        return fallback
    cleaned = value.encode("latin-1", "ignore").decode("latin-1").strip()
    return cleaned or fallback


@app.post("/voice-advisory")
@limiter.limit("10/minute")
def voice_advisory(
    request: Request,
    question: str = Query(..., description="Farmer's spoken or typed question"),
    language: str = Query(
        "en",
        description="en = English, hi = Hindi, pa = Punjabi",
    ),
):
    # This is the one endpoint where Gemini generation is followed by a
    # second slow, network-bound step (TTS), so the two need to share a
    # single wall-clock budget (VOICE_ADVISORY_BUDGET_SECONDS) rather than
    # each independently getting up to ADVISORY_TIMEOUT_SECONDS — that could
    # otherwise sum to well over the 10s a farmer will wait for a spoken
    # answer. extract_crop_and_mandi() and predict() are both local,
    # in-memory operations (no network calls), so they're not separately
    # budgeted here — they're expected to take milliseconds either way.
    budget_start = time.monotonic()

    extracted = extract_crop_and_mandi(question)

    crop = extracted["crop"]
    mandi = extracted["mandi"]

    if not crop:
        raise HTTPException(
            status_code=400,
            detail="Could not identify the crop from the question.",
        )

    if not mandi:
        raise HTTPException(
            status_code=400,
            detail="Could not identify the mandi from the question.",
        )

    forecast_data = _build_prediction(crop=crop, mandi=mandi)

    # Give Gemini whatever's left of the shared budget, minus a guaranteed
    # reserve for TTS afterward — so however long Gemini actually takes (up
    # to its timeout), TTS is never left with zero time.
    elapsed = time.monotonic() - budget_start
    gemini_timeout = max(
        2.0, VOICE_ADVISORY_BUDGET_SECONDS - MIN_TTS_RESERVE_SECONDS - elapsed
    )
    # generate_advisory() already handles Gemini being slow/unavailable on
    # its own (returning a plain-data fallback with used_fallback=True). It
    # still raises HTTPException for a real config problem (e.g. no API key
    # set at all) — catch that here too, same as build_reply_text() does for
    # /sms and /whatsapp, so a misconfigured server still produces a spoken
    # reply instead of a bare 500 with no audio.
    try:
        advisory_text, used_fallback = generate_advisory(
            forecast_data=forecast_data,
            farmer_question=question,
            language_code=language,
            timeout_seconds=gemini_timeout,
        )
    except HTTPException as exc:
        # Only treat a genuine config problem (missing API key, status 500)
        # as fallback-worthy. A bad `language` value (400) is the caller's
        # mistake, not something to paper over with a fallback reply — let
        # it propagate so the client sees the real error.
        if exc.status_code != 500:
            raise
        advisory_text = (
            f"{crop} at {mandi}: Rs {forecast_data['latest_price']}/quintal, "
            f"trend {forecast_data['trend']}."
        )
        used_fallback = True
    audio = BytesIO()

    tts_language = {
      "en": "en",
      "hi": "hi",
      "pa": "pa",
    }[language]

    # gTTS calls out to Google's endpoint over the network, which has been
    # observed to fail intermittently (especially right after a slow/timed-out
    # Gemini call) even though isolated calls with the same text succeed.
    # Retry once after a short pause before giving up, so a transient network
    # blip doesn't take down the whole advisory response — but only within
    # whatever's left of the shared request budget. gTTS's own `timeout=`
    # bounds each attempt's network call so a hung connection here can't by
    # itself blow past VOICE_ADVISORY_BUDGET_SECONDS the way an unbounded
    # call (the previous behavior) could.
    TTS_MAX_ATTEMPTS = 2
    last_tts_error = None
    for attempt in range(1, TTS_MAX_ATTEMPTS + 1):
        remaining = VOICE_ADVISORY_BUDGET_SECONDS - (time.monotonic() - budget_start)
        if attempt > 1 and remaining < 1.0:
            # Not enough budget left for a meaningful retry — stop rather
            # than spend what little remains on a near-certain repeat
            # failure and blow the 10s ceiling anyway.
            print("TTS RETRY SKIPPED: insufficient remaining budget")
            break
        tts_timeout = max(1.5, remaining)
        try:
            audio = BytesIO()
            tts = gTTS(
                text=advisory_text,
                lang=tts_language,
                timeout=tts_timeout,
            )
            tts.write_to_fp(audio)
            audio.seek(0)
            last_tts_error = None
            break
        except Exception as exc:
            last_tts_error = exc
            print(f"TTS ATTEMPT {attempt}/{TTS_MAX_ATTEMPTS} FAILED: {exc}")
            if attempt < TTS_MAX_ATTEMPTS:
                remaining_after = VOICE_ADVISORY_BUDGET_SECONDS - (time.monotonic() - budget_start)
                # Short, budget-aware pause — never sleeps away time that a
                # retry attempt would actually need.
                time.sleep(min(0.5, max(0.0, remaining_after - 1.0)))

    if last_tts_error is not None:
        raise HTTPException(
            status_code=502,
            detail=f"Text-to-speech failed after {TTS_MAX_ATTEMPTS} attempts: {last_tts_error}",
        )

    return StreamingResponse(
        audio,
        media_type="audio/mpeg",
        headers={
            "X-Crop": _ascii_safe_header_value(crop),
            "X-Mandi": _ascii_safe_header_value(mandi),
            "X-Language": language,
            "X-Advisory-Source": "fallback" if used_fallback else "gemini",
        },
    )


def _latest_price_for(crop: str, mandi: str):
    """Best-effort latest reported price for crop+mandi, or None if that
    pair has no rows. Doesn't require the 30-point minimum load_series()
    enforces for forecasting — a single most-recent price is still useful
    to show on the nearby-mandis map even for a thin series.

    Scans the full dataframe on every call, so it's fine for a single
    lookup but should NOT be called in a loop over many mandis — see
    _latest_prices_by_mandi() below for the batched equivalent used by
    /api/nearby-mandis."""
    df = _load_full_dataframe()
    mask = (
        df["crop"].astype(str).str.casefold().eq(crop.casefold())
        & df["mandi"].astype(str).str.casefold().eq(mandi.casefold())
    )
    rows = df.loc[mask, ["date", "price"]].dropna().sort_values("date")
    if rows.empty:
        return None
    last = rows.iloc[-1]
    return {"date": last["date"].date().isoformat(), "price": round(float(last["price"]), 2)}


def _latest_prices_by_mandi(crop: str) -> dict:
    """Latest reported price for every mandi, for a single crop — computed
    with one pass over the dataframe (filter by crop once, then group by
    mandi) instead of one full-dataframe scan per mandi. Used by
    /api/nearby-mandis, which previously called _latest_price_for() once
    per tracked mandi (~110 full ~50k-row scans per request).

    Keys are casefolded mandi names, matching the casefold comparison
    _latest_price_for() uses, so callers should look up with
    mandi_name.casefold()."""
    df = _load_full_dataframe()
    sub = df[df["crop"].astype(str).str.casefold() == crop.casefold()]
    sub = sub.dropna(subset=["date", "price"])
    if sub.empty:
        return {}
    sub = sub.copy()
    sub["_mandi_key"] = sub["mandi"].astype(str).str.casefold()
    latest = sub.sort_values("date").groupby("_mandi_key").tail(1)
    return {
        row["_mandi_key"]: {
            "date": row["date"].date().isoformat(),
            "price": round(float(row["price"]), 2),
        }
        for _, row in latest.iterrows()
    }


@app.get("/api/nearby-mandis")
def get_nearby_mandis(
    lat: float,
    lon: float,
    limit: int = 10,
    crop: str | None = Query(
        None, description="Optional crop name, e.g. Potato — if given, each mandi includes its latest reported price for this crop.",
    ),
):
    latest_by_mandi = _latest_prices_by_mandi(crop) if crop else {}

    nearby_list = []
    for mandi_name, info in PUNJAB_MANDI_COORDINATES.items():
        dist_km = calculate_haversine_distance(lat, lon, info["lat"], info["lon"])
        entry = {
            "mandi": mandi_name,
            "district": info["district"],
            "latitude": info["lat"],
            "longitude": info["lon"],
            "distance_km": dist_km,
        }
        if crop:
            entry["latest_price"] = latest_by_mandi.get(mandi_name.casefold())
        nearby_list.append(entry)

    nearby_list.sort(key=lambda x: x["distance_km"])

    return {
        "user_location": {"lat": lat, "lon": lon},
        "unit": "INR per quintal" if crop else None,
        "total_mandis": len(nearby_list),
        "mandis": nearby_list[:limit]
    }


# --- Router registration -----------------------------------------------
# Imported here, at the very end of the file, on purpose: routers/voice.py
# and routers/alerts.py both do `from app import <names>` for functions
# they need (_build_prediction, generate_advisory, SUBSCRIPTIONS_PATH,
# etc.). Those names are all defined above this point in the file, so by
# the time these imports execute, app's (partially-initialized) module
# object already has them. Importing these routers any earlier would raise
# an ImportError from failing to find those attributes. routers/voice.py
# also imports alert-command helpers from routers/alerts.py directly.
from routers.voice import router as voice_router  # noqa: E402
app.include_router(voice_router)

from routers.alerts import (  # noqa: E402
    router as alerts_router,
    _maybe_start_internal_alert_scheduler,
)
app.include_router(alerts_router)
# APIRouter has no .on_event of its own, so this startup hook (moved out of
# app.py along with the rest of the alerts code) is registered directly on
# the app instance here instead of via a decorator in routers/alerts.py.
app.on_event("startup")(_maybe_start_internal_alert_scheduler)
