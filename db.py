"""Turso (libSQL) storage for price-alert subscriptions.

Replaces the old subscriptions.json file store (see HANDOFF_REPORT.md,
Tier 1 #1 in the "what to build next" report). Render's filesystem is
ephemeral -- anything written to local disk is wiped on every redeploy, so
subscriptions.json silently reset and a farmer's alert could vanish with
zero notice. Turso is a real remote database, so that problem goes away
entirely: nothing here depends on Render's local disk at all.

Uses the `libsql` package's plain remote connection (no local replica file,
no sync()) -- there's no persistent disk on Render worth replicating to
anyway, so the simpler pure-remote mode is the correct choice here, not
just the easy one. See https://docs.turso.tech/sdk/python for the current
API this is built against.

Concurrency note: deliberately does NOT cache one shared connection object
across the process. FastAPI can run sync request handlers across multiple
worker threads, and the optional internal alert scheduler
(_maybe_start_internal_alert_scheduler in routers/alerts.py) runs in its
own background thread too -- and it's not documented whether one `libsql`
connection is safe to share across threads. Opening a short-lived
connection per call sidesteps that question entirely. Given how rarely
subscriptions are created/checked (a handful of WhatsApp messages and one
/check-alerts sweep every several minutes), the extra connection overhead
is irrelevant.
Startup timeout: init_db() wraps its connection attempt in a hard
timeout (see _INIT_TIMEOUT_SECONDS below). Without it, a bad auth token,
a paused/deleted database, or a network hiccup between Render and Turso
can leave the underlying call hanging indefinitely with no error at all
-- which is exactly what took the whole app down on 2026-09-17 (Render's
own port-scan timeout is ~15 minutes, so the deploy just sat there
until Render itself gave up). This wrapper fails loudly within seconds
instead. See app.py for the second half of that fix: even if this
raises, app.py's startup hook now catches it and lets the rest of the
app (predictions, dashboard, voice) start normally -- only the alerts
feature should ever be degraded by a Turso problem, not the whole site.
"""

import concurrent.futures
import os

import libsql

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# How long init_db() will wait for Turso before giving up and raising, in
# seconds. Generous for a single CREATE TABLE IF NOT EXISTS against a
# healthy remote database (normally well under a second), while being
# dramatically faster than silently hanging for Render's own ~15 minute
# deploy timeout.
_INIT_TIMEOUT_SECONDS = 10

# Fields that may legitimately be NULL in the table (not yet fired, or no
# starting price recorded). Dropped from the returned dict when NULL so
# callers see the same shape the old JSON file gave them -- a missing key,
# the way existing code already checks for these with .get(...), not an
# explicit None.
_OPTIONAL_NULLABLE_FIELDS = ("starting_price", "notified_at", "notified_price")


def _get_connection():
    """Opens a fresh connection for one call. See the module docstring's
    concurrency note for why this isn't cached/shared."""
    if not (TURSO_DATABASE_URL and TURSO_AUTH_TOKEN):
        raise RuntimeError(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must both be set to "
            "use the subscriptions database. Set them in Render's "
            "environment variables (see the Turso dashboard for a "
            "database's URL and auth token)."
        )
    return libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)


