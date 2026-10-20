"""Shared-secret guard for the endpoints that spend Gemini / text-to-speech quota (M10).

/advisory, /compare-advisory and /voice-advisory are protected only by a daily
call counter. Set API_SHARED_SECRET and every caller must also send the same
value in an X-Api-Secret header (AnnaSetu's server does this).

Unset: the endpoints answer only callers on this machine (loopback), so a
local AnnaSetu install talking to 127.0.0.1 works with no setup. Any other
caller gets 503 ("secret not configured") instead of being let in, so a service
that is reachable from the network never runs open by accident. A startup
warning says which mode is active.

Note: browser pages that call these endpoints directly (templates/voice_test.html)
cannot hold a secret. Once the variable is set they get 403, by design.
"""
import hmac
import ipaddress
import os

from fastapi import Header, HTTPException, Request

import observability

ENV_NAME = "API_SHARED_SECRET"


def _expected() -> str:
    # Read on every call, not at import, so a changed env var or a test takes effect.
    return os.getenv(ENV_NAME, "").strip()


def _is_local(host) -> bool:
    """True only for a loopback address (127.0.0.0/8, ::1, ::ffff:127.x).

    Anything that is not a literal IP (None, "testclient", a unix-socket
    path) counts as NOT local, so unknown callers are refused.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def require_api_secret(request: Request, x_api_secret: str = Header(None)) -> None:
    """FastAPI dependency.

    Secret set:   403 unless the X-Api-Secret header matches.
    Secret unset: callers on this machine pass; everyone else gets 503.
    """
    expected = _expected()
    if not expected:
        client = request.client.host if request.client else None
        if _is_local(client):
            return
        observability.log_event("api_secret_rejected", level="warning",
                                reason="secret_not_configured")
        raise HTTPException(
            status_code=503,
            detail=f"{ENV_NAME} is not set on this service, so it only answers callers "
                   f"on the same machine. Set {ENV_NAME} and send the same value in "
                   "an X-Api-Secret header.",
        )
    supplied = (x_api_secret or "").strip()
    # Compare bytes: hmac.compare_digest raises TypeError on non-ASCII str.
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        observability.log_event("api_secret_rejected", level="warning",
                                reason="missing" if not supplied else "wrong")
        raise HTTPException(status_code=403, detail="Missing or incorrect API secret.")


def warn_if_unset() -> None:
    """Startup hook: say so in the log when no secret is set."""
    if not _expected():
        observability.log_event(
            "startup_warning", level="warning", component="advisory",
            problem=f"{ENV_NAME} is not set",
            effect="/advisory, /compare-advisory and /voice-advisory answer only callers "
                   "on this machine; any other caller gets 503. Set the variable to "
                   "let a remote server (for example AnnaSetu) use them.",
        )
