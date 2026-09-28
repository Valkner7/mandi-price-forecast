"""
Step 19 — Run 5 distinct farmer scenario queries end-to-end, log latency.

Times each stage of the pipeline separately (extraction, prediction, LLM
advisory, TTS) so you know where time is actually going, not just a single
end-to-end number. Run this yourself with GEMINI_API_KEY set to get real
advisory/TTS timings — without a key those stages are skipped, not faked.

Usage:
    export GEMINI_API_KEY=your-key-here
    python3 test_scenarios.py
"""

import os
import sys
import time
from io import BytesIO

from voice_extraction import extract_crop_and_mandi
# `predict` in routers/predict.py is the FastAPI route and returns a
# JSONResponse; the plain-dict core is _build_prediction (re-exported by
# app.py). Importing the route as "predict" broke this file at import time
# after the routers/ refactor (b2afda6), so CI could not have caught anything.
from app import _build_prediction as predict, generate_advisory
from fastapi import HTTPException
from gtts import gTTS

# Five realistic farmer questions, spanning all three languages and both
# well-populated mandis (Rayya, Rajpura), plus one deliberately sparse
# mandi (Patiala) to measure how fast the "not enough data" path fails.
SCENARIOS = [
    ("en", "When should I sell potato in rayya mandi?"),
    ("hi", "प्याज़ रैया मंडी में क्या भाव है?"),
    ("pa", "ਆਲੂ ਦਾ ਕੀ ਭਾਅ ਹੈ ਪਟਿਆਲੇ ਵਾਲੀ ਮੰਡੀ ਚ"),
    ("en", "What is the price of onion in Rajpura mandi"),
    ("hi", "मुझे आलू रैया मंडी में कब बेचना चाहिए?"),
]


def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        return result, (time.perf_counter() - start) * 1000, None
    except HTTPException as error:
        return None, (time.perf_counter() - start) * 1000, error.detail
    except Exception as error:
        return None, (time.perf_counter() - start) * 1000, str(error)


def run_scenario(language, question):
    row = {"question": question, "language": language}
    total_start = time.perf_counter()

    extracted, t_extract, err = timed(extract_crop_and_mandi, question)
    row["extract_ms"] = round(t_extract, 1)
    crop, mandi = (extracted or {}).get("crop"), (extracted or {}).get("mandi")
    row["crop"], row["mandi"] = crop, mandi

    if not crop or not mandi:
        row["stage_failed"] = "extraction"
        row["detail"] = "Crop or mandi not recognized"
        row["total_ms"] = round((time.perf_counter() - total_start) * 1000, 1)
        return row

    forecast, t_predict, err = timed(predict, crop=crop, mandi=mandi)
    row["predict_ms"] = round(t_predict, 1)
    if forecast is None:
        row["stage_failed"] = "predict"
        row["detail"] = err
        row["total_ms"] = round((time.perf_counter() - total_start) * 1000, 1)
        return row

    result, t_advisory, err = timed(
        generate_advisory, forecast_data=forecast, farmer_question=question, language_code=language
    )
    row["advisory_ms"] = round(t_advisory, 1)
    if result is None:
        row["stage_failed"] = "advisory (no GEMINI_API_KEY set?)"
        row["detail"] = err
        row["total_ms"] = round((time.perf_counter() - total_start) * 1000, 1)
        return row
    advisory, used_fallback = result
    row["advisory_source"] = "fallback" if used_fallback else "gemini"

    def do_tts():
        buf = BytesIO()
        gTTS(text=advisory, lang=language).write_to_fp(buf)
        return buf.tell()

    audio_bytes, t_tts, err = timed(do_tts)
    row["tts_ms"] = round(t_tts, 1)
    if audio_bytes is None:
        row["stage_failed"] = "tts"
        row["detail"] = err

    row["total_ms"] = round((time.perf_counter() - total_start) * 1000, 1)
    return row


# A crop/mandi pair the trained global model is known to serve (both are in
# models/lgbm_price_model_meta.json's crops_seen/mandis_seen and have long
# history). If this pair ever comes back as anything but LightGBM_global, the
# model is not serving -- e.g. a dependency the joblib artifact needs at
# predict() time is missing from requirements.txt, which is what happened
# when scikit-learn was removed as "unused" (f365637): the model loaded, every
# predict() threw, and /predict silently fell back to ETS.
SERVING_CHECK_PAIR = ("Potato", "Rayya")


def check_lightgbm_serving() -> bool:
    crop, mandi = SERVING_CHECK_PAIR
    forecast, _, err = timed(predict, crop=crop, mandi=mandi)
    if forecast is None:
        print(f"[serving check] FAIL: predict({crop}, {mandi}) raised: {err}")
        return False
    if forecast.get("model") != "LightGBM_global":
        print(
            f"[serving check] FAIL: {crop}/{mandi} was served by {forecast.get('model')!r}, "
            f"expected 'LightGBM_global'. model_note: {forecast.get('model_note')!r}"
        )
        return False
    print(f"[serving check] OK: {crop}/{mandi} served by LightGBM_global")
    return True


