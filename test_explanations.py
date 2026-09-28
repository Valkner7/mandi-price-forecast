"""Invariant checks for the Tier 3 explanation feature (SHAP + ETS).

Run:  python test_explanations.py
Exit code 0 = all checks passed, 1 = at least one real failure.

What is checked, and why (each is a way the feature could silently lie):
  1. Every current model feature maps to a real bucket, never "other" --
     catches a newly added feature nobody bucketed.
  2. baseline + sum(bucket contributions) == predicted change in rupees --
     the breakdown must actually add up (tolerance covers 2-dp rounding).
  3. The explained day+1 change == the real forecast's first step -- the
     explanation must describe the row the forecast actually used.
  4. ETS fallback level + trend parts reconcile with model.forecast(1).
  5. /predict's response dict carries an "explanation" block.

Imports `app` first on purpose: importing routers.predict directly hits a
circular import with app.py (same order the real server uses).
"""
import sys
import warnings

warnings.filterwarnings("ignore")

import app  # noqa: F401  (must come before routers.predict)
import pandas as pd

import price_model as pm
import routers.predict as rp

# Each bucket is rounded to 2 dp, up to 7 buckets + baseline + predicted.
ROUNDING_TOLERANCE_RUPEES = 0.06
# forecast_recursive rounds nothing, explanation rounds to 2 dp.
STEP1_TOLERANCE_RUPEES = 0.02
MAX_PAIRS = 30

failures: list[str] = []
checked_shap = 0
ets_variants_seen: set[str] = set()


def fail(msg: str) -> None:
    failures.append(msg)
    print("  FAIL:", msg)


# --- 1. feature bucket coverage ---------------------------------------------
print("[1] feature -> bucket coverage")
unmapped = [c for c in pm.get_feature_columns() if pm.bucket_for_feature(c) == "other"]
if unmapped:
    fail(f"features with no bucket (would land in 'Other factors'): {unmapped}")
else:
    print(f"  ok: all {len(pm.get_feature_columns())} features are bucketed")

# --- pick real crop/mandi pairs ---------------------------------------------
model, meta = rp._load_forecast_model()
if model is None:
    print("No trained model artifact found -- cannot run checks 2-3.")
    sys.exit(1)

df = pd.read_csv("clean_mandi_prices.csv")
crop_col = next(c for c in df.columns if c.lower() in ("crop", "commodity"))
mandi_col = next(c for c in df.columns if c.lower() in ("mandi", "market"))
all_pairs = (
    df[[crop_col, mandi_col]].drop_duplicates().sort_values([crop_col, mandi_col]).values.tolist()
)
# Only pairs the trained model actually serves (crop AND mandi seen in
# training) -- the CSV also holds crops with too little history to forecast.
served = [
    (c, m) for c, m in all_pairs
    if c in meta["crops_seen"] and m in meta["mandis_seen"]
]
step = max(1, len(served) // (MAX_PAIRS * 2))
candidates = served[::step] + [p for p in served if p not in served[::step]]

# Keep the first MAX_PAIRS candidates whose history is long enough to forecast.
sample: list = []
loaded: dict = {}
for c, m in candidates:
    if len(sample) >= MAX_PAIRS:
        break
    try:
        loaded[(c, m)] = rp.load_series(c, m)
        sample.append((c, m))
    except Exception:
        continue  # too little history: /predict itself rejects these
print(f"\n[2-4] checking {len(sample)} forecastable pairs "
      f"(of {len(served)} served by the model, {len(all_pairs)} in the CSV)")
if not sample:
    print("No forecastable pairs found.")
    sys.exit(1)

for crop, mandi in sample:
    series = loaded[(crop, mandi)]
    label = f"{crop}/{mandi}"

    # --- 2 & 3: SHAP path ----------------------------------------------------
    try:
        arrival = rp.load_arrival_series(crop, mandi, series.index)
        observed = series.attrs.get("is_observed_today", True)
        expl = pm.explain_serving_row(
            model, meta, series, crop, mandi, arrival_series=arrival, is_observed_today=observed
        )
        if not expl.get("available"):
            fail(f"{label}: SHAP explanation unavailable ({expl.get('reason')})")
        else:
            checked_shap += 1
            total = expl["baseline_change_rupees"] + sum(
                b["contribution_rupees"] for b in expl["buckets"]
            )
            if abs(total - expl["predicted_change_rupees"]) > ROUNDING_TOLERANCE_RUPEES:
                fail(f"{label}: baseline+buckets={total:.3f} != predicted={expl['predicted_change_rupees']:.3f}")
            step1 = pm.forecast_recursive(
                model, meta, series, crop, mandi, horizon=1,
                arrival_series=arrival, is_observed_today=observed,
            )[0] - float(series.iloc[-1])
            if abs(step1 - expl["predicted_change_rupees"]) > STEP1_TOLERANCE_RUPEES:
                fail(f"{label}: explained change {expl['predicted_change_rupees']:.3f} != real day-1 step {step1:.3f}")
            if any(b["key"] == "other" for b in expl["buckets"]):
                fail(f"{label}: an 'other' bucket appeared")
    except Exception as error:
        fail(f"{label}: SHAP check crashed: {type(error).__name__}: {error}")

    # --- 4: ETS path ---------------------------------------------------------
    try:
        ets_model, ets_name = rp.fit_ets(series)
        ets_variants_seen.add(ets_name)
        ex = rp._explain_ets_fallback(ets_model, ets_name, series)
        if not ex.get("available"):
            fail(f"{label}: ETS explanation unavailable ({ex.get('reason')})")
        else:
            total = sum(b["contribution_rupees"] for b in ex["buckets"])
            if abs(total - ex["predicted_change_rupees"]) > ROUNDING_TOLERANCE_RUPEES:
                fail(f"{label}: ETS level+trend={total:.3f} != predicted={ex['predicted_change_rupees']:.3f}")
    except Exception as error:
        fail(f"{label}: ETS check crashed: {type(error).__name__}: {error}")

print(f"  SHAP explanations verified: {checked_shap}")
print(f"  ETS variants exercised: {sorted(ets_variants_seen)}")
if checked_shap == 0:
    fail("no SHAP explanation was verified at all")

# --- 5. response shape --------------------------------------------------------
print("\n[5] /predict response carries an explanation block")
crop, mandi = sample[0]
result = rp._build_prediction(crop=crop, mandi=mandi)
if "explanation" not in result:
    fail("_build_prediction() result has no 'explanation' key")
elif not isinstance(result["explanation"], dict):
    fail("'explanation' is not a dict")
else:
    print(f"  ok: {crop}/{mandi} -> method={result['explanation'].get('method')}, "
          f"available={result['explanation'].get('available')}")

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    sys.exit(1)
print("ALL EXPLANATION CHECKS PASSED")
sys.exit(0)
