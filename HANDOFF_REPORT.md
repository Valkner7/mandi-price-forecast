# Handoff Report: Mandi Setu (v4)

**Purpose:** a continuation brief. If a Claude session ends mid-task, paste this
file into a new session so work resumes without re-discovering the codebase, the
roadmap status, or how the user likes to work.

**Last updated:** 30 September 2026 (afternoon IST). Supersedes v3 and the
`HANDOFF_REPORT.md` committed earlier.

**What changed since v3 (all pushed to `main` unless marked pending):**

1. `5c2742c` `update-mandi-data` now commits the fetched CSV BEFORE retraining, then
   commits the model in a second step. It also has a `concurrency` group and
   `git pull --rebase` before each push. This limits data loss if the retrain fails; it
   does NOT fix the current failures (root cause still unknown, Section 4 item 1).
2. `263638b` `render.yaml` gained the Turso keys and the optional env vars.
3. `3b71590` `matplotlib` and `jupyter` removed from `requirements.txt`. `/status` was
   `ok` after the deploy (30 Sep 08:45 UTC), so the removal was safe.
4. `0026595` `# noqa: F401` on the deliberate re-exports in `app.py`; f-string cleanup;
   four stale `PROJECT_STATUS.md` references fixed.
5. `f932695` quarantine file no longer gets the same rows appended twice.
6. `12407dc` a `db.mark_fired` failure no longer aborts the alert sweep.
7. PENDING (this commit batch): data-age note now appears after 7 days instead of 90
   (`DATA_NOTE_STALE_DAYS`); stale alerts state the data date; service worker is
   network-first for `/predict` and never caches error responses.

Correction to v3 finding 5: the `app.py:224` imports are deliberate re-exports
(`routers/alerts.py`, `routers/voice.py` and `test_scenarios.py` import them from
`app`); do NOT delete them. `import app` in `test_explanations.py` is an ordering
import. Only the f-string was real cleanup.

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
  reproduce a full retrain. `test_scenarios.py` also runs there (exit 0; the
  advisory and TTS stages skip without a Gemini key). The sandbox can `git clone` the
  repo over https. The sandbox can read GitHub Actions run pages and
  status badges over github.com (the unauthenticated API rate limit is usually
  exhausted), but not run logs, which need a login. It cannot reach data.gov.in.
