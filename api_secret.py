"""Shared-secret guard for the endpoints that spend Gemini / text-to-speech quota (M10).

/advisory, /compare-advisory and /voice-advisory are protected only by a daily
call counter. Set API_SHARED_SECRET and every caller must also send the same
value in an X-Api-Secret header (AnnaSetu's server does this).

Unset (the default) keeps the old open behaviour, so deploying this change
breaks nothing; a startup warning says the endpoints are open.

Note: browser pages that call these endpoints directly (templates/voice_test.html)
cannot hold a secret. Once the variable is set they get 403, by design.
"""
import hmac
import os

from fastapi import Header, HTTPException

import observability

ENV_NAME = "API_SHARED_SECRET"


def _expected() -> str:
    # Read on every call, not at import, so a changed env var or a test takes effect.
    return os.getenv(ENV_NAME, "").strip()


def require_api_secret(x_api_secret: str = Header(None)) -> None:
    """FastAPI dependency: 403 unless the header matches. No-op if no secret is set."""
    expected = _expected()
    if not expected:
        return
    supplied = (x_api_secret or "").strip()
    # Compare bytes: hmac.compare_digest raises TypeError on non-ASCII str.
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        observability.log_event("api_secret_rejected", level="warning",
                                reason="missing" if not supplied else "wrong")
        raise HTTPException(status_code=403, detail="Missing or incorrect API secret.")


def warn_if_unset() -> None:
    """Startup hook: say so in the log when the endpoints are open to anyone."""
    if not _expected():
        observability.log_event(
            "startup_warning", level="warning", component="advisory",
            problem=f"{ENV_NAME} is not set",
            effect="/advisory, /compare-advisory and /voice-advisory are open to anyone "
                   "(only the daily call cap limits them)",
        )
