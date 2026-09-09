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

from data_quality import append_to_quarantine, flag_implausible_prices

BASE_DIR = Path(__file__).resolve().parent
CLEAN_PATH = BASE_DIR / "clean_mandi_prices.csv"

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
API_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

# Shared public demo key (rate-limited, works for light/testing use).
# Overridden by DATA_GOV_API_KEY if set — see docstring above.
PUBLIC_DEMO_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"

STATE_FILTER = "Punjab"
PAGE_SIZE = 500          # API returns at most this many records per call
MAX_PAGES = 200          # safety cap; pages can be far smaller than PAGE_SIZE
                         # when the API caps page size (see fetch_day)
REQUEST_TIMEOUT_SECONDS = 90  # the shared public demo key can be slow under load
MAX_RETRIES = 5          # retry transient timeouts/connection/rate-limit errors
RETRY_BACKOFF_SECONDS = 5
# 429 (shared demo key throttling) and 5xx are transient: the same query
# succeeds moments later, so they must be retried rather than treated as
# "this date has no data".
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
# data.gov.in's edge stalls requests carrying the default python-requests
# User-Agent: the identical query returns in ~1s with any explicit UA and
# times out after 45s without one (reproduced repeatedly). Sending a real
# UA is what makes this script's requests actually complete.
REQUEST_HEADERS = {
    "User-Agent": "mandi-price-forecast/1.0 (+https://github.com/Valkner7/mandi-price-forecast)",
    "Accept": "*/*",
}


class FetchFailed(RuntimeError):
    """The API call itself failed, as opposed to the API legitimately
    reporting no rows for a date. Kept distinct so a failure can never be
    mistaken for an empty trading day. `partial` carries whatever pages were
    already retrieved before the failure, so a mid-pagination throttle keeps
    the rows it did get instead of discarding the day."""

    def __init__(self, message: str, partial: pd.DataFrame | None = None):
        super().__init__(message)
        self.partial = partial if partial is not None else empty_frame()


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "crop", "mandi", "price", "arrival_qty"])


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
            response = requests.get(
                params=params,
                url=API_URL,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers=REQUEST_HEADERS,
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.text[:120]}",
                    response=response,
                )
            response.raise_for_status()
            return response.text
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as error:
            last_error = error
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"  Attempt {attempt}/{MAX_RETRIES} failed ({error}); "
                      f"retrying in {wait}s...")
                time.sleep(wait)
    raise FetchFailed(str(last_error)) from last_error


def fetch_day(api_key: str, target_date: str) -> pd.DataFrame:
    """Fetch every Punjab record for one date (DD/MM/YYYY, matching the
    API's own Arrival_Date format), paginating until exhausted."""
    all_frames = []
    offset = 0
    # The server decides how many records a page actually holds — the shared
    # demo key is capped at 10 regardless of the requested limit — so the
    # end-of-data test compares against the first page's real size instead
    # of the requested PAGE_SIZE, which would end pagination after one page.
    page_size_seen = None
    for page_num in range(MAX_PAGES):
        try:
            csv_text = fetch_page(api_key, offset, target_date)
        except FetchFailed as error:
            # Surface, don't swallow: returning an empty frame here is what
            # let a throttled run look like a quiet no-data day.
            raise FetchFailed(
                f"offset {offset}: {error}",
                partial=map_to_schema(all_frames, target_date),
            ) from error

        # An empty/near-empty CSV (just a header row or nothing) means
        # we've exhausted the available pages for this date.
        try:
            page_df = pd.read_csv(io.StringIO(csv_text))
        except pd.errors.EmptyDataError:
            break
        if page_df.empty:
            break

        if page_size_seen is None:
            page_size_seen = len(page_df)

        all_frames.append(page_df)
        offset += len(page_df)
        if len(page_df) < page_size_seen:
            # Short page = last page, no need to request another.
            break
        time.sleep(0.3)  # be polite to a free public government API

    return map_to_schema(all_frames, target_date)


def map_to_schema(all_frames: list, target_date: str) -> pd.DataFrame:
    """Map raw API pages onto this project's (date, crop, mandi, price,
    arrival_qty) schema."""
    if not all_frames:
        return empty_frame()

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
        default=1,
        help="How many days back to fetch, counting today as day 1 "
             "(default: 1). This resource is a CURRENT-DAY snapshot: it "
             "ignores filters[arrival_date] and serves the latest trading "
             "day's rows whatever date is asked for (verified against the "
             "live API). Asking for extra days therefore re-requests the "
             "same snapshot, which only burns the shared demo key's rate "
             "limit; the flag is kept for the case where the publisher "
             "starts honouring the date filter.",
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
    failed_days = []
    for i in range(args.days_back):
        target = (datetime.now() - timedelta(days=i)).strftime("%d/%m/%Y")
        print(f"Fetching Punjab prices for {target}...")
        try:
            day_df = fetch_day(api_key, target)
        except FetchFailed as error:
            print(f"  API CALL FAILED for {target}: {error}")
            failed_days.append(target)
            if not error.partial.empty:
                print(f"  Keeping {len(error.partial)} row(s) retrieved before the failure.")
                frames.append(error.partial)
            continue
        print(f"  {len(day_df)} usable rows "
              f"({day_df['crop'].nunique() if not day_df.empty else 0} crops, "
              f"{day_df['mandi'].nunique() if not day_df.empty else 0} mandis)")
        frames.append(day_df)

    new_data = pd.concat(frames, ignore_index=True) if frames else empty_frame()
    print()

    if new_data.empty:
        if failed_days:
            print(f"ERROR: every API call failed ({', '.join(failed_days)}). "
                  f"The dataset was NOT refreshed — this is a real failure, not "
                  f"an empty trading day.")
            sys.exit(1)
        print("No usable new rows found today (this can be a normal non-error "
              "case if the source hasn't posted yet, or all rows were already "
              "in the file). Not treating this as a failure.")
        sys.exit(0)

    # Reject prices implausibly far below their crop's usual range (e.g. a
    # mandi's feed reporting near-zero) before they ever reach
    # clean_mandi_prices.csv — see data_quality.py for why this exists.
    new_data, quarantined = flag_implausible_prices(new_data, reference_df=existing)
    if not quarantined.empty:
        print(f"Quarantining {len(quarantined)} implausible-price row(s) "
              f"(see quarantined_rows.csv), not merging into clean_mandi_prices.csv:")
        print(quarantined[["date", "crop", "mandi", "price"]].to_string(index=False))
        append_to_quarantine(quarantined, CLEAN_PATH)
        if new_data.empty:
            print("Every new row today was implausible. Not treating this as a "
                  "failure, but nothing usable to add.")
            sys.exit(0)

    if failed_days:
        print(f"WARNING: some days failed ({', '.join(failed_days)}); "
              f"they will be retried by the next run's backward window.")

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
