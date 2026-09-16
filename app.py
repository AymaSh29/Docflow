"""
DocFlow - Document Request Tracker (prototype)

A shared tool for a document-request pipeline. Every request is assigned to
an owner from the moment it's created, and moves through five stages on one
shared status board, each with its own timestamp:

    Received -> Picked Up -> In Preparation -> Approved -> Delivered

Also covers: reassigning a request that's sat too long waiting to be picked
up (configurable timeout), one-click delivery confirmation, and a median
wait-time vs. prep-time readout.

Storage: local SQLite file (docflow.db), shared by everyone hitting this app.
Data layer lives in db.py so it can be tested without Streamlit installed.
"""

import pandas as pd
import streamlit as st

import db

DEFAULT_TIMEOUT_MINUTES = 30
TEAM_MEMBERS = ["Ayma", "Kostas", "Team Member A", "Team Member B"]

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
            owner = st.selectbox("Owner (who this is assigned to)", TEAM_MEMBERS)
        with col2:
            timeout_minutes = st.number_input(
                "Reassignment timeout (minutes)",
                min_value=1,
                value=DEFAULT_TIMEOUT_MINUTES,
                step=5,
                help="If the owner hasn't picked this up within this many minutes, it's flagged for reassignment on the Status Board.",
            )
        submitted = st.form_submit_button("Submit request")

        if submitted:
            if not requester_name or not document_type:
                st.error("Please fill in at least your name and the document type.")
            else:
                db.insert_request(requester_name, document_type, description, owner, int(timeout_minutes))
                st.success(f"Request submitted and assigned to {owner}.")

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
                header_col, stage_col = st.columns([4, 1])
                with header_col:
                    st.markdown(
                        f"**#{row['id']} - {row['document_type']}** requested by {row['requester_name']}, "
                        f"owned by **{row['owner']}**"
                    )
                    if row["description"]:
                        st.caption(row["description"])
                with stage_col:
                    if overdue:
                        st.error("OVERDUE")
                    else:
                        st.write(f"**{row['stage']}**")

                empty = "not yet"
                ts_cols = st.columns(5)
                ts_cols[0].caption(f"Received\n\n{row['received_at'] or empty}")
                ts_cols[1].caption(f"Picked up\n\n{row['picked_up_at'] or empty}")
                ts_cols[2].caption(f"In preparation\n\n{row['prep_started_at'] or empty}")
                ts_cols[3].caption(f"Approved\n\n{row['approved_at'] or empty}")
                ts_cols[4].caption(f"Delivered\n\n{row['delivered_at'] or empty}")

                action_cols = st.columns(5)

                with action_cols[0]:
                    if row["stage"] == db.STAGE_RECEIVED:
                        new_owner = st.selectbox(
                            "Reassign to",
                            [m for m in TEAM_MEMBERS if m != row["owner"]],
                            key=f"reassign_{row['id']}",
                            label_visibility="collapsed",
                        )
                        if st.button("Reassign", key=f"reassign_btn_{row['id']}"):
                            db.reassign(row["id"], new_owner)
                            st.rerun()

                with action_cols[1]:
                    if row["stage"] == db.STAGE_RECEIVED:
                        if st.button("Pick Up", key=f"pickup_btn_{row['id']}"):
                            db.pick_up(row["id"])
                            st.rerun()

                with action_cols[2]:
                    if row["stage"] == db.STAGE_PICKED_UP:
                        if st.button("Start Preparation", key=f"prep_btn_{row['id']}"):
                            db.start_prep(row["id"])
                            st.rerun()

                with action_cols[3]:
                    if row["stage"] == db.STAGE_IN_PREPARATION:
                        if st.button("Approve", key=f"approve_btn_{row['id']}"):
                            db.approve(row["id"])
                            st.rerun()

                with action_cols[4]:
                    if row["stage"] == db.STAGE_APPROVED:
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
        # Pandas can silently store an empty timestamp column as NaN (float) instead of
        # Python's None. Unlike None, NaN is truthy, so falsy-based fallbacks below would
        # treat a missing timestamp as present and crash trying to parse it. Normalize
        # every NaN back to None first so the checks behave correctly.
        df = df.astype(object).where(df.notnull(), None)

        # Wait = time sitting before anyone picks it up.
        # Prep = time actually being worked, from pickup through delivery
        # (covers preparation and approval together).
        df["wait_minutes"] = df.apply(
            lambda r: db.minutes_between(r["received_at"], r["picked_up_at"]),
            axis=1,
        )
        df["prep_minutes"] = df.apply(
            lambda r: db.minutes_between(r["picked_up_at"], r["delivered_at"]),
            axis=1,
        )

        delivered = df[df["stage"] == db.STAGE_DELIVERED]

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

        st.caption("Wait = time from received to picked up. Prep = time from picked up to delivered (covers preparation and approval).")
        st.dataframe(
            df[["id", "document_type", "stage", "owner", "wait_minutes", "prep_minutes", "reassigned_count"]],
            use_container_width=True,
            hide_index=True,
        )
