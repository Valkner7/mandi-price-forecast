# Verification note — flat-forecast fix actually implemented (2026-09-10)

## What was wrong
`README.md` and `ISSUES_FIXED.md` both described a fix (new `zscore_7`,
`days_since_price_change`, `momentum_7_30`, `cv_7`, `is_observed_today`
features) and claimed it took the model from a 5.8% to a 44.8% win rate
vs. naive persistence. Neither the code nor the trained artifact backed
this up:

- `price_model.py` had none of those five identifiers anywhere, in any
  commit, on any branch.
- `models/lgbm_price_model_meta.json` (the actual deployed artifact) had
  `crop_mandi_win_rate_vs_naive: 0.0583` and `backtest_mae: 125.775` vs.
  `naive_backtest_mae: 125.77` — essentially tied, the *old* number.
- `/predict`'s live confidence note reads this win rate straight from the
  metadata file, so the deployed product itself was still honestly
  reporting ~5.8%, contradicting what the docs claimed.

## What was actually done this session
1. Implemented all five features in `price_model.py`:
   - `_safe_ratio` / `_safe_ratio_scalar`: shared division helper that
     floors near-zero denominators (|d| < 1e-3, matching the self-check's
     existing `roll_std_*` tolerance) to a 0 ratio instead of blowing up,
     used identically by training and serving.
   - `_days_since_price_change`: vectorized run-length of the price's
     current flat streak.
   - `_add_derived_signal_features`: computes `zscore_7`, `momentum_7_30`,
     `cv_7`, `days_since_price_change`, `is_observed_today` for training
     rows; wired into `add_features()`.
   - `_build_serving_row()` computes the same five, scalar-wise, from
     `price_history`/the already-computed rolling stats — same formulas,
     same epsilon floor.
   - All five added to `get_feature_columns()`.
2. Threaded `is_observed_today` through `forecast_recursive()` and
   `forecast_recursive_batch()` (True only for the first forecast day —
   the real "today" — forced False for every synthetic day after) and
   into `app.py`'s `/predict` and `/trends` call sites, which now derive
   it from `load_series()` / `_load_series_bulk()`'s daily-resample step
   (before ffill) rather than assuming every "today" is a real report.
3. Updated `train_forecast_model.py`'s self-check to reconstruct
   `is_observed_today` per sampled row from the panel's own `is_observed`
   column, so it doesn't false-flag a mismatch.
4. Retrained via `python train_forecast_model.py`.

## Verified results (this session, not asserted)
```
crop_mandi_combinations_tested: 223
crop_mandi_wins_vs_naive: 100
crop_mandi_win_rate_vs_naive: 0.4484        # matches README's 44.8% (100/223)
model_mae: 128.033   vs.  naive_mae: 128.169  # model now beats naive overall
Self-check passed: 200/200 sampled rows match between training and serving features.
best_iteration_: 108 trees                  # not the degenerate 1-tree case
```
Top feature importances after retrain: `mandi`, then **`zscore_7`** (2nd
overall), `days_since_start`, `month`, **`days_since_price_change`**,
`roll_std_30`, ..., **`arrival_lag_5`** also appears in the top 12 —
confirms arrival-volume data is now contributing, not just wired in inert.

End-to-end sanity check (`Potato`/`Rayya`, previously flat): 7-day forecast
now shows real day-to-day movement (639.0 → 646.6) instead of a flat line
at the last known price (635.0).

## Files changed
- `price_model.py`
- `train_forecast_model.py`
- `app.py`
- `models/lgbm_price_model.joblib`, `models/lgbm_price_model_meta.json`
  (retrained artifact — matches the code above; don't hand-edit, regenerate
  via `train_forecast_model.py` if the code changes further)

No README changes were needed — its 44.8%/100/223 numbers were already
correct, they just weren't backed by real code/artifact until now.
