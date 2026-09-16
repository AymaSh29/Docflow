"""
Manual sanity test for db.py, run without Streamlit.

Covers the five-stage model: Received -> Picked Up -> In Preparation ->
Approved -> Delivered, always owned, plus timeout-based reassignment.

Run: python3 test_db.py
"""

import os
import sqlite3
from datetime import datetime, timedelta

import db

TEST_DB = "test_docflow.db"

if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

db.init_db(TEST_DB)


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    assert condition, f"Failed: {label}"


# --- Request 1: full happy path through all five stages ---
req1_id = db.insert_request("Ayma", "NDA", "Standard NDA for a new vendor", "Ayma", 30, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 is owned from creation", row1["owner"] == "Ayma")
check("Request 1 starts at Received", row1["stage"] == db.STAGE_RECEIVED)
check("Request 1 has a received_at timestamp", row1["received_at"] is not None)
check("Request 1 not overdue immediately", not db.is_overdue(row1))

db.pick_up(req1_id, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 moves to Picked Up", row1["stage"] == db.STAGE_PICKED_UP)
check("Request 1 has a picked_up_at timestamp", row1["picked_up_at"] is not None)

db.start_prep(req1_id, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 moves to In Preparation", row1["stage"] == db.STAGE_IN_PREPARATION)
check("Request 1 has a prep_started_at timestamp", row1["prep_started_at"] is not None)

db.approve(req1_id, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 moves to Approved", row1["stage"] == db.STAGE_APPROVED)
check("Request 1 has an approved_at timestamp", row1["approved_at"] is not None)

db.mark_delivered(req1_id, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 moves to Delivered", row1["stage"] == db.STAGE_DELIVERED)
check("Request 1 has a delivered_at timestamp", row1["delivered_at"] is not None)

# --- Request 2: sits unpicked past timeout -> auto-reassigned + both notified ---
req2_id = db.insert_request("Kostas", "Invoice", "Q3 invoice batch", "Team Member A", 30, TEST_DB)

conn = sqlite3.connect(TEST_DB)
past = (datetime.now() - timedelta(minutes=45)).isoformat(timespec="seconds")
conn.execute("UPDATE requests SET received_at = ? WHERE id = ?", (past, req2_id))
conn.commit()
conn.close()

row2 = db.fetch_one(req2_id, TEST_DB)
check("Request 2 (45 min old, 30 min timeout) is flagged overdue", db.is_overdue(row2))
check("Team Member A's backup is Team Member B", db.BACKUP_OF["Team Member A"] == "Team Member B")

reassigned = db.auto_reassign_overdue(TEST_DB)
check("auto_reassign_overdue reports request 2 as reassigned", any(r["id"] == req2_id for r in reassigned))

row2 = db.fetch_one(req2_id, TEST_DB)
check("Request 2 auto-reassigned to its named backup (Team Member B)", row2["owner"] == "Team Member B")
check("Request 2 reassigned_count incremented", row2["reassigned_count"] == 1)
check("Request 2 no longer overdue right after reassignment", not db.is_overdue(row2))
check("Request 2 still at Received after reassignment", row2["stage"] == db.STAGE_RECEIVED)

notes_for_old_owner = db.fetch_notifications(TEST_DB, recipient="Team Member A")
notes_for_backup = db.fetch_notifications(TEST_DB, recipient="Team Member B")
check("Original owner (Team Member A) got a notification", len(notes_for_old_owner) == 1)
check("Backup (Team Member B) got a notification", len(notes_for_backup) == 1)
check("Notification mentions the request id", f"#{req2_id}" in notes_for_old_owner[0]["message"])

# Running auto_reassign_overdue again right away should NOT re-trigger anything,
# since the wait clock reset when it was reassigned.
reassigned_again = db.auto_reassign_overdue(TEST_DB)
check("No duplicate reassignment on immediate re-check", not any(r["id"] == req2_id for r in reassigned_again))
check(
    "No duplicate notifications on immediate re-check",
    len(db.fetch_notifications(TEST_DB, recipient="Team Member B")) == 1,
)

# Even if the BACKUP also lets it sit past the timeout, it must not bounce
# back to the original owner - one automatic handoff only, per request.
conn = sqlite3.connect(TEST_DB)
long_past = (datetime.now() - timedelta(minutes=45)).isoformat(timespec="seconds")
conn.execute("UPDATE requests SET received_at = ? WHERE id = ?", (long_past, req2_id))
conn.commit()
conn.close()

row2 = db.fetch_one(req2_id, TEST_DB)
check("Request 2 (now with backup as owner) is overdue again", db.is_overdue(row2))

reassigned_third_check = db.auto_reassign_overdue(TEST_DB)
row2 = db.fetch_one(req2_id, TEST_DB)
check("No ping-pong: request 2 does NOT bounce back to Team Member A", row2["owner"] == "Team Member B")
check("No ping-pong: reassigned_count stays at 1", row2["reassigned_count"] == 1)
check("No ping-pong: request 2 not reported as reassigned again", not any(r["id"] == req2_id for r in reassigned_third_check))
check(
    "No ping-pong: Team Member A doesn't get a second notification",
    len(db.fetch_notifications(TEST_DB, recipient="Team Member A")) == 1,
)

# --- insert_request requires an owner ---
try:
    db.insert_request("Sampo", "Report", "Monthly lab report", "", 30, TEST_DB)
    check("insert_request rejects an empty owner", False)
except ValueError:
    check("insert_request rejects an empty owner", True)

# --- fetch_all sanity ---
all_rows = db.fetch_all(TEST_DB)
check("fetch_all returns both requests", len(all_rows) == 2)

# --- wait/prep minutes math (same logic app.py uses) ---
wait = db.minutes_between(row1["received_at"], row1["picked_up_at"])
prep = db.minutes_between(row1["picked_up_at"], row1["delivered_at"])
check("Request 1 wait_minutes is a small non-negative number", wait is not None and wait >= 0)
check("Request 1 prep_minutes is a small non-negative number", prep is not None and prep >= 0)

os.remove(TEST_DB)
print("\nAll checks passed.")
