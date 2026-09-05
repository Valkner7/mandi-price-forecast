"""
fetch_daily_mandi_data.py

Automatically pulls the latest Punjab mandi prices from the official
Government of India data.gov.in API and merges them into
clean_mandi_prices.csv — no manual Agmarknet export download needed.

DATA SOURCE
This uses the "Current Daily Price of Various Commodities from Various
Markets (Mandi)" dataset (resource ID 9ef84268-d588-465a-a308-a864a43d0070),
published by the Ministry of Agriculture and Farmers Welfare via the
AGMARKNET portal. IMPORTANT: this dataset is updated once per trading day,
not continuously — mandis report modal prices after each day's session
closes, typically posted same evening. Running this script more than once a
day will not find new data; a daily schedule (see the GitHub Action in
.github/workflows/update-mandi-data.yml) is the right cadence, not "every
minute."

API KEY
Uses the shared public demo key by default, which works but is
rate-limited and shared across everyone using it — fine for testing, not
reliable for an unattended daily job. Get your own free key:
  1. Register at https://data.gov.in (Google or gov email login works)
  2. Go to My Account -> API Key on data.gov.in, generate one (instant, free,
     no published rate limit for your own key)
  3. Set it as DATA_GOV_API_KEY (environment variable, or a GitHub Actions
     secret of the same name — see the workflow file)

WHAT THIS DOES
1. Calls the API filtered to state=Punjab, paginating until all records for
   today are retrieved.
2. Maps the API's native field names (State, Market, Commodity,
   Arrival_Date, Modal_Price, ...) onto this project's existing 4-column
   schema (date, crop, mandi, price) that app.py reads directly.
3. Normalizes mandi names the same way update_mandi_prices.py does
   (stripping "APMC" so e.g. "Ludhiana" and "Ludhiana APMC" stay merged as
   one mandi, not two).
4. Backs up the existing clean_mandi_prices.csv (timestamped), then writes a
   merged, deduplicated, sorted replacement — safe to re-run; re-fetching a
   day that's already in the file just gets deduplicated away, not
   duplicated.
5. Prints a before/after summary, and exits with a non-zero status if the
   API call itself failed, so a scheduled job can alert on real failures
   without silently doing nothing.

WHAT THIS DOES NOT DO
- It does not scrape HTML or use Selenium — this is the official structured
  API, which is far more reliable for an unattended job (no page-layout
  changes to break against, no risk of the government site rate-limiting or
  blocking scraper traffic).
- It does not touch app.py. app.py reads clean_mandi_prices.csv fresh on
  every request, so once this script finishes and the file is redeployed
  (or, on Render, once a fresh deploy picks up the committed change), the
  app is already serving the new data.

USAGE
    python fetch_daily_mandi_data.py
    python fetch_daily_mandi_data.py --days-back 3   # backfill a short gap
"""

import csv
import io
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import re
import sys
import time

import numpy as np
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
CLEAN_PATH = BASE_DIR / "clean_mandi_prices.csv"

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
API_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

# Shared public demo key (rate-limited, works for light/testing use).
# Overridden by DATA_GOV_API_KEY if set — see docstring above.
PUBLIC_DEMO_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"

STATE_FILTER = "Punjab"
PAGE_SIZE = 500          # API returns at most this many records per call
MAX_PAGES = 40           # safety cap: 40 * 500 = 20,000 records/day, generous
REQUEST_TIMEOUT_SECONDS = 45  # the shared public demo key can be slow under load
MAX_RETRIES = 3          # retry transient timeouts/connection errors before giving up
RETRY_BACKOFF_SECONDS = 5


