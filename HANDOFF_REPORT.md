# Handoff Report: Mandi Setu

**Purpose:** a continuation brief. If a Claude session ends mid-task, paste this
file into a new session so work resumes without re-discovering the codebase, the
roadmap status, or how the user likes to work.

**Last updated:** 29 September 2026 (evening). Supersedes the earlier version of
this file, which described a state that has since moved on (Tier 1 to Tier 4 of
the roadmap are now implemented).

**Read Section 0 first. It matters as much as the technical state.**

---

## 0. How this user likes to work

- **Consult before acting.** Propose the next concrete step and wait for a
  go-ahead, then do just that step. Once the user gives a clear green light to
  work through a list ("do whatever is best", "start with the pending work"),
  that is permission to make the individual judgment calls and execute.
- The user is comfortable with technical detail and values being told *why*:
  tradeoffs and uncertainty should be stated plainly, not smoothed over.
- **Check real code and data before implementing.** Thresholds and parameters
  have been chosen by reading the actual dataset and code, not by guessing.
- The user is hands-on: they run PowerShell themselves and paste screenshots of
  terminal or dashboard output. Give copy-pasteable commands.
- **Windows and PowerShell habits that have caused wasted round-trips:**
  - Commands must run from `D:\mandi-price-forecast`. Running from
    `C:\Windows\system32` makes every git command fail with "not a git repository".
  - Replace placeholders such as `<path you found>` with real values. Pasting them
    literally fails.
  - Long commit messages have been cut off by the terminal, leaving a `>>` prompt.
    Use Ctrl+C and keep messages short. Very long pastes get cut off too, so send
    big files in chunks of about 4 KB.
  - Downloads: Chrome on this machine blocks `.py` files (use a `.txt` extension
    and rename). Some downloads have also simply not landed; a `.docx` did.
    Editing a file in place with a PowerShell script worked when downloads did not.
  - Chrome saves duplicate downloads as `name (1).ext`, so a copy command can
    silently use a stale file. Give re-sent files new, unmistakable names and have
    the user verify the content with `Select-String` before copying and committing.
  - The Turso auth token must never be pasted into chat.
- **Claude cannot push to GitHub, and cannot reach Render or Turso from its
  sandbox.** Every production check comes from the user reading Render's dashboard
  or logs and reporting back. The sandbox clone is disposable; always re-clone
  `https://github.com/Valkner7/mandi-price-forecast.git` and trust GitHub as
  ground truth, not the sandbox's git history.
- The sandbox runs Python 3.12 while the repo pins Python 3.14 versions, but it
  can pip install the pinned libraries (numpy 2.5.3 works on 3.12) and run
  `train_forecast_model.py` on a scratch copy of the repo; that was used to
  reproduce a full retrain. `test_scenarios.py` has not been tried there, so CI is
  still where the tests run. The sandbox can read GitHub Actions run pages and
  status badges over github.com (the unauthenticated API rate limit is usually
  exhausted), but not run logs, which need a login. It cannot reach data.gov.in.

## 1. Repo and deployment state

- Repo: `https://github.com/Valkner7/mandi-price-forecast.git`
- Live app: `https://mandi-price-forecast-1.onrender.com`. Render is running
  `41791dd` (Live, deployed 29 Sep at 1:22 PM IST). It has no `ENABLE_IP_DEBUG`
  applied as of the last check.
- Latest commit on `main`: `fdf26a1` (removed the dead Turso migration script). It
  sits on `41791dd` (README env vars), `bb63fe5` and `ee6d365` (temporary
  `/debug-client-ip`).
- Structure: FastAPI, `app.py` (slim entrypoint) plus `routers/predict.py`,
  `alerts.py`, `voice.py`, `status.py`. Turso storage in `db.py`, cost guard in
  `usage_guard.py`, logging in `observability.py`.
- CI (checked 29 Sep from the GitHub run pages): `test-scenarios` is green for
  `41791dd` and `fdf26a1`. `update-mandi-data` is failing, see Section 3 item 3.

## 2. Roadmap status (checked in code, not in production)

