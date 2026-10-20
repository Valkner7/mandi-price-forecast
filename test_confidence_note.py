"""The Hindi and Punjabi confidence notes must say "only sometimes", like English.

English says forecasts beat "no price change" "only some of the time". The Hindi
and Punjabi versions used to say "not always better", which sounds far more
hopeful than the backtest supports (about 46% of crop-mandi pairs). They must
keep the same strength as English, and must not reintroduce "always".

Run:  python test_confidence_note.py   (offline)   Exit code 0 = pass.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
for var in ("API_SHARED_SECRET", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.pop(var, None)

import app  # noqa: E402,F401
from routers.predict import FORECAST_CONFIDENCE_NOTE as NOTE  # noqa: E402

failures = []


def check(label, ok, extra=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{'' if ok else '  -> ' + str(extra)}")
    if not ok:
        failures.append(label)


check("same languages as the rest of the service", set(NOTE) == {"en", "hi", "pa"}, sorted(NOTE))
check("en says 'only some of the time'", "only some of the time" in NOTE["en"])
check("hi says 'केवल कभी-कभी' (only sometimes)", "केवल कभी-कभी" in NOTE["hi"])
check("hi does not say 'हमेशा' (always)", "हमेशा" not in NOTE["hi"])
check("pa says 'ਸਿਰਫ਼ ਕਦੇ-ਕਦੇ' (only sometimes)", "ਸਿਰਫ਼ ਕਦੇ-ਕਦੇ" in NOTE["pa"])
check("pa does not say 'ਹਮੇਸ਼ਾ' (always)", "ਹਮੇਸ਼ਾ" not in NOTE["pa"])

# The fallback advisory carries this note, so check it arrives in each language.
from routers.predict import build_fallback_advisory  # noqa: E402

fd = {"crop": "Potato", "mandi": "Rayya", "latest_price": 700.0, "latest_date": "2026-10-01",
      "trend": "falling", "forecast": [{"price": 690.0}], "forecast_horizon_days": 7}
for lang in ("hi", "pa"):
    check(f"fallback advisory ({lang}) contains the note", NOTE[lang] in build_fallback_advisory(fd, lang))

sys.exit(1 if failures else 0)
