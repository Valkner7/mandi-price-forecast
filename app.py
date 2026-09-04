import os
import time
import json
import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from mandi_coords import PUNJAB_MANDI_COORDINATES, calculate_haversine_distance

from datetime import datetime, timezone
import concurrent.futures
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google import genai
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from voice_extraction import extract_crop_and_mandi
from io import BytesIO
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from gtts import gTTS
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioRestClient
import price_model as pm


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "clean_mandi_prices.csv"
SUBSCRIPTIONS_PATH = BASE_DIR / "subscriptions.json"
STATIC_DASHBOARD_DIR = BASE_DIR / "static" / "dashboard"
FORECAST_MODEL_PATH = BASE_DIR / "models" / "lgbm_price_model.joblib"
FORECAST_MODEL_META_PATH = BASE_DIR / "models" / "lgbm_price_model_meta.json"

app = FastAPI(title="Mandi Price Forecast API", version="1.0.0")

# Mount Static Folder (for Leaflet CSS, JS, and Images)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Root Route to serve dashboard UI
@app.get("/")
async def read_index():
    index_path = BASE_DIR / "static" / "dashboard" / "index.html"
    return FileResponse(str(index_path))

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

# Serves the price-forecast dashboard (plain HTML/CSS/JS, no build step) at
# /dashboard — index.html calls /meta, /predict, /history and /trends on
# this same app. If static/dashboard doesn't exist yet (e.g. a fresh clone
# before it's been added), skip the mount instead of failing to start.
if STATIC_DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(STATIC_DASHBOARD_DIR), html=True), name="dashboard")
# Override with `export GEMINI_MODEL=...` if this model name ever 404s —
# verify the current valid model name in Google AI Studio before your demo.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# If Gemini is slow or overloaded (e.g. 503 UNAVAILABLE under high demand),
# don't leave the farmer staring at a spinner for 30-60s while the SDK
# retries in the background. Give up after this many seconds and fall back
# to a plain, template-built advisory using the same trusted forecast data.
ADVISORY_TIMEOUT_SECONDS = float(os.getenv("ADVISORY_TIMEOUT_SECONDS", "10"))

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

# Honest confidence framing, from the project's own validation notebook:
# on this dataset, the forecasting model beats a simple "no change" baseline
# in only ~28% of tested crop-mandi combinations on a held-out window. Short
# horizon mandi prices behave close to a random walk — this is surfaced
# directly in the product (not just the pitch deck) so the app never
# overstates its own precision.
FORECAST_CONFIDENCE_NOTE = {
    "en": "This is a directional estimate, not a precise prediction — on this "
          "kind of data, forecasts improve on simply expecting no price change "
          "only some of the time.",
    "hi": "यह एक दिशात्मक अनुमान है, सटीक भविष्यवाणी नहीं — इस तरह के डेटा में, "
          "पूर्वानुमान हमेशा कीमत में कोई बदलाव न मानने से बेहतर नहीं होते।",
    "pa": "ਇਹ ਇੱਕ ਦਿਸ਼ਾਤਮਕ ਅਨੁਮਾਨ ਹੈ, ਸਟੀਕ ਭਵਿੱਖਬਾਣੀ ਨਹੀਂ — ਇਸ ਤਰ੍ਹਾਂ ਦੇ ਡਾਟੇ ਵਿੱਚ, "
          "ਅਨੁਮਾਨ ਹਮੇਸ਼ਾ ਕੀਮਤ ਵਿੱਚ ਕੋਈ ਬਦਲਾਅ ਨਾ ਮੰਨਣ ਨਾਲੋਂ ਬਿਹਤਰ ਨਹੀਂ ਹੁੰਦੇ।",
}


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
) -> tuple[str, bool]:
    """Turn trusted forecast data into a short advisory in one chosen language.

    Returns (advisory_text, used_fallback). used_fallback is True when Gemini
    was too slow or unavailable and we fell back to a plain, template-built
    advisory instead of raising and leaving the caller with nothing."""

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
    try:
        response = future.result(timeout=ADVISORY_TIMEOUT_SECONDS)
        executor.shutdown(wait=False)
        return response.text.strip(), False
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False)
        print(f"GEMINI TIMEOUT: no response within {ADVISORY_TIMEOUT_SECONDS}s")
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
    # separately in the notebook.
    series = series.resample("D").last().ffill()
    series.attrs["raw"] = raw_series
    return series


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
    no signal. Served with no-cache so browsers always pick up SW updates."""
    sw_code = """
const CACHE_NAME = "mandi-bol-v1";
const APP_SHELL_URL = "/voice-test";

self.addEventListener("install", (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.add(APP_SHELL_URL).catch(() => {}))
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const { request } = event;
    if (request.method !== "GET") return; // never cache POSTs (voice-advisory, sms)

    const url = new URL(request.url);

    if (url.pathname === "/voice-test") {
        event.respondWith(networkFirstThenCache(request));
        return;
    }

    if (url.pathname === "/predict") {
        event.respondWith(staleWhileRevalidate(request));
        return;
    }
});

