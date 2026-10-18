"""Checks the shared-secret guard on the Gemini/voice endpoints (M10).

Run:  python test_api_secret.py
Exit code 0 = all checks passed, 1 = at least one failure.
Offline: no Gemini key or network needed. Every request omits the required
`question` parameter, so a request that gets past the guard stops at validation
(422) and never reaches Gemini or text-to-speech; a rejected one gets 403.
FastAPI runs the guard first, so 403 before 422 also proves the order.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ.pop("API_SHARED_SECRET", None)

import app  # noqa: F401  (imported first, same order as the server)
from fastapi.testclient import TestClient

c = TestClient(app.app)
failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


Q = {"crop": "Potato", "mandi": "Rayya", "language": "en"}
CQ = {"crop": "Potato", "mandis": "Rayya,Khanna", "language": "en"}
VQ = {"language": "en"}


def hit(name, headers=None):
    if name == "advisory":
        return c.get("/advisory", params=Q, headers=headers)
    if name == "compare":
        return c.get("/compare-advisory", params=CQ, headers=headers)
    return c.post("/voice-advisory", params=VQ, headers=headers)


NAMES = ["advisory", "compare", "voice"]

print("Secret NOT set (default: open, nothing breaks)")
for n in NAMES:
    r = hit(n)
    check(f"{n}: no header passes the guard (422)", r.status_code == 422, r.status_code)

os.environ["API_SHARED_SECRET"] = "s3cret-Value"
print("Secret set")
for n in NAMES:
    r = hit(n)
    check(f"{n}: no header -> 403", r.status_code == 403, r.status_code)
    r = hit(n, {"X-Api-Secret": "wrong"})
    check(f"{n}: wrong secret -> 403", r.status_code == 403, r.status_code)
    r = hit(n, {"X-Api-Secret": "s3cret-valu"})
    check(f"{n}: prefix of the secret -> 403", r.status_code == 403, r.status_code)
    r = hit(n, {"X-Api-Secret": "s3cret-Value"})
    check(f"{n}: right secret passes the guard (422)", r.status_code == 422, r.status_code)

r = c.get("/advisory", params=Q, headers={"X-Api-Secret": "s\xe9cret-\xf1".encode("latin-1")})
check("non-ASCII header value -> 403, not a 500", r.status_code == 403, r.status_code)
check("403 body says why", "secret" in r.text.lower())

print("Other endpoints are untouched")
for path in ("/status", "/meta", "/history?crop=Potato&mandi=Rayya"):
    r = c.get(path)
    check(f"GET {path} needs no secret", r.status_code != 403, r.status_code)
r = c.get("/predict", params={"crop": "Potato", "mandi": "Rayya"})
check("GET /predict needs no secret", r.status_code != 403, r.status_code)

os.environ["API_SHARED_SECRET"] = "   "
r = hit("advisory")
check("blank secret counts as unset", r.status_code == 422, r.status_code)

os.environ.pop("API_SHARED_SECRET", None)
print("Startup warning")
import observability
seen = []
orig = observability.log_event
observability.log_event = lambda *a, **k: seen.append((a, k))
import api_secret
api_secret.warn_if_unset()
check("warns when unset", any(k.get("level") == "warning" for _, k in seen))
seen.clear()
os.environ["API_SHARED_SECRET"] = "x"
api_secret.warn_if_unset()
check("silent when set", not seen)
observability.log_event = orig

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("All checks passed.")