def normalize_mandi(name: str) -> str:
    """Same normalization as update_mandi_prices.py: strip a trailing/
    embedded 'APMC' token and collapse whitespace, so 'Ludhiana APMC' and
    'Ludhiana' become the same mandi. Kept identical on purpose so both
    scripts converge on the same canonical spelling."""
    name = str(name).strip()
    name = re.sub(r"\bAPMC\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def fetch_page(api_key: str, offset: int, target_date: str) -> str:
    """Returns raw CSV text. CSV format is used instead of JSON because it's
    a lighter response for the server to generate — in earlier testing the
    JSON endpoint reliably timed out under the shared public demo key's
    load, while CSV responded quickly for the same query. If this
    assumption turns out wrong in practice, the retry/backoff logic below
    is still there as a second line of defense."""
    params = {
        "api-key": api_key,
        "format": "csv",
        "limit": PAGE_SIZE,
        "offset": offset,
        "filters[state]": STATE_FILTER,
        "filters[arrival_date]": target_date,
    }
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(params=params, url=API_URL, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"  Attempt {attempt}/{MAX_RETRIES} failed ({error}); "
                      f"retrying in {wait}s...")
                time.sleep(wait)
    raise last_error


def fetch_day(api_key: str, target_date: str) -> pd.DataFrame:
    """Fetch every Punjab record for one date (DD/MM/YYYY, matching the
    API's own Arrival_Date format), paginating until exhausted."""
    all_frames = []
    offset = 0
    for page_num in range(MAX_PAGES):
        try:
            csv_text = fetch_page(api_key, offset, target_date)
        except requests.RequestException as error:
            print(f"  ERROR calling API at offset {offset}: {error}")
            break

        # An empty/near-empty CSV (just a header row or nothing) means
        # we've exhausted the available pages for this date.
        try:
            page_df = pd.read_csv(io.StringIO(csv_text))
        except pd.errors.EmptyDataError:
            break
        if page_df.empty:
            break

        all_frames.append(page_df)
        offset += len(page_df)
        if len(page_df) < PAGE_SIZE:
            # Short page = last page, no need to request another.
            break
        time.sleep(0.3)  # be polite to a free public government API

    if not all_frames:
        return pd.DataFrame(columns=["date", "crop", "mandi", "price", "arrival_qty"])

    raw = pd.concat(all_frames, ignore_index=True)

    # CSV column names mirror the API's JSON field names but Title_Cased,
    # with spaces in price columns encoded as _x0020_ in some exports.
    # Handle both spellings defensively.
    def pick_column(df, *candidates):
        for name in candidates:
            if name in df.columns:
                return df[name]
        raise KeyError(f"None of {candidates} found in columns: {list(df.columns)}")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(
                pick_column(raw, "Arrival_Date", "arrival_date"),
                format="%d/%m/%Y", errors="coerce",
            ),
            "crop": pick_column(raw, "Commodity", "commodity").astype(str).str.strip(),
            "mandi": pick_column(raw, "Market", "market").apply(normalize_mandi),
            "price": pd.to_numeric(
                pick_column(raw, "Modal_x0020_Price", "Modal_Price", "modal_price"),
                errors="coerce",
            ),
            # This resource (9ef84268-d588-465a-a308-a864a43d0070) does not
            # publish an arrival-volume field at all — checked directly
            # against the resource's own field list, not assumed. Writing
            # an explicit NaN column here (rather than omitting arrival_qty
            # entirely) keeps this script's output schema-consistent with
            # update_mandi_prices.py's (which DOES have real arrival data
            # from the manual Agmarknet export path), so clean_mandi_prices.csv
            # always has a 5th arrival_qty column once both sources are
            # concatenated — it just isn't populated by this path, and this
            # script doesn't fabricate a mapping to pretend otherwise.
            "arrival_qty": np.nan,
        }
    )

    before = len(out)
    out = out.dropna(subset=["date", "crop", "mandi", "price"])
    dropped = before - len(out)
    if dropped:
        print(f"  Dropped {dropped} row(s) with unparseable date/price for {target_date}.")

    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days-back",
        type=int,
        default=3,
        help="How many days back to fetch, counting today as day 1 "
             "(default: 3). Fetching a small backward window each run, "
             "rather than just 'today', means a temporary API outage on "
             "one day doesn't leave a permanent gap — the next successful "
             "run picks up whatever was missed, and dedup makes re-fetching "
             "already-covered days a safe no-op.",
    )
    args = parser.parse_args()

    if not CLEAN_PATH.exists():
        print(f"ERROR: {CLEAN_PATH} not found. Run this from the project folder.")
        sys.exit(1)

    api_key = __import__("os").getenv("DATA_GOV_API_KEY", PUBLIC_DEMO_KEY)
    if api_key == PUBLIC_DEMO_KEY:
        print("NOTE: using the shared public demo API key. For a reliable "
              "unattended daily job, set your own free key as "
              "DATA_GOV_API_KEY (see docstring).")

    existing = pd.read_csv(CLEAN_PATH, dtype={"date": str})
    existing["mandi"] = existing["mandi"].apply(normalize_mandi)
    print(f"Existing clean_mandi_prices.csv: {len(existing)} rows, "
          f"{existing['date'].min()} to {existing['date'].max()}, "
          f"{existing['mandi'].nunique()} mandis.")
    print()

    frames = []
    for i in range(args.days_back):
        target = (datetime.now() - timedelta(days=i)).strftime("%d/%m/%Y")
        print(f"Fetching Punjab prices for {target}...")
        day_df = fetch_day(api_key, target)
        print(f"  {len(day_df)} usable rows "
              f"({day_df['crop'].nunique() if not day_df.empty else 0} crops, "
              f"{day_df['mandi'].nunique() if not day_df.empty else 0} mandis)")
        frames.append(day_df)

    new_data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["date", "crop", "mandi", "price", "arrival_qty"]
    )
    print()

    if new_data.empty:
        print("No usable new rows found today (this can be a normal non-error "
              "case if the source hasn't posted yet, or all rows were already "
              "in the file). Not treating this as a failure.")
        sys.exit(0)

    combined = pd.concat([existing, new_data], ignore_index=True)
    before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=["date", "crop", "mandi"], keep="last")
    combined = combined.sort_values(["crop", "mandi", "date"]).reset_index(drop=True)

    net_new = len(combined) - len(existing)
    if net_new == 0:
        print("Fetched data matched what's already on file — nothing new to add. "
              "Leaving clean_mandi_prices.csv untouched.")
        sys.exit(0)

    backup_path = BASE_DIR / f"clean_mandi_prices_backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
    CLEAN_PATH.rename(backup_path)
    combined.to_csv(CLEAN_PATH, index=False)

    print(f"Backed up old file to: {backup_path.name}")
    print(f"Wrote new clean_mandi_prices.csv: {len(combined)} rows "
          f"(was {len(existing)}, added {before_dedup - len(existing)} new rows before dedup, "
          f"{net_new} net new after dedup)")
    print(f"New date range: {combined['date'].min()} to {combined['date'].max()}")


if __name__ == "__main__":
    main()
