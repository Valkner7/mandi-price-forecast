"""
Tier 4: structured logging + in-memory serving counters.

Why this exists: on 2026-09-14 scikit-learn was dropped from
requirements.txt as "unused" (f365637). LightGBM's sklearn-style model then
failed inside predict(), /predict silently fell back to ETS, and the only
trace was one free-text print() line nobody was watching; it was found on
2026-09-28. Nothing here prevents a bug like that, but it makes the *next*
one visible: every fallback is a
machine-readable log line, the counts are queryable at GET /status, and
/status?strict=true returns HTTP 503 when something is degraded so an
external monitor (UptimeRobot, cron-job.org) can alert on it.

Deliberately small and dependency-free:
  - log_event(): one JSON object per line on stdout (Render captures
    stdout; JSON lines can be searched/filtered with plain text tools).
    Never raises -- a logging problem must not break a request.
  - record_prediction() / record_error(): thread-safe counters kept in
    memory. They reset on every restart or deploy, and each Render
    instance has its own -- /status says so ("since_start") rather than
    implying a lifetime total.

Nothing in this module imports from app.py or routers/, so any module can
import it without creating an import cycle.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

STARTED_AT = time.time()

# Dedicated logger with its own stdout handler and propagate=False, so this
# neither depends on nor changes how uvicorn configures its own loggers.
_logger = logging.getLogger("mandi")
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.propagate = False
_logger.setLevel(_LEVELS.get(os.getenv("LOG_LEVEL", "info").lower(), logging.INFO))


def short(value, limit: int = 300) -> str:
    """str(value), truncated -- keeps error text from bloating a log line
    or a /status response."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def mask_phone(value) -> str:
    """Last 4 characters only, e.g. 'whatsapp:+91XXXXXX1234' -> '***1234'.
    Subscriber numbers shouldn't sit in plain text in log storage."""
    text = str(value or "")
    return f"***{text[-4:]}" if len(text) > 4 else "***"


def log_event(event: str, level: str = "info", **fields) -> None:
    """Write one JSON object on one line: {"ts", "level", "event", ...fields}.
    ts/level/event always win over a same-named field. Non-JSON-serialisable
    values are stringified. Never raises."""
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": level,
            "event": event,
        }
        for key, value in fields.items():
            record.setdefault(key, value)
        _logger.log(_LEVELS.get(level, logging.INFO), json.dumps(record, default=str))
    except Exception:
        pass


_lock = threading.Lock()
_predictions_by_model: dict = {}
_fallback_reasons: dict = {}
_errors_by_kind: dict = {}
_last_error: dict | None = None


def record_prediction(model: str, fallback_reason: str | None = None) -> None:
    """Count one /predict-path forecast by the model that actually produced
    it. fallback_reason is set only when ETS served because LightGBM
    could not (e.g. "no_model_loaded", "predict_error"); that case is also
    logged as a warning so it shows up in the logs, not just in counters."""
    with _lock:
        _predictions_by_model[model] = _predictions_by_model.get(model, 0) + 1
        if fallback_reason:
            _fallback_reasons[fallback_reason] = _fallback_reasons.get(fallback_reason, 0) + 1
    if fallback_reason:
        log_event("model_fallback", level="warning", served_by=model, reason=fallback_reason)


def record_error(kind: str, error, **context) -> None:
    """Count and log an error by kind (e.g. "lightgbm_predict_error"),
    remembering the most recent one for /status."""
    global _last_error
    message = short(error)
    with _lock:
        _errors_by_kind[kind] = _errors_by_kind.get(kind, 0) + 1
        _last_error = {
            "kind": kind,
            "message": message,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **{k: short(v, 100) for k, v in context.items()},
        }
    log_event(kind, level="error", error=message, **context)


def serving_snapshot() -> dict:
    """Counters since this process started (they reset on every restart or
    deploy, and are per instance)."""
    with _lock:
        total = sum(_predictions_by_model.values())
        lgbm = _predictions_by_model.get("LightGBM_global", 0)
        return {
            "since_start": True,
            "predictions_total": total,
            "predictions_by_model": dict(_predictions_by_model),
            "lightgbm_share": round(lgbm / total, 3) if total else None,
            "fallback_reasons": dict(_fallback_reasons),
            "errors_by_kind": dict(_errors_by_kind),
            "last_error": dict(_last_error) if _last_error else None,
        }


def reset_for_tests() -> None:
    global _last_error
    with _lock:
        _predictions_by_model.clear()
        _fallback_reasons.clear()
        _errors_by_kind.clear()
        _last_error = None
