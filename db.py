"""
DocFlow data layer.

Kept separate from app.py (the Streamlit UI) so the request-tracking logic
can be tested with plain Python + sqlite3, no Streamlit dependency needed.

Every request is assigned to an owner from the moment it's created, and
moves through five named stages, each with its own timestamp:
    Received -> Picked Up -> In Preparation -> Approved -> Delivered
"""

import sqlite3
from datetime import datetime

DB_PATH = "docflow.db"

STAGE_RECEIVED = "Received"
STAGE_PICKED_UP = "Picked Up"
STAGE_IN_PREPARATION = "In Preparation"
STAGE_APPROVED = "Approved"
STAGE_DELIVERED = "Delivered"

STAGE_ORDER = [STAGE_RECEIVED, STAGE_PICKED_UP, STAGE_IN_PREPARATION, STAGE_APPROVED, STAGE_DELIVERED]

# Fixed backup pairing: if an owner doesn't act in time, their request goes
# to this named person. Kept simple (a fixed pairing) for the prototype -
# a real version would probably let each person configure their own backup.
BACKUP_OF = {
    "Ayma": "Kostas",
    "Kostas": "Ayma",
    "Team Member A": "Team Member B",
    "Team Member B": "Team Member A",
}


def get_conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table, column, definition):
    """Add a column if it's missing, without touching existing rows/data."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path=DB_PATH):
    conn = get_conn(db_path)

    # If a requests table already exists from an earlier version of this app
    # (e.g. the old 4-stage schema), "CREATE TABLE IF NOT EXISTS" would leave
    # it untouched and every row read afterward would be missing the new
    # columns (stage, owner, received_at, ...), causing a KeyError. Detect an
    # old/incompatible schema and rebuild the table instead of silently
    # reading stale structure. This only applies to that one historical case -
    # newer, smaller additions (like delivered_by below) use _ensure_column
    # instead, which adds a column in place and keeps existing data.
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
    if existing_cols and "stage" not in existing_cols:
        conn.execute("DROP TABLE requests")
        existing_cols = set()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_name TEXT NOT NULL,
            document_type TEXT NOT NULL,
            description TEXT,
            owner TEXT NOT NULL,
            stage TEXT NOT NULL,
            received_at TEXT NOT NULL,
            picked_up_at TEXT,
            prep_started_at TEXT,
            approved_at TEXT,
            delivered_at TEXT,
            delivered_by TEXT,
            timeout_minutes INTEGER NOT NULL DEFAULT 30,
            reassigned_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    _ensure_column(conn, "requests", "delivered_by", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            recipient TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def insert_request(requester_name, document_type, description, owner, timeout_minutes, db_path=DB_PATH):
    """A request always has an owner from the moment it's created, and starts at 'Received'."""
    if not owner:
        raise ValueError("A request must be assigned to an owner when it's created.")
    conn = get_conn(db_path)
    received_at = now_iso()
    cur = conn.execute(
        """
        INSERT INTO requests
            (requester_name, document_type, description, owner, stage, received_at, timeout_minutes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (requester_name, document_type, description, owner, STAGE_RECEIVED, received_at, timeout_minutes),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def fetch_all(db_path=DB_PATH):
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_one(req_id, db_path=DB_PATH):
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def pick_up(req_id, db_path=DB_PATH):
    """Received -> Picked Up: the owner has started looking at the request."""
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET picked_up_at = ?, stage = ? WHERE id = ?",
        (now_iso(), STAGE_PICKED_UP, req_id),
    )
    conn.commit()
    conn.close()


def start_prep(req_id, db_path=DB_PATH):
    """Picked Up -> In Preparation: actual document work has started."""
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET prep_started_at = ?, stage = ? WHERE id = ?",
        (now_iso(), STAGE_IN_PREPARATION, req_id),
    )
    conn.commit()
    conn.close()


def approve(req_id, db_path=DB_PATH):
    """In Preparation -> Approved: the prepared document has been signed off."""
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET approved_at = ?, stage = ? WHERE id = ?",
        (now_iso(), STAGE_APPROVED, req_id),
    )
    conn.commit()
    conn.close()


def mark_delivered(req_id, confirmed_by, db_path=DB_PATH):
    """
    Approved -> Delivered: one-click delivery confirmation.

    confirmed_by is who clicked it. The caller (app.py) is responsible for
    only letting this be called when confirmed_by is the request's current
    owner - delivery must be confirmed by the preparer, not just anyone
    looking at the board.
    """
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET delivered_at = ?, stage = ?, delivered_by = ? WHERE id = ?",
        (now_iso(), STAGE_DELIVERED, confirmed_by, req_id),
    )
    conn.commit()
    conn.close()


def reassign(req_id, new_owner, db_path=DB_PATH):
    """
    Reassign an overdue request to a new owner. Only meaningful while the
    request is still 'Received' (waiting to be picked up) - restarts the
    wait clock (received_at) against the new owner and counts the reassignment.
    """
    conn = get_conn(db_path)
    conn.execute(
        """
        UPDATE requests
        SET owner = ?, received_at = ?, reassigned_count = reassigned_count + 1
        WHERE id = ?
        """,
        (new_owner, now_iso(), req_id),
    )
    conn.commit()
    conn.close()


def parse(ts):
    return datetime.fromisoformat(ts) if ts else None


def minutes_between(start, end):
    if not start or not end:
        return None
    return (parse(end) - parse(start)).total_seconds() / 60.0


def is_overdue(row, reference_time=None):
    """A request is overdue if it's still waiting to be picked up past its timeout."""
    if row["stage"] != STAGE_RECEIVED or not row["received_at"]:
        return False
    ref = reference_time or now_iso()
    elapsed = minutes_between(row["received_at"], ref)
    return elapsed is not None and elapsed > row["timeout_minutes"]


def create_notification(request_id, recipient, message, db_path=DB_PATH):
    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO notifications (request_id, recipient, message, created_at) VALUES (?, ?, ?, ?)",
        (request_id, recipient, message, now_iso()),
    )
    conn.commit()
    conn.close()


