"""
forecast_models.py
-------------------
STATUS: exploratory / superseded, not wired into the production app.

This file predates and is superseded by price_model.py +
train_forecast_model.py, which power the app's live /predict endpoint (a
single global LightGBM model trained offline across every crop-mandi
combination, with the original per-series ETS model in app.py as a
fallback). It's kept around for reference and offline experimentation, not
deleted, but nothing here runs in production.

Why each piece here was superseded, specifically:

  1. Naive Bayes baseline (GaussianNB) — its job (an honest "does this beat
     naive persistence" sanity check) is now covered by
     train_forecast_model.py's own backtest report, which runs
     automatically on every retrain and reports both an aggregate win rate
     and a per-crop-mandi breakdown, rather than needing a separate script
     run by hand.

  2. RNN forecaster (Keras LSTM) — like the ETS model it was meant to beat,
     this is a one-model-per-crop-mandi-series approach: every new
     crop/mandi needs its own model trained from scratch with its own
     cold-start minimum, and it doesn't learn from cross-series patterns.
     That's exactly the scalability problem the global LightGBM model was
     built to solve (see PROJECT_STATUS.md and the forecasting-upgrade
     handoff notes for the full reasoning and the latency/accuracy
     benchmarks that drove the decision). TensorFlow has accordingly been
     dropped from requirements.txt; the `from tensorflow import keras`
     import below is already a guarded, lazy import, so this module still
     works for the Naive Bayes path with TF absent — it only raises if you
     specifically call run_rnn_forecaster() (or run this file without
     --skip-rnn) without TF installed.

Adds two models to the mandi-price-forecast pipeline, on top of the
existing naive persistence baseline and ETS model already in
`mandi_price_forecasting.ipynb`:

  1. Naive Bayes baseline (GaussianNB)
     - Classifies next-day price movement into {down, flat, up}
     - Fast, interpretable "does the model even beat guessing the class
       with the highest prior" sanity check before trusting the RNN.

  2. RNN forecaster (Keras LSTM)
     - Regresses the actual next-day price from a sliding window of
       past prices (+ a few engineered features).
     - This is the "real" model you compare everything else against.

Usage
-----
    python forecast_models.py --crop Potato --mandi Rayya
    python forecast_models.py --crop Onion  --mandi Rayya --seq-len 21 --epochs 60
    python forecast_models.py --crop Potato --mandi Rayya --skip-rnn   # NB only, no TF needed
    # RNN path needs `pip install tensorflow` manually — it's no longer in
    # requirements.txt now that the production app doesn't need it.

Expects `clean_mandi_prices.csv` in the same directory (or pass --csv),
with columns: date, crop, mandi, price  (this matches the repo's schema).
"""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.naive_bayes import GaussianNB
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, mean_squared_error

warnings.filterwarnings("ignore")

RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def load_series(csv_path: str, crop: str, mandi: str) -> pd.DataFrame:
    """Load one crop+mandi series, sorted by date, daily-resampled with
    forward-fill for small gaps (matches the gap-handling approach the
    notebook already uses for the naive/ETS baselines)."""
    df = pd.read_csv(csv_path, parse_dates=["date"])
    sub = df[(df["crop"] == crop) & (df["mandi"] == mandi)].copy()
    if sub.empty:
        raise ValueError(f"No rows for crop={crop!r}, mandi={mandi!r} in {csv_path}")

    sub = sub.sort_values("date").drop_duplicates(subset="date")
    sub = sub.set_index("date").asfreq("D")
    sub["price"] = sub["price"].ffill().bfill()

    if len(sub) < 60:
        raise ValueError(
            f"Only {len(sub)} daily points for {crop}/{mandi} after resampling — "
            "too short to train an RNN or trust a classifier. Pick a crop/mandi "
            "with more history (README recommends Potato or Onion)."
        )
    return sub[["price"]]


# --------------------------------------------------------------------------
# Feature engineering (shared by both models)
# --------------------------------------------------------------------------

def make_features(df: pd.DataFrame, n_lags: int = 7) -> pd.DataFrame:
    """Lag returns, rolling stats, day-of-week — used by the Naive Bayes
    baseline. The RNN uses raw scaled price windows instead (see
    `make_sequences`), since RNNs learn temporal structure themselves."""
    out = df.copy()
    out["return_1d"] = out["price"].pct_change()
    for lag in range(1, n_lags + 1):
        out[f"lag_return_{lag}"] = out["return_1d"].shift(lag)
    out["roll_mean_7"] = out["price"].shift(1).rolling(7).mean()
    out["roll_std_7"] = out["price"].shift(1).rolling(7).std()
    out["dow"] = out.index.dayofweek
    out = out.dropna()
    return out


