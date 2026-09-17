"""One-off migration: subscriptions.json -> Turso.

Run this once, after TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are set, to
carry over any alerts that existed in the old local subscriptions.json
file before this app moved to Turso for storage (see db.py and
HANDOFF_REPORT.md, Tier 1 #1).

Safe to run even if there's nothing to migrate: if subscriptions.json
doesn't exist, or exists but is empty, this does nothing and exits
cleanly. Safe to run more than once against an empty-so-far Turso table,
but NOT idempotent against a table that already has these same rows —
running it twice on a table that already received the same subscriptions
will fail on the second run (id is a PRIMARY KEY, so re-inserting the
same ids raises an integrity error rather than silently duplicating
them). That's intentional: a loud failure here is better than silently
duplicating a farmer's alert.

Usage:
    python migrate_subscriptions_to_turso.py
"""

import json
from pathlib import Path

import db

BASE_DIR = Path(__file__).resolve().parent
OLD_SUBSCRIPTIONS_PATH = BASE_DIR / "subscriptions.json"


def main() -> None:
    if not OLD_SUBSCRIPTIONS_PATH.exists():
        print(f"No {OLD_SUBSCRIPTIONS_PATH.name} found — nothing to migrate.")
        return

    with open(OLD_SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
        old_subs = json.load(f)

    if not old_subs:
        print(f"{OLD_SUBSCRIPTIONS_PATH.name} exists but is empty — nothing to migrate.")
        return

    print(f"Found {len(old_subs)} subscription(s) in {OLD_SUBSCRIPTIONS_PATH.name}. Migrating to Turso...")
    db.init_db()

    migrated = 0
    for sub in old_subs:
        db.insert_subscription_full(sub)
        migrated += 1
        print(f"  - migrated {sub.get('id', '?')} ({sub.get('crop', '?')} @ {sub.get('mandi', '?')})")

    print(f"Done. Migrated {migrated} subscription(s).")
    print(
        "You can now delete subscriptions.json locally (it's no longer read "
        "by the app), and it won't be recreated on Render since alerts.py no "
        "longer writes to it."
    )


if __name__ == "__main__":
    main()
