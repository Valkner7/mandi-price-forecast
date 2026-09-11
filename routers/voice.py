"""Twilio SMS/WhatsApp webhook logic.

Extracted out of app.py's monolith (Step 4 of the app.py breakup — see
HANDOFF_REPORT.md). This module owns the Twilio-specific transport concerns:
signature verification, TwiML reply building, and the /sms and /whatsapp
routes themselves.

The actual forecasting/advisory logic (_build_prediction, generate_advisory)
still lives in app.py for now — build_reply_text() below imports those from
`app` at module load time. Alert-command handling (stop_alerts_for,
list_alerts_for, create_alert_from_message, etc.) now lives in
routers/alerts.py and is imported from there instead.

Both cross-module imports only work because app.py registers this router
(and routers/alerts.py, which this module also depends on) at the very
bottom of the file, *after* every name imported here is already defined —
by the time Python executes `from routers.voice import router` in app.py,
app's module object already has all of these attributes set, even though
app.py as a whole hasn't finished executing yet.
"""

from fastapi import APIRouter, Request, HTTPException, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from app import (
    TWILIO_AUTH_TOKEN,
    limiter,
    _build_prediction,
    generate_advisory,
)
from routers.alerts import (
    _ALERT_STOP_PHRASES,
    _ALERT_LIST_PHRASES,
    _looks_like_alert_command,
    stop_alerts_for,
    list_alerts_for,
    create_alert_from_message,
)
from voice_extraction import extract_crop_and_mandi

router = APIRouter()


def detect_sms_language(text: str) -> str:
    """Guess en/hi/pa from script, since SMS has no language picker.
    Devanagari (Hindi) and Gurmukhi (Punjabi) occupy distinct Unicode
    blocks, so a single character is enough to decide."""
    for ch in text:
        code = ord(ch)
        if 0x0900 <= code <= 0x097F:
            return "hi"
        if 0x0A00 <= code <= 0x0A7F:
            return "pa"
    return "en"


def build_reply_text(body: str, sender: str | None = None) -> str:
    """Shared core for every text-in/text-out channel (SMS, WhatsApp, and
    any future one): extract -> predict -> advise -> plain reply string.

    Channel-specific endpoints (below) only handle their own transport
    envelope (Twilio form fields in, TwiML out) and call this. Keeping the
    actual logic in one place means WhatsApp can't drift from SMS behavior,
    and a future channel (e.g. Telegram) is just another thin wrapper.

    `sender` (Twilio's "From", e.g. "whatsapp:+91...") is only used for
    price-alert commands, which need a phone number to notify later.
    """
    body = (body or "").strip()

    if not body:
        return "Send a crop and mandi name, e.g. 'Potato Rayya' — English, Hindi, or Punjabi all work."

    lowered = body.lower()

    # Alert-related commands are checked before the normal price-query flow
    # since they can contain a crop+mandi too (e.g. "alert me potato rayya 850").
    if any(phrase in lowered for phrase in _ALERT_STOP_PHRASES):
        return stop_alerts_for(sender) if sender else "Price alerts work over WhatsApp only."
    if any(phrase in lowered for phrase in _ALERT_LIST_PHRASES):
        return list_alerts_for(sender) if sender else "Price alerts work over WhatsApp only."
    if _looks_like_alert_command(lowered):
        return create_alert_from_message(body, sender)

    language = detect_sms_language(body)

    extracted = extract_crop_and_mandi(body)
    crop, mandi = extracted["crop"], extracted["mandi"]

    if not crop or not mandi:
        missing = ", ".join(name for name, val in [("crop", crop), ("mandi", mandi)] if not val)
        return f"Could not identify the {missing} from your message. Try e.g. 'Potato Rayya'."

    try:
        forecast_data = _build_prediction(crop=crop, mandi=mandi)
    except HTTPException as error:
        return str(error.detail)

    # generate_advisory() now handles Gemini being slow/unavailable itself
    # (see ADVISORY_TIMEOUT_SECONDS) and returns a plain-data fallback
    # instead of raising. It still raises HTTPException for a real config
    # problem (e.g. no API key set at all) — catch that separately so a
    # misconfigured server doesn't leave the farmer with no reply at all.
    try:
        advisory_text, _used_fallback = generate_advisory(
            forecast_data=forecast_data,
            farmer_question=body,
            language_code=language,
        )
    except HTTPException:
        advisory_text = (
            f"{crop} at {mandi}: Rs {forecast_data['latest_price']}/quintal, "
            f"trend {forecast_data['trend']}."
        )

    return advisory_text


