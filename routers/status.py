"""
GET /status -- is the app actually serving the model it claims to?

Tier 4 observability (see observability.py for the why). Returns HTTP 200
with a JSON body either way, so a platform health check never restarts the
service over a degraded-but-working state. To let an external monitor
alert instead, call /status?strict=true: that returns HTTP 503 whenever
status is "degraded".

Exposes no secrets: package versions, counters, dates, and truncated error
text only.
"""

import platform
import time
import warnings
from datetime import datetime, timezone
from importlib import metadata

import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

import observability
import usage_guard
from routers.predict import _load_forecast_model, _load_full_dataframe

router = APIRouter()

# "Degraded" thresholds. Deliberately simple and explained in the payload's
# `reasons`, so nobody has to guess why /status turned yellow.
MIN_PREDICTIONS_FOR_SHARE_CHECK = 10   # don't judge the LightGBM share on a handful of requests
MIN_LIGHTGBM_SHARE = 0.5               # below this, ETS is serving most forecasts
MAX_DATA_AGE_DAYS = 7                  # the price CSV is refreshed by a daily job

_PACKAGES = ("lightgbm", "scikit-learn", "numpy", "pandas", "statsmodels", "fastapi")


def _package_versions() -> dict:
    versions = {}
    for name in _PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None  # None = not installed
    return versions


def run_model_selftest(log_success: bool = False) -> dict:
    """Runs LightGBM's own predict() on a zero row -- the exact call that
    failed in production when scikit-learn was missing. Needs no data and
    takes about a millisecond, so /status runs it live on every call
    instead of trusting a stale startup result. Failures are always
    logged; successes only when log_success is set (the startup hook), so
    a monitor polling /status doesn't fill the logs."""
    result = {
        "ok": False,
        "error": None,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    model, meta = _load_forecast_model()
    if model is None:
        result["error"] = "no trained model artifact could be loaded"
    else:
        try:
            n_features = len(meta["feature_columns"])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # "X does not have valid feature names"
                out = np.asarray(model.predict(np.zeros((1, n_features))), dtype=float)
            if out.shape[0] != 1 or not np.isfinite(out).all():
                raise ValueError(f"unexpected predict() output: {out!r}")
            result["ok"] = True
        except Exception as exc:  # any failure here means "not serving"
            result["error"] = observability.short(f"{type(exc).__name__}: {exc}")
    if not result["ok"]:
        observability.log_event("model_selftest", level="error", ok=False, error=result["error"])
    elif log_success:
        observability.log_event("model_selftest", ok=True)
    return result


def run_model_selftest_startup() -> None:
    """Startup hook (registered in app.py). Non-fatal by construction: a
    failing self-test is logged loudly, never raised, so the app still
    comes up and serves the ETS fallback."""
    try:
        run_model_selftest(log_success=True)
    except Exception as exc:
        observability.log_event("model_selftest", level="error", ok=False, error=str(exc))


def build_status() -> dict:
    reasons = []

    selftest = run_model_selftest()
    model, meta = _load_forecast_model()
    if model is None:
        reasons.append("model_not_loaded")
    elif not selftest["ok"]:
        reasons.append("model_selftest_failed")

    serving = observability.serving_snapshot()
    share = serving["lightgbm_share"]
    if (
        serving["predictions_total"] >= MIN_PREDICTIONS_FOR_SHARE_CHECK
        and share is not None
        and share < MIN_LIGHTGBM_SHARE
    ):
        reasons.append("mostly_ets_fallback")

    # db.init_db() runs once at startup and its failure is non-fatal by design
    # (see app.py), so the app keeps serving forecasts while alerts stay
    # broken until the next restart. Surface that instead of hiding it.
    if serving["errors_by_kind"].get("subscriptions_db_init_failed"):
        reasons.append("subscriptions_db_init_failed")

    data = {}
    try:
        df = _load_full_dataframe()
        latest = df["date"].max()
        days_stale = (datetime.now().date() - latest.date()).days
        data = {"latest_date": latest.date().isoformat(), "days_stale": days_stale, "rows": int(len(df))}
        if days_stale > MAX_DATA_AGE_DAYS:
            reasons.append("data_stale")
    except Exception as exc:
        data = {"error": observability.short(exc)}
        reasons.append("data_unreadable")

    usage = usage_guard.usage_snapshot()
    if usage["gemini_calls"] >= usage["gemini_limit"]:
        reasons.append("gemini_daily_limit_reached")
    if usage["gtts_calls"] >= usage["gtts_limit"]:
        reasons.append("gtts_daily_limit_reached")

    meta = meta or {}
    return {
        "status": "degraded" if reasons else "ok",
        "reasons": reasons,
        "runtime": {
            "started_at": datetime.fromtimestamp(observability.STARTED_AT, timezone.utc).isoformat(timespec="seconds"),
            "uptime_seconds": int(time.time() - observability.STARTED_AT),
            "python": platform.python_version(),
            "packages": _package_versions(),
        },
        "model": {
            "artifact_loaded": model is not None,
            "trained_at": meta.get("trained_at"),
            "selftest": selftest,
            # Informational only: an artifact trained before Tier 2 #5/#6
            # lacks these until the next retrain, which is expected, not a fault.
            "has_per_pair_accuracy": "per_pair_accuracy" in meta,
            "has_directional_accuracy": "directional_accuracy" in meta,
        },
        "serving": serving,
        "data": data,
        "usage": usage,
    }


@router.get("/status")
def status(
    strict: bool = Query(
        False,
        description="If true, respond HTTP 503 when status is 'degraded' "
        "(for an external uptime monitor). Default: always HTTP 200.",
    ),
):
    body = build_status()
    code = 503 if (strict and body["status"] != "ok") else 200
    return JSONResponse(content=body, status_code=code, headers={"Cache-Control": "no-store"})
