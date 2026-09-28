"""Checks for the Tier 4 observability layer (observability.py, routers/status.py).

Run:  python test_observability.py
Exit code 0 = all checks passed, 1 = at least one failed.

What is checked, and why (each is a way monitoring could silently lie):
  1. log_event writes ONE parseable JSON line, never raises, and a caller's
     field named "ts" can't overwrite the timestamp (level/event can't collide at all).
  2. Counters: which model served each forecast, fallback reasons, last error.
  3. /status flags a model that loads but cannot predict -- the exact
     failure that went unnoticed in production -- and a missing artifact,
     and mostly-ETS serving; and stays quiet when the model is healthy.
     Model health is tested separately from data freshness, so this test
     does not turn red just because the committed CSV gets old.
  4. ?strict=true returns HTTP 503 only when degraded.
  5. A real /predict-path call is counted under the model that served it;
     a forced LightGBM failure logs model_fallback + lightgbm_predict_error.
  6. The request middleware logs one structured line per request.
  7. Alert logs mask the subscriber's phone number.
  8. /status is actually registered on the app.

Imports `app` first on purpose (same circular-import reason as
test_explanations.py). Calls route functions and the middleware directly,
so it needs no HTTP test client dependency.
"""
import asyncio
import io
import json
import logging
import sys
import warnings

warnings.filterwarnings("ignore")