def make_direction_labels(df: pd.DataFrame, flat_threshold: float = 0.005) -> pd.Series:
    """Next-day direction: -1 down, 0 flat, 1 up. `flat_threshold` is the
    minimum % move (default 0.5%) to count as a real move rather than noise."""
    next_return = df["price"].pct_change().shift(-1)
    labels = pd.Series(0, index=df.index)
    labels[next_return > flat_threshold] = 1
    labels[next_return < -flat_threshold] = -1
    return labels


# --------------------------------------------------------------------------
# Model 1: Naive Bayes baseline (direction classifier)
# --------------------------------------------------------------------------

@dataclass
class NBResult:
    accuracy: float
    f1_macro: float
    mae_price: float
    rmse_price: float
    n_test: int


def run_naive_bayes_baseline(price_df: pd.DataFrame, test_frac: float = 0.2) -> NBResult:
    feats = make_features(price_df)
    labels = make_direction_labels(price_df).reindex(feats.index)
    feats, labels = feats.iloc[:-1], labels.iloc[:-1]  # drop last row (no next-day label)

    feature_cols = [c for c in feats.columns if c not in ("price",)]
    X, y = feats[feature_cols].values, labels.values

    split = int(len(X) * (1 - test_frac))
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    price_test = feats["price"].values[split:]
    actual_next_price = price_df["price"].reindex(feats.index).shift(-1).values[split:]

    clf = GaussianNB()
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    # Turn predicted direction into a point forecast so it's comparable
    # (in price units) to the naive persistence baseline and the RNN:
    # apply the average historical move size for the predicted class.
    class_avg_move = {}
    train_returns = price_df["price"].pct_change().reindex(feats.index[:split]).values
    train_labels = y_train
    for cls in (-1, 0, 1):
        mask = train_labels == cls
        class_avg_move[cls] = np.nanmean(train_returns[mask]) if mask.any() else 0.0

    predicted_moves = np.array([class_avg_move[c] for c in y_pred])
    nb_price_forecast = price_test * (1 + predicted_moves)

    valid = ~np.isnan(actual_next_price)
    return NBResult(
        accuracy=accuracy_score(y_test, y_pred),
        f1_macro=f1_score(y_test, y_pred, average="macro"),
        mae_price=mean_absolute_error(actual_next_price[valid], nb_price_forecast[valid]),
        rmse_price=mean_squared_error(actual_next_price[valid], nb_price_forecast[valid]) ** 0.5,
        n_test=int(valid.sum()),
    )


# --------------------------------------------------------------------------
# Model 2: RNN forecaster (Keras LSTM)
# --------------------------------------------------------------------------

def make_sequences(values: np.ndarray, seq_len: int):
    X, y = [], []
    for i in range(len(values) - seq_len):
        X.append(values[i:i + seq_len])
        y.append(values[i + seq_len])
    return np.array(X), np.array(y)


@dataclass
class RNNResult:
    mae_price: float
    rmse_price: float
    directional_accuracy: float
    n_test: int


def run_rnn_forecaster(
    price_df: pd.DataFrame,
    seq_len: int = 14,
    epochs: int = 50,
    test_frac: float = 0.2,
) -> RNNResult:
    try:
        from sklearn.preprocessing import MinMaxScaler
        from tensorflow import keras
        from tensorflow.keras import layers
    except ImportError as e:
        raise ImportError(
            "RNN model needs TensorFlow. Install with `pip install tensorflow` "
            "(add it to requirements.txt), or re-run with --skip-rnn to only "
            "get the Naive Bayes baseline."
        ) from e

    keras.utils.set_random_seed(RANDOM_STATE)

    prices = price_df["price"].values.astype("float32").reshape(-1, 1)

    split_idx = int(len(prices) * (1 - test_frac))
    train_prices, test_prices = prices[:split_idx], prices[split_idx:]

    # Fit the scaler on train only to avoid leaking test-set info.
    scaler = MinMaxScaler()
    train_scaled = scaler.fit_transform(train_prices)
    test_scaled = scaler.transform(test_prices)

    # Feed the RNN a continuous scaled series (train tail + test) so the
    # first `seq_len` test predictions still have real history behind them.
    full_scaled = np.concatenate([train_scaled, test_scaled])
    X_all, y_all = make_sequences(full_scaled.flatten(), seq_len)

    n_train_seq = len(train_scaled) - seq_len
    X_train, y_train = X_all[:n_train_seq], y_all[:n_train_seq]
    X_test, y_test = X_all[n_train_seq:], y_all[n_train_seq:]

    X_train = X_train.reshape(-1, seq_len, 1)
    X_test = X_test.reshape(-1, seq_len, 1)

    model = keras.Sequential([
        layers.Input(shape=(seq_len, 1)),
        layers.LSTM(32, return_sequences=True),
        layers.LSTM(16),
        layers.Dense(8, activation="relu"),
        layers.Dense(1),
    ])
    model.compile(optimizer="adam", loss="mse")

    early_stop = keras.callbacks.EarlyStopping(
        monitor="loss", patience=8, restore_best_weights=True
    )
    model.fit(
        X_train, y_train,
        epochs=epochs,
        batch_size=16,
        verbose=0,
        callbacks=[early_stop],
    )

    y_pred_scaled = model.predict(X_test, verbose=0).flatten()
    y_pred_price = scaler.inverse_transform(y_pred_scaled.reshape(-1, 1)).flatten()
    y_true_price = scaler.inverse_transform(y_test.reshape(-1, 1)).flatten()

    # Directional accuracy for a fair comparison against the NB baseline.
    prev_price = scaler.inverse_transform(X_test[:, -1, :]).flatten()
    true_dir = np.sign(y_true_price - prev_price)
    pred_dir = np.sign(y_pred_price - prev_price)
    dir_acc = float((true_dir == pred_dir).mean())

    return RNNResult(
        mae_price=mean_absolute_error(y_true_price, y_pred_price),
        rmse_price=mean_squared_error(y_true_price, y_pred_price) ** 0.5,
        directional_accuracy=dir_acc,
        n_test=len(y_test),
    )


