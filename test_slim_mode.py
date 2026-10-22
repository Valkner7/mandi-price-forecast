"""Checks MANDI_SLIM=1 (the lean mode the AnnaSetu bundle runs in).

Slim mode must (1) start without twilio, libsql or python-multipart installed,
(2) keep every endpoint AnnaSetu calls, (3) answer 404 for the switched-off parts
(/whatsapp, /sms, /check-alerts, the React dashboard, the demo pages), and
(4) leave normal mode (MANDI_SLIM unset) untouched. requirements-slim.txt must equal
requirements.txt minus those three libraries.

Run:  python test_slim_mode.py      Exit 0 = all passed.
Offline. Each scenario runs in its own Python process, so imports cannot leak.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


PROBE = r'''
import json, os, sys, warnings
warnings.filterwarnings("ignore")
os.environ.pop("API_SHARED_SECRET", None)
if os.environ.get("BLOCK_EXTRAS") == "1":
    for m in ("twilio", "libsql", "multipart", "python_multipart"):
        sys.modules[m] = None          # any import of these now raises ImportError
import app
from fastapi.testclient import TestClient
c = TestClient(app.app, client=("127.0.0.1", 50000))
out = {"slim": app.SLIM}
def code(method, path, **kw):
    return getattr(c, method)(path, **kw).status_code
out["GET /"] = code("get", "/")
out["HEAD /"] = code("head", "/")
out["root_json"] = c.get("/").headers.get("content-type", "").startswith("application/json")
for p in ("/status", "/meta"):
    out["GET " + p] = code("get", p)
out["GET /predict (no params)"] = code("get", "/predict")
out["GET /history (no params)"] = code("get", "/history")
out["GET /compare (no params)"] = code("get", "/compare")
out["GET /api/nearby-mandis (no params)"] = code("get", "/api/nearby-mandis")
for p in ("/advisory", "/compare-advisory"):
    out["GET " + p + " (local, no params)"] = code("get", p)
out["POST /voice-advisory (local, no params)"] = code("post", "/voice-advisory")
out["GET /check-alerts"] = code("get", "/check-alerts")
out["POST /whatsapp"] = code("post", "/whatsapp")
out["POST /sms"] = code("post", "/sms")
for p in ("/dashboard/", "/voice-test", "/trends-dashboard", "/sw.js", "/nearby", "/favicon.svg"):
    out["GET " + p] = code("get", p)
out["twilio_loaded"] = "twilio" in sys.modules and sys.modules["twilio"] is not None
out["libsql_loaded"] = "libsql" in sys.modules and sys.modules["libsql"] is not None
print("RESULT " + json.dumps(out))
'''


def run(env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("MANDI_SLIM", "BLOCK_EXTRAS")}
    env.update(env_extra)
    p = subprocess.run([sys.executable, "-c", PROBE], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=300)
    for line in p.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:]), p
    return None, p


# --- slim mode, with the extras made unimportable ---------------------------------
r, p = run({"MANDI_SLIM": "1", "BLOCK_EXTRAS": "1"})
check("slim mode starts with twilio/libsql/python-multipart unavailable", r is not None, (p.stderr or p.stdout)[-400:])
if r:
    check("SLIM flag is on", r["slim"] is True)
    check("GET / -> 200 JSON health", r["GET /"] == 200 and r["root_json"], r["GET /"])
    check("HEAD / -> 200", r["HEAD /"] == 200, r["HEAD /"])
    for k in ("GET /status", "GET /meta"):
        check(f"{k} -> 200", r[k] == 200, r[k])
    for k in ("GET /predict (no params)", "GET /history (no params)", "GET /compare (no params)",
              "GET /api/nearby-mandis (no params)"):
        check(f"{k} exists (422, not 404)", r[k] == 422, r[k])
    for k in ("GET /advisory (local, no params)", "GET /compare-advisory (local, no params)",
              "POST /voice-advisory (local, no params)"):
        check(f"{k} exists and the local guard lets AnnaSetu in (422)", r[k] == 422, r[k])
    for k in ("GET /check-alerts", "POST /whatsapp", "POST /sms", "GET /dashboard/", "GET /voice-test",
              "GET /trends-dashboard", "GET /sw.js", "GET /nearby", "GET /favicon.svg"):
        check(f"{k} is switched off (404)", r[k] == 404, r[k])
    check("twilio was never imported", not r["twilio_loaded"])
    check("libsql was never imported", not r["libsql_loaded"])

# --- normal mode is unchanged -----------------------------------------------------
r, p = run({})
check("normal mode starts", r is not None, (p.stderr or p.stdout)[-400:])
if r:
    check("SLIM flag is off by default", r["slim"] is False)
    check("normal: /check-alerts still exists (503 while unset, not 404)", r["GET /check-alerts"] == 503, r["GET /check-alerts"])
    check("normal: POST /whatsapp still exists (not 404)", r["POST /whatsapp"] != 404, r["POST /whatsapp"])
    check("normal: GET /voice-test still served", r["GET /voice-test"] == 200, r["GET /voice-test"])
    check("normal: GET /trends-dashboard still served", r["GET /trends-dashboard"] == 200, r["GET /trends-dashboard"])
    check("normal: GET /sw.js still served", r["GET /sw.js"] == 200, r["GET /sw.js"])
    check("normal: GET /status -> 200", r["GET /status"] == 200)

# --- falsy values keep normal mode --------------------------------------------------
r, p = run({"MANDI_SLIM": "0"})
check("MANDI_SLIM=0 is normal mode", r is not None and r["slim"] is False)

# --- requirements-slim.txt == requirements.txt minus the three extras ----------------
def reqs(name):
    return [l.strip() for l in (HERE / name).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]

full, slim = reqs("requirements.txt"), reqs("requirements-slim.txt")
extras = {"twilio", "libsql", "python-multipart"}
check("requirements-slim.txt = requirements.txt minus twilio, libsql, python-multipart",
      slim == [l for l in full if l not in extras], f"{set(full) ^ set(slim)}")
check("slim list really dropped the three libraries", not (extras & set(slim)))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("All slim-mode checks passed.")