- **More lessons from 29 to 30 Sep 2026:**
  - Start EVERY command block with `cd D:\mandi-price-forecast`. A block pasted from
    `C:\Users\adity` failed with "not a git repository" (git printed its whole help
    text for `git diff --stat`).
  - A generated file did not land in `C:\Users\adity\Downloads`; the user found it at
    `D:\` and gave the path. Offer a `Get-ChildItem -Recurse -Filter "name*"` search
    early, and always verify content with `Select-String` before copying.
  - `curl.exe -s` prints nothing while a cold start is in progress. Use
    `curl.exe -sS -i --max-time 120 <url>` so a hang, an error and a 404 look different.
  - "LF will be replaced by CRLF" warnings on commit are harmless (no line-ending churn
    showed up in the diffs).
  - The user is in IST (GMT+5:30); GitHub cron times and Render logs mix UTC and IST.
    State the timezone whenever you quote a time.
  - The user asked to leave THEIR manual tests until the end and have Claude finish
    everything committable first. Do that: batch the tests into one checklist.

---

## 1. Snapshot (29/30 Sep 2026)

| Item | State | How it was learned |
|---|---|---|
| Repo | `https://github.com/Valkner7/mandi-price-forecast.git`, branch `main`, HEAD `12407dc` plus the pending batch above | git |
| CI `test-scenarios` | Green on `6c12f45`, `fdf26a1`, `41791dd` (report-only) | GitHub run pages |
| CI `check-alerts`, `keep-alive` | Green (latest runs) | GitHub run pages |
| CI `update-mandi-data` | **FAILING.** Runs 58 to 65 (25 to 28 Sep) all failed. Latest run seen: 36505298119 (28 Sep, on `ee6d365`). Run #67 was a re-run on the OLD workflow (`6c12f45`) started 30 Sep; its result was not yet known when this was written | GitHub run pages; logs need a login |
| Render | Responds. The running process started 29 Sep 18:53:22 UTC. `41791dd` was confirmed Live at 1:22 PM IST; whether a later commit is deployed is **unconfirmed** | screenshots, `/status` |
| `/status` at 18:58 UTC | `ok`; model artifact loaded; self-test ok; Python 3.14.3; packages equal `constraints.txt`; Gemini 0/300 and gTTS 0/1000 calls; no errors | user's `curl.exe` |
| Data | `clean_mandi_prices.csv`, 53,660 rows, latest date 2026-09-24 | `/status` |
| Missing days (last 30) | 15: 6, 7, 8 Sep and 11 to 22 Sep. **Cannot be backfilled**: the source serves a current-day snapshot only (fetch script help text and a comment in `routers/status.py`) | `/status`, code |
| Model | LightGBM global model, trained 2026-09-24T21:20:34Z. `has_per_pair_accuracy` and `has_directional_accuracy` are both `false` in production | `/status` |
| `/status` turns "degraded" | When `days_stale > 7` (`MAX_DATA_AGE_DAYS = 7`). With no new data that happens on 2 Oct 2026 | `routers/status.py` |
| Debug endpoint | `/debug-client-ip` returned 404 at 18:57 UTC, about 4.5 minutes after the process started, so `ENABLE_IP_DEBUG=1` was not in that process's environment | user's `curl.exe` |
| Edge | Cloudflare is in front of Render (`Server: cloudflare`, `CF-RAY`, `x-render-origin-server: uvicorn`) | response headers |

Structure: FastAPI. `app.py` (slim entrypoint) plus `routers/predict.py`, `alerts.py`,
`voice.py`, `status.py`. Turso storage in `db.py`, cost guard in `usage_guard.py`,
logging in `observability.py`. React dashboard source in `frontend/`, built output
committed in `static/dashboard/`.

## 2. What has been done

### 2.1 Roadmap status (checked in code, not all in production)

| Item | State |
|---|---|
| Tier 1 #1 Subscriptions to Turso | Done in code. Production proof still needed (Section 4, item 2). |
| Tier 1 #2 Tests in CI | Done, report-only. `test-scenarios.yml` runs `test_scenarios.py`, `test_explanations.py` and `test_observability.py`. |
| Tier 1 #3 Retrain guard | Done. `MIN_NEW_ROWS_TO_RETRAIN = 20`; skips retraining and keeps the old model. |
| Tier 1 #4 Cost guard | Done. `usage_guard.py` covers both Gemini call sites and gTTS; counts appear in `/status`. |
| Tier 2 #5 Per-pair accuracy | Done in code (`_per_pair_accuracy()` in `routers/predict.py`). **Not live**: the deployed model metadata predates it, so it appears only after the next successful retrain. |
| Tier 2 #6 Directional accuracy | Done in code (`backtest_directional_accuracy()`). **Not live**, same reason. |
| Tier 2 #7 Hindi/Punjabi text review | Review sheet built (`Mandi_Setu_Hindi_Punjabi_review.docx`). Waiting on native speakers. |
| Tier 3 SHAP explanations | Done, including ETS fallback, rupee conversion, advisory prompt drivers, `ExplanationPanel.jsx`. The built dashboard is in sync with the source. |
| Tier 4 logging and status | Done (structured JSON logs, `/status` with model self-test). |
| Tier 4 deferred items | Not started, deliberately: model versioning/rollback, drift monitoring, Postgres migration, API auth beyond `slowapi`. |

### 2.2 Work log