async function networkFirstThenCache(request) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        cache.put(request, response.clone());
        return response;
    } catch (err) {
        const cached = await cache.match(request);
        if (cached) return cached;
        throw err;
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    const networkFetch = fetch(request)
        .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
        })
        .catch(() => null);

    const fresh = cached ? null : await networkFetch;
    return cached || fresh || new Response(
        JSON.stringify({ error: "offline_no_cache" }),
        { status: 503, headers: { "Content-Type": "application/json" } }
    );
}
"""
    return Response(
        content=sw_code,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/voice-test", response_class=HTMLResponse)
def voice_test():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mandi Bol — Voice Price Assistant</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Work+Sans:wght@400;500;600;700&family=Noto+Sans+Gurmukhi:wght@500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --paper: #FBF6EA;
            --paper-dim: #F1EADA;
            --ink: #241C12;
            --ink-soft: #5B5140;
            --wheat: #D9A431;
            --wheat-deep: #B8842A;
            --green: #1B4332;
            --green-light: #2D6A4F;
            --rust: #B5432B;
            --line: rgba(36, 28, 18, 0.14);
            --shadow: 0 10px 30px rgba(27, 67, 50, 0.12);
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            background:
                radial-gradient(circle at 12% 8%, rgba(217, 164, 49, 0.16), transparent 45%),
                radial-gradient(circle at 88% 92%, rgba(27, 67, 50, 0.12), transparent 40%),
                var(--paper);
            color: var(--ink);
            font-family: "Work Sans", sans-serif;
            display: flex;
            justify-content: center;
            padding: 48px 20px 64px;
        }
        .page { width: 100%; max-width: 560px; }

        .hero { text-align: center; margin-bottom: 34px; }
        .eyebrow {
            display: inline-block;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--green);
            background: rgba(27, 67, 50, 0.08);
            border: 1px solid rgba(27, 67, 50, 0.18);
            border-radius: 999px;
            padding: 6px 14px;
            margin-bottom: 18px;
        }
        h1 {
            font-family: "Fraunces", serif;
            font-weight: 700;
            font-size: clamp(38px, 8vw, 52px);
            line-height: 1.02;
            margin: 0 0 12px;
            color: var(--green);
        }
        .hero .gurmukhi {
            font-family: "Noto Sans Gurmukhi", sans-serif;
            font-weight: 600;
            font-size: 0.55em;
            color: var(--wheat-deep);
            display: block;
        }
        .tagline {
            margin: 0 auto;
            max-width: 380px;
            color: var(--ink-soft);
            font-size: 15.5px;
            line-height: 1.55;
        }

        .lang-select {
            display: flex;
            justify-content: center;
            gap: 8px;
            margin: 28px 0 32px;
            flex-wrap: wrap;
        }
        .lang-pill input { position: absolute; opacity: 0; width: 0; height: 0; }
        .lang-pill span {
            display: inline-block;
            padding: 9px 20px;
            border-radius: 999px;
            border: 1.5px solid var(--line);
            background: var(--paper);
            font-size: 14.5px;
            font-weight: 600;
            color: var(--ink-soft);
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .lang-pill input:checked + span {
            background: var(--green);
            border-color: var(--green);
            color: var(--paper);
        }
        .lang-pill input:focus-visible + span {
            outline: 2px solid var(--wheat-deep);
            outline-offset: 2px;
        }
        .lang-pill span:hover { border-color: var(--green-light); }

        .mic-section { display: flex; flex-direction: column; align-items: center; }
        .mic-button {
            position: relative;
            width: 108px;
            height: 108px;
            border-radius: 50%;
            border: none;
            background: linear-gradient(160deg, var(--wheat) 0%, var(--wheat-deep) 100%);
            box-shadow: var(--shadow);
            cursor: pointer;
            display: grid;
            place-items: center;
            transition: transform 0.15s ease;
        }
        .mic-button:hover:not(:disabled) { transform: translateY(-2px); }
        .mic-button:active:not(:disabled) { transform: translateY(0) scale(0.97); }
        .mic-button:disabled { opacity: 0.45; cursor: not-allowed; }
        .mic-button:focus-visible { outline: 3px solid var(--green); outline-offset: 4px; }
        .mic-icon { font-size: 38px; filter: drop-shadow(0 2px 2px rgba(0,0,0,0.15)); }
        .mic-rings {
            position: absolute;
            inset: -14px;
            border-radius: 50%;
            border: 2px solid var(--wheat-deep);
            opacity: 0;
        }
        .mic-button.listening {
            background: linear-gradient(160deg, var(--rust) 0%, #8f3320 100%);
        }
        .mic-button.listening .mic-rings {
            opacity: 1;
            animation: pulse-ring 1.6s ease-out infinite;
        }
        @keyframes pulse-ring {
            0% { transform: scale(0.85); opacity: 0.6; }
            100% { transform: scale(1.55); opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
            .mic-button.listening .mic-rings { animation: none; opacity: 0.3; }
            .mic-button, .mic-button:hover { transition: none; }
        }

        .status {
            margin-top: 18px;
            font-size: 14.5px;
            font-weight: 500;
            color: var(--ink-soft);
            min-height: 20px;
            text-align: center;
        }
        .status--error { color: var(--rust); }
        .status--ready { color: var(--green); }

        .ticket {
            position: relative;
            margin: 30px 0 0;
            background: #fff;
            border: 1px solid var(--line);
            border-radius: 4px;
            box-shadow: var(--shadow);
            padding: 22px 22px 18px;
            transform: rotate(-0.6deg);
        }
        .ticket::before {
            content: "";
            position: absolute;
            top: -1px; left: 0; right: 0; height: 12px;
            background-image:
                linear-gradient(135deg, var(--paper) 25%, transparent 25.5%),
                linear-gradient(-135deg, var(--paper) 25%, transparent 25.5%);
            background-position: top left;
            background-size: 16px 16px;
            background-repeat: repeat-x;
        }
        .ticket-label {
            display: block;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--wheat-deep);
            margin-bottom: 6px;
        }
        .ticket-transcript {
            font-family: "Fraunces", serif;
            font-size: 19px;
            font-weight: 500;
            line-height: 1.4;
            margin: 0 0 16px;
            color: var(--ink);
        }
        .ticket-audio {
            width: 100%;
            accent-color: var(--green);
        }

        .fallback {
            margin-top: 40px;
            padding-top: 24px;
            border-top: 1px dashed var(--line);
        }
        .fallback-label {
            text-align: center;
            font-size: 13px;
            color: var(--ink-soft);
            margin: 0 0 12px;
        }
        .fallback-row { display: flex; gap: 8px; }
        #textInput {
            flex: 1;
            min-width: 0;
            padding: 12px 14px;
            border-radius: 8px;
            border: 1.5px solid var(--line);
            background: var(--paper-dim);
            font-family: inherit;
            font-size: 14.5px;
            color: var(--ink);
        }
        #textInput:focus-visible {
            outline: none;
            border-color: var(--green);
            box-shadow: 0 0 0 3px rgba(27, 67, 50, 0.15);
        }
        #textButton {
            padding: 12px 20px;
            border-radius: 8px;
            border: none;
            background: var(--green);
            color: var(--paper);
            font-family: inherit;
            font-weight: 600;
            font-size: 14.5px;
            cursor: pointer;
            transition: background 0.15s ease;
        }
        #textButton:hover { background: var(--green-light); }
        #textButton:focus-visible { outline: 3px solid var(--wheat-deep); outline-offset: 2px; }

        .offline-banner {
            display: none;
            align-items: center;
            gap: 8px;
            justify-content: center;
            background: rgba(181, 67, 43, 0.12);
            border: 1px solid rgba(181, 67, 43, 0.3);
            color: var(--rust);
            font-size: 13.5px;
            font-weight: 600;
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 24px;
        }
        .offline-banner.visible { display: flex; }

        .price-line {
            display: inline-block;
            font-size: 13.5px;
            font-weight: 600;
            color: var(--green);
            background: rgba(27, 67, 50, 0.08);
            border-radius: 999px;
            padding: 4px 12px;
            margin: 0 0 14px;
        }
        .price-line.trend-falling { color: var(--rust); background: rgba(181, 67, 43, 0.1); }

        .recent-section {
            margin-top: 40px;
            padding-top: 24px;
            border-top: 1px dashed var(--line);
        }
        .recent-header {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            margin-bottom: 12px;
        }
        .recent-label {
            font-size: 13px;
            font-weight: 600;
            color: var(--ink-soft);
        }
        .recent-clear {
            border: none;
            background: none;
            color: var(--ink-soft);
            font-size: 12px;
            text-decoration: underline;
            cursor: pointer;
            padding: 0;
        }
        .recent-empty {
            font-size: 13.5px;
            color: var(--ink-soft);
            text-align: center;
            padding: 10px 0;
        }
        .recent-list { display: flex; flex-direction: column; gap: 8px; }
        .recent-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            text-align: left;
            padding: 12px 14px;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: #fff;
            cursor: pointer;
            font-family: inherit;
        }
        .recent-item:hover { border-color: var(--green-light); }
        .recent-item:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }
        .recent-item-name { font-weight: 600; font-size: 14px; color: var(--ink); }
        .recent-item-meta { font-size: 12px; color: var(--ink-soft); margin-top: 2px; }
        .recent-item-price { font-weight: 700; font-size: 14.5px; color: var(--green); white-space: nowrap; }
        .recent-item-price.falling { color: var(--rust); }
    </style>
</head>
<body>
<div class="page">
    <div id="offlineBanner" class="offline-banner">📡 You're offline — showing saved prices below. Asking a new question needs a connection.</div>
    <header class="hero">
        <span class="eyebrow">Live Mandi Rates</span>
        <h1>Mandi Bol<span class="gurmukhi">ਮੰਡੀ ਬੋਲ</span></h1>
        <p class="tagline">Ask about any crop's price, in your own language. Speak or type — get a spoken answer back.</p>
    </header>

    <div class="lang-select" role="radiogroup" aria-label="Choose a language">
        <label class="lang-pill">
            <input type="radio" name="language" value="en" checked>
            <span>English</span>
        </label>
        <label class="lang-pill">
            <input type="radio" name="language" value="hi">
            <span>हिंदी</span>
        </label>
        <label class="lang-pill">
            <input type="radio" name="language" value="pa">
            <span>ਪੰਜਾਬੀ</span>
        </label>
    </div>

    <div class="mic-section">
        <button id="micButton" class="mic-button" aria-label="Tap to speak your question">
            <span class="mic-icon">🎤</span>
            <span class="mic-rings" aria-hidden="true"></span>
        </button>
        <p id="status" class="status">Tap the mic and ask about a crop.</p>
    </div>

    <section id="resultCard" class="ticket" hidden>
        <span class="ticket-label">You asked</span>
        <p id="transcript" class="ticket-transcript"></p>
        <span id="priceInfo" class="price-line" hidden></span>
        <audio id="player" class="ticket-audio" controls></audio>
    </section>

    <div class="fallback">
        <p class="fallback-label">Or type it instead — works without a microphone</p>
        <div class="fallback-row">
            <input id="textInput" type="text" placeholder="e.g. What is the price of potato in Rayya?">
            <button id="textButton">Ask</button>
        </div>
    </div>

    <section class="recent-section">
        <div class="recent-header">
            <span class="recent-label">Recently checked prices (saved on this device)</span>
            <button id="clearRecent" class="recent-clear" hidden>Clear</button>
        </div>
        <div id="recentList" class="recent-list"></div>
        <p id="recentEmpty" class="recent-empty">Prices you check will be saved here so you can see them again without a connection.</p>
    </section>
</div>

<script>
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const button = document.getElementById("micButton");
const status = document.getElementById("status");
const resultCard = document.getElementById("resultCard");
const transcript = document.getElementById("transcript");
const priceInfo = document.getElementById("priceInfo");
const player = document.getElementById("player");
const textInput = document.getElementById("textInput");
const textButton = document.getElementById("textButton");
const offlineBanner = document.getElementById("offlineBanner");
const recentList = document.getElementById("recentList");
const recentEmpty = document.getElementById("recentEmpty");
const clearRecentBtn = document.getElementById("clearRecent");

// --- Step 15: offline price cache (frontend-only) -------------------------
// Service worker caches GET /predict responses; this localStorage list is
// the "index" of what's cached, so the page can render a browsable list of
// prices even with zero network, not just replay whatever URL you already
// know.
const RECENT_KEY = "mandiBolRecentPrices";
const RECENT_LIMIT = 12;

if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("SW registration failed:", err));
    });
}

function loadRecent() {
    try {
        return JSON.parse(localStorage.getItem(RECENT_KEY)) || [];
    } catch {
        return [];
    }
}

function saveRecentEntry(entry) {
    const list = loadRecent().filter(
        (item) => !(item.crop === entry.crop && item.mandi === entry.mandi)
    );
    list.unshift(entry);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_LIMIT)));
    renderRecent();
}

function renderRecent() {
    const list = loadRecent();
    recentList.innerHTML = "";
    recentEmpty.hidden = list.length > 0;
    clearRecentBtn.hidden = list.length === 0;

    for (const item of list) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "recent-item";
        const savedWhen = new Date(item.savedAt).toLocaleDateString();
        row.innerHTML = `
            <span>
                <span class="recent-item-name">${item.crop} · ${item.mandi}</span>
                <span class="recent-item-meta">as of ${item.latest_date} · saved ${savedWhen}</span>
            </span>
            <span class="recent-item-price ${item.trend === "falling" ? "falling" : ""}">
                ₹${item.latest_price}${item.trend === "rising" ? " ↑" : item.trend === "falling" ? " ↓" : ""}
            </span>`;
        row.onclick = () => showCachedEntry(item);
        recentList.appendChild(row);
    }
}

function showCachedEntry(item) {
    resultCard.hidden = false;
    transcript.textContent = `${item.crop} in ${item.mandi}`;
    player.removeAttribute("src");
    showPriceInfo(item);
    setStatus(`Showing saved price from ${item.latest_date} (offline copy).`, "ready");
}

function showPriceInfo(data) {
    priceInfo.hidden = false;
    priceInfo.className = "price-line" + (data.trend === "falling" ? " trend-falling" : "");
    const arrow = data.trend === "rising" ? "↑" : data.trend === "falling" ? "↓" : "→";
    priceInfo.textContent = `₹${data.latest_price}/quintal as of ${data.latest_date} ${arrow} ${data.trend}`;
}

function updateOnlineStatus() {
    offlineBanner.classList.toggle("visible", !navigator.onLine);
}
window.addEventListener("online", updateOnlineStatus);
window.addEventListener("offline", updateOnlineStatus);

clearRecentBtn.onclick = () => {
    localStorage.removeItem(RECENT_KEY);
    renderRecent();
};

function currentLanguage() {
    return document.querySelector('input[name="language"]:checked').value;
}

function setStatus(text, variant) {
    status.textContent = text;
    status.className = "status" + (variant ? " status--" + variant : "");
}

// Best-effort: after a successful voice-advisory call we know the extracted
// crop/mandi (X-Crop / X-Mandi headers), so fetch the plain /predict JSON
// too. It's the same cheap, pure lookup the backend already made, and
// caching it here (service worker + localStorage) is what makes the price
// visible again later with no signal. Never blocks or fails the main answer.
async function cachePriceForOffline(crop, mandi) {
    try {
        const response = await fetch(`/predict?${new URLSearchParams({ crop, mandi })}`);
        if (!response.ok) return;
        const data = await response.json();
        showPriceInfo(data);
        saveRecentEntry({ ...data, savedAt: Date.now() });
    } catch (err) {
        console.warn("Could not cache price for offline use:", err);
    }
}

async function askQuestion(question) {
    resultCard.hidden = false;
    transcript.textContent = question;
    priceInfo.hidden = true;
    player.removeAttribute("src");

    if (!navigator.onLine) {
        setStatus("You're offline — check the saved prices below instead.", "error");
        return;
    }

    setStatus("Getting advice…");
    textButton.disabled = true;
    button.disabled = true;

    try {
        const params = new URLSearchParams({
            question: question,
            language: currentLanguage()
        });

        const response = await fetch(`/voice-advisory?${params.toString()}`, { method: "POST" });

        if (!response.ok) {
            const error = await response.text();
            throw new Error(error);
        }

        const crop = response.headers.get("X-Crop");
        const mandi = response.headers.get("X-Mandi");

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        player.src = url;
        await player.play();
        setStatus("Answer ready.", "ready");

        if (crop && mandi) cachePriceForOffline(crop, mandi);
    } catch (error) {
        console.error(error);
        setStatus("Error: " + error.message + " — try the saved prices below.", "error");
    } finally {
        textButton.disabled = false;
        button.disabled = !SpeechRecognition;
    }
}

updateOnlineStatus();
renderRecent();

textButton.onclick = () => {
    const question = textInput.value.trim();
    if (question) askQuestion(question);
};
textInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") textButton.click();
});

if (!SpeechRecognition) {
    button.disabled = true;
    setStatus("Speech recognition isn't supported here — type your question below instead.");
} else {
    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;

    button.onclick = () => {
        const lang = currentLanguage();
        recognition.lang = lang === "hi" ? "hi-IN" : lang === "pa" ? "pa-IN" : "en-IN";
        button.classList.add("listening");
        setStatus("Listening…");
        recognition.start();
    };

    recognition.onresult = (event) => {
        askQuestion(event.results[0][0].transcript);
    };

    recognition.onerror = (event) => {
        button.classList.remove("listening");
        setStatus("Speech error: " + event.error, "error");
    };

    recognition.onend = () => {
        button.classList.remove("listening");
        if (status.textContent === "Listening…") setStatus("Listening stopped.");
    };
}
</script>
</body>
</html>
""")

