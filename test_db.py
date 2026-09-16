"""
Manual sanity test for db.py, run without Streamlit.

Simulates three requests covering the behaviors DocFlow needs to prove:
1. A normal request that gets assigned, prepped, and delivered.
2. A request that sits assigned past its timeout and gets flagged + reassigned.
3. A request that's submitted but left unassigned.

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


# --- Request 1: normal flow, assigned at submission ---
req1_id = db.insert_request("Ayma", "NDA", "Standard NDA for a new vendor", "Ayma", 30, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 starts Assigned (assignee given at submission)", row1["status"] == db.STATUS_ASSIGNED)
check("Request 1 not overdue immediately", not db.is_overdue(row1))

db.start_prep(req1_id, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 moves to In Prep", row1["status"] == db.STATUS_IN_PREP)

db.mark_delivered(req1_id, TEST_DB)
row1 = db.fetch_one(req1_id, TEST_DB)
check("Request 1 moves to Delivered", row1["status"] == db.STATUS_DELIVERED)
check("Request 1 has a delivered_at timestamp", row1["delivered_at"] is not None)

# --- Request 2: assigned, then sits idle past timeout -> flagged + reassigned ---
req2_id = db.insert_request("Kostas", "Invoice", "Q3 invoice batch", "Team Member A", 30, TEST_DB)

# Manually backdate assigned_at to simulate 45 minutes having passed
conn = sqlite3.connect(TEST_DB)
past = (datetime.now() - timedelta(minutes=45)).isoformat(timespec="seconds")
conn.execute("UPDATE requests SET assigned_at = ? WHERE id = ?", (past, req2_id))
conn.commit()
conn.close()

row2 = db.fetch_one(req2_id, TEST_DB)
check("Request 2 (45 min old, 30 min timeout) is flagged overdue", db.is_overdue(row2))

db.assign_request(req2_id, "Team Member B", is_reassignment=True, db_path=TEST_DB)
row2 = db.fetch_one(req2_id, TEST_DB)
check("Request 2 reassigned to Team Member B", row2["assigned_to"] == "Team Member B")
check("Request 2 reassigned_count incremented", row2["reassigned_count"] == 1)
check("Request 2 no longer overdue right after reassignment", not db.is_overdue(row2))

# --- Request 3: submitted, left unassigned ---
req3_id = db.insert_request("Sampo", "Report", "Monthly lab report", "Unassigned", 30, TEST_DB)
row3 = db.fetch_one(req3_id, TEST_DB)
check("Request 3 stays Submitted (no assignee)", row3["status"] == db.STATUS_SUBMITTED)
check("Request 3 is never 'overdue' (only Assigned items can be)", not db.is_overdue(row3))

# --- fetch_all sanity ---
all_rows = db.fetch_all(TEST_DB)
check("fetch_all returns all 3 requests", len(all_rows) == 3)

# --- wait/prep minutes math (same logic app.py uses) ---
wait = db.minutes_between(row1["submitted_at"], row1["prep_started_at"])
prep = db.minutes_between(row1["prep_started_at"], row1["delivered_at"])
check("Request 1 wait_minutes is a small non-negative number", wait is not None and wait >= 0)
check("Request 1 prep_minutes is a small non-negative number", prep is not None and prep >= 0)

os.remove(TEST_DB)
print("\nAll checks passed.")
