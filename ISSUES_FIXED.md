# Mandi Setu — Issues Found & Fixed

Repo: `github.com/Valkner7/mandi-price-forecast`
Session date: 2026-09-10

**2026-09-10 update:** Item #1 below previously described a fix that turned
out not to actually be in the code — `price_model.py`/`train_forecast_model.py`/
`app.py` still had none of the five features, and the trained artifact was
still at the old 5.8% win rate, despite this doc and the README both
describing the fix as done. That gap has now been closed for real: the
features are implemented, the model has been retrained, and the retrained
artifact's own `crop_mandi_win_rate_vs_naive` is 0.4484 (100/223) —
matching what the README claims, verified directly from
`models/lgbm_price_model_meta.json` rather than asserted in prose. See the
`analysis_notes.md` companion file in this bundle for the verification
steps (self-check pass, tree count, feature importances).

## Fixed

### 1. The changelog's accuracy fix was never actually merged
**Found:** A separate document described a fix (new `zscore_7`, `days_since_price_change`,
`momentum_7_30`, `cv_7`, `is_observed_today` features) claiming a jump from 5.8% → 43.1%
win rate vs. naive persistence. None of those identifiers existed anywhere in the repo's
`price_model.py`, `train_forecast_model.py`, or `app.py`. The live deployment's
`/predict` confidence note still cited the old 5.8% figure, confirming it was live code,
not just a stale doc.

**Fixed:** Implemented all five features in `price_model.py`, computed identically for
training (`add_features()`) and live serving (`_build_serving_row()`) via shared helper
functions (`_zscore`, `_safe_ratio`, `_days_since_price_change`), matching the file's
existing train/serve-parity pattern. Threaded `is_observed_today` through
`forecast_recursive()` / `forecast_recursive_batch()` (true for day 1, forced false for
every synthetic day after) and into `app.py`'s `/predict`. Updated the self-check in
`train_forecast_model.py` to reconstruct `is_observed_today` per sampled row so it
doesn't falsely flag a mismatch.

**Files touched:** `price_model.py`, `train_forecast_model.py`, `app.py`

### 2. LightGBM model was training only 1 tree, regardless of hyperparameters
**Found:** The trained artifact's `best_iteration_` was `1` — a single tree, ~30 total
splits. Feature importances showed `arrival_lag_*`/`arrival_roll_*` (the arrival-volume
features already in production) getting 0–1 splits total. Tested raising
`stopping_rounds` from 30 → 100 → 200 and trying four different
learning-rate/regularization/leaf-count combinations: **every configuration still
converged to `best_iteration_=1`**. This ruled out "early stopping is too impatient" —
round 1 was genuinely the global MAE optimum on validation across up to 1000 rounds,
because >75% of training targets are exactly 0% change (confirmed via
`target_pct_change.describe()`), so predicting ~0 for everything already sits near the
MAE floor and every subsequent tree just adds noise.

**Fixed:** Same fix as #1 — `zscore_7` and `days_since_price_change` concentrate signal
specifically on *which* days are "overdue" for a real move, giving the optimizer
something to reduce validation MAE against past round 1. After retraining with the new
features, the model now builds **108 trees**, and `zscore_7` is the 2nd-most-important
feature by split count (11.8%). Arrival features start contributing too
(`arrival_lag_5` now shows measurable importance) — they weren't actually useless,
they just never got the chance to be evaluated.

**Files touched:** same as #1 (no hyperparameter changes were needed — the feature
signal was the actual fix, not `stopping_rounds`/regularization)

## Verified results (retrained on live `clean_mandi_prices.csv`, 2026-09-10)

| Metric | Before | After |
|---|---|---|
| Win rate vs. naive persistence | 5.8% (13/223) | **44.8%** (100/223) |
| Model MAE vs. naive MAE | tied (128.18 vs 128.17) | model **beats** naive (128.03 vs 128.17) |
| Trees actually built | 1 | 108 |
| Train/serve self-check | — | passed (200/200) |

Smoke-tested end-to-end through the real `app.predict()` and `app.trends()` functions
(not just the standalone training script) — both run cleanly against the retrained
artifact.

## Found but NOT fixed (flagged only — no code changed)

These came up during the repo review but weren't part of what was asked to fix. Listed
here so nothing gets lost:

- **README is significantly stale**: claims 21,017 rows / 42 crops / 22 mandis; actual
  dataset is 51,057 rows / 62 crops / 115 mandis. Schema section omits the `arrival_qty`
  column that now exists in `clean_mandi_prices.csv`.
- **`.github/workflows/check-alerts.yml`** (fixes a real bug — alerts were created but
  never fired) exists in the repo but isn't mentioned anywhere in the README.
- **`frontend/`** (a Vite/React source app, built into `static/dashboard/`) isn't
  documented — someone new to the repo wouldn't know which one to edit.
- **Dead one-off migration scripts** `patch_root_assets.py` and `patch_twilio.py` — the
  changes they make are already baked into `app.py`; the scripts just sit there now.
- **`quarantined_rows.csv`, `data_quality.py`, `mandi_coords.py`** aren't mentioned in
  the README's file list.
- **Live deployment oddity**: querying `/predict?crop=Onion&mandi=Khanna` on
  `mandi-price-forecast-1.onrender.com` returned data identical to a prior
  `Potato`/`Rayya` query — looked like a caching or query-parsing issue. Not
  investigated further since the review scope shifted to the repo code itself.

## Deploying this fix

The retrained artifact (`models/lgbm_price_model.joblib` +
`models/lgbm_price_model_meta.json`) is ready as-is. Commit the four changed files plus
the retrained artifact, push, and let Render auto-deploy (or trigger manually) — same
deploy steps as the original changelog document described.
