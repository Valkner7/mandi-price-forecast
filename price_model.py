"""
price_model.py — single source of truth for feature engineering.

Used identically by train_forecast_model.py (offline trainer) and app.py
(live serving) so the two can't drift apart ("train/serve skew"). If you
change a feature here, both training and serving pick it up automatically.

Architecture recap (see PROJECT_STATUS.md / handoff notes for the full
reasoning): one global LightGBM model, trained offline across every
crop-mandi combination at once (crop/mandi as categorical features),
predicting *percentage* price change one day ahead. Serving loads the
pretrained artifact (cached) and builds a single feature row per request —
no per-request model fitting, unlike the old per-series ETS path.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

# --- Feature configuration --------------------------------------------------
# Shared by add_features() (training) and _build_serving_row() (serving).
# Changing these requires retraining (train_forecast_model.py) before the
# new feature set can be served — the artifact's meta.json records what it
# was trained with so a mismatch can be caught rather than silently ignored.
LAGS = [1, 2, 3, 5, 7, 14]
ROLLING_WINDOWS = [7, 14, 30]

# Below this many daily points, the longest lag/rolling-window features
# would be NaN or built on too little data to mean anything. Mirrors the
# MIN_POINTS=30 threshold app.py's load_series() already uses for ETS,
# +1 because a lag/rolling feature also consumes one row as "the present".
MIN_HISTORY_FOR_FEATURES = 31


def build_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Resample every crop-mandi series in df to daily frequency with
    forward-fill, across the whole dataset at once. Mirrors app.py's
    load_series() gap-handling (same resample("D").last().ffill() logic)
    so training sees the same kind of series serving will see.

    Expects df with columns: date, crop, mandi, price.
    Returns a long-format panel: date, crop, mandi, price (daily, ffilled).
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["crop"] = df["crop"].astype(str).str.strip()
    df["mandi"] = df["mandi"].astype(str).str.strip()
    df = df.dropna(subset=["price"])

    panels = []
    for (crop, mandi), group in df.groupby(["crop", "mandi"]):
        # Multiple records on the same date -> mean, same as load_series().
        series = group.groupby("date")["price"].mean().sort_index()
        if len(series) < MIN_HISTORY_FOR_FEATURES:
            continue
        series = series.resample("D").last().ffill()
        panel = series.reset_index()
        panel.columns = ["date", "price"]
        panel["crop"] = crop
        panel["mandi"] = mandi
        panels.append(panel)

    if not panels:
        return pd.DataFrame(columns=["date", "crop", "mandi", "price"])

    return pd.concat(panels, ignore_index=True)


def _calendar_features(dates: pd.Series) -> pd.DataFrame:
    """Calendar features computed for the given dates (the *current known
    day*, not the day being predicted — see price_model self-check /
    handoff notes §5.1 for why that distinction matters)."""
    dates = pd.to_datetime(dates)
    start = dates.min()
    return pd.DataFrame({
        "day_of_week": dates.dt.dayofweek,
        "day_of_month": dates.dt.day,
        "month": dates.dt.month,
        "days_since_start": (dates - start).dt.days,
    })


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Add lag/rolling/calendar features plus targets to a daily panel
    built by build_panel(). One row per (crop, mandi, date); features are
    computed independently per crop-mandi group (no cross-series leakage).

    Targets:
      - target_next_price:      raw price at t+1 (kept for reference/debug)
      - target_pct_change:      (price[t+1] - price[t]) / price[t] — the
        actual training target. See handoff notes §5.3 for why: a raw-price
        target let the model do worse than naive persistence, an absolute
        rupee-delta target let high-priced crops dominate a shared global
        target scale, and percentage change fixes both.
    """
    panel = panel.sort_values(["crop", "mandi", "date"]).reset_index(drop=True)
    out_frames = []

    for (crop, mandi), group in panel.groupby(["crop", "mandi"], sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        price = group["price"]

        feat = pd.DataFrame(index=group.index)
        feat["date"] = group["date"]
        feat["crop"] = crop
        feat["mandi"] = mandi
        feat["price"] = price

        for lag in LAGS:
            feat[f"lag_{lag}"] = price.shift(lag)

        for window in ROLLING_WINDOWS:
            shifted = price.shift(1)  # never include the current day itself
            feat[f"roll_mean_{window}"] = shifted.rolling(window).mean()
            feat[f"roll_std_{window}"] = shifted.rolling(window).std()
            feat[f"roll_min_{window}"] = shifted.rolling(window).min()
            feat[f"roll_max_{window}"] = shifted.rolling(window).max()

        cal = _calendar_features(group["date"])
        feat = pd.concat([feat, cal], axis=1)

        # Targets: the actual next day.
        next_price = price.shift(-1)
        feat["target_next_price"] = next_price
        feat["target_pct_change"] = (next_price - price) / price.replace(0, np.nan)

        out_frames.append(feat)

    result = pd.concat(out_frames, ignore_index=True)
    result["crop"] = result["crop"].astype("category")
    result["mandi"] = result["mandi"].astype("category")
    return result


def get_feature_columns() -> list[str]:
    """The exact, ordered list of feature columns the model is trained
    and served on. Both train_forecast_model.py and _build_serving_row()
    call this instead of hardcoding the list, so they can't drift apart."""
    cols = ["crop", "mandi"]
    cols += [f"lag_{lag}" for lag in LAGS]
    for window in ROLLING_WINDOWS:
        cols += [
            f"roll_mean_{window}",
            f"roll_std_{window}",
            f"roll_min_{window}",
            f"roll_max_{window}",
        ]
    cols += ["day_of_week", "day_of_month", "month", "days_since_start"]
    return cols


def _build_serving_row(
    price_history: np.ndarray | pd.Series,
    dates: pd.DatetimeIndex,
    crop: str,
    mandi: str,
    crop_categories: list[str],
    mandi_categories: list[str],
    series_start_date: pd.Timestamp,
) -> pd.DataFrame:
    """Build ONE feature row at serving time from a plain price history
    array (most-recent price last) and its matching dates. Must stay
    field-for-field identical to what add_features() would compute for
    the last row of an equivalent panel — this is the train/serve-skew
    risk point, checked automatically by train_forecast_model.py's
    self-check on every retrain.

    `current_date` is the last *known* day (the day we're forecasting
    FROM), not the day being predicted — an earlier version of this
    function used current_date + 1 here by mistake; see handoff notes
    §5.1. Calendar features must describe the day whose lag/rolling
    features we're building, i.e. "today", matching add_features()'s
    per-row semantics exactly.
    """
    price_history = pd.Series(np.asarray(price_history, dtype=float))
    current_date = pd.Timestamp(dates[-1])

    # price_history's LAST element is "today" (the current known day, i.e.
    # the same row add_features() would be building this feature set for).
    # lag_k in add_features() is price.shift(k), so for "today"'s row it's
    # the price k days BEFORE today — price_history.iloc[-(k+1)], not
    # iloc[-k] (which would be today itself for k=1). Getting this backward
    # was the original off-by-one bug caught by the self-check (handoff
    # notes §5.1's sibling bug, same root cause in the lag/rolling logic).
    row = {}
    for lag in LAGS:
        row[f"lag_{lag}"] = (
            float(price_history.iloc[-(lag + 1)]) if len(price_history) >= lag + 1 else np.nan
        )

    for window in ROLLING_WINDOWS:
        # add_features() computes rolling stats on shifted = price.shift(1),
        # so "today"'s rolling window covers the `window` days strictly
        # BEFORE today, excluding today itself. Slice off the last element
        # (today) before taking the trailing window.
        history_before_today = price_history.iloc[:-1]
        window_slice = history_before_today.iloc[-window:]
        if len(window_slice) >= window:
            row[f"roll_mean_{window}"] = float(window_slice.mean())
            row[f"roll_std_{window}"] = float(window_slice.std())
            row[f"roll_min_{window}"] = float(window_slice.min())
            row[f"roll_max_{window}"] = float(window_slice.max())
        else:
            row[f"roll_mean_{window}"] = np.nan
            row[f"roll_std_{window}"] = np.nan
            row[f"roll_min_{window}"] = np.nan
            row[f"roll_max_{window}"] = np.nan

    row["day_of_week"] = int(current_date.dayofweek)
    row["day_of_month"] = int(current_date.day)
    row["month"] = int(current_date.month)
    row["days_since_start"] = int((current_date - series_start_date).days)

    df_row = pd.DataFrame([row])
    # Categorical dtype must be assigned on the constructed column directly
    # (not via `pd.Categorical([crop], categories=...)[0]`, which collapses
    # back to a plain string and loses the dtype — see handoff notes §5.2).
    # An unseen crop/mandi naturally becomes NaN here, which LightGBM treats
    # as "missing" and still predicts from the other features.
    df_row["crop"] = pd.Categorical([crop], categories=crop_categories)
    df_row["mandi"] = pd.Categorical([mandi], categories=mandi_categories)

    return df_row[get_feature_columns()]


def forecast_recursive(
    model,
    meta: dict,
    price_series: pd.Series,
    crop: str,
    mandi: str,
    horizon: int = 7,
) -> list[float]:
    """Recursive multi-day forecast: predict day+1's pct change, apply it
    to get day+1's price, append it to the working history, repeat for
    `horizon` days. Mirrors how ETS.forecast(horizon) already worked in
    app.py, so /predict's response shape doesn't need to change.

    price_series: a pandas Series of daily prices, DatetimeIndex, most
    recent last (same shape as app.py's load_series() output).
    """
    crop = crop.strip()
    mandi = mandi.strip()
    crop_categories = meta["crops_seen"]
    mandi_categories = meta["mandis_seen"]
    series_start_date = pd.Timestamp(meta["series_start_date"])

    history = list(price_series.values)
    dates = list(price_series.index)
    forecasts = []

    for _ in range(horizon):
        row = _build_serving_row(
            price_history=np.array(history),
            dates=pd.DatetimeIndex(dates),
            crop=crop,
            mandi=mandi,
            crop_categories=crop_categories,
            mandi_categories=mandi_categories,
            series_start_date=series_start_date,
        )
        pct_change = float(model.predict(row)[0])
        next_price = history[-1] * (1.0 + pct_change)
        next_date = dates[-1] + pd.Timedelta(days=1)

        forecasts.append(next_price)
        history.append(next_price)
        dates.append(next_date)

    return forecasts


# --- Artifact I/O ------------------------------------------------------------

def save_artifact(model, meta: dict, model_path: Path, meta_path: Path) -> None:
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)


def load_artifact(model_path: Path, meta_path: Path):
    model = joblib.load(model_path)
    with open(meta_path) as f:
        meta = json.load(f)
    return model, meta
