# Handoff Report: Mandi Setu — Roadmap Implementation

**Purpose of this document:** This is a continuation brief. If the current
Claude session hits a length/usage limit mid-task, paste this file (or its
path) into a new session so work can resume without re-discovering the
codebase, the roadmap, the current incident, or the user's working
preferences from scratch.

**Read Section 0 first — it covers how this user likes to work, which
matters as much as the technical state.**

---

## 0. How this user likes to work (read this before doing anything)

- **The user has explicitly asked to be consulted before every action** —
  before running a read-only investigation, before writing or editing any
  file, before running any command. Don't batch up multiple steps and
  present them as a fait accompli; propose the next concrete step, wait
  for an explicit go-ahead, then do just that step. That said, once they
  give a clear green light to move through a list of pending items ("do
  whatever is best," "complete the pending work one by one"), that IS
  permission to make the individual judgment calls along the way and just
  execute — re-litigating every micro-decision after that point would be
  ignoring what they just said. Use judgment about which mode you're in.
- They're comfortable with technical detail and clearly value being told
  *why*, not just *what* — design tradeoffs, uncertainty, and reasoning
  should be surfaced explicitly rather than smoothed over.
- When something is genuinely uncertain (e.g. an SDK/package choice that
  may have changed since training data, or a production failure with more
  than one plausible cause), say so plainly and either verify via search
  or ask the user to check, rather than guessing silently.
- **Before implementing anything, check real project data/code rather
  than guessing at parameters.** E.g.: a row-count threshold was picked
  by actually querying the dataset's real day-over-day counts, not by
  assuming a round number; whether test_scenarios.py could even function
  as a real CI gate was discovered by reading it, not assumed from its
  name. This user has caught guessed values before — keep verifying.
- They are hands-on: they run PowerShell commands themselves, paste
  screenshots of terminal/dashboard output for debugging, and want
  copy-pasteable commands rather than vague instructions.
- **Their Chrome install blocks downloading `.py` files** (an
  organization policy, extension-based). Workaround that's confirmed
  working: give files a `.txt` extension when presenting them, and tell
  the user to copy/rename into place. Don't rediscover this the hard way
  again.
- **Filename collisions silently break `Copy-Item`.** When re-sending a
  file with the same name as an earlier download, Chrome saves it as
  `name (1).ext` instead of overwriting — `Copy-Item "D:\name.ext" ...`
  then runs without error but copies the STALE original. Always give
  re-sent files new, unmistakable names (e.g. `db_fix.txt`, not
  `db.txt`), and always have the user verify new content actually landed
  (`Select-String -Path ... -Pattern "<something only in the new
  version>"`) before copying into the repo and committing — this has
  already caused one wasted round-trip, don't repeat it.
- The user works from a Windows machine (PowerShell), with the repo cloned
  locally in addition to the read-only sandbox clone Claude works from —
  **Claude cannot push to GitHub directly** (no network access to github.com's
  write side / no credentials) and **cannot reach Render or Turso from its
  sandbox at all** (network allowlist doesn't include either). Every
  verification of whether something actually works in production has come
  from the user checking Render's dashboard/logs directly and reporting
  back — Claude cannot confirm this itself.

## 1. Repo & deployment state (as of this report)

- Repo: `https://github.com/Valkner7/mandi-price-forecast.git`
- Live app: `https://mandi-price-forecast-1.onrender.com`
- Framework: FastAPI, split into `app.py` (slim entrypoint) +
  `routers/predict.py`, `routers/alerts.py`, `routers/voice.py`. (Fully
  complete from an earlier session — see Section 7, archived history.)
- **Pushed to `main` and confirmed on GitHub:** commit `1611a4d`
  ("Fail fast on Turso connection timeout; don't let it crash the whole
  app"), which followed `19d5c89` ("Move subscriptions storage from local
  JSON to Turso"). **Whether `1611a4d` actually deployed successfully on
  Render was NOT confirmed as of this report being written** — the last
  known state was "In progress" (~1m27s into the build) with no result
  reported back yet. This is the single most important thing to check
  first on resume — see Section 3.
- Sandbox note: the Claude sandbox this report was written from has NOT
  committed anything to git locally, and was cloned once at the start of
  this whole effort — its working directory reflects every edit made
  across the whole session cumulatively, but has no meaningful git
  history of its own. Don't trust `git log` in the sandbox for anything;
  trust the user's own repo/GitHub as ground truth.

## 2. The overall task: implementing the "What to Build Next" roadmap

The user shared a prioritization document titled **"Mandi Setu — What to
Build Next, Ranked by Real-World Impact."** It is not checked into the
repo as a file — it only exists in the conversation that produced this
report — so it's reproduced in full below for continuity.

<details>
<summary>Full roadmap doc (click to expand)</summary>

### Tier 1 — Fix what's currently broken (do first, low effort, high stakes)

| # | Change | Why it's Tier 1 |
|---|---|---|
| 1 | Subscriptions → SQLite (or better) | `subscriptions.json` resets on every Render redeploy. A farmer who signs up for a price alert can silently stop receiving it with zero notice. |
| 2 | Put `test_scenarios.py` into CI (GitHub Actions) | The tests already exist — they just don't run automatically. Should land before any new logic (including XAI) is added. |
| 3 | Minimum-new-rows retrain guard | Without this, a holiday or data-source outage silently retrains the model on near-nothing, shipping a worse model with no signal anything changed. One `if` statement. |
| 4 | Daily cost guard on Gemini/gTTS calls | Currently unbounded in aggregate. Cheap insurance against a surprise bill once usage grows past demo scale. |

### Tier 2 — Increase trust for the specific farmer using it right now

| # | Change | Why it matters more than it looks |
|---|---|---|
| 5 | Per-crop/per-mandi accuracy in `/predict`'s confidence block | The app currently shows one aggregate number across 223 pairs. `backtest_vs_naive()` already computes per-pair accuracy internally — it just isn't surfaced. Likely the single highest-impact-per-hour change in the whole list (verify this claim by actually reading the function before promising it's a small change). |
| 6 | Directional accuracy (rising/falling/stable precision & recall) | Farmers care about direction more than rupee-level MAE. Cheap to compute from data already available. |
| 7 | Native-speaker review of Hindi/Punjabi fallback advisory text | One afternoon of work; wrong text actively damages trust with target users. |

### Tier 3 — Full Explainable AI (SHAP) pipeline

Worth building, but after Tiers 1–2 — it's polish on a model Tier 2 already
makes more trustworthy for less effort.

| Step | What it adds |
|---|---|
| `explain_serving_row()` — exact Tree SHAP for day+1 | Per-prediction "why" breakdown |
| Bucketing 44 raw features into ~6 human buckets | Makes SHAP output usable by a non-technical farmer at all |
| ETS fallback explanation (level/trend, not SHAP) | Keeps the app honest when the fallback model is used |
| Feeding grounded "top drivers" into the Gemini advisory prompt | Cheapest, most direct way SHAP output reaches the farmer (most will read/hear advisory text, not a bar chart) |
| Frontend `ExplanationPanel.jsx` | Visual bar-chart breakdown; smaller reach (dashboard users only) |

**Required before shipping any of Tier 3:** the model's target is a
*percentage* price change, not rupees. Raw SHAP contributions come out in
fractional units (e.g. `0.008`) — displaying them as rupees requires an
explicit `contribution × today's price` conversion, or the panel shows
numbers that look like currency but aren't.

### Tier 4 — Correctly deprioritized (real, but not yet)

- Model versioning / rollback registry
- Drift monitoring (predicted vs. actual error over time)
- Migrating off the CSV to Postgres/TimescaleDB
- API authentication / rate-limit tiers beyond current `slowapi` limits
- Structured logging & observability dashboards *(exception: worth doing
  right after Tier 1–2, since you can't verify those fixes worked in
  production without it)*

</details>

## 3. Status: Tier 1, item #1 (Subscriptions → Turso) — CODE DONE, LATEST DEPLOY UNCONFIRMED

### What's live vs. what's pending, as of this report

- Commits `19d5c89` then `1611a4d` are both pushed to `main` on GitHub —
  confirmed via the user's own `git push` output.
- `19d5c89`'s deploy **failed** (19m04s timeout — see below for the full
  story). Render auto-rolled-back; the OLD pre-Turso code was live for a
  while as a result.
- `1611a4d` (the fail-fast timeout + non-fatal-startup fix, built
  specifically in response to that failure) was pushed and a new deploy
  was triggered. **Last observed status: "In progress," ~1m27s in.
  Whether it succeeded, or failed fast with a new specific error, is not
  yet known as of this report.** This is the first thing to check on
  resume.

### Design decisions made along the way (don't relitigate without reason)

- **"Option B" schema** (one row per subscription, real columns) over
  "Option A" (one JSON blob in a single row) — so the old JSON file's
  "merge-on-save" concurrency workaround could be deleted entirely
  (replaced with atomic single-row `UPDATE`s), not just relocated into a
  different storage system.
- **Python package: `libsql`, not `libsql-client`/`libsql_client`.** The
  latter is deprecated by Turso. There's also a *separate* newer product,
  "Turso Database" (beta, different package: `turso_serverless`) — does
  not apply here, the user confirmed via their dashboard this is a
  standard/classic Turso Cloud (libSQL-based) database. Re-check
  https://docs.turso.tech/sdk/python if revisiting — this ecosystem has
  been actively shifting and training-data knowledge here is likely stale.
- **No shared/cached connection object** — each `db.py` function opens a
  fresh connection per call, since it's undocumented whether one `libsql`
  connection is safe to share across FastAPI's worker threads and the
  optional background alert-scheduler thread.
- **`db.init_db()` has a hard 10-second timeout**, run in a background
  thread it doesn't wait for on timeout (Python can't forcibly cancel a
  blocking call), because the FIRST deploy attempt hung silently for ~14
  minutes with zero log output, which turned out to matter a lot — see
  below.
- **A Turso failure at startup is now non-fatal to the whole app**
  (`app.py`'s `_init_subscriptions_db_startup` wrapper catches and logs
  instead of raising) — nothing except the alerts feature actually
  depends on the subscriptions database, so a Turso problem shouldn't be
  able to take down predictions/dashboard/voice too.

### The incident, for context if it recurs

`19d5c89`'s deploy failed with "Timed out" after 19m04s. The deploy log
showed `uvicorn` launching, then **total silence for ~14 minutes** — not
even uvicorn's own near-instant "Started server process" line — before
Render's own port-scan gave up. Ruled out: a malformed `TURSO_DATABASE_URL`
(user confirmed the value, `libsql://mandi-subscriptions-valkner7.aws-ap-south-1.turso.io`,
is correctly formed). Leading theory, not fully confirmed: `db.init_db()`
hung indefinitely on the Turso connection attempt instead of erroring,
and because FastAPI's lifespan startup can't finish (so the app never
starts accepting ANY connections, including Render's health check) until
every `@app.on_event("startup")` handler returns, one hung hook took the
entire app down with it. The `1611a4d` fix (timeout + non-fatal wrapper)
directly targets this. **The actual root cause of why the connection
attempt hung — bad/expired auth token vs. network issue vs. something
else — was never conclusively identified**, because there was no fast,
specific error to look at. If `1611a4d` also fails, whatever error it
produces THIS time is the first real diagnostic data point — read it
carefully rather than guessing further.

### What's NOT yet confirmed (do this first if picking up here)

1. **Whether `1611a4d` actually deployed successfully, or what error it
   produced if not.** Ask the user for the current Render status before
   doing anything else related to Turso.
2. **The real functional test still hasn't happened** even if the deploy
   succeeds — create one alert over WhatsApp, confirm via "my alerts,"
   trigger another redeploy, check "my alerts" again. Surviving the
   redeploy is the actual proof Tier 1 #1 is fixed.
3. **Whether there was ever any real subscriber data in the old
   `subscriptions.json` on Render to migrate** — never confirmed, user
   doesn't have Render shell access. `migrate_subscriptions_to_turso.py`
   is written and safe either way (no-ops if there's nothing there), but
   hasn't been run.
4. `ISSUES_FIXED.md` still describes the old JSON-file approach and
   hasn't been updated. Offered twice, not yet answered either way — ask
   again, don't just do it.
5. Nothing Turso/Render-related has ever been tested from Claude's
   sandbox — no network path there. All verification has come from the
   user's own dashboard access and screenshots.

## 4. Status: Tier 1, items #2, #3, #4 — CODE DONE, NOT YET PUSHED

Built in one batch while waiting on the `1611a4d` deploy result above, after
the user explicitly delegated all three open design questions ("do
whatever is best for my project"). **None of this has been handed to the
user as files yet, let alone pushed** — that's the immediate next step if
picking up here mid-task.

### Tier 1 #2 — CI for test_scenarios.py

- **Discovery that changed the plan:** `test_scenarios.py` as originally
  written is a latency/diagnostics script, not a real pass/fail test —
  it never used `assert` or a non-zero exit code, so wiring it into CI
  as-is would always show green regardless of what actually happened.
  Fixed as part of this: `main()` now returns an exit code, and
  `__main__` does `sys.exit(main())`.
- **Failure classification logic** (in `test_scenarios.py`'s `main()`):
  extraction/predict-stage failures always count as real (neither depends
  on `GEMINI_API_KEY` or an external network call); an advisory-stage
  failure only counts as real if `GEMINI_API_KEY` was actually set (unset
  is the script's own documented, expected reason for that stage to be
  skipped); tts-stage failures are reported but don't fail the run on
  their own (gTTS depends on an external network call the script doesn't
  control — a flaky external service shouldn't look identical to a code
  regression in CI).
- **New workflow:** `.github/workflows/test-scenarios.yml`. Runs on push
  to `main` and on PRs. **Report-only, not blocking** — chosen because
  the user currently pushes straight to `main` with no branch protection
  rules, so "gate merges" would require setting up a PR workflow first,
  which is a bigger change than "add CI." Flipping it to a required check
  later is a GitHub Settings → Branches toggle, not a code change.
  `GEMINI_API_KEY` is an optional secret for this workflow (without it,
  advisory/tts stages are skipped, not faked, and that's not treated as
  a failure).

### Tier 1 #3 — minimum-new-rows retrain guard

- **Important prior discovery:** `train_forecast_model.py` ALREADY exits
  non-zero on a training/self-check failure (train/serve skew check) —
  this guard is additive to that, not a replacement.
- **Decision: skip retrain and keep the old model**, not retrain-anyway-
  but-flag. Reasoning: a model retrained on too little data can be
  actively bad, and a flag alone doesn't stop a bad model from actually
  serving wrong prices in the meantime — stale-but-known-good beats
  fresh-but-possibly-broken for something farmers make real decisions on.
- **Threshold picked from real data, not guessed:** queried
  `clean_mandi_prices.csv`'s actual day-over-day row counts before
  choosing a number. Recent history showed even the lightest normal days
  bringing in 50+ new rows (backfill-catchup days sometimes 700+).
  Landed on `MIN_NEW_ROWS_TO_RETRAIN = 20` — comfortably below every
  normal day observed, while still catching a genuinely near-empty fetch.
  Configurable as a module constant, not an env var (matches the
  existing `BACKTEST_DAYS`/`VAL_DAYS` pattern in the same file).
- **Mechanism:** the trained artifact's `meta.json` now also stores
  `total_rows_at_train_time`. Each run compares the freshly-loaded
  dataset's row count against that stored value; below threshold, prints
  a clear message and returns 0 (success, not a failure — this is
  correct behavior, not an error) without touching the model file.
  `clean_mandi_prices.csv` itself still gets committed by the surrounding
  workflow either way; only the retrain step is skipped. First run after
  this change always retrains regardless of row count (existing
  `meta.json` predates this field, so there's nothing to compare against
  yet) — expected, not a bug.

### Tier 1 #4 — daily cost guard on Gemini/gTTS

- **Important framing correction, worth remembering:** Gemini and gTTS
  have DIFFERENT risk profiles, even though the roadmap doc groups them
  under one line item. Gemini is a real, metered, billed API — the risk
  is a surprise bill. gTTS (the `gtts` Python package) is the free/
  unofficial Google Translate TTS endpoint, NOT a paid API — there's no
  direct $ cost; the risk is Google rate-limiting or blocking the calling
  IP if hit too hard, which would be a "sudden outage" of the voice
  feature specifically (matches the roadmap doc's own "outage" phrasing).
- **Decision: hard cutoff for both**, not a soft alert — reasoning: a
  guard that only logs and keeps calling anyway doesn't actually guard
  against anything; it just tells you about the bill after it's already
  happened.
- **New module: `usage_guard.py`.** In-memory-only daily counters
  (deliberate, documented tradeoff — resets on process restart, which on
  Render's free tier can happen more than once a day due to spin-down,
  not just at UTC midnight; accepted because this is low-stakes state,
  unlike subscriptions.json's old problem of silently losing a farmer's
  alert, so it doesn't need Turso or any other persistent store). Limits
  configurable via `GEMINI_DAILY_CALL_LIMIT` / `GTTS_DAILY_CALL_LIMIT`
  env vars, defaulting to 300 / 1000 — **these defaults are guesses for
  demo-scale usage, not measured real numbers; revisit once there's
  actual traffic data.**
- **Wired into THREE call sites in `routers/predict.py`** (there are two
  separate Gemini call sites, not one — easy to miss):
  - `generate_advisory()` — over-limit falls back to the existing
    `build_fallback_advisory()` plain-template path (already existed for
    Gemini timeouts/errors; the guard just triggers it for a different
    reason too).
  - `generate_compare_advisory()` — a second, near-identical Gemini call
    site for the mandi-comparison feature. Over-limit falls back to
    `comparison_data["summary"]`, matching that function's own existing
    fallback pattern.
  - `voice_advisory()` — gTTS guard. No fallback audio exists (there's no
    substitute for "audio" other than "no audio"), so over-limit raises a
    clear 503 pointing the caller at the text-based `/advisory` endpoint
    instead, matching the honest-failure style the function's own
    retry-exhausted path already uses (a 502).
- **Not yet done:** no status/debug endpoint exposes `usage_snapshot()`
  anywhere yet — it exists in `usage_guard.py` but nothing calls it. Low
  priority, but would be a natural small follow-up if the user wants to
  actually see today's counts without digging through logs.

## 5. What's left (in priority order)

1. **Get the Tier 1 #2/3/4 files actually handed to the user and pushed**
   (see Section 4 — code exists, nothing's been delivered yet as of this
   report).
2. **Resolve the `1611a4d` deploy outcome** (Section 3) if still unknown.
3. **Run the real end-to-end Turso test** (WhatsApp alert survives a
   redeploy) once #2 is confirmed working.
4. **Tier 2, item #5** — surface `backtest_vs_naive()`'s existing
   per-pair accuracy in `/predict`'s response. Flagged as probably the
   best ROI in the whole roadmap, but this claim was never actually
   verified by reading `routers/predict.py`'s `backtest_vs_naive()` in
   detail (it's referenced/called in `train_forecast_model.py`, worth
   confirming its exact per-pair output shape before promising this is
   simple to surface).
5. **Tier 2, items #6–7**, then **Tier 3** (SHAP — remember the
   rupee-conversion gotcha in Section 2), then **Tier 4** (deliberately
   deferred, see roadmap doc).

## 6. Instructions for the next Claude session

1. Re-clone or confirm `/home/claude/mandi-price-forecast` still exists;
   if not: `git clone https://github.com/Valkner7/mandi-price-forecast.git`.
   Note this sandbox clone is **not necessarily in sync with the real
   GitHub repo** if the user has pushed changes since — ask, or re-clone
   fresh, rather than assuming, and don't trust the sandbox's own `git
   log` (see Section 1's note).
2. **Ask the user for current status first** — specifically, the
   `1611a4d` deploy outcome (Section 3) and whether the Tier 1 #2/3/4
   files (Section 4) have been delivered/pushed yet. Don't assume this
   report's "as of this report" state still holds.
3. Follow Section 0's working-style notes from the very first message —
   especially the `.txt`-extension download workaround and the filename-
   collision gotcha. Don't rediscover either the hard way again.
4. Before touching `db.py`/`app.py` (Turso) or `usage_guard.py`/
   `routers/predict.py` (cost guard) again, re-read the relevant part of
   Sections 3–4 in full — the reasoning behind each design choice, and
   what's already been ruled out or decided, isn't obvious from the code
   alone, and re-litigating already-settled decisions wastes the user's
   time.

---

## 7. Archived: original app.py monolith refactor (COMPLETE, historical only)

An earlier session split the original 3,021-line `app.py` monolith into
`routers/predict.py`, `routers/alerts.py`, `routers/voice.py`, plus
`static/sw.js` and `templates/voice_test.html` for previously-embedded
inline JS/HTML. This is fully done and live — confirmed by the current
repo structure. No further action needed here; this section is kept only
as historical record of how the codebase got its current shape.
