"""Checks for the feed-level data-age fields on /predict.

Run:  python test_data_age.py
Exit code 0 = all checks passed, 1 = at least one failure.

What is checked, and why:
  1. The age is measured from the newest row in the WHOLE dataset, so one
     sporadic mandi cannot trigger it.
  2. No warning at 0 or 1 days (a normal evening-to-morning gap); a warning
     from DATASET_STALE_WARN_DAYS on (a missed day of data).
  3. The age never goes negative if the server clock is behind the data.
  4. A real /predict response carries the same fields as the helper.
  5. The per-pair data_note keeps its own, separate 7-day rule.

Imports `app` first on purpose (circular import, same order as the server).
"""
import sys
import warnings

warnings.filterwarnings("ignore")

import app  # noqa: F401  (must come before routers.predict)
import pandas as pd

import routers.predict as rp

failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


latest = rp._load_full_dataframe()["date"].max().normalize()
day = pd.Timedelta(days=1)

print("[1] age is measured from the newest row in the whole dataset")
r = rp._dataset_freshness(today=latest + 7 * day)
check("dataset_latest_date matches the CSV", r["dataset_latest_date"] == latest.date().isoformat(), r)
check("age is 7 for a clock 7 days later", r["data_age_days"] == 7, r)

print("[2] warning threshold")
for n in range(0, 5):
    r = rp._dataset_freshness(today=latest + n * day)
    expect = n >= rp.DATASET_STALE_WARN_DAYS
    check(f"age {n}: warning {'present' if expect else 'absent'}", ("data_age_warning" in r) == expect, r)
r = rp._dataset_freshness(today=latest + 3 * day)
check("warning names the date and the age",
      latest.date().isoformat() in r["data_age_warning"] and "3 days" in r["data_age_warning"],
      r.get("data_age_warning"))

print("[3] clock behind the data")
r = rp._dataset_freshness(today=latest - 2 * day)
check("age is clamped at 0", r["data_age_days"] == 0 and "data_age_warning" not in r, r)

print("[4] /predict carries the fields")
p = rp._build_prediction("Onion", "Adampur")
check("data_age_days present", "data_age_days" in p and "dataset_latest_date" in p, list(p))
check("matches the helper (same server clock)",
      p["data_age_days"] == rp._dataset_freshness()["data_age_days"], p.get("data_age_days"))
check("warning key appears exactly when age >= threshold",
      ("data_age_warning" in p) == (p["data_age_days"] >= rp.DATASET_STALE_WARN_DAYS), p.get("data_age_days"))

print("[5] per-pair note keeps its own 7-day rule")
check("DATA_NOTE_STALE_DAYS unchanged at 7", rp.DATA_NOTE_STALE_DAYS == 7, rp.DATA_NOTE_STALE_DAYS)
check("feed threshold is lower than the pair threshold", rp.DATASET_STALE_WARN_DAYS < rp.DATA_NOTE_STALE_DAYS)

if failures:
    print(f"\n{len(failures)} CHECK(S) FAILED: " + "; ".join(failures))
    sys.exit(1)
print("\nALL DATA-AGE CHECKS PASSED")
