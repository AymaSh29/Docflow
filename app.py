"""
DocFlow - Document Request Tracker (prototype)

A tiny shared tool for a document-request pipeline:
- submit a request
- assign it to a person
- track it through Submitted -> Assigned -> In Prep -> Delivered
- reassign automatically-flagged, overdue requests (configurable timeout)
- confirm delivery in one click
- see a median wait-time vs prep-time readout

Storage: local SQLite file (docflow.db), shared by everyone hitting this app.
Data layer lives in db.py so it can be tested without Streamlit installed.
"""

import pandas as pd
import streamlit as st

import db

DEFAULT_TIMEOUT_MINUTES = 30
TEAM_MEMBERS = ["Unassigned", "Ayma", "Kostas", "Team Member A", "Team Member B"]

st.set_page_config(page_title="DocFlow", page_icon="\U0001F4C4", layout="wide")
db.init_db()

st.title("DocFlow - Document Request Tracker")

tab_submit, tab_board, tab_metrics = st.tabs(["Submit Request", "Status Board", "Readout"])

# --- Submit tab ---
with tab_submit:
    st.subheader("New document request")
    with st.form("submit_form", clear_on_submit=True):
        requester_name = st.text_input("Your name")
        document_type = st.text_input("Document type (e.g. NDA, Invoice, Report)")
        description = st.text_area("Description / notes", height=80)
        col1, col2 = st.columns(2)
        with col1:
            assigned_to = st.selectbox("Assign to", TEAM_MEMBERS)
        with col2:
            timeout_minutes = st.number_input(
                "Reassignment timeout (minutes)",
                min_value=1,
                value=DEFAULT_TIMEOUT_MINUTES,
                step=5,
                help="If this request is still 'Assigned' (not yet in prep) past this many minutes, it's flagged for reassignment on the Status Board.",
            )
        submitted = st.form_submit_button("Submit request")

        if submitted:
            if not requester_name or not document_type:
                st.error("Please fill in at least your name and the document type.")
            else:
                db.insert_request(requester_name, document_type, description, assigned_to, int(timeout_minutes))
                st.success("Request submitted.")

# --- Status board tab ---
with tab_board:
    st.subheader("Shared status board")
    refresh_col, _ = st.columns([1, 5])
    with refresh_col:
        if st.button("Refresh"):
            st.rerun()

    rows = db.fetch_all()

    if not rows:
        st.info("No requests yet. Submit one from the 'Submit Request' tab.")
    else:
        for row in rows:
            overdue = db.is_overdue(row)
            with st.container(border=True):
                header_col, status_col = st.columns([4, 1])
                with header_col:
                    st.markdown(
                        f"**#{row['id']} - {row['document_type']}** requested by {row['requester_name']}"
                    )
                    if row["description"]:
                        st.caption(row["description"])
                with status_col:
                    if overdue:
                        st.error("OVERDUE")
                    else:
                        st.write(f"**{row['status']}**")

                ts_col1, ts_col2, ts_col3, ts_col4 = st.columns(4)
                ts_col1.caption(f"Submitted\n\n{row['submitted_at'] or '-'}")
                ts_col2.caption(f"Assigned to {row['assigned_to'] or '-'}\n\n{row['assigned_at'] or '-'}")
                ts_col3.caption(f"Prep started\n\n{row['prep_started_at'] or '-'}")
                ts_col4.caption(f"Delivered\n\n{row['delivered_at'] or '-'}")

                action_col1, action_col2, action_col3 = st.columns(3)

                with action_col1:
                    if row["status"] in (db.STATUS_SUBMITTED, db.STATUS_ASSIGNED):
                        new_assignee = st.selectbox(
                            "Reassign to",
                            TEAM_MEMBERS,
                            key=f"reassign_{row['id']}",
                            label_visibility="collapsed",
                        )
                        if st.button("Assign / Reassign", key=f"assign_btn_{row['id']}"):
                            if new_assignee != "Unassigned":
                                db.assign_request(row["id"], new_assignee, is_reassignment=overdue)
                                st.rerun()

                with action_col2:
                    if row["status"] == db.STATUS_ASSIGNED:
                        if st.button("Start Prep", key=f"prep_btn_{row['id']}"):
                            db.start_prep(row["id"])
                            st.rerun()

                with action_col3:
                    if row["status"] == db.STATUS_IN_PREP:
                        if st.button("Mark Delivered", key=f"deliver_btn_{row['id']}", type="primary"):
                            db.mark_delivered(row["id"])
                            st.rerun()

                if row["reassigned_count"]:
                    st.caption(f"Reassigned {row['reassigned_count']} time(s)")

# --- Readout tab ---
with tab_metrics:
    st.subheader("Wait time vs prep time")
    rows = db.fetch_all()
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No data yet.")
    else:
        df["wait_minutes"] = df.apply(
            lambda r: db.minutes_between(
                r["submitted_at"], r["prep_started_at"] or r["delivered_at"]
            ),
            axis=1,
        )
        df["prep_minutes"] = df.apply(
            lambda r: db.minutes_between(r["prep_started_at"], r["delivered_at"]),
            axis=1,
        )

        delivered = df[df["status"] == db.STATUS_DELIVERED]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total requests", len(df))
        col2.metric(
            "Median wait time (min)",
            round(delivered["wait_minutes"].median(), 1) if not delivered.empty else "-",
        )
        col3.metric(
            "Median prep time (min)",
            round(delivered["prep_minutes"].median(), 1) if not delivered.empty else "-",
        )

        st.caption("Wait = time from submission to prep starting. Prep = time from prep starting to delivery.")
        st.dataframe(
            df[["id", "document_type", "status", "assigned_to", "wait_minutes", "prep_minutes", "reassigned_count"]],
            use_container_width=True,
            hide_index=True,
        )
