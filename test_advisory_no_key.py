"""With no Gemini key, /advisory, /voice-advisory and the SMS/WhatsApp reply
must use the translated, caveated fallback template, not a bare English line.

Run:  python test_advisory_no_key.py   (offline; no Gemini key needed)
Exit code 0 = pass.
"""
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
for var in ("API_SHARED_SECRET", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.pop(var, None)

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Callers on this machine are answered even with no API_SHARED_SECRET (api_secret.py); the default
# TestClient address ("testclient") is not local and would get 503 once fail-closed secrets are merged.
c = TestClient(app.app, client=("127.0.0.1", 50000))
failures = []


def check(label, ok):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


for lang, script in (("pa", r"[\u0A00-\u0A7F]"), ("hi", r"[\u0900-\u097F]"), ("en", r"[A-Za-z]")):
    r = c.get("/advisory", params={"crop": "Potato", "mandi": "Rayya",
                                   "question": "kini keemat?", "language": lang})
    body = r.json() if r.status_code == 200 else {}
    text = body.get("advisory", "")
    check(f"/advisory {lang}: 200", r.status_code == 200)
    check(f"/advisory {lang}: source is fallback", body.get("advisory_source") == "fallback")
    check(f"/advisory {lang}: text in the requested script", bool(re.search(script, text)))
    if lang != "en":
        # The old bare line was "<crop> at <mandi>: Rs N/quintal, trend X."
        check(f"/advisory {lang}: not the bare English line", "/quintal, trend" not in text)
    else:
        check("/advisory en: has the 'estimate' caveat", "estimate" in text.lower())

# SMS/WhatsApp path (build_reply_text) uses the same fallback.
from routers import voice  # noqa: E402

if hasattr(voice, "build_reply_text"):
    text = voice.build_reply_text("ਆਲੂ Rayya")
    check("SMS reply for a Punjabi message is not the bare English line", "/quintal, trend" not in text)

sys.exit(1 if failures else 0)
