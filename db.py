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


def get_conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=DB_PATH):
    conn = get_conn(db_path)
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
            timeout_minutes INTEGER NOT NULL DEFAULT 30,
            reassigned_count INTEGER NOT NULL DEFAULT 0
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


def mark_delivered(req_id, db_path=DB_PATH):
    """Approved -> Delivered: one-click delivery confirmation."""
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET delivered_at = ?, stage = ? WHERE id = ?",
        (now_iso(), STAGE_DELIVERED, req_id),
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
