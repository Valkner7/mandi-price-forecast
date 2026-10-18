"""Checks the 500-character cap on the `question` parameter (N28).

Run:  python test_question_limit.py   (offline; no Gemini key needed)
Exit code 0 = pass. Over-long questions must get 422 before reaching Gemini.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ.pop("API_SHARED_SECRET", None)

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
base = {"crop": "Potato", "mandi": "Rayya", "mandis": "Rayya,Khanna", "language": "en"}
failures = []
for method, path in (("get", "/advisory"), ("get", "/compare-advisory"), ("post", "/voice-advisory")):
    r = getattr(c, method)(path, params={**base, "question": "x" * 501})
    ok = r.status_code == 422
    print(f"  {'ok  ' if ok else 'FAIL'} {path}: 501 chars -> {r.status_code}")
    if not ok:
        failures.append(path)
sys.exit(1 if failures else 0)