def _create_table() -> None:
    conn = _get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            crop TEXT NOT NULL,
            mandi TEXT NOT NULL,
            target_price REAL NOT NULL,
            direction TEXT NOT NULL DEFAULT 'cross',
            starting_price REAL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            notified_at TEXT,
            notified_price REAL
        )
        """
    )
    conn.commit()


def init_db() -> None:
    """Creates the subscriptions table if it doesn't exist yet. Safe to
    call on every startup -- CREATE TABLE IF NOT EXISTS is a no-op once
    the table already exists. Called from app.py's startup event.

    Runs the actual connection+query in a background thread so a hang
    (rather than a clean error) can still be interrupted by a timeout --
    Python has no way to forcibly cancel a blocking call otherwise. If it
    times out, the stuck thread is abandoned (not killed -- Python can't
    do that) and left to finish or die on its own; the caller doesn't
    wait for it. That's a deliberate tradeoff: one leaked thread in the
    rare case Turso is genuinely unreachable is a lot better than the
    whole app hanging for 15 minutes, which is what happened without
    this."""
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="turso-init-db"
    )
    future = executor.submit(_create_table)
    try:
        future.result(timeout=_INIT_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(
            f"Connecting to Turso timed out after {_INIT_TIMEOUT_SECONDS}s. "
            "Check that TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in Render's "
            "environment variables are correct and current (compare against "
            "the Turso dashboard), and that the database hasn't been paused "
            "or deleted."
        ) from None
    finally:
        # wait=False: never block here regardless of outcome. On success
        # the thread already finished, so this just frees the executor
        # object; on timeout it lets the stuck thread be abandoned instead
        # of this call waiting for it anyway (which would defeat the
        # timeout above entirely).
        executor.shutdown(wait=False)


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    sub = dict(zip(columns, row))
    sub["active"] = bool(sub["active"])
    for key in _OPTIONAL_NULLABLE_FIELDS:
        if sub.get(key) is None:
            sub.pop(key, None)
    return sub


def load_subscriptions() -> list[dict]:
    """Returns every subscription (active and inactive) as a list of
    dicts, in the same shape routers/alerts.py already expects from the
    old load_subscriptions() -- callers filter by .get("active") etc.
    themselves, same as before."""
    conn = _get_connection()
    cursor = conn.execute("SELECT * FROM subscriptions")
    columns = [d[0] for d in cursor.description]
    return [_row_to_dict(columns, row) for row in cursor.fetchall()]


def insert_subscription(sub: dict) -> None:
    """Adds one new subscription. Replaces the old
    load-list/append/save-whole-list pattern with a single INSERT."""
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO subscriptions
            (id, phone, crop, mandi, target_price, direction,
             starting_price, created_at, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sub["id"],
            sub["phone"],
            sub["crop"],
            sub["mandi"],
            sub["target_price"],
            sub.get("direction", "cross"),
            sub.get("starting_price"),
            sub["created_at"],
            1 if sub.get("active", True) else 0,
        ),
    )
    conn.commit()


def insert_subscription_full(sub: dict) -> None:
    """Like insert_subscription(), but also writes notified_at/
    notified_price/active as given rather than assuming a brand-new
    active alert. Used only by migrate_subscriptions_to_turso.py to carry
    over already-fired/cancelled alerts from the old subscriptions.json
    exactly as they were, instead of resetting their history."""
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO subscriptions
            (id, phone, crop, mandi, target_price, direction,
             starting_price, created_at, active, notified_at, notified_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sub["id"],
            sub["phone"],
            sub["crop"],
            sub["mandi"],
            sub["target_price"],
            sub.get("direction", "cross"),
            sub.get("starting_price"),
            sub["created_at"],
            1 if sub.get("active", True) else 0,
            sub.get("notified_at"),
            sub.get("notified_price"),
        ),
    )
    conn.commit()


def deactivate_for_phone(phone: str) -> int:
    """Cancels every active alert for one phone number. Returns how many
    were actually cancelled, same as the old stop_alerts_for()'s count."""
    conn = _get_connection()
    cursor = conn.execute(
        "UPDATE subscriptions SET active = 0 WHERE phone = ? AND active = 1",
        (phone,),
    )
    conn.commit()
    return cursor.rowcount


def mark_fired(sub_id: str, notified_at: str, notified_price: float) -> None:
    """Atomically marks one subscription as fired. Replaces the old
    load-everything / merge-in-memory-by-id / save-everything dance that
    subscriptions.json needed (see the old check_all_alerts() docstring)
    purely to avoid clobbering concurrent changes made while a slow round
    of checks was in flight. A real database doesn't have that problem --
    this single UPDATE, scoped to one row by id, is already atomic and
    can't race with a concurrent create/cancel the way a whole-file
    rewrite could."""
    conn = _get_connection()
    conn.execute(
        "UPDATE subscriptions SET active = 0, notified_at = ?, notified_price = ? WHERE id = ?",
        (notified_at, notified_price, sub_id),
    )
    conn.commit()