def fetch_notifications(db_path=DB_PATH, recipient=None):
    conn = get_conn(db_path)
    if recipient:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE recipient = ? ORDER BY id DESC", (recipient,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def auto_reassign_overdue(db_path=DB_PATH):
    """
    Check every request still waiting to be picked up. Any that have sat
    past their timeout get automatically reassigned to their owner's named
    backup, and both the original owner and the backup get a notification
    logged in the app.

    Called on every app load/interaction (see app.py) rather than on a true
    background timer, since a Streamlit app has no process running when
    nobody has it open. For this prototype that means: the moment anyone
    hits the app after the timeout has passed, the reassignment has already
    happened before they see the board.

    This only fires ONCE per request (guarded by reassigned_count == 0).
    Without that guard, if the backup also lets it sit past the timeout,
    it would bounce back to the original owner, then back again,
    indefinitely, re-firing notifications every cycle. Kostas's requirement
    describes one handoff (owner -> named backup), not an infinite
    ping-pong, so once a request has been handed to the backup, it just
    stays flagged OVERDUE on the board if the backup doesn't act either.
    """
    reassigned = []
    for row in fetch_all(db_path):
        if row["stage"] != STAGE_RECEIVED or not is_overdue(row):
            continue
        if row["reassigned_count"] > 0:
            continue  # already handed off once - don't keep bouncing between the pair

        old_owner = row["owner"]
        backup = BACKUP_OF.get(old_owner)
        if not backup or backup == old_owner:
            continue  # no backup configured for this person - leave it flagged, don't loop

        reassign(row["id"], backup, db_path)

        create_notification(
            row["id"],
            old_owner,
            f"Request #{row['id']} ({row['document_type']}) timed out waiting on you "
            f"and was automatically reassigned to {backup}.",
            db_path,
        )
        create_notification(
            row["id"],
            backup,
            f"Request #{row['id']} ({row['document_type']}) was automatically reassigned "
            f"to you as backup for {old_owner} because it timed out.",
            db_path,
        )
        reassigned.append({"id": row["id"], "from": old_owner, "to": backup})

    return reassigned
