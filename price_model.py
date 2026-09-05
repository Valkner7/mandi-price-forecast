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

    Expects df with columns: date, crop, mandi, price, and optionally
    arrival_qty. If arrival_qty is absent, the returned panel still gets
    an arrival_qty column (all-NaN) so downstream code never has to
    special-case its absence.

    Price is a "level": forward-filling a gap day with the last known
    price is a reasonable assumption. Arrival is a "flow" (volume traded
    on that specific day): forward-filling would fabricate a repeat
    trading day that didn't happen, so gap days are left NaN instead.
    On days with multiple records, arrival is summed (total volume that
    day), not averaged like price.

    Returns a long-format panel: date, crop, mandi, price, arrival_qty
    (daily; price ffilled, arrival_qty not).
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["crop"] = df["crop"].astype(str).str.strip()
    df["mandi"] = df["mandi"].astype(str).str.strip()
    has_arrival = "arrival_qty" in df.columns
    if has_arrival:
        df["arrival_qty"] = pd.to_numeric(df["arrival_qty"], errors="coerce")
    df = df.dropna(subset=["price"])

    panels = []
    for (crop, mandi), group in df.groupby(["crop", "mandi"]):
        # Multiple records on the same date -> mean, same as load_series().
        price_series = group.groupby("date")["price"].mean().sort_index()
        if len(price_series) < MIN_HISTORY_FOR_FEATURES:
            continue
        price_series = price_series.resample("D").last().ffill()

        if has_arrival:
            # sum(min_count=1): a day with only NaN arrival values stays
            # NaN rather than becoming a fabricated 0.
            arrival_series = group.groupby("date")["arrival_qty"].sum(min_count=1).sort_index()
            arrival_series = arrival_series.reindex(price_series.index)  # NOT ffilled
        else:
            arrival_series = pd.Series(np.nan, index=price_series.index)

        panel = price_series.reset_index()
        panel.columns = ["date", "price"]
        panel["crop"] = crop
        panel["mandi"] = mandi
        panel["arrival_qty"] = arrival_series.values
        panels.append(panel)

    if not panels:
        return pd.DataFrame(columns=["date", "crop", "mandi", "price", "arrival_qty"])

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


def _add_lag_rolling_features(feat: pd.DataFrame, series: pd.Series, prefix: str) -> None:
    """Add lag_{k} and roll_{stat}_{window} columns for one numeric series
    onto `feat` in place, named f"{prefix}lag_{k}" / f"{prefix}roll_{stat}_{window}".
    Shared by price and arrival_qty so the two feature families can't
    drift apart from each other (e.g. one getting a rolling-window fix the
    other doesn't)."""
    for lag in LAGS:
        feat[f"{prefix}lag_{lag}"] = series.shift(lag)

    for window in ROLLING_WINDOWS:
        shifted = series.shift(1)  # never include the current day itself
        feat[f"{prefix}roll_mean_{window}"] = shifted.rolling(window).mean()
        feat[f"{prefix}roll_std_{window}"] = shifted.rolling(window).std()
        feat[f"{prefix}roll_min_{window}"] = shifted.rolling(window).min()
        feat[f"{prefix}roll_max_{window}"] = shifted.rolling(window).max()


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Add lag/rolling/calendar features plus targets to a daily panel
    built by build_panel(). One row per (crop, mandi, date); features are
    computed independently per crop-mandi group (no cross-series leakage).

    Adds the same lag/rolling feature family for arrival_qty as for price
    (arrival_lag_{k}, arrival_roll_{stat}_{window}). If the panel's
    arrival_qty column is all-NaN (true whenever the underlying data has
    no arrival history), these columns are simply all-NaN too — harmless,
    since LightGBM treats NaN as "missing" and never splits on a feature
    that's always missing.

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
        arrival = group["arrival_qty"] if "arrival_qty" in group.columns else pd.Series(np.nan, index=group.index)

        feat = pd.DataFrame(index=group.index)
        feat["date"] = group["date"]
        feat["crop"] = crop
        feat["mandi"] = mandi
        feat["price"] = price

        _add_lag_rolling_features(feat, price, prefix="")
        _add_lag_rolling_features(feat, arrival, prefix="arrival_")

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
    cols += [f"arrival_lag_{lag}" for lag in LAGS]
    for window in ROLLING_WINDOWS:
        cols += [
            f"arrival_roll_mean_{window}",
            f"arrival_roll_std_{window}",
            f"arrival_roll_min_{window}",
            f"arrival_roll_max_{window}",
        ]
    cols += ["day_of_week", "day_of_month", "month", "days_since_start"]
    return cols


