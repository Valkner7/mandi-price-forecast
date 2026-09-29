# Handoff Report: Mandi Setu

**Purpose:** a continuation brief. If a Claude session ends mid-task, paste this
file into a new session so work resumes without re-discovering the codebase, the
roadmap status, or how the user likes to work.

**Last updated:** 29 September 2026. Supersedes the earlier version of this file,
which described a state that has since moved on (Tier 1 to Tier 4 of the roadmap
are now implemented).

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
- The sandbox also lacks the project's dependencies and runs Python 3.12 while
  the repo pins Python 3.14 versions, so the test scripts cannot be run there.
  GitHub Actions is the place they run.

## 1. Repo and deployment state

- Repo: `https://github.com/Valkner7/mandi-price-forecast.git`
- Live app: `https://mandi-price-forecast-1.onrender.com` (it responds; the
  running commit has not been confirmed).
- Last commit pushed from this effort: `bb63fe5` (README fix describing Turso
  storage). It sits on top of `ee6d365` (temporary `/debug-client-ip`).
- Structure: FastAPI, `app.py` (slim entrypoint) plus `routers/predict.py`,
  `alerts.py`, `voice.py`, `status.py`. Turso storage in `db.py`, cost guard in
  `usage_guard.py`, logging in `observability.py`.
- **Not confirmed:** whether the latest Render deploy went live, and whether CI
  is green on the latest push. Ask the user first.

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
exists, and anything on Render's disk was wiped by earlier redeploys. Do not run
`migrate_subscriptions_to_turso.py`. It is dead code and can be deleted.

## 3. What is left, in order

1. **Confirm the latest Render deploy is Live** (Deploys tab).
2. **Real Turso test:** on WhatsApp send `alert me potato rayya 850`, then
   `my alerts`; redeploy; send `my alerts` again. Still listed means Tier 1 #1 is
   proven.
3. **Check CI** on the Actions tab is green for the latest commit.
4. **Rate-limiter client IP.** `app.py` uses `Limiter(key_func=get_remote_address)`
   (the direct connection IP). Behind Render's proxy that may be the proxy's
   address, meaning all users share one bucket (a hypothesis, not confirmed).
   `/debug-client-ip` exists to test this but returned "Not Found" when tried, so
   it was not active. Steps: set `ENABLE_IP_DEBUG=1` in Render's Environment tab,
   wait for the redeploy to go Live, open the endpoint from two networks (note each
   network's public IP), and run
   `curl.exe -s -H "X-Forwarded-For: 1.2.3.4" https://mandi-price-forecast-1.onrender.com/debug-client-ip`
   to see whether a client-supplied value can be spoofed. Then change the limiter
   key, remove the endpoint, and delete the env var, all in one change. Render's
   behaviour here is not reliably documented, so trust the live test.
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
