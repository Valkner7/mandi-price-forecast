"""
data_quality.py

Shared price-plausibility filter used by both data-ingestion paths
(fetch_daily_mandi_data.py and update_mandi_prices.py) so a source-side
reporting fault doesn't silently make it into clean_mandi_prices.csv and
from there into the forecasting model's training data.

WHY THIS EXISTS
Investigation (Sep 2026) found the Patti mandi's modal-price feed decaying
smoothly from normal levels down to near-zero (as low as ₹0.08) across all
three of the app's forecastable crops (Potato, Onion, Tomato), starting
~June 2026 and continuing for 248 rows straight through the most recent
data. It's isolated to one mandi across every crop it reports — a
source-side fault in that market's feed, not a real, simultaneous price
crash for three unrelated commodities (real prices for these crops
elsewhere in the dataset run ₹100s–1000s/quintal). Neither ingestion
script previously checked plausibility, only whether a value parsed as a
number at all — so this passed straight into clean_mandi_prices.csv and
from there into the training data.

APPROACH
Flag rows whose price is below a small fraction of that crop's own median
price across the whole dataset. Crop-level median (not crop+mandi) is used
deliberately:
  - it's stable even for a mandi with sparse history, so a market that has
    only ever reported 5 rows for a crop still gets judged against a
    meaningful baseline instead of its own (possibly already-corrupted)
    history;
  - it's robust to the presence of the very fault we're detecting — a
    couple hundred near-zero rows barely move a median computed over
    thousands of rows for the same crop.
This is a floor only. Unusually HIGH prices are already handled at serve
time by the z-score anomaly detector in app.py (detect_price_anomalies) and
deliberately aren't touched here — that check is relative to each
crop-mandi's own volatility and surfaced to users as "worth a second look,"
not silently dropped.
"""

from pathlib import Path

import pandas as pd

DEFAULT_MIN_FRACTION_OF_MEDIAN = 0.05  # a price must be >= 5% of its crop's
                                        # overall median to be considered
                                        # plausible; tune if a real crop
                                        # legitimately has this much spread


def flag_implausible_prices(
    df: pd.DataFrame,
    reference_df: pd.DataFrame | None = None,
    min_fraction_of_median: float = DEFAULT_MIN_FRACTION_OF_MEDIAN,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split df into (clean, quarantined) based on a per-crop price floor.

    reference_df, if given, supplies the crop medians — e.g. the existing
    on-disk file, so a single new day's fetch (which may have too few rows
    per crop to compute its own stable median) is judged against the full
    historical distribution instead. Falls back to df itself if omitted or
    empty. Crops absent from the reference (a brand-new crop's first-ever
    row) have no defined floor and are let through rather than blocked.
    """
    basis = reference_df if reference_df is not None and not reference_df.empty else df
    crop_median = basis.groupby("crop")["price"].median()

    floor = df["crop"].map(crop_median) * min_fraction_of_median
    implausible = df["price"].lt(floor) & floor.notna()

    return df[~implausible].copy(), df[implausible].copy()


def quarantine_path_for(clean_path) -> Path:
    return Path(clean_path).parent / "quarantined_rows.csv"


def append_to_quarantine(quarantined: pd.DataFrame, clean_path) -> None:
    """Append newly-quarantined rows to quarantined_rows.csv (created on
    first use) rather than silently discarding them — keeps an audit trail
    for manual review and lets you notice if a mandi's feed recovers."""
    if quarantined.empty:
        return
    path = quarantine_path_for(clean_path)
    write_header = not path.exists()
    quarantined.to_csv(path, mode="a", header=write_header, index=False)
