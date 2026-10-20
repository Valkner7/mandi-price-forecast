"""Checks the shared-secret guards on the Gemini/voice endpoints (M10) and on
/check-alerts (Issue 5: fail closed when the secret is unset).

With no secret set: /advisory, /compare-advisory and /voice-advisory answer only
callers on this machine (loopback) and refuse everyone else with 503;
/check-alerts is switched off for everyone (503).

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

import routers.alerts as alerts

# "local" is how a local AnnaSetu reaches the service; "remote" is anyone else.
c = TestClient(app.app, client=("127.0.0.1", 50000))
remote = TestClient(app.app, client=("203.0.113.7", 50000))
failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


Q = {"crop": "Potato", "mandi": "Rayya", "language": "en"}
CQ = {"crop": "Potato", "mandis": "Rayya,Khanna", "language": "en"}
VQ = {"language": "en"}


def hit(name, headers=None, client=None):
    client = client or c
    if name == "advisory":
        return client.get("/advisory", params=Q, headers=headers)
    if name == "compare":
        return client.get("/compare-advisory", params=CQ, headers=headers)
    return client.post("/voice-advisory", params=VQ, headers=headers)


NAMES = ["advisory", "compare", "voice"]

print("Secret NOT set: this machine is answered, everyone else is refused")
for n in NAMES:
    r = hit(n)
    check(f"{n}: local caller passes the guard (422)", r.status_code == 422, r.status_code)
    r = hit(n, client=remote)
    check(f"{n}: remote caller -> 503", r.status_code == 503, r.status_code)
    r = hit(n, {"X-Api-Secret": "anything"}, client=remote)
    check(f"{n}: remote caller sending a header is still 503", r.status_code == 503, r.status_code)
r = hit("advisory", client=remote)
check("503 body names the variable to set", "API_SHARED_SECRET" in r.text)
for host, label, want in (("::1", "IPv6 loopback", 422),
                          ("::ffff:127.0.0.1", "IPv4-mapped loopback", 422),
                          ("127.5.5.5", "any 127.x address", 422),
                          ("10.0.0.5", "private LAN address (e.g. a reverse proxy)", 503),
                          ("192.168.1.9", "LAN address", 503),
                          ("testclient", "non-IP client name", 503)):
    r = hit("advisory", client=TestClient(app.app, client=(host, 50000)))
    check(f"{label} -> {want}", r.status_code == want, r.status_code)

os.environ["API_SHARED_SECRET"] = "s3cret-Value"
print("Secret set: locality no longer matters")
r = hit("advisory", {"X-Api-Secret": "s3cret-Value"}, client=remote)
check("remote caller with the right secret passes the guard (422)", r.status_code == 422, r.status_code)
r = hit("advisory", client=remote)
check("remote caller without it -> 403 (not 503)", r.status_code == 403, r.status_code)
os.environ.pop("API_SHARED_SECRET", None)

os.environ["API_SHARED_SECRET"] = "s3cret-Value"
print("Secret set (local caller)")
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
check("blank secret counts as unset: local still answered", r.status_code == 422, r.status_code)
r = hit("advisory", client=remote)
check("blank secret counts as unset: remote refused", r.status_code == 503, r.status_code)

# ---- /check-alerts ----
print("/check-alerts: switched off while ALERTS_CRON_SECRET is unset")
alerts.check_all_alerts = lambda: {"checked": 0, "notified": 0}   # never send anything real
for who, client in (("local", c), ("remote", remote)):
    for unset in (None, "", "   "):
        alerts.ALERTS_CRON_SECRET = unset
        r = client.get("/check-alerts")
        check(f"{who}, secret={unset!r}: GET -> 503", r.status_code == 503, r.status_code)
    alerts.ALERTS_CRON_SECRET = None
    r = client.post("/check-alerts", headers={"X-Cron-Secret": "guess"})
    check(f"{who}: POST with a header is still 503", r.status_code == 503, r.status_code)
r = remote.get("/check-alerts")
check("503 body names the variable to set", "ALERTS_CRON_SECRET" in r.text)
r = remote.get("/check-alerts", params={"secret": ""})
check("empty ?secret= does not match an unset secret", r.status_code == 503, r.status_code)

print("/check-alerts: secret set")
alerts.ALERTS_CRON_SECRET = "cron-Secret-1"
r = remote.get("/check-alerts")
check("no secret -> 403", r.status_code == 403, r.status_code)
r = remote.get("/check-alerts", headers={"X-Cron-Secret": "wrong"})
check("wrong header -> 403", r.status_code == 403, r.status_code)
r = remote.get("/check-alerts", params={"secret": "wrong"})
check("wrong ?secret= -> 403", r.status_code == 403, r.status_code)
r = remote.get("/check-alerts", params={"secret": "cron-Secret-"})
check("prefix of the secret -> 403", r.status_code == 403, r.status_code)
r = remote.get("/check-alerts", params={"secret": "s\u00e9cret-\u00f1"})
check("non-ASCII ?secret= -> 403, not a 500", r.status_code == 403, r.status_code)
r = remote.get("/check-alerts", headers={"X-Cron-Secret": "cron-Secret-1"})
check("right header -> 200", r.status_code == 200 and r.json().get("status") == "ok", r.status_code)
r = remote.post("/check-alerts", headers={"X-Cron-Secret": "cron-Secret-1"})
check("right header on POST -> 200", r.status_code == 200, r.status_code)
r = remote.get("/check-alerts", params={"secret": "cron-Secret-1"})
check("right ?secret= -> 200", r.status_code == 200, r.status_code)
alerts.ALERTS_CRON_SECRET = None

os.environ.pop("API_SHARED_SECRET", None)
print("Startup warning")
import observability
seen = []
orig = observability.log_event
observability.log_event = lambda *a, **k: seen.append((a, k))
import api_secret
api_secret.warn_if_unset()
check("warns when unset", any(k.get("level") == "warning" for _, k in seen))
check("warning says remote callers get 503", any("503" in str(k.get("effect", "")) for _, k in seen))
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
