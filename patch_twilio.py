import pathlib

path = pathlib.Path("app.py")
text = path.read_text(encoding="utf-8")

old = '''def _verify_twilio_request(request: Request, form: dict) -> bool:
    """
    Confirms an incoming /sms or /whatsapp POST genuinely came from Twilio,
    using Twilio's request signature scheme (X-Twilio-Signature header).
    Fails CLOSED: if TWILIO_AUTH_TOKEN isn't configured, or the signature
    doesn't match, the request is rejected rather than allowed through.

    CAVEAT: signature validation depends on Twilio and this server agreeing
    on the exact public URL (scheme + host) of the request. If Render sits
    behind a proxy that rewrites the Host header, this may need adjustment
    (e.g. explicitly reconstructing the URL from X-Forwarded-* headers)
    after a live test against the real Twilio webhook.
    """
    if not TWILIO_AUTH_TOKEN:
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    url = str(request.url)
    return validator.validate(url, form, signature)'''

new = '''def _public_request_url(request: Request) -> str:
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
    return validator.validate(url, form, signature)'''

count = text.count(old)
if count != 1:
    print(f"ABORTING: expected 1 match, found {count}. Nothing changed.")
else:
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print("app.py patched successfully.")