@app.get("/predict")
def predict(
    crop: str = Query(..., description="Crop name, e.g. Potato"),
    mandi: str = Query(..., description="Mandi name, e.g. Rayya"),
):
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
            forecast_values = pm.forecast_recursive(
                lgbm_model, lgbm_meta, series, crop, mandi, horizon=horizon
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
            "validated_on": "39 crop-mandi combinations, held-out test window (see mandi_price_forecasting notebook)",
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
def meta():
    """Crop and mandi names present in the dataset, so a frontend can
    populate its dropdowns from real data instead of a hardcoded list.
    Doesn't filter by data sufficiency — /predict is still the source of
    truth for whether a given crop-mandi pair has enough history to
    forecast (see its 422 response)."""
    df = _load_full_dataframe()
    crops = sorted(df["crop"].dropna().astype(str).str.strip().unique().tolist())
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
    alphabetically."""
    crop_names = [c.strip() for c in crops.split(",") if c.strip()]
    if not crop_names:
        raise HTTPException(status_code=400, detail="Provide at least one crop name.")

    results_by_crop = {}
    rising = falling = stable = 0

    for crop in crop_names:
        mandi_names = _viable_mandis_for_crop(crop)
        rows = []
        for mandi_name in mandi_names:
            try:
                forecast_data = predict(crop=crop, mandi=mandi_name)
            except HTTPException:
                continue  # shouldn't happen given the min-points filter, but don't let one bad pair fail the whole dashboard
            latest = forecast_data["latest_price"]
            forecast_last = forecast_data["forecast"][-1]["price"]
            pct_change = round(((forecast_last - latest) / latest) * 100, 1) if latest else 0.0
            rows.append({
                "mandi": mandi_name,
                "latest_price": latest,
                "trend": forecast_data["trend"],
                "forecast_price": forecast_last,
                "pct_change": pct_change,
                "forecast_horizon_days": forecast_data["forecast_horizon_days"],
            })
            if forecast_data["trend"] == "rising":
                rising += 1
            elif forecast_data["trend"] == "falling":
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

    return {
        "crops": results_by_crop,
        "summary": {"rising": rising, "falling": falling, "stable": stable},
        "unit": "INR per quintal",
        "note": "Grouped by crop, sorted by size of forecast move (biggest movers first). "
        "Same 7-day ETS forecast and honesty caveats as /predict — see its 'confidence' field.",
    }


@app.get("/trends-dashboard", response_class=HTMLResponse)
def trends_dashboard():
    """Rate-board style page for mandi boards/policymakers — a market-wide
    view, distinct from /voice-test's one-farmer-one-question voice UI.
    Fetches /trends client-side and renders it; no server-side templating
    needed for a page this simple."""
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mandi Rate Board — Punjab Crop Price Trends</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --board: #1B2420;
    --panel: #232E28;
    --chalk: #F1EDE2;
    --chalk-dim: #B9C2B8;
    --slate: #6B7A72;
    --turmeric: #E0A227;
    --rust: #C1502E;
    --rule: rgba(241, 237, 226, 0.14);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--board);
    background-image:
      radial-gradient(ellipse at top left, rgba(241,237,226,0.05), transparent 55%),
      radial-gradient(ellipse at bottom right, rgba(224,162,39,0.04), transparent 55%);
    color: var(--chalk);
    font-family: 'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif;
    min-height: 100vh;
    padding: 32px 20px 64px;
  }
  .wrap { max-width: 960px; margin: 0 auto; }
  header { border-bottom: 2px dashed var(--rule); padding-bottom: 20px; margin-bottom: 24px; }
  .eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--turmeric);
    margin: 0 0 8px;
  }
  h1 {
    font-family: 'Oswald', 'Arial Narrow', sans-serif;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-size: clamp(28px, 5vw, 44px);
    margin: 0 0 6px;
  }
  .sub { color: var(--chalk-dim); font-size: 15px; margin: 0 0 20px; max-width: 60ch; }
  .summary-strip {
    display: flex;
    gap: 28px;
    flex-wrap: wrap;
    font-family: 'IBM Plex Mono', monospace;
  }
  .stat { display: flex; align-items: baseline; gap: 8px; }
  .stat .n { font-size: 28px; font-weight: 700; }
  .stat .label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--chalk-dim); }
  .stat.up .n { color: var(--turmeric); }
  .stat.down .n { color: var(--rust); }
  .stat.flat .n { color: var(--slate); }

  .crop-panel { margin-bottom: 28px; }
  .crop-title {
    font-family: 'Oswald', 'Arial Narrow', sans-serif;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 15px;
    color: var(--chalk-dim);
    margin: 0 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--rule);
  }
  table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 6px; overflow: hidden; }
  th, td {
    text-align: left;
    padding: 10px 14px;
    font-size: 14px;
    border-bottom: 1px solid var(--rule);
  }
  th {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--chalk-dim);
    font-weight: 500;
  }
  tr:last-child td { border-bottom: none; }
  td.num, th.num { font-family: 'IBM Plex Mono', monospace; text-align: right; }
  .arrow { display: inline-block; width: 1.4em; text-align: center; }
  .rising { color: var(--turmeric); }
  .falling { color: var(--rust); }
  .stable-cell { color: var(--slate); }
  .mandi-name { font-weight: 600; }

  .foot-note {
    margin-top: 32px;
    font-size: 12.5px;
    color: var(--slate);
    border-top: 1px dashed var(--rule);
    padding-top: 16px;
    max-width: 65ch;
  }
  .state-msg { color: var(--chalk-dim); font-family: 'IBM Plex Mono', monospace; font-size: 14px; padding: 24px 0; }
  a { color: var(--turmeric); }

  @media (max-width: 640px) {
    th:nth-child(4), td:nth-child(4) { display: none; } /* hide "in 7d" col on very small screens */
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">Punjab &middot; Live from clean_mandi_prices.csv</p>
    <h1>Mandi Rate Board</h1>
    <p class="sub">Which crops are moving, and where — a market-wide view for mandi boards and policymakers, built from the same 7-day forecasts as the farmer advisory.</p>
    <div class="summary-strip" id="summary-strip">
      <div class="stat"><span class="n">—</span><span class="label">loading</span></div>
    </div>
  </header>

  <div id="content">
    <p class="state-msg">Reading today's rates&hellip;</p>
  </div>

  <p class="foot-note">
    Forecasts are directional estimates, not guaranteed prices — see <code>/predict</code>'s confidence note for the model's own honestly-reported accuracy.
    Only crops with enough price history to forecast reliably are shown here (currently Potato, Onion, Tomato).
  </p>
</div>

<script>
async function loadTrends() {
  const contentEl = document.getElementById('content');
  const summaryEl = document.getElementById('summary-strip');
  try {
    const res = await fetch('/trends');
    if (!res.ok) throw new Error('Request failed: ' + res.status);
    const data = await res.json();

    summaryEl.innerHTML = `
      <div class="stat up"><span class="n">${data.summary.rising}</span><span class="label">Rising</span></div>
      <div class="stat down"><span class="n">${data.summary.falling}</span><span class="label">Falling</span></div>
      <div class="stat flat"><span class="n">${data.summary.stable}</span><span class="label">Stable</span></div>
    `;

    const crops = Object.keys(data.crops);
    if (crops.length === 0) {
      contentEl.innerHTML = '<p class="state-msg">No crop-mandi pairs with enough history right now.</p>';
      return;
    }

    contentEl.innerHTML = crops.map(crop => {
      const rows = data.crops[crop];
      const rowsHtml = rows.map(r => {
        const arrow = r.trend === 'rising' ? '&#9650;' : (r.trend === 'falling' ? '&#9660;' : '&mdash;');
        const trendClass = r.trend === 'rising' ? 'rising' : (r.trend === 'falling' ? 'falling' : 'stable-cell');
        const pctSign = r.pct_change > 0 ? '+' : '';
        return `
          <tr>
            <td class="mandi-name">${r.mandi}</td>
            <td class="num">&#8377;${r.latest_price}</td>
            <td class="num ${trendClass}"><span class="arrow">${arrow}</span> ${pctSign}${r.pct_change}%</td>
            <td class="num">&#8377;${r.forecast_price} in ${r.forecast_horizon_days}d</td>
          </tr>`;
      }).join('');

      return `
        <section class="crop-panel">
          <h2 class="crop-title">${crop}</h2>
          <table>
            <thead>
              <tr>
                <th>Mandi</th>
                <th class="num">Latest</th>
                <th class="num">7-day move</th>
                <th class="num">Forecast</th>
              </tr>
            </thead>
            <tbody>${rowsHtml}</tbody>
          </table>
        </section>`;
    }).join('');
  } catch (err) {
    contentEl.innerHTML = '<p class="state-msg">Could not load rates right now. Try refreshing.</p>';
    console.error(err);
  }
}
loadTrends();
</script>
</body>
</html>
""")


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
            results.append(predict(crop=crop, mandi=mandi_name))
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

    prices = [r["latest_price"] for r in ranked]
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
    advisory_text, used_fallback = generate_compare_advisory(
        comparison_data=comparison_data,
        farmer_question=question,
        language_code=language,
    )
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
    forecast_data = predict(crop=crop, mandi=mandi)

    # Then ask the LLM only to explain those facts.
    advisory_text, used_fallback = generate_advisory(
        forecast_data=forecast_data,
        farmer_question=question,
        language_code=language,
    )

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
def voice_advisory(
    question: str = Query(..., description="Farmer's spoken or typed question"),
    language: str = Query(
        "en",
        description="en = English, hi = Hindi, pa = Punjabi",
    ),
):
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

    forecast_data = predict(crop=crop, mandi=mandi)

    advisory_text, used_fallback = generate_advisory(
        forecast_data=forecast_data,
        farmer_question=question,
        language_code=language,
    )
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
    # blip doesn't take down the whole advisory response.
    TTS_MAX_ATTEMPTS = 2
    TTS_RETRY_DELAY_SECONDS = 1.5
    last_tts_error = None
    for attempt in range(1, TTS_MAX_ATTEMPTS + 1):
        try:
            audio = BytesIO()
            tts = gTTS(
                text=advisory_text,
                lang=tts_language,
            )
            tts.write_to_fp(audio)
            audio.seek(0)
            last_tts_error = None
            break
        except Exception as exc:
            last_tts_error = exc
            print(f"TTS ATTEMPT {attempt}/{TTS_MAX_ATTEMPTS} FAILED: {exc}")
            if attempt < TTS_MAX_ATTEMPTS:
                time.sleep(TTS_RETRY_DELAY_SECONDS)

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