| Item | State |
|---|---|
| Tier 1 #1 Subscriptions to Turso | Done in code. Production proof still needed (see Section 3). |
| Tier 1 #2 Tests in CI | Done, report-only. `test-scenarios.yml` also runs `test_explanations.py`. |
| Tier 1 #3 Retrain guard | Done. `MIN_NEW_ROWS_TO_RETRAIN = 20`, skips retraining and keeps the old model. |
| Tier 1 #4 Cost guard | Done. `usage_guard.py` covers both Gemini call sites and gTTS; counts in `/status`. |
| Tier 2 #5 Per-pair accuracy | Done. `_per_pair_accuracy()` in `routers/predict.py`. |
| Tier 2 #6 Directional accuracy | Done. `backtest_directional_accuracy()`. |
| Tier 2 #7 Hindi/Punjabi text review | Sheet built (`Mandi_Setu_Hindi_Punjabi_review.docx`). Waiting on native speakers. |
| Tier 3 SHAP explanations | Done, including ETS fallback, rupee conversion, advisory prompt drivers, `ExplanationPanel.jsx`. |
| Tier 4 logging and status | Done (structured JSON logs, `/status` with model self-test). |
| Tier 4 deferred items | Not started, deliberately: model versioning/rollback, drift monitoring, Postgres migration, API auth beyond `slowapi`. |

The migration of old subscriptions is **not needed**: no local `subscriptions.json`
exists, and anything on Render's disk was wiped by earlier redeploys.
`migrate_subscriptions_to_turso.py` and its helper `insert_subscription_full()`
were deleted in `fdf26a1`.

## 3. What is left, in order

1. **Deploy `fdf26a1`.** Render is running `41791dd` (deployed manually on
   29 Sep at 1:22 PM IST; the deploy page showed Live). `fdf26a1` (dead-code
   removal) is pushed and CI-green but not deployed. Use Manual Deploy, Deploy
   latest commit. Send the WhatsApp alert from item 2 first, so this deploy
   doubles as the redeploy in that test.
2. **Real Turso test:** on WhatsApp send `alert me potato rayya 850`, then
   `my alerts`; redeploy; send `my alerts` again. Still listed means Tier 1 #1 is
   proven.
3. **`update-mandi-data` workflow is failing.** Runs 58 to 65 (25 to 28 Sep) all
   failed. The last bot commit was 24 Sep 23:49 UTC, so `clean_mandi_prices.csv`
   ends at 2026-09-24 and the model has not retrained since. Earlier history is
   patchy too (runs 41 to 53 failed, 54 to 57 passed), which matches the gaps in
   the data (5 to 9 Sep, 10 to 23 Sep). Nothing alerted on it; it went unnoticed.
   Ruled out so far: the Sep 25 commits `0cfd59d` and `984f330` touched
   `train_forecast_model.py`, and `984f330` also edited the fetch script, but the
   fetch diff is comment-only plus one unused constant, `pyflakes` finds no
   undefined names, and a forced retrain in the sandbox with the exact
   `constraints.txt` versions completes (self-check passed, artifact written).
   Leading hypothesis, NOT confirmed: the data.gov.in fetch step fails
   (`fetch_daily_mandi_data.py` exits 1 only when every API call fails), plausibly
   the shared demo key. Run logs need a GitHub login, so Claude cannot read them.
   Needed from the user: open the latest failed run's `update-data` job, name the
   red step and paste its last ~15 log lines, and say whether a `DATA_GOV_API_KEY`
   repo secret exists (the workflow already uses it; a free key can be registered
   at data.gov.in). No code change until the failing step is known.