import app  # noqa: F401  (must come before routers.*)
import observability
import routers.alerts as ra
import routers.predict as rp
import routers.status as st

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class LogCapture:
    """Temporarily attaches a handler to the app's logger and parses each line as JSON."""

    def __enter__(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("mandi").addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        logging.getLogger("mandi").removeHandler(self.handler)

    @property
    def lines(self):
        return [ln for ln in self.stream.getvalue().splitlines() if ln.strip()]

    @property
    def events(self):
        return [json.loads(ln) for ln in self.lines]


# --------------------------------------------------------------------- 1
print("[1] log_event")
with LogCapture() as cap:
    observability.log_event("unit_test", level="warning", crop="Potato", weird=object(), ts="HIJACK")
ev = cap.events
check("emits exactly one line", len(cap.lines) == 1, str(cap.lines))
check("line is valid JSON with ts/level/event", bool(ev) and {"ts", "level", "event"} <= set(ev[0]))
check("caller cannot overwrite ts", bool(ev) and ev[0]["event"] == "unit_test" and ev[0]["ts"] != "HIJACK")
check("non-serialisable value is stringified, not raised", bool(ev) and isinstance(ev[0].get("weird"), str))
check("mask_phone keeps only the last 4", observability.mask_phone("whatsapp:+919876543210") == "***3210")

# --------------------------------------------------------------------- 2
print("[2] counters")
observability.reset_for_tests()
observability.record_prediction("LightGBM_global")
observability.record_prediction("LightGBM_global")
observability.record_prediction("ETS_level", fallback_reason="predict_error")
observability.record_error("unit_error", ValueError("boom " * 200), crop="Potato")
snap = observability.serving_snapshot()
check("counts by model", snap["predictions_by_model"] == {"LightGBM_global": 2, "ETS_level": 1})
check("lightgbm_share = 2/3", snap["lightgbm_share"] == round(2 / 3, 3))
check("fallback reason counted", snap["fallback_reasons"] == {"predict_error": 1})
check("last_error kept and truncated", snap["last_error"]["kind"] == "unit_error" and len(snap["last_error"]["message"]) <= 300)
check("snapshot says it is since process start", snap["since_start"] is True)

# --------------------------------------------------------------------- 3
print("[3] /status degraded rules")
observability.reset_for_tests()
real_load = st._load_forecast_model
real_model, real_meta = real_load()
check("test needs the committed model artifact", real_model is not None)

body = st.build_status()
check("healthy model: selftest ok", body["model"]["selftest"]["ok"] is True, str(body["model"]["selftest"]))
check("healthy model: no model reasons",
      not ({"model_not_loaded", "model_selftest_failed"} & set(body["reasons"])), str(body["reasons"]))
check("reports package versions incl. scikit-learn", "scikit-learn" in body["runtime"]["packages"])

st._load_forecast_model = lambda: (object(), real_meta)   # loads, but cannot predict
try:
    with LogCapture() as cap:
        broken = st.build_status()
finally:
    st._load_forecast_model = real_load
check("unpredictable model -> model_selftest_failed", "model_selftest_failed" in broken["reasons"], str(broken["reasons"]))
check("...and overall status is degraded", broken["status"] == "degraded")
check("...and the failure was logged as an error event",
      any(e["event"] == "model_selftest" and e["level"] == "error" for e in cap.events))

st._load_forecast_model = lambda: (None, None)
try:
    missing = st.build_status()
finally:
    st._load_forecast_model = real_load
check("no artifact -> model_not_loaded", "model_not_loaded" in missing["reasons"], str(missing["reasons"]))

observability.reset_for_tests()
for _ in range(12):
    observability.record_prediction("ETS_level", fallback_reason="predict_error")
check("12 ETS forecasts, 0 LightGBM -> mostly_ets_fallback", "mostly_ets_fallback" in st.build_status()["reasons"])
observability.reset_for_tests()
for _ in range(12):
    observability.record_prediction("LightGBM_global")
check("12 LightGBM forecasts -> no mostly_ets_fallback", "mostly_ets_fallback" not in st.build_status()["reasons"])
observability.reset_for_tests()
for _ in range(3):
    observability.record_prediction("ETS_level", fallback_reason="predict_error")
check("only 3 forecasts -> too few to judge", "mostly_ets_fallback" not in st.build_status()["reasons"])

observability.reset_for_tests()
observability.record_error("subscriptions_db_init_failed", RuntimeError("no Turso"))
check("failed Turso init at startup -> subscriptions_db_init_failed", "subscriptions_db_init_failed" in st.build_status()["reasons"])

# --------------------------------------------------------------------- 4
print("[4] strict mode")
st._load_forecast_model = lambda: (object(), real_meta)
try:
    check("degraded + strict -> 503", st.status(strict=True).status_code == 503)
    check("degraded + not strict -> 200", st.status(strict=False).status_code == 200)
finally:
    st._load_forecast_model = real_load
observability.reset_for_tests()
if not st.build_status()["reasons"]:
    check("healthy + strict -> 200", st.status(strict=True).status_code == 200)
else:
    print(f"  skip healthy + strict check (other flags present: {st.build_status()['reasons']})")

# --------------------------------------------------------------------- 5
print("[5] real /predict-path counting and forced fallback")
observability.reset_for_tests()
result = rp._build_prediction(crop="Potato", mandi="Rayya")
snap = observability.serving_snapshot()
check("served by LightGBM_global", result["model"] == "LightGBM_global", result["model"])
check("counted under that model", snap["predictions_by_model"] == {"LightGBM_global": 1}, str(snap))

observability.reset_for_tests()
rp._load_forecast_model = lambda: (object(), real_meta)
try:
    with LogCapture() as cap:
        fallback = rp._build_prediction(crop="Potato", mandi="Rayya")
finally:
    rp._load_forecast_model = real_load
snap = observability.serving_snapshot()
names = [e["event"] for e in cap.events]
check("forced failure served by ETS", fallback["model"].startswith("ETS"), fallback["model"])
check("logged lightgbm_predict_error", "lightgbm_predict_error" in names, str(names))
check("logged model_fallback", "model_fallback" in names, str(names))
check("error carries crop and mandi",
      snap["last_error"] and snap["last_error"].get("crop") == "Potato" and snap["last_error"].get("mandi") == "Rayya", str(snap["last_error"]))
check("fallback reason counted as predict_error", snap["fallback_reasons"] == {"predict_error": 1}, str(snap["fallback_reasons"]))

observability.reset_for_tests()
rp._load_forecast_model = lambda: (None, None)
try:
    rp._build_prediction(crop="Potato", mandi="Rayya")
finally:
    rp._load_forecast_model = real_load
check("no artifact -> reason no_model_loaded", observability.serving_snapshot()["fallback_reasons"] == {"no_model_loaded": 1})

# --------------------------------------------------------------------- 6
print("[6] request middleware")


class _Url:
    path = "/predict"


class _Req:
    method = "GET"
    url = _Url()


class _Resp:
    def __init__(self, code):
        self.status_code = code


async def _ok(_req):
    return _Resp(200)


async def _boom(_req):
    raise RuntimeError("kaboom")


with LogCapture() as cap:
    asyncio.run(app.log_request_timing(_Req(), _ok))
e = cap.events
check("one structured line with method/path/status/duration",
      len(e) == 1 and e[0]["event"] == "request" and e[0]["status"] == 200 and e[0]["path"] == "/predict" and "duration_ms" in e[0], str(e))
with LogCapture() as cap:
    try:
        asyncio.run(app.log_request_timing(_Req(), _boom))
        reraised = False
    except RuntimeError:
        reraised = True
check("unhandled error is logged AND re-raised", reraised and any(x["event"] == "request_failed" for x in cap.events))

# --------------------------------------------------------------------- 7
print("[7] alert logs mask phone numbers")
saved = ra.TWILIO_ACCOUNT_SID
ra.TWILIO_ACCOUNT_SID = None   # force the no-credentials branch: nothing is ever actually sent
try:
    with LogCapture() as cap:
        ra.send_whatsapp_message("whatsapp:+919876543210", "price alert body")
finally:
    ra.TWILIO_ACCOUNT_SID = saved
raw = "\n".join(cap.lines)
check("full number is not in the log", "9876543210" not in raw and "919876543210" not in raw, raw)
check("masked number is", "***3210" in raw, raw)

# --------------------------------------------------------------------- 8
print("[8] wiring")
# openapi() is the stable public view of routes; app.routes no longer lists included
# routers' routes flat in recent FastAPI versions.
paths = app.app.openapi()["paths"]
check("/status is registered", "/status" in paths and "/predict" in paths)
hooks = [h.__name__ for h in app.app.router.on_startup]
check("model self-test runs first at startup, before the trends warm-up",
      "run_model_selftest_startup" in hooks and hooks.index("run_model_selftest_startup") < hooks.index("_warm_trends_cache_startup"), str(hooks))

print()
if FAILURES:
    print(f"{len(FAILURES)} CHECK(S) FAILED: {FAILURES}")
    sys.exit(1)
print("ALL OBSERVABILITY CHECKS PASSED")
