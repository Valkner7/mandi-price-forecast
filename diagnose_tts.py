"""
Standalone gTTS diagnostic — isolates exactly why the TTS stage is failing,
one language at a time, with the real exception printed (not just "tts" as
a stage name like test_scenarios.py's summary table shows).

Usage:
    python diagnose_tts.py
"""

import time
from gtts import gTTS

LANGS = {
    "en": "This is a test.",
    "hi": "यह एक परीक्षण है।",
    "pa": "ਇਹ ਇੱਕ ਟੈਸਟ ਹੈ।",
}

for lang, text in LANGS.items():
    print("=" * 60)
    print(f"Testing lang='{lang}'  text={text!r}")
    print("=" * 60)
    t0 = time.perf_counter()
    try:
        tts = gTTS(text=text, lang=lang)
        out_path = f"test_{lang}.mp3"
        tts.save(out_path)
        elapsed = time.perf_counter() - t0
        import os
        size = os.path.getsize(out_path)
        print(f"SUCCESS in {elapsed:.1f}s -> wrote {out_path} ({size} bytes)")
        if size < 500:
            print("WARNING: file is suspiciously small for real audio — might be an empty/error response saved as if it worked.")
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"FAILED after {elapsed:.1f}s")
        print(f"Exception type: {type(e).__name__}")
        print(f"Exception detail: {e}")
    print()

print("Done. If .mp3 files were created in this folder, try playing test_en.mp3 to confirm it's real audible speech, not silence.")