Earlier (from `git log`, 25 to 29 Sep): `0cfd59d` per-pair accuracy; `984f330`
directional accuracy; `0658633` Tier 3 SHAP; `4d96322` fix LightGBM silently
falling back to ETS; `992bb10` Tier 4 logging and `/status`; `887dbc2` missing-day gaps
in `/status` and `X-Cron-Secret` header; `56be4a2` pin libraries to the versions Render
runs and move CI to Python 3.14; `e9d604b` startup steps on background threads;
`ee6d365` temporary `/debug-client-ip`; `bb63fe5`, `865e838`, `41791dd` README and
handoff updates.

This effort (29 Sep):

- Confirmed the `41791dd` deploy was Live (screenshot, 1:22 PM IST).
- `fdf26a1`: deleted `migrate_subscriptions_to_turso.py` and its only helper
  `insert_subscription_full()` in `db.py`. CI green.
- `6c12f45`: updated the handoff report.
- Investigated the failing `update-mandi-data` workflow (details in Section 4, item 1).
- Analysed the rate limiter with real evidence from Render's log (Section 4, item 3).
- Reviewed the whole repository (Section 3).

## 3. Git review (whole repository, 30 Sep 2026)

### 3.1 Facts

- 197 commits between 2 Sep and 30 Sep 2026. Authors: Aditya Singh 69 and Aditya Singh7
  103 (probably the same person under two git identities), mandi-price-bot 13,
  Valkner7 5, divyansh08trivedi 3, Claude 2, Dev3ri 2.
- 75 tracked files; `.git` is 2.8 MB. About 6,900 lines across the main Python modules;
  the largest is `routers/predict.py` at 1,760 lines.
- Branches: `main` and `origin/frontend-redesign`. No tags.
- Workflows: `check-alerts` (every 10 min), `keep-alive` (every 10 min),
  `test-scenarios` (push, PR, manual), `update-mandi-data` (18:00 and 21:00 UTC, manual).

### 3.2 Checked and healthy