def _twiml_response(message: str) -> Response:
    reply = MessagingResponse()
    reply.message(message[:600])  # defensive cap; Twilio splits long replies anyway
    return Response(content=str(reply), media_type="application/xml; charset=utf-8")


def _public_request_url(request: Request) -> str:
    """
    Reconstructs the public-facing URL (scheme + host + path + query) that
    Twilio actually sent the request to, using X-Forwarded-* headers when
    present. Render (and most PaaS reverse proxies) terminate TLS and can
    forward requests to the app as plain HTTP on an internal host, so
    request.url alone may not match the URL Twilio signed against. Falls
    back to request.url's own scheme/host unchanged if those headers
    aren't present, e.g. running locally without a proxy in front.
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    path = request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{proto}://{host}{path}{query}"


def _verify_twilio_request(request: Request, form: dict) -> bool:
    """
    Confirms an incoming /sms or /whatsapp POST genuinely came from Twilio,
    using Twilio's request signature scheme (X-Twilio-Signature header).
    Fails CLOSED: if TWILIO_AUTH_TOKEN isn't configured, or the signature
    doesn't match, the request is rejected rather than allowed through.
    """
    if not TWILIO_AUTH_TOKEN:
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    url = _public_request_url(request)
    return validator.validate(url, form, signature)


@router.post("/sms")
@limiter.limit("20/minute")
async def sms_webhook(request: Request):
    """Twilio SMS webhook: a farmer texts a crop + mandi (any of the three
    languages), gets a price + short advisory back — no app, no internet
    on their end required. Point your Twilio number's messaging webhook at
    POST https://<your-host>/sms.

    Note: SMS delivery to Indian numbers requires DLT registration and may
    not actually be deliverable end-to-end. /whatsapp below has no such
    requirement and is the recommended channel for a live demo.
    """
    form = await request.form()
    if not _verify_twilio_request(request, dict(form)):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    body = (form.get("Body") or "").strip()
    sender = (form.get("From") or "").strip()
    return _twiml_response(build_reply_text(body, sender=sender))


@router.post("/whatsapp")
@limiter.limit("20/minute")
async def whatsapp_webhook(request: Request):
    """Twilio WhatsApp webhook — identical behavior to /sms, just a
    different transport. WhatsApp Sandbox has no DLT requirement, works
    within minutes of joining the sandbox, and is testable live from your
    own phone during a demo.

    Setup:
    1. In the Twilio Console, open Messaging -> Try it out -> Send a WhatsApp message,
       and join your sandbox (send the shown join code to the shown number from WhatsApp).
    2. Set the sandbox's "When a message comes in" webhook to
       POST https://<your-public-host>/whatsapp
    3. Message the sandbox number from your phone with e.g. "Potato Rayya".

    Twilio sends the same form-encoded fields for WhatsApp as SMS (Body,
    From, To — From/To are just prefixed with "whatsapp:"), so no payload
    parsing changes are needed here. `From` (e.g. "whatsapp:+91...") is
    passed through as `sender` so price-alert commands know who to notify
    later.
    """
    form = await request.form()
    if not _verify_twilio_request(request, dict(form)):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    body = (form.get("Body") or "").strip()
    sender = (form.get("From") or "").strip()
    return _twiml_response(build_reply_text(body, sender=sender))
