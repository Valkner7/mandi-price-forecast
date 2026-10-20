"""The Gemini model name must be the same everywhere, and not a retired one.

Checks routers/predict.py's default, diagnose_gemini.py's default, render.yaml
and the README example. Google retires Flash models every few months
(gemini-2.5-flash: shut down 16 Oct 2026; gemini-3.7-flash: deprecated), so this
also fails if a known-retired name creeps back in.

Run:  python test_gemini_model.py   (offline)   Exit code 0 = pass.
"""
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ.pop("GEMINI_MODEL", None)
for var in ("API_SHARED_SECRET", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.pop(var, None)

HERE = os.path.dirname(os.path.abspath(__file__))
RETIRED = ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-3.7-flash")
failures = []


def check(label, ok, extra=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{'' if ok else '  -> ' + str(extra)}")
    if not ok:
        failures.append(label)


def read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


import app  # noqa: E402,F401  (loads routers.predict)
from routers import predict  # noqa: E402

default = getattr(predict, "DEFAULT_GEMINI_MODEL", predict.GEMINI_MODEL)
check("code default is a gemini-3.x flash model", re.fullmatch(r"gemini-3\.\d+-flash", default) is not None, default)
check("code default is not a retired model", default not in RETIRED, default)
check("GEMINI_MODEL is the default when the env var is unset", predict.GEMINI_MODEL == default, predict.GEMINI_MODEL)

diag = re.search(r'os\.getenv\("GEMINI_MODEL",\s*"([^"]+)"\)', read("diagnose_gemini.py"))
check("diagnose_gemini.py default equals the code default", bool(diag) and diag.group(1) == default, diag.group(1) if diag else "not found")

render = re.search(r"key:\s*GEMINI_MODEL\s*\n\s*value:\s*(\S+)", read("render.yaml"))
check("render.yaml GEMINI_MODEL equals the code default", bool(render) and render.group(1) == default, render.group(1) if render else "not found")

readme = re.search(r"export GEMINI_MODEL=(\S+)", read("README.md"))
check("README example equals the code default", bool(readme) and readme.group(1) == default, readme.group(1) if readme else "not found")

sys.exit(1 if failures else 0)