- No secrets found in the working tree or in any of the 197 commits. The scan looked
  for JWT-shaped tokens (Turso's format), Google API keys, Twilio account SIDs, `sk-`
  keys and a hex `TWILIO_AUTH_TOKEN` assignment. It is a pattern scan, not a guarantee.
- `.env` and `subscriptions.json` were never committed; `.gitignore` covers them.
- Every third-party import is declared in `requirements.txt`. The one exception,
  `tensorflow` in `forecast_models.py`, is intentional (guarded lazy import).
- `forecast_models.py` and `update_mandi_prices.py` are not dead code: the README
  documents them as a reference module and a manual tool.
- The README was last edited 29 Sep and documents every environment variable the code
  reads except the temporary `ENABLE_IP_DEBUG`.
- `frontend-redesign` is fully merged into `main`.
- Stale-claim probes on the older docs (`subscriptions.json`, "ephemeral", "in-memory")
  found nothing that contradicts current behaviour. I did not read those docs line by
  line.

### 3.3 Findings to act on

| # | Finding | Suggested action | Risk |
|---|---|---|---|
| 1 | `update-mandi-data` failing, permanent data loss per failed day | Section 4, item 1 | Urgent |
| 2 | `render.yaml` lacks `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` (also `GEMINI_DAILY_CALL_LIMIT`, `GTTS_DAILY_CALL_LIMIT`, `LOG_LEVEL`, `ADVISORY_TIMEOUT_SECONDS`, `VOICE_ADVISORY_BUDGET_SECONDS`, `ALERT_CHECK_INTERVAL_SECONDS`, `GOOGLE_API_KEY`). A blueprint rebuild would start without persistence | Add the two Turso keys as `sync: false`; add the optional ones with their defaults | Low |
| 3 | `requirements.txt` lists `matplotlib` and `jupyter`; nothing imports them and the repo has no notebooks | Remove in their own commit; check CI and `/status` after the deploy. CI's retrain job does not use `requirements.txt` | Low |
| 4 | Stale remote branch `frontend-redesign` (0 commits ahead, 60 behind; last commit 9 Sep by divyansh08trivedi) | Optional: `git push origin --delete frontend-redesign`. It is a teammate's branch, so ask first | Low |
| 5 | Unused imports at `app.py:224` (`_build_prediction`, `generate_advisory`, `FORECAST_CONFIDENCE_NOTE`), an f-string without placeholders at `routers/predict.py:273`, an unused `app` import in `test_explanations.py:24` | Cosmetic. Check whether the imports are deliberate re-exports or side-effect imports before deleting | Low |
| 6 | `forecast_models.py` docstring refers to `PROJECT_STATUS.md`, which does not exist | Fix the reference or drop the sentence | Cosmetic |
| 7 | `routers/predict.py` is 1,760 lines | No action now; a candidate to split if it becomes hard to maintain | None |
| 8 | Two git author identities for the owner | Cosmetic | None |

### 3.4 Model-quality observations

From the committed model metadata, plus a sandbox retrain on the same data and code
(its MAE matched the committed value exactly, so the extra figures are trustworthy for
this data, but they are not from production):

- Backtest MAE 142.893 against 143.084 for naive "tomorrow equals today". The model
  beat the naive baseline in 103 of 222 crop-mandi pairs (46.4%).
- Directional accuracy overall 52.7%. Rising: precision 63.2%, recall 13.9%. Falling:
  precision 53.6%, recall 19.3%. Stable: precision 51.8%, recall 93.4%. The model mostly
  predicts "stable".
- 54.7% of panel rows were dropped because their target was forward-filled, not a real
  observation.
- Meaning: the forecast has a thin edge over persistence. That is not a bug, but check
  that `FORECAST_CONFIDENCE_NOTE` and the advisory wording are consistent with it, and
  discuss with the user before any modelling work.

### 3.5 Findings from a second, independent review (30 Sep)

Items marked (code) were re-read in the code while updating this report. The
statistical ones come from the reviewer's run and were NOT re-run here.

- (code) Only `/sms`, `/whatsapp`, `/voice-advisory` are rate-limited. `/advisory` and
  `/compare-advisory` call Gemini, are unauthenticated GETs and have no per-client
  limit; only the 300/day cost guard protects them. A limit was deliberately NOT added
  yet: it keys on client IP and Section 4 item 3 is unresolved, so a per-IP limit could
  end up shared by every user behind one proxy address. Decide after the IP test.
- Twilio-facing routes see Twilio's IPs, not farmers'; keying on the `From` number
  after signature validation would be more meaningful.
- (code, now fixed) Staleness was only disclosed after 90 days; it is now 7. Note that
  about 24% of crop-mandi pairs (354 of 1,474) are more than 7 days older than the
  dataset's latest date even when the data job is healthy (sporadic mandis), so they
  will carry the note permanently. Raise `DATA_NOTE_STALE_DAYS` if that is too noisy.
- (reviewer) "Next-day" targets can be forward-filled: in training some rows are more
  than 30 days stale (max 1,016 days), including the Jun to Nov 2025 hole. Suggested:
  cap staleness in training and report the backtest on rows where today is real. Not
  done: it changes the model, so discuss first.
- (reviewer) Model minus naive MAE is -0.19 rupees, 95% CI [-0.90, +0.52] (bootstrapped
  by date). Read this as "no demonstrable edge on price level"; direction is modestly
  better (52.7% vs 47.1% for always-stable). The Hindi and Punjabi
  `FORECAST_CONFIDENCE_NOTE` versions read as rosier than the English one ("not always
  better" vs "only some of the time"); tell the native-speaker reviewers.
- (reviewer, unfixed) `check-alerts` and `keep-alive` only warn on a non-200, so a green
  run does not prove the cron secret matches. Repeating an alert message creates
  duplicate subscriptions.
- (code, now fixed) The service worker showed cached forecasts first and could cache
  error responses.

## 4. What is left, in priority order

Run from `D:\mandi-price-forecast` unless a step says otherwise.

**1. Fix `update-mandi-data` (urgent).** Runs 58 to 65 failed. The last bot commit was
24 Sep 23:49 UTC, so the data ends on the 24th and the model has not retrained since.
Every failed day is a permanent gap, and `/status` goes degraded on 2 Oct. Earlier
history is patchy too (runs 41 to 53 failed, 54 to 57 passed), which matches the
11 to 22 Sep gap.

- Ruled out: the Sep 25 commits touched `train_forecast_model.py` and
  `fetch_daily_mandi_data.py`, but the fetch diff is comment-only plus one unused
  constant, `pyflakes` reports no undefined names, and a forced retrain in the sandbox
  with the exact `constraints.txt` versions completes (self-check passed, artifact
  written, "Done.").
- Leading hypothesis, NOT confirmed: the data.gov.in fetch fails on every call
  (`fetch_daily_mandi_data.py` exits 1 only in that case), plausibly the shared demo key.
- Needed from the user: open
  `https://github.com/Valkner7/mandi-price-forecast/actions/runs/36505298119`, click the
  `update-data` job, name the red step and paste its last ~15 log lines. Also whether a
  `DATA_GOV_API_KEY` repo secret exists (the workflow already reads it; a free key can
  be registered at data.gov.in). Also whether GitHub's Actions failure emails are
  enabled: four days of failures went unnoticed.
- Already done: the workflow now commits data before retraining (`5c2742c`), so a
  retrain failure can no longer lose a fetched day. Tonight's scheduled runs (18:00 and
  21:00 UTC) are the first on that code.
- Then: if the fetch is the failing step, add the key secret and re-run manually
  (Actions, Run workflow). If it is `pip install`, the pins or Python 3.14 wheels are
  the suspect. If it is training, the log names the check. Make no code change before
  the failing step is known.
- After a successful run the retrain will happen (about 880 new rows a day is far above
  the 20-row guard), which also makes Tier 2 #5 and #6 appear in production.

**2. Deploy the latest commit and prove Turso persistence.** In order: on WhatsApp send
`alert me potato rayya 850`, then `my alerts` (BEFORE deploying); in Render use Manual
Deploy, Deploy latest commit (`6c12f45`) and wait for Live; send `my alerts` again. Still
listed means Tier 1 #1 is proven.

**3. Rate-limiter client IP.** `app.py` uses `Limiter(key_func=get_remote_address)`.

- Old assumption (wrong or unproven): uvicorn trusts `X-Forwarded-For` only from
  127.0.0.1 by default, so on Render everyone would share the proxy's IP.
- Evidence against it: Render's log at 1:24:33 PM showed
  `34.83.207.104:0 - "GET / HTTP/1.1"`. In uvicorn a client port of 0 appears when the
  address was taken from an `X-Forwarded-For` entry, so the client address is probably
  already rewritten. `render.yaml`'s start command has no proxy flags, so something
  else enables that (a `FORWARDED_ALLOW_IPS` variable or a loopback proxy; unchecked).
- Unknown: which entry is used. Leftmost is client-supplied and spoofable (limit
  bypass); rightmost is already safe. Cloudflare is in front, so the chain may have two
  or more entries, and Cloudflare normally adds `CF-Connecting-IP`. The debug endpoint
  prints `x_forwarded_for`, `cf_connecting_ip`, `true_client_ip`, `x_real_ip` and
  `direct_client_host`.
- Test: in Render's Environment tab confirm `ENABLE_IP_DEBUG=1` (exact key, value `1`)
  and look for `FORWARDED_ALLOW_IPS`; deploy; then run:

```powershell
cd D:\mandi-price-forecast
curl.exe -sS -i --max-time 120 https://mandi-price-forecast-1.onrender.com/debug-client-ip
curl.exe -sS -H "X-Forwarded-For: 1.2.3.4" https://mandi-price-forecast-1.onrender.com/debug-client-ip
curl.exe -s https://api.ipify.org
```

  Repeat the first command from a second network (phone hotspot) and note its public IP
  too. The user's home IP was 152.56.69.129. Compare `x_forwarded_for` with
  `direct_client_host` (which shows the rewritten value).
- Decision: rightmost entry picked, so only delete the endpoint and the env var.
  Leftmost picked, so key the limiter on a trustworthy entry (`CF-Connecting-IP` if it
  reaches the app, otherwise the rightmost `X-Forwarded-For` entry, or configure trusted
  proxies), remove the endpoint (`/debug-client-ip` block and the
  `from fastapi import Request as _DebugRequest` line in `app.py`) and delete the env
  var, all in ONE change. The uvicorn analysis used version 0.54.0 in the sandbox;
  Render installs its own (uvicorn is not pinned in `constraints.txt`), so trust the
  live test.

**4. Housekeeping commits.** Done on 30 Sep: `render.yaml` keys, `matplotlib`/`jupyter`
removal, cosmetic cleanups, this report. Left: the optional deletion of the teammate's
`frontend-redesign` branch (ask first), and fixing the README gap for `ENABLE_IP_DEBUG`
only if the debug endpoint is kept.

**5. Hindi/Punjabi corrections.** When reviewers reply, update `_FALLBACK_TREND_WORDS`,
`_FALLBACK_TEMPLATES`, `_FALLBACK_DATA_NOTE` and `FORECAST_CONFIDENCE_NOTE` in
`routers/predict.py`, keeping placeholders such as `{mandi}` intact. Also ask how English
crop and mandi names read inside Hindi and Punjabi sentences and aloud.
`voice_extraction.py` contains Hindi and Punjabi keyword matching for understanding
farmers; it is not farmer-facing text and needs a different check.

**6. Revisit usage limits with real traffic.** Defaults (300 Gemini and 1000 gTTS calls a
day, via `GEMINI_DAILY_CALL_LIMIT` and `GTTS_DAILY_CALL_LIMIT`) are guesses. Counters are
in memory and reset on restart.

**7. Optional settings only the user can change.** Make CI a required check (GitHub
Settings, Branches). Add `GEMINI_API_KEY` as a repo secret to exercise advisory/audio in
CI. Add `DATA_GOV_API_KEY` (item 1). Optionally set `RENDER_DEPLOY_HOOK_URL` so the daily
job redeploys Render automatically.

**8. Deferred by design:** model versioning/rollback, drift monitoring, Postgres
migration, API auth beyond `slowapi`.

---

## 5. Design decisions already made (do not relitigate without reason)

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
- **Decision (29 Sep):** no blind rate-limiter change and no blind change to
  `update-mandi-data`. The live debug output and the failing step name decide both.

---

## 6. Instructions for the next Claude session

1. Re-clone or refresh (`git fetch --all && git reset --hard origin/main`); trust GitHub,
   not the sandbox's history. Note the sandbox lags: it has no push access.
2. Ask the user first for: whether the latest Render deploy is Live and which commit; a
   redacted screenshot of the Environment tab (`ENABLE_IP_DEBUG`, `FORWARDED_ALLOW_IPS`);
   the debug-endpoint outputs; the red step of the failed `update-mandi-data` run and
   whether `DATA_GOV_API_KEY` exists; the result of the Turso test; whether the
   Hindi/Punjabi reviewers have replied.
3. Follow Section 0 from the very first message, including the `cd` line in every
   command block.
4. What works from the sandbox: `github.com` run pages and status badges (workflow status
   and the commit each run used, but NOT logs); `pip install` of the pinned libraries on
   Python 3.12 and running `train_forecast_model.py` in a scratch copy of the repo (to
   force a real retrain, lower `total_rows_at_train_time` in the scratch copy's
   `models/lgbm_price_model_meta.json`); `pyflakes`. What does not: Render, Turso,
   data.gov.in, and (usually) `api.github.com`, whose unauthenticated rate limit is
   shared and exhausted.
5. Before touching `db.py`, `app.py` startup, `usage_guard.py` or the fallback strings,
   re-read the relevant part of Section 5.
6. Do not claim anything works in production unless the user has reported it from Render
   or the live app.
