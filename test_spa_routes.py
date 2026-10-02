"""Checks that client-side (React Router) pages load on a direct visit.

Run:  python test_spa_routes.py
Exit code 0 = all checks passed, 1 = at least one failure.

Why: the dashboard is a single-page app. "/nearby" only exists in the browser's
router, so a reload or a shared link sends the request to the server, which
must answer with index.html instead of a 404.
"""
import sys
import warnings

warnings.filterwarnings("ignore")

import app  # noqa: F401  (imported first, same order as the server)
from fastapi.testclient import TestClient

c = TestClient(app.app)
failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


index = c.get("/")
check("GET / serves the dashboard", index.status_code == 200 and "/assets/" in index.text, index.status_code)

nearby = c.get("/nearby")
check("GET /nearby returns 200", nearby.status_code == 200, nearby.status_code)
check("GET /nearby serves the same index.html as /", nearby.text == index.text)
check("HEAD /nearby works (uptime monitors)", c.head("/nearby").status_code == 200)

api = c.get("/api/nearby-mandis", params={"lat": 31.33, "lon": 75.57})
check("API route /api/nearby-mandis is not shadowed", api.headers.get("content-type", "").startswith("application/json"),
      api.headers.get("content-type"))

check("unknown paths still 404", c.get("/definitely-not-a-page").status_code == 404)

if failures:
    print(f"\n{len(failures)} CHECK(S) FAILED: " + "; ".join(failures))
    sys.exit(1)
print("\nALL SPA ROUTE CHECKS PASSED")
