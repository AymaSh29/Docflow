"""
DocFlow data layer.

Kept separate from app.py (the Streamlit UI) so the request-tracking logic
can be tested with plain Python + sqlite3, no Streamlit dependency needed.
"""

import sqlite3
from datetime import datetime

DB_PATH = "docflow.db"

STATUS_SUBMITTED = "Submitted"
STATUS_ASSIGNED = "Assigned"
STATUS_IN_PREP = "In Prep"
STATUS_DELIVERED = "Delivered"


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
            submitted_at TEXT NOT NULL,
            assigned_to TEXT,
            assigned_at TEXT,
            prep_started_at TEXT,
            delivered_at TEXT,
            status TEXT NOT NULL,
            timeout_minutes INTEGER NOT NULL DEFAULT 30,
            reassigned_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def insert_request(requester_name, document_type, description, assigned_to, timeout_minutes, db_path=DB_PATH):
    conn = get_conn(db_path)
    submitted_at = now_iso()
    if assigned_to and assigned_to != "Unassigned":
        status = STATUS_ASSIGNED
        assigned_at = submitted_at
    else:
        status = STATUS_SUBMITTED
        assigned_at = None
    cur = conn.execute(
        """
        INSERT INTO requests
            (requester_name, document_type, description, submitted_at,
             assigned_to, assigned_at, status, timeout_minutes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            requester_name,
            document_type,
            description,
            submitted_at,
            None if assigned_to == "Unassigned" else assigned_to,
            assigned_at,
            status,
            timeout_minutes,
        ),
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


def assign_request(req_id, assignee, is_reassignment=False, db_path=DB_PATH):
    conn = get_conn(db_path)
    if is_reassignment:
        conn.execute(
            """
            UPDATE requests
            SET assigned_to = ?, assigned_at = ?, status = ?, reassigned_count = reassigned_count + 1
            WHERE id = ?
            """,
            (assignee, now_iso(), STATUS_ASSIGNED, req_id),
        )
    else:
        conn.execute(
            "UPDATE requests SET assigned_to = ?, assigned_at = ?, status = ? WHERE id = ?",
            (assignee, now_iso(), STATUS_ASSIGNED, req_id),
        )
    conn.commit()
    conn.close()


def start_prep(req_id, db_path=DB_PATH):
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET prep_started_at = ?, status = ? WHERE id = ?",
        (now_iso(), STATUS_IN_PREP, req_id),
    )
    conn.commit()
    conn.close()


def mark_delivered(req_id, db_path=DB_PATH):
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE requests SET delivered_at = ?, status = ? WHERE id = ?",
        (now_iso(), STATUS_DELIVERED, req_id),
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
    if row["status"] != STATUS_ASSIGNED or not row["assigned_at"]:
        return False
    ref = reference_time or now_iso()
    elapsed = minutes_between(row["assigned_at"], ref)
    return elapsed is not None and elapsed > row["timeout_minutes"]