def _serving_lag_rolling_row(history: pd.Series, prefix: str) -> dict:
    """Compute lag_{k}/roll_{stat}_{window} values for ONE series at
    serving time (history's LAST element is "today"). Shared by price and
    arrival_qty so serving can't drift between the two the way training's
    _add_lag_rolling_features() can't. See _build_serving_row's docstring
    for the off-by-one reasoning this mirrors.

    Rolling stats use a strict NaN check (window_slice.isna().any()) rather
    than pandas Series.mean()/.std()'s default skipna=True. Training's
    shifted.rolling(window).mean() uses pandas' default min_periods=window,
    which returns NaN if ANY value in the window is NaN. Series.mean()
    defaults to skipna=True and would silently average over only the
    non-NaN values instead — invisible for price (no internal NaNs) but a
    real train/serve skew for arrival_qty, which has NaN gap days by
    design (see build_panel's docstring). Matching training's strict
    behavior here is what keeps the self-check passing once arrival
    history has real gaps.
    """
    row = {}
    for lag in LAGS:
        row[f"{prefix}lag_{lag}"] = (
            float(history.iloc[-(lag + 1)]) if len(history) >= lag + 1 else np.nan
        )

    history_before_today = history.iloc[:-1]
    for window in ROLLING_WINDOWS:
        window_slice = history_before_today.iloc[-window:]
        if len(window_slice) >= window and not window_slice.isna().any():
            row[f"{prefix}roll_mean_{window}"] = float(window_slice.mean())
            row[f"{prefix}roll_std_{window}"] = float(window_slice.std())
            row[f"{prefix}roll_min_{window}"] = float(window_slice.min())
            row[f"{prefix}roll_max_{window}"] = float(window_slice.max())
        else:
            row[f"{prefix}roll_mean_{window}"] = np.nan
            row[f"{prefix}roll_std_{window}"] = np.nan
            row[f"{prefix}roll_min_{window}"] = np.nan
            row[f"{prefix}roll_max_{window}"] = np.nan

    return row


def _build_serving_row(
    price_history: np.ndarray | pd.Series,
    dates: pd.DatetimeIndex,
    crop: str,
    mandi: str,
    crop_categories: list[str],
    mandi_categories: list[str],
    series_start_date: pd.Timestamp,
    arrival_history: np.ndarray | pd.Series | None = None,
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

    `arrival_history` is optional and defaults to None, in which case it's
    treated as all-NaN (same length as price_history) — every existing
    caller that doesn't yet have arrival data needs zero changes.
    """
    price_history = pd.Series(np.asarray(price_history, dtype=float))
    current_date = pd.Timestamp(dates[-1])

    if arrival_history is None:
        arrival_history = pd.Series(np.nan, index=range(len(price_history)))
    else:
        arrival_history = pd.Series(np.asarray(arrival_history, dtype=float))
        if len(arrival_history) != len(price_history):
            raise ValueError(
                f"arrival_history length ({len(arrival_history)}) must match "
                f"price_history length ({len(price_history)})"
            )

    row = _serving_lag_rolling_row(price_history, prefix="")
    row.update(_serving_lag_rolling_row(arrival_history, prefix="arrival_"))

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
    arrival_series: pd.Series | None = None,
) -> list[float]:
    """Recursive multi-day forecast: predict day+1's pct change, apply it
    to get day+1's price, append it to the working history, repeat for
    `horizon` days. Mirrors how ETS.forecast(horizon) already worked in
    app.py, so /predict's response shape doesn't need to change.

    price_series: a pandas Series of daily prices, DatetimeIndex, most
    recent last (same shape as app.py's load_series() output).

    arrival_series: optional, same shape/index convention as price_series.
    Honest limitation: a real arrival figure only ever informs step 1 of
    the recursive forecast. There's no model here for what arrival will be
    on the synthetic future days being predicted (that would be a second,
    separate forecasting model — out of scope), so from step 2 onward the
    working arrival history is padded with NaN, same as if no arrival data
    existed at all. If arrival_series is None entirely, every step behaves
    exactly as before this feature was added.
    """
    crop = crop.strip()
    mandi = mandi.strip()
    crop_categories = meta["crops_seen"]
    mandi_categories = meta["mandis_seen"]
    series_start_date = pd.Timestamp(meta["series_start_date"])

    history = list(price_series.values)
    dates = list(price_series.index)
    arrival_history = list(arrival_series.values) if arrival_series is not None else None
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
            arrival_history=np.array(arrival_history) if arrival_history is not None else None,
        )
        pct_change = float(model.predict(row)[0])
        next_price = history[-1] * (1.0 + pct_change)
        next_date = dates[-1] + pd.Timedelta(days=1)

        forecasts.append(next_price)
        history.append(next_price)
        dates.append(next_date)
        if arrival_history is not None:
            # No model for future arrival: pad with NaN from step 2 onward.
            arrival_history.append(np.nan)

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