def check_confidence_matches_model() -> bool:
    """When LightGBM fails and ETS serves, the confidence block must not quote
    LightGBM's backtest numbers. Forces the fallback with a stand-in model
    object that cannot predict, keeping the real artifact's meta."""
    import routers.predict as rp

    original = rp._load_forecast_model
    real_model, real_meta = original()
    if real_meta is None:
        print("[confidence check] SKIP: no model artifact loaded")
        return True
    rp._load_forecast_model = lambda: (object(), real_meta)
    try:
        crop, mandi = SERVING_CHECK_PAIR
        forecast, _, err = timed(predict, crop=crop, mandi=mandi)
    finally:
        rp._load_forecast_model = original
    if forecast is None:
        print(f"[confidence check] FAIL: ETS fallback path raised: {err}")
        return False
    conf = forecast["confidence"]
    problems = []
    if forecast.get("model") == "LightGBM_global":
        problems.append("fallback was not exercised (model still LightGBM_global)")
    if "beat naive" in conf["validated_on"]:
        problems.append("validated_on quotes the LightGBM backtest for an ETS forecast")
    if conf["per_pair"].get("available"):
        problems.append("per_pair accuracy is marked available for an ETS forecast")
    if conf["directional_accuracy"].get("available"):
        problems.append("directional_accuracy is marked available for an ETS forecast")
    if problems:
        print("[confidence check] FAIL: " + "; ".join(problems))
        return False
    print("[confidence check] OK: ETS fallback shows no LightGBM accuracy claims")
    return True


def main() -> int:
    serving_ok = check_lightgbm_serving()
    confidence_ok = check_confidence_matches_model()
    results = [run_scenario(lang, q) for lang, q in SCENARIOS]

    print(f"\n{'#':<3}{'lang':<5}{'crop/mandi':<28}{'extract':>9}{'predict':>9}{'advisory':>10}{'tts':>9}{'TOTAL':>9}  src   stage_failed")
    print("-" * 122)
    for i, r in enumerate(results, 1):
        cm = f"{r.get('crop') or '?'}/{r.get('mandi') or '?'}"
        print(
            f"{i:<3}{r['language']:<5}{cm:<28}"
            f"{r.get('extract_ms', '-'):>9}{r.get('predict_ms', '-'):>9}"
            f"{r.get('advisory_ms', '-'):>10}{r.get('tts_ms', '-'):>9}"
            f"{r['total_ms']:>9}  {r.get('advisory_source', '-'):<5} {r.get('stage_failed', '')}"
        )
        if r.get("stage_failed") and r.get("detail"):
            print(f"      -> {r['detail']}")

    complete = [r for r in results if "advisory_ms" in r and "stage_failed" not in r]
    print()
    if complete:
        avg_total = sum(r["total_ms"] for r in complete) / len(complete)
        print(f"Full end-to-end pipeline completed for {len(complete)}/5 scenarios. Average: {avg_total:.0f}ms")
    else:
        failed_stages = sorted(set(r.get("stage_failed", "unknown") for r in results if "stage_failed" in r))
        print(f"No scenario completed the full pipeline. Stages that failed: {', '.join(failed_stages)}")
        print("See the '-> ...' detail lines above each failed row for the real reason — don't assume it's the API key.")

    # Exit-code classification, added for CI (Tier 1 #2 in the "What to
    # Build Next" roadmap). Distinguishes real regressions from expected
    # environmental gaps, rather than treating every non-"complete" row as
    # a failure:
    #   - extraction/predict failures are always real: neither depends on
    #     GEMINI_API_KEY or any network call this script doesn't control,
    #     so a failure there means actual pipeline logic broke.
    #   - an advisory failure only counts as real if GEMINI_API_KEY was
    #     actually set. An unset key is this script's own documented,
    #     expected reason for that stage to be skipped (see the module
    #     docstring) — not a regression.
    #   - tts failures are reported above but deliberately don't fail the
    #     run on their own: gTTS depends on an external network call this
    #     script doesn't control, and a flaky external service shouldn't
    #     be indistinguishable in CI from an actual code regression.
    key_present = bool(os.getenv("GEMINI_API_KEY"))
    real_failures = [
        r for r in results
        if r.get("stage_failed") in ("extraction", "predict")
        or (r.get("stage_failed", "").startswith("advisory") and key_present)
    ]
    if not (serving_ok and confidence_ok):
        print("\nModel-serving pre-flight check(s) failed -- treating this as a real failure.")
        return 1
    if real_failures:
        print(f"\n{len(real_failures)} scenario(s) failed for reasons unrelated to a missing GEMINI_API_KEY — treating this as a real failure.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
