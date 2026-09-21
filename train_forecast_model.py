"""
train_forecast_model.py — offline trainer for the global LightGBM price
model. Run as a scheduled job (see .github/workflows/update-mandi-data.yml),
NEVER inside a live /predict request — that's the whole point of moving off
the old per-request fit_ets() pattern (see handoff notes §3b for the
benchmarked latency cost of live fitting).

Usage:
    python train_forecast_model.py

Produces:
    models/lgbm_price_model.joblib
    models/lgbm_price_model_meta.json

Exit behavior: exits non-zero on training failure or a failed self-check,
so a CI step that runs this before committing (see the GitHub Actions
workflow) never lets a bad artifact reach production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation

import price_model as pm

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "clean_mandi_prices.csv"
MODEL_PATH = BASE_DIR / "models" / "lgbm_price_model.joblib"
META_PATH = BASE_DIR / "models" / "lgbm_price_model_meta.json"

# How many of the most recent days to hold out entirely for the backtest
# report (never seen during training or early-stopping validation).
BACKTEST_DAYS = 45
# Of the remaining (non-backtest) data, how many of the most recent days
# are used as the early-stopping validation slice.
VAL_DAYS = 30
# Tier 1 #3 guard ("What to Build Next" roadmap): minimum number of new
# rows since the last training run required to bother retraining at all.
# Below this, skip retraining and keep serving the existing model artifact
# instead -- a holiday or a data-source outage can otherwise silently
# retrain on almost nothing, shipping a possibly-worse model with no
# visible signal anything changed. Checked against real data before
# picking this number: even the lightest normal days in this dataset's
# recent history still bring in 50+ new rows (weekday averages are much
# higher, sometimes 700+ after a backfilled gap) -- 20 sits comfortably
# below every normal day while still catching a genuinely near-empty
# fetch. Revisit if the data source's reporting pattern changes.
MIN_NEW_ROWS_TO_RETRAIN = 20


def _previous_total_rows() -> int | None:
    """Row count recorded at the last successful training run (see
    "total_rows_at_train_time" written into meta at the end of main()
    below), or None if there isn't one yet -- the very first run, before
    any meta.json exists. Reads META_PATH directly rather than going
    through pm.load_artifact(), which would also load the (irrelevant
    here, and much heavier) model file itself just to check one number."""
    if not META_PATH.exists():
        return None
    try:
        with open(META_PATH) as f:
            meta = json.load(f)
        return meta.get("total_rows_at_train_time")
    except (json.JSONDecodeError, OSError):
        return None


def _time_based_split(features: pd.DataFrame):
    """Global time-based split: the SAME cutoff dates are applied to every
    crop-mandi series, not a random row shuffle. A random shuffle would let
    the model see e.g. day 500 of a series in training and day 499 in
    validation, leaking future information backwards. Mirrors the honesty
    methodology the project's existing ETS evaluation notebook already
    uses (see PROJECT_STATUS.md)."""
    features = features.dropna(subset=["target_pct_change"])

    # Drop rows whose target is a forward-filled repeat of today's price
    # rather than a genuine next-day Agmarknet report. Mandis here report
    # roughly every other day, so ~53% of the naive daily grid is fabricated
    # flat days — training/evaluating on those trivially rewards predicting
    # 0% change (both model and naive "win" it for free) and drowns out the
    # real price-movement signal. See price_model.build_panel()/add_features()
    # for where target_is_observed comes from.
    n_before = len(features)
    features = features[features["target_is_observed"] == True]  # noqa: E712
    n_after = len(features)
    print(f"  dropped {n_before - n_after} rows with a forward-filled (non-real) target "
          f"({(n_before - n_after) / n_before:.1%}); {n_after} rows remain for split/train/eval")

    max_date = features["date"].max()

    backtest_cutoff = max_date - pd.Timedelta(days=BACKTEST_DAYS)
    val_cutoff = backtest_cutoff - pd.Timedelta(days=VAL_DAYS)

    train = features[features["date"] <= val_cutoff]
    val = features[(features["date"] > val_cutoff) & (features["date"] <= backtest_cutoff)]
    test = features[features["date"] > backtest_cutoff]

    return train, val, test


def _feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    return df[pm.get_feature_columns()]


def _mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def _rmse(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((np.asarray(y_true) - np.asarray(y_pred)) ** 2)))


def backtest_vs_naive(test: pd.DataFrame, model) -> dict:
    """Backtest the model's next-day PRICE prediction (reconstructed from
    its pct-change prediction) against naive persistence P(t+1) = P(t),
    overall and per crop-mandi. Same win-rate-report methodology as the
    existing ETS evaluation, for a direct comparison."""
    X_test = _feature_matrix(test)
    pred_pct_change = model.predict(X_test)
    pred_price = test["price"].values * (1.0 + pred_pct_change)
    actual_price = test["target_next_price"].values
    naive_price = test["price"].values  # naive persistence: tomorrow = today

    overall_mae = _mae(actual_price, pred_price)
    overall_rmse = _rmse(actual_price, pred_price)
    naive_mae = _mae(actual_price, naive_price)
    naive_rmse = _rmse(actual_price, naive_price)

    per_group_wins = 0
    per_group_total = 0
    test = test.copy()
    test["_pred_price"] = pred_price
    for (crop, mandi), group in test.groupby(["crop", "mandi"], observed=True):
        if len(group) < 3:
            continue  # too little test data for this pair to be meaningful
        g_model_mae = _mae(group["target_next_price"], group["_pred_price"])
        g_naive_mae = _mae(group["target_next_price"], group["price"])
        per_group_total += 1
        if g_model_mae < g_naive_mae:
            per_group_wins += 1

    win_rate = per_group_wins / per_group_total if per_group_total else 0.0

    return {
        "model_mae": round(overall_mae, 3),
        "model_rmse": round(overall_rmse, 3),
        "naive_mae": round(naive_mae, 3),
        "naive_rmse": round(naive_rmse, 3),
        "crop_mandi_combinations_tested": per_group_total,
        "crop_mandi_wins_vs_naive": per_group_wins,
        "crop_mandi_win_rate_vs_naive": round(win_rate, 4),
    }


def self_check_serving_matches_training(features: pd.DataFrame, panel: pd.DataFrame, n_samples: int = 200) -> None:
    """Built-in self-check: samples held-out rows, reconstructs their
    features via the serving-time function (_build_serving_row), and
    asserts they match the training-time features (add_features) exactly.
    This is what catches train/serve skew automatically on every retrain
    — it's what caught the off-by-one calendar bug during development (see
    handoff notes §5.1). Tight tolerance on everything except roll_std_*,
    which gets a looser tolerance for pandas-vs-numpy floating point noise
    on near-constant windows.

    Raises AssertionError (crashing the trainer, and therefore the CI job)
    on any mismatch, by design — a silently-skewed model is worse than no
    model.
    """
    feature_cols = pm.get_feature_columns()
    crop_categories = sorted(panel["crop"].unique().tolist())
    mandi_categories = sorted(panel["mandi"].unique().tolist())

    # Eligibility (which rows to sample) is judged on the PRICE-derived
    # feature columns only, not the arrival_* columns. Whenever the
    # underlying data has no arrival history, arrival_* is 100% NaN across
    # every row — requiring it non-NaN here would zero out every row and
    # crash training with "no fully-populated feature rows to sample from."
    # The comparison below still checks the FULL feature set (arrival
    # included) for each sampled row, so a NaN-vs-NaN match on arrival_*
    # genuinely exercises that code path rather than silently skipping it.
    price_feature_cols = [c for c in feature_cols if not c.startswith("arrival_")]
    checkable = features.dropna(subset=price_feature_cols).reset_index(drop=True)
    if len(checkable) == 0:
        raise AssertionError("Self-check found no fully-populated feature rows to sample from.")

    n = min(n_samples, len(checkable))
    sample = checkable.sample(n=n, random_state=42)

    mismatches = []
    for _, row in sample.iterrows():
        crop, mandi, as_of_date = row["crop"], row["mandi"], row["date"]

        # Reconstruct the price history available "as of" this row's date
        # from the panel, exactly as serving would have it.
        series = panel[
            (panel["crop"] == crop) & (panel["mandi"] == mandi) & (panel["date"] <= as_of_date)
        ].sort_values("date")
        price_history = series["price"].values
        arrival_history = series["arrival_qty"].values
        dates = pd.DatetimeIndex(series["date"].values)
        series_start = panel[(panel["crop"] == crop) & (panel["mandi"] == mandi)]["date"].min()
        # Whether as_of_date itself was a real Agmarknet report vs a
        # forward-filled repeat — needed to reconstruct is_observed_today
        # exactly as serving would have known it "as of" this row's date.
        as_of_is_observed = bool(series["is_observed"].iloc[-1])

        served_row = pm._build_serving_row(
            price_history=price_history,
            dates=dates,
            crop=crop,
            mandi=mandi,
            crop_categories=crop_categories,
            mandi_categories=mandi_categories,
            series_start_date=series_start,
            arrival_history=arrival_history,
            is_observed_today=as_of_is_observed,
        ).iloc[0]

        for col in feature_cols:
            if col in ("crop", "mandi"):
                if str(served_row[col]) != str(row[col]):
                    mismatches.append((crop, mandi, as_of_date, col, row[col], served_row[col]))
                continue

            trained_val = float(row[col])
            served_val = float(served_row[col])
            tol = 1e-6 if not col.startswith("roll_std_") else 1e-3
            if not np.isclose(trained_val, served_val, atol=tol, rtol=1e-4, equal_nan=True):
                mismatches.append((crop, mandi, as_of_date, col, trained_val, served_val))

    if mismatches:
        preview = "\n".join(
            f"  {crop}/{mandi} @ {date} col={col}: trained={t} served={s}"
            for crop, mandi, date, col, t, s in mismatches[:10]
        )
        raise AssertionError(
            f"Self-check FAILED: {len(mismatches)}/{n} sampled rows had a "
            f"train/serve feature mismatch. First few:\n{preview}"
        )

    print(f"Self-check passed: {n}/{n} sampled rows match between training and serving features.")


def main() -> int:
    print(f"Loading data from {DATA_PATH} ...")
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])

    # Tier 1 #3 guard -- see MIN_NEW_ROWS_TO_RETRAIN's comment above for
    # the reasoning and how that number was picked. previous_total is
    # None (guard skipped) on the very first-ever run, before any
    # meta.json exists yet.
    previous_total = _previous_total_rows()
    if previous_total is not None:
        new_rows = len(df) - previous_total
        if new_rows < MIN_NEW_ROWS_TO_RETRAIN:
            print(
                f"Only {new_rows} new row(s) since the last training run "
                f"({previous_total} -> {len(df)} total rows), below the "
                f"MIN_NEW_ROWS_TO_RETRAIN threshold of {MIN_NEW_ROWS_TO_RETRAIN}. "
                "Skipping retrain and keeping the existing model artifact -- "
                "this is expected on a light-reporting day (e.g. a holiday) "
                "or during a data-source outage, not an error. "
                "clean_mandi_prices.csv itself will still be committed with "
                "whatever new rows did come in; only the retrain is skipped."
            )
            return 0

    print("Building daily panel across all crop-mandi combinations ...")
    panel = pm.build_panel(df)
    if panel.empty:
        print("ERROR: no crop-mandi combination has enough history to train on.")
        return 1
    print(f"  panel: {len(panel)} rows, "
          f"{panel[['crop', 'mandi']].drop_duplicates().shape[0]} crop-mandi pairs")

    print("Computing features and targets ...")
    features = pm.add_features(panel)

    print("Splitting train/val/test by date (global cutoff, no shuffle) ...")
    train, val, test = _time_based_split(features)
    print(f"  train={len(train)} val={len(val)} test={len(test)}")
    if len(train) == 0 or len(test) == 0:
        print("ERROR: not enough history to form a train and test split. "
              "Need more days of data than BACKTEST_DAYS + VAL_DAYS.")
        return 1

    X_train, y_train = _feature_matrix(train), train["target_pct_change"]
    X_val, y_val = _feature_matrix(val), val["target_pct_change"]

    print("Training LightGBM (objective=regression_l1, i.e. MAE) ...")
    model = LGBMRegressor(
        objective="regression_l1",
        n_estimators=500,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=20,
        reg_alpha=0.1,   # L1
        reg_lambda=0.1,  # L2
        random_state=42,
        verbosity=-1,
    )

    fit_kwargs = dict(
        categorical_feature=["crop", "mandi"],
        callbacks=[log_evaluation(period=0)],
    )
    if len(X_val) > 0:
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            eval_metric="l1",
            callbacks=[early_stopping(stopping_rounds=30, verbose=False), log_evaluation(period=0)],
            categorical_feature=["crop", "mandi"],
        )
    else:
        model.fit(X_train, y_train, **fit_kwargs)

    print("Backtesting vs. naive persistence on held-out test window ...")
    backtest = backtest_vs_naive(test, model)
    for k, v in backtest.items():
        print(f"  {k}: {v}")

    print("Running train/serve skew self-check ...")
    self_check_serving_matches_training(features, panel)

    crop_categories = sorted(panel["crop"].unique().tolist())
    mandi_categories = sorted(panel["mandi"].unique().tolist())
    meta = {
        "crops_seen": crop_categories,
        "mandis_seen": mandi_categories,
        "min_history_for_features": pm.MIN_HISTORY_FOR_FEATURES,
        "lags": pm.LAGS,
        "rolling_windows": pm.ROLLING_WINDOWS,
        "feature_columns": pm.get_feature_columns(),
        "series_start_date": str(panel["date"].min().date()),
        "trained_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "backtest_mae": backtest["model_mae"],
        "naive_backtest_mae": backtest["naive_mae"],
        "backtest_rmse": backtest["model_rmse"],
        "naive_backtest_rmse": backtest["naive_rmse"],
        "crop_mandi_combinations_tested": backtest["crop_mandi_combinations_tested"],
        "crop_mandi_win_rate_vs_naive": backtest["crop_mandi_win_rate_vs_naive"],
        "backtest_days": BACKTEST_DAYS,
        "val_days": VAL_DAYS,
        # Consumed by _previous_total_rows() on the NEXT run, to power the
        # MIN_NEW_ROWS_TO_RETRAIN guard above.
        "total_rows_at_train_time": len(df),
    }

    print(f"Saving artifact to {MODEL_PATH} and {META_PATH} ...")
    pm.save_artifact(model, meta, MODEL_PATH, META_PATH)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
