"""Price-alert subscriptions and checking logic.

Extracted out of app.py's monolith (Step 5 of the app.py breakup — see
HANDOFF_REPORT.md). Owns: the subscriptions.json file store, alert-command
parsing (used by routers/voice.py's build_reply_text), alert triggering
logic, outbound WhatsApp sends, and the /check-alerts endpoint.

Like routers/voice.py, this module imports forecasting internals
(_build_prediction) from `app` at load time. This only works because
app.py registers this router at the very end of the file, after every name
imported here is already defined — see the comment there for details.

The startup-scheduler function (_maybe_start_internal_alert_scheduler) is
exported WITHOUT an @app.on_event decorator, since APIRouter has no
on_event of its own — app.py registers it directly on the app instance
after including this router.
"""

import hmac
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from twilio.rest import Client as TwilioRestClient

from app import (
    SUBSCRIPTIONS_PATH,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM,
    ALERTS_CRON_SECRET,
    ENABLE_INTERNAL_ALERT_SCHEDULER,
    ALERT_CHECK_INTERVAL_SECONDS,
    FORECAST_CONFIDENCE_NOTE,
    _build_prediction,
)
from voice_extraction import extract_crop_and_mandi

router = APIRouter()

_subscriptions_lock = threading.Lock()


def load_subscriptions() -> list[dict]:
    if not SUBSCRIPTIONS_PATH.exists():
        return []
    try:
        with open(SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[ALERTS] Failed to read subscriptions file, treating as empty: {exc}")
        return []


def save_subscriptions(subs: list[dict]) -> None:
    with open(SUBSCRIPTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2, ensure_ascii=False)


# Two tiers, not one flat list. Phrases like "alert me" / "notify me" are
# unambiguous alert-setting actions — trigger on these alone (so a farmer
# who forgets the price still gets routed to "what price should I watch?").
# Phrases like "tell me when" / "let me know when" are much more likely to
# appear in an ordinary informational question too (e.g. "tell me when
# potato prices usually rise in Rayya") — only treat these as an alert
# command if a numeric price threshold is also present in the message,
# since a genuine alert-setting message almost always includes one.
_ALERT_ACTION_PHRASES = ["alert me", "set alert", "set an alert", "create alert", "create an alert", "notify me", "remind me", "warn me"]
_ALERT_AMBIGUOUS_PHRASES = ["tell me when", "let me know when"]
_ALERT_STOP_PHRASES = ["stop alert", "cancel alert", "stop alerts", "cancel alerts"]
_ALERT_LIST_PHRASES = ["my alerts", "list alerts", "show alerts"]

# Guards the _ALERT_AMBIGUOUS_PHRASES gate below. A genuine alert-setting
# message is forward-looking ("tell me when it crosses 850" — notify me
# later, about the future). An ordinary informational question can use the
# exact same phrase about the past ("tell me when onion was 850 last
# week"), and a bare "is there a number in this message" check can't tell
# the two apart — both contain "850". These markers catch the common
# past-tense/historical phrasings so that case falls through to the normal
# price-query flow instead of being misrouted into alert-creation (or,
# worse, silently creating an unwanted alert subscription if a crop/mandi
# happen to be extractable from the same message).
_ALERT_HISTORICAL_MARKERS = [
    "was", "were", "used to be", "last week", "last month", "last year",
    "yesterday", "ago", "previously", "in the past", "historically",
    "on average", "used to cost",
]


def _looks_like_alert_command(lowered_text: str) -> bool:
    if any(phrase in lowered_text for phrase in _ALERT_ACTION_PHRASES):
        return True
    if any(phrase in lowered_text for phrase in _ALERT_AMBIGUOUS_PHRASES):
        # Require an actual number too, so "tell me when potato prices
        # usually rise" (an ordinary question, no threshold) doesn't get
        # misrouted into alert-creation. Also bail out on an obvious
        # historical marker — "tell me when onion was 850 last week" has
        # a number, but it's a question about the past, not a request to
        # be notified about the future — see _ALERT_HISTORICAL_MARKERS.
        if any(
            re.search(rf"\b{re.escape(marker)}\b", lowered_text)
            for marker in _ALERT_HISTORICAL_MARKERS
        ):
            return False
        return _parse_price_threshold(lowered_text) is not None
    return False


def _parse_price_threshold(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return float(match.group(1)) if match else None


def _parse_alert_direction(lowered_text: str) -> str:
    """'above'/'below' if the farmer said so explicitly; otherwise 'cross',
    meaning "notify me the first time the price crosses this number in
    either direction from where it is now" — matches how people naturally
    phrase it ("tell me when it crosses 850") without requiring them to
    specify a direction."""
    if any(w in lowered_text for w in ["above", "over", "exceeds", "more than", "greater than"]):
        return "above"
    if any(w in lowered_text for w in ["below", "under", "less than", "falls to", "drops to"]):
        return "below"
    return "cross"


def _alert_direction_phrase_en(direction: str) -> str:
    return {"above": "goes above", "below": "goes below", "cross": "crosses"}.get(direction, "crosses")


def _alert_is_triggered(sub: dict, current_price: float) -> bool:
    target = sub["target_price"]
    direction = sub.get("direction", "cross")
    if direction == "above":
        return current_price >= target
    if direction == "below":
        return current_price <= target
    # "cross": relative to the price at the moment the alert was created.
    starting = sub.get("starting_price", target)
    if starting < target:
        return current_price >= target
    if starting > target:
        return current_price <= target
    return True  # started exactly at target — already "crossed"


def send_whatsapp_message(to: str, body: str) -> bool:
    """Send a proactive (not reply) WhatsApp message. Returns False (and
    just logs) if Twilio credentials aren't configured, so alert creation
    still works locally without outbound send — useful while developing,
    but obviously outbound sends need real credentials to actually notify
    anyone."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        print(f"[ALERTS] Twilio outbound credentials not set; would have sent to {to}: {body}")
        return False
    try:
        client = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to, body=body[:1000])
        return True
    except Exception as exc:
        print(f"[ALERTS] Failed to send WhatsApp message to {to}: {exc}")
        return False


def create_alert_from_message(body: str, sender: str) -> str:
    """Handles an inbound message that looks like an alert-setting command.
    Returns the reply text. Alerts require a WhatsApp sender (a real phone
    number we can message back later) — SMS numbers work for one-shot
    price queries but not for this, so we say so plainly rather than
    silently failing later."""
    if not sender or not sender.startswith("whatsapp:"):
        return "Price alerts currently work over WhatsApp only. Message this number on WhatsApp to set one, e.g. 'alert me potato rayya 850'."

    extracted = extract_crop_and_mandi(body)
    crop, mandi = extracted["crop"], extracted["mandi"]
    if not crop or not mandi:
        missing = ", ".join(name for name, val in [("crop", crop), ("mandi", mandi)] if not val)
        return f"Could not identify the {missing} for your alert. Try e.g. 'alert me potato rayya 850'."

    target_price = _parse_price_threshold(body)
    if target_price is None:
        return f"What price should I watch for {crop} at {mandi}? Try e.g. 'alert me {crop.lower()} {mandi.lower()} 850'."

    try:
        forecast_data = _build_prediction(crop=crop, mandi=mandi)
    except HTTPException as error:
        return str(error.detail)

    direction = _parse_alert_direction(body.lower())
    new_sub = {
        "id": str(uuid.uuid4()),
        "phone": sender,
        "crop": crop,
        "mandi": mandi,
        "target_price": target_price,
        "direction": direction,
        "starting_price": forecast_data["latest_price"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    with _subscriptions_lock:
        subs = load_subscriptions()
        subs.append(new_sub)
        save_subscriptions(subs)

    return (
        f"Alert set: I'll message you here when {crop} at {mandi} "
        f"{_alert_direction_phrase_en(direction)} ₹{target_price}/quintal. "
        f"Current price: ₹{forecast_data['latest_price']}/quintal. "
        f"Reply 'my alerts' to see active alerts, or 'stop alerts' to cancel all of them."
    )


def list_alerts_for(sender: str) -> str:
    subs = load_subscriptions()
    mine = [s for s in subs if s.get("phone") == sender and s.get("active")]
    if not mine:
        return "You have no active price alerts. Try 'alert me potato rayya 850'."
    lines = [
        f"- {s['crop']} at {s['mandi']}: {_alert_direction_phrase_en(s.get('direction', 'cross'))} ₹{s['target_price']}"
        for s in mine
    ]
    return "Your active alerts:\n" + "\n".join(lines)


def stop_alerts_for(sender: str) -> str:
    with _subscriptions_lock:
        subs = load_subscriptions()
        count = 0
        for s in subs:
            if s.get("phone") == sender and s.get("active"):
                s["active"] = False
                count += 1
        save_subscriptions(subs)
    if count == 0:
        return "You had no active alerts to cancel."
    return f"Cancelled {count} active alert{'s' if count != 1 else ''}."


def check_all_alerts() -> dict:
    """Called by /check-alerts (external cron) or the optional internal
    scheduler. Groups active subscriptions by crop+mandi so each pair is
    only predicted once no matter how many farmers are watching it.

    Concurrency note: the (potentially slow — one predict() per distinct
    crop+mandi group) checking work below happens outside the lock, using
    a snapshot of subscriptions taken at the start. To avoid losing any
    alert a farmer creates or cancels via WhatsApp while that snapshot is
    stale, this function does NOT save that snapshot back wholesale.
    Instead it tracks only the specific alerts that actually fired (by id),
    then re-loads subscriptions.json fresh immediately before saving and
    applies just those updates on top of the current file — so concurrent
    changes made elsewhere during the check are preserved rather than
    silently overwritten (a merge-on-save, not last-write-wins). This is a
    single-process guard (the lock doesn't span multiple worker processes),
    which is fine for a single-process hackathon deployment."""
    with _subscriptions_lock:
        subs = load_subscriptions()

    active = [s for s in subs if s.get("active")]
    if not active:
        return {"checked": 0, "notified": 0}

    groups: dict[tuple[str, str], list[dict]] = {}
    for s in active:
        groups.setdefault((s["crop"], s["mandi"]), []).append(s)

    # Collect only the updates for alerts that actually fired, keyed by id,
    # rather than mutating and later saving the whole (possibly-stale)
    # `subs` snapshot. Applied on top of a fresh reload just before saving.
    fired_updates: dict[str, dict] = {}
    notified = 0
    for (crop, mandi), group in groups.items():
        try:
            forecast_data = _build_prediction(crop=crop, mandi=mandi)
        except HTTPException as exc:
            print(f"[ALERTS] Skipping {crop}/{mandi}: {exc.detail}")
            continue

        current_price = forecast_data["latest_price"]
        for sub in group:
            if not _alert_is_triggered(sub, current_price):
                continue
            message = (
                f"Price alert: {sub['crop']} at {sub['mandi']} is now "
                f"₹{current_price}/quintal (your target was ₹{sub['target_price']}). "
                f"{FORECAST_CONFIDENCE_NOTE['en']}"
            )
            if send_whatsapp_message(sub["phone"], message):
                fired_updates[sub["id"]] = {
                    "active": False,
                    "notified_at": datetime.now(timezone.utc).isoformat(),
                    "notified_price": current_price,
                }
                notified += 1

    with _subscriptions_lock:
        # Reload fresh rather than reusing the stale `subs` snapshot, so any
        # alert created/cancelled by a farmer during the loop above isn't
        # lost. Only the specific alerts that fired get updated, by id.
        current_subs = load_subscriptions()
        for sub in current_subs:
            update = fired_updates.get(sub.get("id"))
            if update:
                sub.update(update)
        save_subscriptions(current_subs)

    return {"checked": len(active), "notified": notified}


@router.get("/check-alerts")
@router.post("/check-alerts")
def check_alerts_endpoint(secret: str = Query(None, description="Must match ALERTS_CRON_SECRET if that env var is set")):
    """Point an external free scheduler (cron-job.org, UptimeRobot, a
    scheduled GitHub Action, etc.) at this endpoint every 5-10 minutes.
    This is the reliable path on Render's free tier: the request itself
    wakes a sleeping service, so this both checks alerts AND keeps the
    demo responsive. Protect it with ALERTS_CRON_SECRET once deployed —
    it sends real outbound messages and shouldn't be publicly triggerable.
    """
    if ALERTS_CRON_SECRET and not hmac.compare_digest(secret or "", ALERTS_CRON_SECRET):
        raise HTTPException(status_code=403, detail="Missing or incorrect secret.")
    result = check_all_alerts()
    return {"status": "ok", **result}


def _maybe_start_internal_alert_scheduler():
    """Optional convenience for LOCAL rehearsal only. On Render's free
    tier this thread is asleep whenever the service is asleep and will
    NOT fire on schedule — use the external-cron /check-alerts path above
    for the actual deployed demo instead.

    NOTE: not decorated with @app.on_event here (APIRouter has none) —
    app.py calls app.on_event("startup")(this function) directly after
    including this router."""
    if not ENABLE_INTERNAL_ALERT_SCHEDULER:
        return

    def _loop():
        while True:
            try:
                result = check_all_alerts()
                print(f"[ALERTS] internal scheduler check: {result}")
            except Exception as exc:
                print(f"[ALERTS] internal scheduler error: {exc}")
            time.sleep(ALERT_CHECK_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
