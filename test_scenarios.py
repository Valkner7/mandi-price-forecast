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

import time
from io import BytesIO

from voice_extraction import extract_crop_and_mandi
from app import predict, generate_advisory
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


def main():
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

    measured_totals = [r["total_ms"] for r in results if "stage_failed" not in r or r["stage_failed"] == "tts"]
    complete = [r for r in results if "advisory_ms" in r and "stage_failed" not in r]
    print()
    if complete:
        avg_total = sum(r["total_ms"] for r in complete) / len(complete)
        print(f"Full end-to-end pipeline completed for {len(complete)}/5 scenarios. Average: {avg_total:.0f}ms")
    else:
        failed_stages = sorted(set(r.get("stage_failed", "unknown") for r in results if "stage_failed" in r))
        print(f"No scenario completed the full pipeline. Stages that failed: {', '.join(failed_stages)}")
        print("See the '-> ...' detail lines above each failed row for the real reason — don't assume it's the API key.")


if __name__ == "__main__":
    main()