4. **Rate-limiter client IP (hypothesis revised).** `app.py` uses
   `Limiter(key_func=get_remote_address)`. Earlier assumption: uvicorn trusts
   `X-Forwarded-For` only from 127.0.0.1 by default, so on Render every user would
   share the proxy's IP. New evidence, Render log at 1:24:33 PM:
   `34.83.207.104:0 - "GET / HTTP/1.1"`. In uvicorn a client port of 0 appears
   when the address was taken from an `X-Forwarded-For` entry, so the client
   address is probably already rewritten from the header. The `render.yaml` start
   command (`uvicorn app:app --host 0.0.0.0 --port $PORT`) has no proxy flags, so
   something else enables this (a `FORWARDED_ALLOW_IPS` variable or a loopback
   proxy; unchecked). Still unknown: which entry is used. Leftmost is
   client-supplied and spoofable (limit bypass); rightmost is already safe.
   `/debug-client-ip` returned "Not Found" on 29 Sep because the running deploy
   had no `ENABLE_IP_DEBUG`. Steps: in Render's Environment tab confirm
   `ENABLE_IP_DEBUG=1` (exact) and look for `FORWARDED_ALLOW_IPS`; deploy; open the
   endpoint from two networks (the user's home IP was 152.56.69.129) and run
   `curl.exe -s -H "X-Forwarded-For: 1.2.3.4" https://mandi-price-forecast-1.onrender.com/debug-client-ip`.
   Compare `x_forwarded_for` with `direct_client_host` (which shows the rewritten
   value). Rightmost picked: only delete the endpoint and the env var. Leftmost
   picked: key the limiter on the rightmost entry (or configure trusted proxies),
   remove the endpoint and delete the env var, all in one change. The uvicorn
   analysis used version 0.54.0 in the sandbox; Render installs its own (uvicorn
   is not pinned in `constraints.txt`), so trust the live test.
5. **Hindi/Punjabi corrections:** when reviewers reply, update
   `_FALLBACK_TREND_WORDS`, `_FALLBACK_TEMPLATES`, `_FALLBACK_DATA_NOTE` and
   `FORECAST_CONFIDENCE_NOTE` in `routers/predict.py`, keeping placeholders such
   as `{mandi}` intact. Also ask how English crop and mandi names read inside
   Hindi and Punjabi sentences and aloud. `voice_extraction.py` contains Hindi and
   Punjabi keyword matching for understanding farmers; it is not farmer-facing text
   and needs a different check.
6. **Revisit usage limits with real traffic.** Defaults (300 Gemini and 1000 gTTS
   calls a day, via `GEMINI_DAILY_CALL_LIMIT` and `GTTS_DAILY_CALL_LIMIT`) are
   guesses. Counters are in memory and reset on restart.
7. Optional: make CI a required check (GitHub Settings, Branches); add
   `GEMINI_API_KEY` as a repo secret to exercise advisory/audio in CI.

## 4. Design decisions already made (do not relitigate without reason)

- **Turso schema:** one row per subscription with real columns, so the old
  merge-on-save workaround became single-row atomic updates.
- **Package:** `libsql` (not the deprecated `libsql-client`, and not the separate
  `turso_serverless` product). Re-check https://docs.turso.tech/sdk/python if
  revisiting; this ecosystem moves.
- **No shared connection:** each `db.py` function opens a fresh connection, because
  it is undocumented whether one connection is safe across worker threads and the
  alert scheduler thread.
- **Startup hardening:** `init_db()` has a 10-second timeout, and startup steps run
  on background threads so that a Turso problem cannot stop the app from serving.
  This came from an incident on 17 September 2026 where a deploy hung about 19
  minutes with no log output. The exact root cause was never identified.
- **Retrain guard:** skip retraining and keep the old model (a stale but known-good
  model beats a fresh possibly-bad one). The threshold of 20 was chosen from the
  real day-over-day row counts, which never dropped below 50 on normal days.
- **Cost guard:** hard cutoff, not a soft alert. Gemini falls back to the template
  advisory; gTTS returns a clear 503 pointing to the text `/advisory` endpoint.
  Gemini has a billing risk, while gTTS (the free unofficial endpoint) has a
  rate-limiting or blocking risk, so they were treated differently.
- **CI is report-only**, because the user pushes straight to `main` with no branch
  protection. `test_scenarios.py` was rewritten to return a real exit code: advisory
  failures count only if `GEMINI_API_KEY` is set, and TTS failures are reported but
  not fatal.
- **SHAP:** the model predicts a percentage change, so contributions are converted
  to rupees (`contribution x today's price`) before display.

## 5. Instructions for the next Claude session

1. Re-clone the repo fresh and ask the user for current status: the Render deploy
   result, the CI result, and whether the Hindi/Punjabi reviewers have replied.
2. Follow Section 0 from the very first message.
3. Before touching `db.py`, `app.py` startup, `usage_guard.py` or the fallback
   strings, re-read the relevant part of Section 4.
4. Do not claim anything works in production unless the user has reported it from
   Render or the live app.
