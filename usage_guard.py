"""Daily call-volume guard for Gemini and gTTS calls.

Tier 1 #4 in the "What to Build Next" roadmap: "cheap insurance against a
surprise bill or a sudden outage the moment usage grows past demo scale."
Note these are two DIFFERENT risks, guarded for two different reasons:

- Gemini (generate_advisory / generate_compare_advisory in
  routers/predict.py) is a real, metered, billed API — the risk is a
  surprise bill.
- gTTS (the Python `gtts` package) is the free/unofficial Google
  Translate text-to-speech endpoint, not a paid API — there's no direct
  $ cost here. The risk is Google rate-limiting or blocking the calling
  IP if it's hit too hard, which would be a genuine "sudden outage" of
  the voice feature (and is the actual failure mode "sudden outage" in
  the roadmap's own phrasing is about).

Both are hard cutoffs, not soft alerts: once today's limit is hit, the
call is skipped entirely (Gemini falls back to the existing plain-template
advisory; gTTS returns a clear error) rather than just logging a warning
and proceeding anyway. A guard that doesn't actually prevent the call
isn't much of a guard.

In-memory only, deliberately — resets whenever the process restarts,
which on Render's free tier can happen more than once a day (spin-down
after inactivity, or a redeploy), not just at UTC midnight. That's an
accepted tradeoff, not an oversight: this is low-stakes state (worst
case, a little extra same-day usage before the guard kicks back in),
unlike subscriptions.json's old problem of silently losing a farmer's
alert — so it doesn't need Turso or any other persistent store. Revisit
only if real usage volume makes the reset behavior an actual problem in
practice, not preemptively.

Limits are configurable via environment variables so they can be tuned in
Render without a code change; defaults are deliberately generous guesses
for demo/early-usage scale, not a measured real-world ceiling — adjust
once you have real traffic numbers to look at.
"""

import os
import threading
from datetime import datetime, timezone

GEMINI_DAILY_LIMIT = int(os.getenv("GEMINI_DAILY_CALL_LIMIT", "300"))
GTTS_DAILY_LIMIT = int(os.getenv("GTTS_DAILY_CALL_LIMIT", "1000"))

_lock = threading.Lock()
_state = {"date": None, "gemini_calls": 0, "gtts_calls": 0}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _roll_if_new_day() -> None:
    """Must be called while holding _lock. Resets both counters the first
    time this module sees a new UTC date -- not on a timer, just checked
    opportunistically on each call, which is all a single-process app
    needs."""
    today = _today()
    if _state["date"] != today:
        _state["date"] = today
        _state["gemini_calls"] = 0
        _state["gtts_calls"] = 0


def gemini_call_allowed() -> bool:
    """Call BEFORE attempting a Gemini call. False means today's budget is
    already used up -- the caller should skip straight to its existing
    fallback path instead of calling Gemini at all. Doesn't increment
    anything by itself; call record_gemini_call() separately, and only
    once the call is actually about to be attempted, so a check that
    results in skipping the call doesn't also consume budget for a call
    that never happened."""
    with _lock:
        _roll_if_new_day()
        return _state["gemini_calls"] < GEMINI_DAILY_LIMIT


def record_gemini_call() -> None:
    with _lock:
        _roll_if_new_day()
        _state["gemini_calls"] += 1


def gtts_call_allowed() -> bool:
    """Same pattern as gemini_call_allowed(), for gTTS."""
    with _lock:
        _roll_if_new_day()
        return _state["gtts_calls"] < GTTS_DAILY_LIMIT


def record_gtts_call() -> None:
    with _lock:
        _roll_if_new_day()
        _state["gtts_calls"] += 1


def usage_snapshot() -> dict:
    """Current day's counts and configured limits -- for logging, or a
    future admin/status endpoint. Read-only; doesn't affect the counters."""
    with _lock:
        _roll_if_new_day()
        return {
            "date": _state["date"],
            "gemini_calls": _state["gemini_calls"],
            "gemini_limit": GEMINI_DAILY_LIMIT,
            "gtts_calls": _state["gtts_calls"],
            "gtts_limit": GTTS_DAILY_LIMIT,
        }
