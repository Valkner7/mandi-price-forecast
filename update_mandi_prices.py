"""
update_mandi_prices.py

Merges one or more raw Agmarknet CSV exports — the kind you download by
hand from https://agmarknet.gov.in ("Daily Price Arrival Report...csv") —
into clean_mandi_prices.csv.

HOW THIS DIFFERS FROM fetch_daily_mandi_data.py
fetch_daily_mandi_data.py pulls incrementally from the data.gov.in API on
a schedule (see .github/workflows/update-mandi-data.yml) — no manual step,
runs daily. This script is for the other case: you (or a teammate) went to
the Agmarknet website, manually filtered/exported a date range or a crop
you don't have yet, and downloaded a CSV like:

    raw_agmarknet/potato_raw.csv
    Daily_Price_Arrival_Report-07-11-2025_to_29-08-2026_for_Punjab.csv

This script takes files in that raw export shape and folds them into the
same clean_mandi_prices.csv the app reads — same normalization, same
backup-then-merge-then-dedupe safety as fetch_daily_mandi_data.py, so it's
safe to re-run on a file you've already imported (it becomes a no-op).

RAW FILE SHAPE (Agmarknet's export format)
Row 1 is a title row (blank cells + a report title) — skipped.
Row 2 is the real header:
    State/UT, District, Market, Commodity Group, Commodity, Variety,
    Grade, Min Price, Max Price, Modal Price, Price Unit,
    Arrival Quantity, Arrival Unit, Arrival Date
Arrival Date is DD-MM-YYYY (hyphens) — note this is a different separator
than the API's DD/MM/YYYY (slashes) that fetch_daily_mandi_data.py parses;
each script's own parsing matches its own source, that's expected.

USAGE
    # Import every raw export sitting in raw_agmarknet/ (default):
    python update_mandi_prices.py

    # Import specific file(s):
    python update_mandi_prices.py raw_agmarknet/potato_raw.csv
    python update_mandi_prices.py raw_agmarknet/*.csv path/to/other_export.csv
"""

import argparse
import re
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
CLEAN_PATH = BASE_DIR / "clean_mandi_prices.csv"
DEFAULT_RAW_DIR = BASE_DIR / "raw_agmarknet"


def normalize_mandi(name: str) -> str:
    """Same normalization as fetch_daily_mandi_data.py: strip a trailing/
    embedded 'APMC' token and collapse whitespace, so 'Ludhiana APMC' and
    'Ludhiana' become the same mandi. Kept identical on purpose so both
    scripts converge on the same canonical spelling."""
    name = str(name).strip()
    name = re.sub(r"\bAPMC\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def pick_column(df, *candidates):
    for name in candidates:
        if name in df.columns:
            return df[name]
    raise KeyError(f"None of {candidates} found in columns: {list(df.columns)}")


def load_raw_export(path: Path) -> pd.DataFrame:
    """Parse one raw Agmarknet export into the app's 4-column schema."""
    # Row 1 is a title row (mostly blank cells), row 2 is the real header —
    # skip the title so pandas picks up the real column names.
    raw = pd.read_csv(path, skiprows=1)

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(
                pick_column(raw, "Arrival Date"),
                format="%d-%m-%Y", errors="coerce",
            ),
            "crop": pick_column(raw, "Commodity").astype(str).str.strip(),
            "mandi": pick_column(raw, "Market").apply(normalize_mandi),
            # Agmarknet's manual export formats prices with thousands
            # separators (e.g. "3,000.00") — strip commas before parsing,
            # or pandas silently reads every such value as NaN and this
            # script would wrongly drop the majority of real, valid rows.
            "price": pd.to_numeric(
                pick_column(raw, "Modal Price").astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            ),
        }
    )

    before = len(out)
    out = out.dropna(subset=["date", "crop", "mandi", "price"])
    dropped = before - len(out)
    if dropped:
        print(f"  Dropped {dropped} row(s) with unparseable date/price in {path.name}.")

    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out


def resolve_input_files(args_files: list[str]) -> list[Path]:
    if args_files:
        paths = [Path(f) for f in args_files]
        missing = [p for p in paths if not p.exists()]
        if missing:
            print(f"ERROR: file(s) not found: {', '.join(str(m) for m in missing)}")
            sys.exit(1)
        return paths

    if not DEFAULT_RAW_DIR.exists():
        print(f"ERROR: no files given and default folder {DEFAULT_RAW_DIR} doesn't exist.")
        print("Usage: python update_mandi_prices.py <file1.csv> [file2.csv ...]")
        sys.exit(1)

    found = sorted(DEFAULT_RAW_DIR.glob("*.csv"))
    if not found:
        print(f"No .csv files found in {DEFAULT_RAW_DIR}. Nothing to do.")
        sys.exit(0)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "files", nargs="*",
        help="Raw Agmarknet export CSV(s) to import. Defaults to every "
             "*.csv in raw_agmarknet/ if none are given.",
    )
    args = parser.parse_args()

    if not CLEAN_PATH.exists():
        print(f"ERROR: {CLEAN_PATH} not found. Run this from the project folder.")
        sys.exit(1)

    input_paths = resolve_input_files(args.files)
    print(f"Importing {len(input_paths)} raw file(s): {', '.join(p.name for p in input_paths)}")
    print()

    frames = []
    for path in input_paths:
        print(f"Reading {path}...")
        try:
            df = load_raw_export(path)
        except Exception as error:
            print(f"  ERROR parsing {path.name}: {error}")
            continue
        print(f"  {len(df)} usable rows "
              f"({df['crop'].nunique()} crop(s), {df['mandi'].nunique()} mandi(s), "
              f"{df['date'].min()} to {df['date'].max()})" if len(df) else "  0 usable rows")
        frames.append(df)

    new_data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["date", "crop", "mandi", "price"]
    )
    print()

    if new_data.empty:
        print("No usable rows found in the given file(s). Nothing to merge.")
        sys.exit(0)

    existing = pd.read_csv(CLEAN_PATH, dtype={"date": str})
    existing["mandi"] = existing["mandi"].apply(normalize_mandi)
    print(f"Existing clean_mandi_prices.csv: {len(existing)} rows, "
          f"{existing['date'].min()} to {existing['date'].max()}, "
          f"{existing['mandi'].nunique()} mandis.")

    combined = pd.concat([existing, new_data], ignore_index=True)
    before_dedup = len(combined)
    # keep="last" so if the same date/crop/mandi appears in both the
    # existing file and a freshly re-imported raw export, the newly
    # imported row wins — makes re-running this on an updated manual
    # export a safe way to correct a value, not just a no-op.
    combined = combined.drop_duplicates(subset=["date", "crop", "mandi"], keep="last")
    combined = combined.sort_values(["crop", "mandi", "date"]).reset_index(drop=True)

    net_new = len(combined) - len(existing)
    if net_new == 0:
        print()
        print("Every row in the given file(s) was already present in clean_mandi_prices.csv "
              "(same date/crop/mandi). Nothing new to add — leaving the file untouched.")
        sys.exit(0)

    backup_path = BASE_DIR / f"clean_mandi_prices_backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
    CLEAN_PATH.rename(backup_path)
    combined.to_csv(CLEAN_PATH, index=False)

    print()
    print(f"Backed up old file to: {backup_path.name}")
    print(f"Wrote new clean_mandi_prices.csv: {len(combined)} rows "
          f"(was {len(existing)}, {before_dedup - len(existing)} new rows before dedup, "
          f"{net_new} net new after dedup)")
    print(f"New date range: {combined['date'].min()} to {combined['date'].max()}")


if __name__ == "__main__":
    main()