# --------------------------------------------------------------------------
# Naive persistence baseline (for reference — same as the notebook's)
# --------------------------------------------------------------------------

def run_naive_persistence(price_df: pd.DataFrame, test_frac: float = 0.2) -> tuple[float, float]:
    prices = price_df["price"].values
    split = int(len(prices) * (1 - test_frac))
    test = prices[split:]
    pred = prices[split - 1:-1]  # P(t+1) = P(t)
    mae = mean_absolute_error(test, pred)
    rmse = mean_squared_error(test, pred) ** 0.5
    return mae, rmse


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Naive Bayes baseline + RNN forecaster")
    parser.add_argument("--csv", default="clean_mandi_prices.csv")
    parser.add_argument("--crop", default="Potato")
    parser.add_argument("--mandi", default="Rayya")
    parser.add_argument("--seq-len", type=int, default=14)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--test-frac", type=float, default=0.2)
    parser.add_argument("--skip-rnn", action="store_true", help="Only run the Naive Bayes baseline")
    args = parser.parse_args()

    print(f"Loading {args.crop} @ {args.mandi} from {args.csv} ...")
    price_df = load_series(args.csv, args.crop, args.mandi)
    print(f"{len(price_df)} daily points after resampling/ffill.\n")

    naive_mae, naive_rmse = run_naive_persistence(price_df, args.test_frac)
    print("== Naive persistence baseline (P(t+1) = P(t)) ==")
    print(f"MAE:  {naive_mae:.2f}")
    print(f"RMSE: {naive_rmse:.2f}\n")

    print("== Naive Bayes baseline (direction classifier -> price) ==")
    nb = run_naive_bayes_baseline(price_df, args.test_frac)
    print(f"Direction accuracy: {nb.accuracy:.3f}")
    print(f"Direction F1 (macro): {nb.f1_macro:.3f}")
    print(f"MAE:  {nb.mae_price:.2f}")
    print(f"RMSE: {nb.rmse_price:.2f}")
    print(f"(n_test={nb.n_test})\n")

    if args.skip_rnn:
        return

    print("== RNN forecaster (LSTM) ==")
    try:
        rnn = run_rnn_forecaster(price_df, args.seq_len, args.epochs, args.test_frac)
        print(f"MAE:  {rnn.mae_price:.2f}")
        print(f"RMSE: {rnn.rmse_price:.2f}")
        print(f"Directional accuracy: {rnn.directional_accuracy:.3f}")
        print(f"(n_test={rnn.n_test})\n")

        print("== Summary (lower MAE/RMSE is better) ==")
        print(f"{'Model':<20}{'MAE':>10}{'RMSE':>10}")
        print(f"{'Naive persistence':<20}{naive_mae:>10.2f}{naive_rmse:>10.2f}")
        print(f"{'Naive Bayes':<20}{nb.mae_price:>10.2f}{nb.rmse_price:>10.2f}")
        print(f"{'RNN (LSTM)':<20}{rnn.mae_price:>10.2f}{rnn.rmse_price:>10.2f}")
    except ImportError as e:
        print(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