# =============================================================================
# Price-alert subscriptions (Tier 1 #4)
#
# A farmer messages "alert me potato rayya 850" over WhatsApp; we store a
# subscription and, on each periodic check, compare it against the same
# predict() every other feature uses. When triggered, we send a real
# outbound WhatsApp message (not a reply — Twilio's REST API, used because
# there's no inbound message to reply to at check time).
#
# Storage is a single JSON file for demo simplicity — swap for a real DB
# post-hackathon, but this is enough for a live demo and doesn't add a new
# dependency.
# =============================================================================

def load_subscriptions() -> list[dict]:
    if not SUBSCRIPTIONS_PATH.exists():
        return []
    try:
        with open(SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[ALERTS] Failed to read subscriptions file, treating as empty: {exc}")
        return []


def save_subscriptions(subs: list[dict]) -> None:
    with open(SUBSCRIPTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2, ensure_ascii=False)


# Two tiers, not one flat list. Phrases like "alert me" / "notify me" are
# unambiguous alert-setting actions — trigger on these alone (so a farmer
# who forgets the price still gets routed to "what price should I watch?").
# Phrases like "tell me when" / "let me know when" are much more likely to
# appear in an ordinary informational question too (e.g. "tell me when
# potato prices usually rise in Rayya") — only treat these as an alert
# command if a numeric price threshold is also present in the message,
# since a genuine alert-setting message almost always includes one.
_ALERT_ACTION_PHRASES = ["alert me", "set alert", "set an alert", "create alert", "create an alert", "notify me", "remind me", "warn me"]
_ALERT_AMBIGUOUS_PHRASES = ["tell me when", "let me know when"]
_ALERT_STOP_PHRASES = ["stop alert", "cancel alert", "stop alerts", "cancel alerts"]
_ALERT_LIST_PHRASES = ["my alerts", "list alerts", "show alerts"]


def _looks_like_alert_command(lowered_text: str) -> bool:
    if any(phrase in lowered_text for phrase in _ALERT_ACTION_PHRASES):
        return True
    if any(phrase in lowered_text for phrase in _ALERT_AMBIGUOUS_PHRASES):
        # Require an actual number too, so "tell me when potato prices
        # usually rise" (an ordinary question, no threshold) doesn't get
        # misrouted into alert-creation.
        return _parse_price_threshold(lowered_text) is not None
    return False


def _parse_price_threshold(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return float(match.group(1)) if match else None


def _parse_alert_direction(lowered_text: str) -> str:
    """'above'/'below' if the farmer said so explicitly; otherwise 'cross',
    meaning "notify me the first time the price crosses this number in
    either direction from where it is now" — matches how people naturally
    phrase it ("tell me when it crosses 850") without requiring them to
    specify a direction."""
    if any(w in lowered_text for w in ["above", "over", "exceeds", "more than", "greater than"]):
        return "above"
    if any(w in lowered_text for w in ["below", "under", "less than", "falls to", "drops to"]):
        return "below"
    return "cross"


def _alert_direction_phrase_en(direction: str) -> str:
    return {"above": "goes above", "below": "goes below", "cross": "crosses"}.get(direction, "crosses")


def _alert_is_triggered(sub: dict, current_price: float) -> bool:
    target = sub["target_price"]
    direction = sub.get("direction", "cross")
    if direction == "above":
        return current_price >= target
    if direction == "below":
        return current_price <= target
    # "cross": relative to the price at the moment the alert was created.
    starting = sub.get("starting_price", target)
    if starting < target:
        return current_price >= target
    if starting > target:
        return current_price <= target
    return True  # started exactly at target — already "crossed"


def send_whatsapp_message(to: str, body: str) -> bool:
    """Send a proactive (not reply) WhatsApp message. Returns False (and
    just logs) if Twilio credentials aren't configured, so alert creation
    still works locally without outbound send — useful while developing,
    but obviously outbound sends need real credentials to actually notify
    anyone."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        print(f"[ALERTS] Twilio outbound credentials not set; would have sent to {to}: {body}")
        return False
    try:
        client = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to, body=body[:1000])
        return True
    except Exception as exc:
        print(f"[ALERTS] Failed to send WhatsApp message to {to}: {exc}")
        return False


def create_alert_from_message(body: str, sender: str) -> str:
    """Handles an inbound message that looks like an alert-setting command.
    Returns the reply text. Alerts require a WhatsApp sender (a real phone
    number we can message back later) — SMS numbers work for one-shot
    price queries but not for this, so we say so plainly rather than
    silently failing later."""
    if not sender or not sender.startswith("whatsapp:"):
        return "Price alerts currently work over WhatsApp only. Message this number on WhatsApp to set one, e.g. 'alert me potato rayya 850'."

    extracted = extract_crop_and_mandi(body)
    crop, mandi = extracted["crop"], extracted["mandi"]
    if not crop or not mandi:
        missing = ", ".join(name for name, val in [("crop", crop), ("mandi", mandi)] if not val)
        return f"Could not identify the {missing} for your alert. Try e.g. 'alert me potato rayya 850'."

    target_price = _parse_price_threshold(body)
    if target_price is None:
        return f"What price should I watch for {crop} at {mandi}? Try e.g. 'alert me {crop.lower()} {mandi.lower()} 850'."

    try:
        forecast_data = predict(crop=crop, mandi=mandi)
    except HTTPException as error:
        return str(error.detail)

    direction = _parse_alert_direction(body.lower())
    new_sub = {
        "id": str(uuid.uuid4()),
        "phone": sender,
        "crop": crop,
        "mandi": mandi,
        "target_price": target_price,
        "direction": direction,
        "starting_price": forecast_data["latest_price"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    with _subscriptions_lock:
        subs = load_subscriptions()
        subs.append(new_sub)
        save_subscriptions(subs)

    return (
        f"Alert set: I'll message you here when {crop} at {mandi} "
        f"{_alert_direction_phrase_en(direction)} ₹{target_price}/quintal. "
        f"Current price: ₹{forecast_data['latest_price']}/quintal. "
        f"Reply 'my alerts' to see active alerts, or 'stop alerts' to cancel all of them."
    )


def list_alerts_for(sender: str) -> str:
    subs = load_subscriptions()
    mine = [s for s in subs if s.get("phone") == sender and s.get("active")]
    if not mine:
        return "You have no active price alerts. Try 'alert me potato rayya 850'."
    lines = [
        f"- {s['crop']} at {s['mandi']}: {_alert_direction_phrase_en(s.get('direction', 'cross'))} ₹{s['target_price']}"
        for s in mine
    ]
    return "Your active alerts:\n" + "\n".join(lines)


def stop_alerts_for(sender: str) -> str:
    with _subscriptions_lock:
        subs = load_subscriptions()
        count = 0
        for s in subs:
            if s.get("phone") == sender and s.get("active"):
                s["active"] = False
                count += 1
        save_subscriptions(subs)
    if count == 0:
        return "You had no active alerts to cancel."
    return f"Cancelled {count} active alert{'s' if count != 1 else ''}."


def check_all_alerts() -> dict:
    """Called by /check-alerts (external cron) or the optional internal
    scheduler. Groups active subscriptions by crop+mandi so each pair is
    only predicted once no matter how many farmers are watching it.

    Concurrency note: the (potentially slow — one predict() per distinct
    crop+mandi group) checking work below happens outside the lock, using
    a snapshot of subscriptions taken at the start. To avoid losing any
    alert a farmer creates or cancels via WhatsApp while that snapshot is
    stale, this function does NOT save that snapshot back wholesale.
    Instead it tracks only the specific alerts that actually fired (by id),
    then re-loads subscriptions.json fresh immediately before saving and
    applies just those updates on top of the current file — so concurrent
    changes made elsewhere during the check are preserved rather than
    silently overwritten (a merge-on-save, not last-write-wins). This is a
    single-process guard (the lock doesn't span multiple worker processes),
    which is fine for a single-process hackathon deployment."""
    with _subscriptions_lock:
        subs = load_subscriptions()

    active = [s for s in subs if s.get("active")]
    if not active:
        return {"checked": 0, "notified": 0}

    groups: dict[tuple[str, str], list[dict]] = {}
    for s in active:
        groups.setdefault((s["crop"], s["mandi"]), []).append(s)

    # Collect only the updates for alerts that actually fired, keyed by id,
    # rather than mutating and later saving the whole (possibly-stale)
    # `subs` snapshot. Applied on top of a fresh reload just before saving.
    fired_updates: dict[str, dict] = {}
    notified = 0
    for (crop, mandi), group in groups.items():
        try:
            forecast_data = predict(crop=crop, mandi=mandi)
        except HTTPException as exc:
            print(f"[ALERTS] Skipping {crop}/{mandi}: {exc.detail}")
            continue

        current_price = forecast_data["latest_price"]
        for sub in group:
            if not _alert_is_triggered(sub, current_price):
                continue
            message = (
                f"Price alert: {sub['crop']} at {sub['mandi']} is now "
                f"₹{current_price}/quintal (your target was ₹{sub['target_price']}). "
                f"{FORECAST_CONFIDENCE_NOTE['en']}"
            )
            if send_whatsapp_message(sub["phone"], message):
                fired_updates[sub["id"]] = {
                    "active": False,
                    "notified_at": datetime.now(timezone.utc).isoformat(),
                    "notified_price": current_price,
                }
                notified += 1

    with _subscriptions_lock:
        # Reload fresh rather than reusing the stale `subs` snapshot, so any
        # alert created/cancelled by a farmer during the loop above isn't
        # lost. Only the specific alerts that fired get updated, by id.
        current_subs = load_subscriptions()
        for sub in current_subs:
            update = fired_updates.get(sub.get("id"))
            if update:
                sub.update(update)
        save_subscriptions(current_subs)

    return {"checked": len(active), "notified": notified}


@app.get("/check-alerts")
@app.post("/check-alerts")
def check_alerts_endpoint(secret: str = Query(None, description="Must match ALERTS_CRON_SECRET if that env var is set")):
    """Point an external free scheduler (cron-job.org, UptimeRobot, a
    scheduled GitHub Action, etc.) at this endpoint every 5-10 minutes.
    This is the reliable path on Render's free tier: the request itself
    wakes a sleeping service, so this both checks alerts AND keeps the
    demo responsive. Protect it with ALERTS_CRON_SECRET once deployed —
    it sends real outbound messages and shouldn't be publicly triggerable.
    """
    if ALERTS_CRON_SECRET and secret != ALERTS_CRON_SECRET:
        raise HTTPException(status_code=403, detail="Missing or incorrect secret.")
    result = check_all_alerts()
    return {"status": "ok", **result}


@app.on_event("startup")
def _maybe_start_internal_alert_scheduler():
    """Optional convenience for LOCAL rehearsal only. On Render's free
    tier this thread is asleep whenever the service is asleep and will
    NOT fire on schedule — use the external-cron /check-alerts path above
    for the actual deployed demo instead."""
    if not ENABLE_INTERNAL_ALERT_SCHEDULER:
        return

    def _loop():
        while True:
            try:
                result = check_all_alerts()
                print(f"[ALERTS] internal scheduler check: {result}")
            except Exception as exc:
                print(f"[ALERTS] internal scheduler error: {exc}")
            time.sleep(ALERT_CHECK_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()


def detect_sms_language(text: str) -> str:
    """Guess en/hi/pa from script, since SMS has no language picker.
    Devanagari (Hindi) and Gurmukhi (Punjabi) occupy distinct Unicode
    blocks, so a single character is enough to decide."""
    for ch in text:
        code = ord(ch)
        if 0x0900 <= code <= 0x097F:
            return "hi"
        if 0x0A00 <= code <= 0x0A7F:
            return "pa"
    return "en"


def build_reply_text(body: str, sender: str | None = None) -> str:
    """Shared core for every text-in/text-out channel (SMS, WhatsApp, and
    any future one): extract -> predict -> advise -> plain reply string.

    Channel-specific endpoints (below) only handle their own transport
    envelope (Twilio form fields in, TwiML out) and call this. Keeping the
    actual logic in one place means WhatsApp can't drift from SMS behavior,
    and a future channel (e.g. Telegram) is just another thin wrapper.

    `sender` (Twilio's "From", e.g. "whatsapp:+91...") is only used for
    price-alert commands, which need a phone number to notify later.
    """
    body = (body or "").strip()

    if not body:
        return "Send a crop and mandi name, e.g. 'Potato Rayya' — English, Hindi, or Punjabi all work."

    lowered = body.lower()

    # Alert-related commands are checked before the normal price-query flow
    # since they can contain a crop+mandi too (e.g. "alert me potato rayya 850").
    if any(phrase in lowered for phrase in _ALERT_STOP_PHRASES):
        return stop_alerts_for(sender) if sender else "Price alerts work over WhatsApp only."
    if any(phrase in lowered for phrase in _ALERT_LIST_PHRASES):
        return list_alerts_for(sender) if sender else "Price alerts work over WhatsApp only."
    if _looks_like_alert_command(lowered):
        return create_alert_from_message(body, sender)

    language = detect_sms_language(body)

    extracted = extract_crop_and_mandi(body)
    crop, mandi = extracted["crop"], extracted["mandi"]

    if not crop or not mandi:
        missing = ", ".join(name for name, val in [("crop", crop), ("mandi", mandi)] if not val)
        return f"Could not identify the {missing} from your message. Try e.g. 'Potato Rayya'."

    try:
        forecast_data = predict(crop=crop, mandi=mandi)
    except HTTPException as error:
        return str(error.detail)

    # generate_advisory() now handles Gemini being slow/unavailable itself
    # (see ADVISORY_TIMEOUT_SECONDS) and returns a plain-data fallback
    # instead of raising. It still raises HTTPException for a real config
    # problem (e.g. no API key set at all) — catch that separately so a
    # misconfigured server doesn't leave the farmer with no reply at all.
    try:
        advisory_text, _used_fallback = generate_advisory(
            forecast_data=forecast_data,
            farmer_question=body,
            language_code=language,
        )
    except HTTPException:
        advisory_text = (
            f"{crop} at {mandi}: Rs {forecast_data['latest_price']}/quintal, "
            f"trend {forecast_data['trend']}."
        )

    return advisory_text


def _twiml_response(message: str) -> Response:
    reply = MessagingResponse()
    reply.message(message[:600])  # defensive cap; Twilio splits long replies anyway
    return Response(content=str(reply), media_type="application/xml; charset=utf-8")


@app.post("/sms")
async def sms_webhook(request: Request):
    """Twilio SMS webhook: a farmer texts a crop + mandi (any of the three
    languages), gets a price + short advisory back — no app, no internet
    on their end required. Point your Twilio number's messaging webhook at
    POST https://<your-host>/sms.

    Note: SMS delivery to Indian numbers requires DLT registration and may
    not actually be deliverable end-to-end. /whatsapp below has no such
    requirement and is the recommended channel for a live demo.
    """
    form = await request.form()
    body = (form.get("Body") or "").strip()
    sender = (form.get("From") or "").strip()
    return _twiml_response(build_reply_text(body, sender=sender))


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """Twilio WhatsApp webhook — identical behavior to /sms, just a
    different transport. WhatsApp Sandbox has no DLT requirement, works
    within minutes of joining the sandbox, and is testable live from your
    own phone during a demo.

    Setup:
    1. In the Twilio Console, open Messaging -> Try it out -> Send a WhatsApp message,
       and join your sandbox (send the shown join code to the shown number from WhatsApp).
    2. Set the sandbox's "When a message comes in" webhook to
       POST https://<your-public-host>/whatsapp
    3. Message the sandbox number from your phone with e.g. "Potato Rayya".

    Twilio sends the same form-encoded fields for WhatsApp as SMS (Body,
    From, To — From/To are just prefixed with "whatsapp:"), so no payload
    parsing changes are needed here. `From` (e.g. "whatsapp:+91...") is
    passed through as `sender` so price-alert commands know who to notify
    later.
    """
    form = await request.form()
    body = (form.get("Body") or "").strip()
    sender = (form.get("From") or "").strip()
    return _twiml_response(build_reply_text(body, sender=sender))


def _latest_price_for(crop: str, mandi: str):
    """Best-effort latest reported price for crop+mandi, or None if that
    pair has no rows. Doesn't require the 30-point minimum load_series()
    enforces for forecasting — a single most-recent price is still useful
    to show on the nearby-mandis map even for a thin series."""
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


@app.get("/api/nearby-mandis")
async def get_nearby_mandis(
    lat: float,
    lon: float,
    limit: int = 10,
    crop: str | None = Query(
        None, description="Optional crop name, e.g. Potato — if given, each mandi includes its latest reported price for this crop.",
    ),
):
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
            entry["latest_price"] = _latest_price_for(crop, mandi_name)
        nearby_list.append(entry)

    nearby_list.sort(key=lambda x: x["distance_km"])

    return {
        "user_location": {"lat": lat, "lon": lon},
        "unit": "INR per quintal" if crop else None,
        "total_mandis": len(nearby_list),
        "mandis": nearby_list[:limit]
    }

