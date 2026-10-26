import streamlit as st
import auth
import db
import dataset
import recipient_ui
import os
import json
import uuid
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from emailer import send_bulk, DAILY_CAP

# Push Streamlit secrets into os.environ
for key, value in st.secrets.items():
    os.environ[key] = str(value)

st.set_page_config(page_title="Bulk Email Sender", page_icon="📧")

# Scheduled times are picked and shown in this zone, and stored in UTC
APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))

def local_time(utc_naive):
    """A naive UTC datetime from the database, in APP_TZ."""
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(APP_TZ)

# ---------- Session / login ----------
def login_screen():
    st.title("📧 Bulk Email Sender — Login")
    if "failed_attempts" not in st.session_state:
        st.session_state.failed_attempts = 0
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Log in"):
            user = auth.verify_user(u, p)
            if user:
                st.session_state.failed_attempts = 0
                st.session_state.user = user
                st.rerun()
            else:
                st.session_state.failed_attempts += 1
                if st.session_state.failed_attempts >= 5:
                    st.error("Too many failed attempts. Please wait.")
                    time.sleep(5)
                else:
                    st.error("Invalid credentials or inactive account.")

if "user" not in st.session_state:
    login_screen()
    st.stop()

user = st.session_state.user
# keep recipient choices (filters, typed addresses …) while their widgets are off screen
recipient_ui.keep_alive()
custom_uploader_was_shown = st.session_state.pop("custom_uploader_shown", False)
st.sidebar.write(f"Signed in as **{user['username']}** ({user['role']})")
if st.sidebar.button("Log out"):
    del st.session_state.user
    st.rerun()

# ---------- Sidebar: test email ----------
with st.sidebar.expander("🧪 Send test email"):
    default_to = os.environ.get("GMAIL_ADDRESS", "")
    test_to = st.text_input("Send test to", value=default_to, key="test_to")
    if st.button("Send test", key="send_test"):
        if not test_to or "@" not in test_to:
            st.warning("Enter a valid email address.")
        else:
            results = send_bulk(
                [test_to.strip()],
                subject="Test email — Bulk Email Sender",
                body=(
                    "This is a test message from the Bulk Email Sender app.\n\n"
                    f"Sent by: {user['username']}\n"
                    "If you received this, the Workspace SMTP path is working."
                ),
            )
            addr, status, err = results[0]
            try:
                db.log_emails_bulk(
                    [(None, "TEST", addr, "Test email", status, err, user["username"], None)]
                )
            except Exception:
                pass
            if status == "SENT":
                st.success(f"Test sent to {addr}.")
            else:
                st.error(f"Failed: {err}")

@st.cache_data(show_spinner="Loading verified distributors…", max_entries=3)
def load_verified_list(list_id, uploaded_at):
    """(table, CSV for download) of a stored list, or None if it was deleted.
    uploaded_at is part of the cache key so a reused id can't serve old data."""
    data = db.get_verified_list_data(list_id)
    if data is None:
        return None
    df = dataset.read_saved_table(data)
    return df, dataset.to_csv_bytes(df)

def verified_upload_box(current):
    """Upload box that replaces the verified distributors list."""
    if "verified_upload_nonce" not in st.session_state:
        st.session_state.verified_upload_nonce = 0
    st.caption(
        "Upload a fresh CSV or Excel export when the list changes. It replaces the "
        "list for everyone using the app (earlier uploads stay in the database). "
        "Any extra columns it has — not just Area and City — can be used as filters."
    )
    up = st.file_uploader(
        "Verified distributors file", type=dataset.FILE_TYPES,
        key=f"verified_upload_{st.session_state.verified_upload_nonce}",
    )
    if not up:
        return
    data = up.getvalue()
    try:
        sheet = recipient_ui.pick_sheet(data, up.name, "verified_upload_sheet")
        new_df = recipient_ui.load_table(data, up.name, sheet)
    except Exception as e:
        st.error(f"Couldn't read {up.name}: {e}")
        return
    email_cols = dataset.detect_email_columns(new_df)
    found = dataset.extract_recipients(new_df, email_cols[:1]) if email_cols else None
    if new_df.empty:
        st.error(f"{up.name} has no rows.")
        return
    if not found or not found["recipients"]:
        st.error(f"Couldn't find any email addresses in {up.name} — check the file.")
        return
    filters = dataset.detect_location_columns(new_df, email_cols)
    st.write(
        f"**{len(new_df):,}** rows · **{len(found['recipients']):,}** unique "
        f"emails in **{email_cols[0]}** · filters: "
        f"{', '.join(filters) or 'none detected (any column can still be used)'}"
    )
    st.caption(f"Columns: {', '.join(new_df.columns)}")
    if current and len(new_df) < 0.5 * current["row_count"]:
        st.warning(f"This file has far fewer rows than the current list "
                   f"({len(new_df):,} vs {current['row_count']:,}) — is it the right one?")
    label = "Replace the current list" if current else "Save as the verified list"
    if st.button(label, type="primary", key="verified_save"):
        try:
            db.save_verified_list(
                up.name, dataset.to_csv_bytes(new_df, row_numbers=True), len(new_df),
                user["username"],
            )
        except Exception as e:
            st.error(f"Couldn't save the list: {e}")
        else:
            st.session_state.verified_upload_nonce += 1
            st.session_state.pop("verified_list", None)  # switch to the new list
            st.session_state.verified_flash = (
                f"Verified distributors list updated from {up.name} ({len(new_df):,} rows)."
            )
            st.rerun()

def verified_distributor_recipients():
    """Recipients from the stored verified distributors list, plus the upload
    box that replaces that list. Returns the selected addresses.

    The session sticks to the list it started with, so a colleague uploading a
    new one can't swap the list (and reset the filters) mid-compose."""
    try:
        newest = db.get_verified_list_meta()
    except Exception as e:
        st.error(
            f"Couldn't load the verified distributors list: {e}\n\n"
            f"If the table is missing, create it with:\n\n`{db.VERIFIED_LISTS_DDL}`"
        )
        return []
    if newest and "verified_list" not in st.session_state:
        st.session_state.verified_list = newest
    current = st.session_state.get("verified_list")

    flash = st.session_state.pop("verified_flash", None)
    if flash:
        st.success(flash)
    if current:
        st.caption(
            f"Using **{current['filename']}** — {current['row_count']:,} rows, uploaded by "
            f"{current['uploaded_by']} on {current['uploaded_at']:%Y-%m-%d %H:%M}."
        )
        if newest and newest["id"] != current["id"]:
            st.warning(
                f"A newer list ({newest['filename']}) was uploaded by "
                f"{newest['uploaded_by']} on {newest['uploaded_at']:%Y-%m-%d %H:%M}. "
                "You're still on the one above, so your filters and ticks are unchanged."
            )
            if st.button("Switch to the newer list", key="verified_switch"):
                st.session_state.verified_list = newest
                st.rerun()
    else:
        st.info("No verified distributors list yet. Upload the CSV / Excel file below to set it up.")

    loaded = None
    if current:
        try:
            loaded = load_verified_list(current["id"], str(current["uploaded_at"]))
        except Exception as e:
            st.error(f"Couldn't load the verified distributors list: {e}")
            return []
        if loaded is None:
            st.session_state.pop("verified_list", None)
            st.warning("The list you were using is no longer in the database. "
                       "Reload the page to pick up the current one.")
            return []

    with st.expander(
        "⬆️ Upload an updated list" if current else "⬆️ Upload the verified distributors list",
        expanded=not current,
    ):
        verified_upload_box(current)
        if loaded:
            st.download_button(
                "Download the current list (CSV)", loaded[1],
                file_name=f"{current['filename'].rsplit('.', 1)[0]}.csv", mime="text/csv",
            )

    if not loaded:
        return []
    return recipient_ui.dataset_picker(
        loaded[0], "verified", source_id=str(current["id"]),
        problem_hint="Fix these in your copy of the list and upload it again "
                     "(row numbers match the file that was uploaded).",
    )

def schedule_controls(rtype, recipients, subject, body, att_name, att_bytes, can_send):
    """Date and time pickers, and the button that queues the email for later."""
    now = datetime.now(APP_TZ)
    if "schedule_default" not in st.session_state:
        # the next whole hour, fixed once so the pickers don't move on every rerun
        st.session_state.schedule_default = (now + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
    default = st.session_state.schedule_default
    c1, c2 = st.columns(2)
    send_date = c1.date_input("Send on", value=default.date(), min_value=now.date(),
                              key="schedule_date")
    send_time = c2.time_input("At", value=default.time(), step=timedelta(minutes=15),
                              key="schedule_time")
    send_at = datetime.combine(send_date, send_time, tzinfo=APP_TZ)
    st.caption(
        f"Times are in {APP_TZ.key}. The emails go out within a few minutes of that time, "
        f"up to {DAILY_CAP} in any 24 hours; any more follow automatically as the "
        "allowance frees up."
    )

    # stops a second click scheduling the same email twice
    fingerprint = (rtype, subject, body, att_name, tuple(recipients), send_at)
    already = st.session_state.get("last_scheduled") == fingerprint
    if already:
        st.success(f"Scheduled for {send_at:%d %b %Y, %H:%M}. Follow or cancel it under "
                   "Scheduled Sends.")
    if st.button("Schedule send", type="primary", disabled=not can_send or already):
        if send_at <= now:
            st.error("Pick a time in the future.")
            return
        try:
            db.schedule_send(
                rtype, subject, body, att_name, att_bytes, recipients,
                send_at.astimezone(timezone.utc).replace(tzinfo=None), user["username"],
            )
        except Exception as e:
            st.error(f"Couldn't schedule the send: {e}")
            return
        st.session_state.last_scheduled = fingerprint
        st.rerun()

pages = ["Compose & Send", "Scheduled Sends", "Pending Sends", "Activity Log"]
if user["role"] == "admin":
    pages.append("Admin")
page = st.sidebar.radio("Navigate", pages)

# ---------- Compose & Send ----------
if page == "Compose & Send":
    st.header("Compose & Send")

    if "confirm_send" not in st.session_state:
        st.session_state.confirm_send = False
    if "sending" not in st.session_state:
        st.session_state.sending = False
    if "staged" not in st.session_state:
        st.session_state.staged = None

    recipient_mode = st.radio(
        "Recipients", ["Retailer Type", "Custom", "Verified Distributors"], horizontal=True,
        key=recipient_ui.keep("recipient_mode"),
    )

    if recipient_mode == "Retailer Type":
        rtype = st.selectbox("Retailer Type", db.get_retailer_types())
        all_recipients = db.get_emails_for_type(rtype) if rtype else []
        # selections reset when the retailer type changes
        recipients = recipient_ui.recipient_picker(all_recipients, "rt", reset_token=rtype)

    elif recipient_mode == "Custom":
        rtype = "CUSTOM"
        upload = st.file_uploader(
            "Upload a CSV or Excel file (optional)", type=dataset.FILE_TYPES,
            key="custom_list_file",
            help="Any spreadsheet works — the email column is found automatically and "
                 "the other columns can be used as filters.",
        )
        # keep the file while the uploader is off screen (other mode / page);
        # an empty uploader that was on screen last run means it was removed
        if upload:
            st.session_state.custom_file = (upload.name, upload.getvalue())
        elif custom_uploader_was_shown:
            st.session_state.pop("custom_file", None)
        st.session_state.custom_uploader_shown = True
        list_file = st.session_state.get("custom_file")
        if list_file and not upload:
            c1, c2 = st.columns([4, 1])
            c1.caption(f"Still using **{list_file[0]}** from earlier — upload another "
                       "file to replace it.")
            if c2.button("Remove file", key="custom_remove_file"):
                st.session_state.pop("custom_file", None)
                st.rerun()
        file_recipients = recipient_ui.file_picker(*list_file, "custom") if list_file else []

        raw = st.text_area(
            "Add more email addresses" if list_file else "Enter email addresses",
            placeholder="One per line, or comma-separated",
            height=100 if list_file else 150,
            key=recipient_ui.keep("custom_typed"),
        )
        typed, rejected = dataset.parse_typed_emails(raw)
        recipients, seen = [], set()
        for e in file_recipients + typed:
            if e.lower() not in seen:
                seen.add(e.lower())
                recipients.append(e)

        if rejected:
            shown = [
                f"{r} (did you mean {', '.join(f for f, _ in fixes)}?)"
                if (fixes := dataset.suggest_cell_fixes(r)) else r
                for r in rejected[:10]
            ]
            st.warning(
                f"Skipped {len(rejected)} typed entr{'y' if len(rejected) == 1 else 'ies'} "
                f"that aren't valid email addresses: {', '.join(shown)}"
                + (" …" if len(rejected) > 10 else "")
            )
        if list_file and typed:
            added = len(recipients) - len(file_recipients)
            st.info(
                f"**{len(recipients)}** recipients in total: {len(file_recipients)} selected "
                f"from the file + {added} typed"
                + (f" ({len(typed) - added} typed already in the file)" if len(typed) > added else "")
                + "."
            )
        elif not list_file and raw.strip() and recipients:
            st.info(f"{len(recipients)} valid email address(es) entered.")
        elif not list_file and raw.strip() and not recipients:
            st.warning("No valid email addresses found — check formatting.")

    else:
        rtype = "VERIFIED DISTRIBUTORS"
        recipients = verified_distributor_recipients()

    subject = st.text_input("Subject")
    body = st.text_area("Message body", height=200)
    up = st.file_uploader("Attachment (optional)")
    att_name = up.name if up else None
    att_bytes = up.getvalue() if up else None

    over_cap = len(recipients) > DAILY_CAP
    can_send = bool(recipients and subject and body)

    when = st.radio(
        "When", ["Send now", "Schedule for later"], horizontal=True, key="send_when",
        disabled=st.session_state.confirm_send or st.session_state.sending,
    )
    if when == "Schedule for later":
        schedule_controls(rtype, recipients, subject, body, att_name, att_bytes, can_send)

    if when == "Send now" and over_cap:
        st.warning(
            f"⚠️ {len(recipients)} recipients exceeds the daily cap of {DAILY_CAP}. "
            f"The first **{DAILY_CAP}** will be sent now and the remaining "
            f"**{len(recipients) - DAILY_CAP}** will be saved to Pending Sends."
        )

    btn_label = f"Send first {DAILY_CAP} now & save remainder" if over_cap else "Send now"

    if when == "Send now" and not st.session_state.confirm_send and not st.session_state.sending:
        if st.button(btn_label, type="primary", disabled=not can_send):
            st.session_state.staged = {
                "rtype": rtype,
                "recipients": recipients,
                "subject": subject,
                "body": body,
                "att_name": att_name,
                "att_bytes": att_bytes,
                "over_cap": over_cap,
            }
            st.session_state.confirm_send = True
            st.rerun()

    if st.session_state.confirm_send and st.session_state.staged:
        s = st.session_state.staged
        send_count = min(len(s["recipients"]), DAILY_CAP)
        st.warning(
            f"About to send **{send_count}** emails "
            f"({'+ save remainder' if s['over_cap'] else ''}). "
            "This cannot be undone."
        )
        col1, col2 = st.columns(2)
        if col1.button("✅ Yes, send", type="primary"):
            st.session_state.confirm_send = False
            st.session_state.sending = True
            st.rerun()
        if col2.button("❌ Cancel"):
            st.session_state.confirm_send = False
            st.session_state.staged = None
            st.rerun()

    if st.session_state.sending and st.session_state.staged:
            st.warning(
                "⚠️ **Sending in progress — please keep this tab open.** "
                "Do not close, refresh, or navigate away from this page until you see "
                "the 'Done' message, or the send may be interrupted."
            )
            s = st.session_state.staged
            first_batch = s["recipients"][:DAILY_CAP]
            remainder = s["recipients"][DAILY_CAP:]

            # Save the remainder FIRST — before the long send — so an interruption
            # mid-send can't lose it. The flag stops a rerun from double-saving.
            if remainder and not s.get("remainder_saved"):
                try:
                    db.save_remainder(
                        label=f"{s['rtype']} — remainder ({len(remainder)} recipients)",
                        retailer_type=s["rtype"],
                        subject=s["subject"],
                        body=s["body"],
                        attachment_name=s["att_name"],
                        attachment_bytes=s["att_bytes"],
                        recipients=remainder,
                        created_by=user["username"],
                    )
                    s["remainder_saved"] = True
                    st.info(f"✅ {len(remainder)} remaining recipients saved to Pending Sends.")
                except Exception as e:
                    st.error(f"Could not save remainder to Pending Sends: {e}")

            batch_id = str(uuid.uuid4())
            bar = st.progress(0, text="Sending…")
            results = send_bulk(
                first_batch, s["subject"], s["body"], s["att_name"], s["att_bytes"],
                progress_cb=lambda i, n: bar.progress(i / n, text=f"Sent {i}/{n}"),
            )

            records = [
                (None, s["rtype"], addr, s["subject"], status, err, user["username"], batch_id)
                for addr, status, err in results
            ]
            try:
                db.log_emails_bulk(records)
            except Exception as e:
                st.warning(f"Emails were sent, but writing to the activity log failed: {e}")

            sent = sum(1 for _, st_, _ in results if st_ == "SENT")
            st.success(f"Done. {sent}/{len(first_batch)} sent.")
            failed = [(a, e) for a, st_, e in results if st_ == "FAILED"]
            if failed:
                st.error("Failures:")
                st.table(failed)

            st.session_state.sending = False
            st.session_state.staged = None

# ---------- Scheduled Sends ----------
elif page == "Scheduled Sends":
    st.header("Scheduled Sends")
    st.caption(
        "Scheduled emails are sent by `scheduler_worker.py`, which has to run every few "
        "minutes on a machine that can reach the database (for example with Windows "
        f"Task Scheduler). Times are in {APP_TZ.key}."
    )

    flash = st.session_state.pop("scheduled_flash", None)
    if flash:
        st.info(flash)

    try:
        sends = db.get_scheduled_sends()
    except Exception as e:
        st.error(f"Couldn't load scheduled sends: {e}")
        sends = []
    if not sends:
        st.info("Nothing scheduled. Choose “Schedule for later” on Compose & Send.")

    status_labels = {"PENDING": "⏰ Scheduled", "SENDING": "📤 Sending",
                     "SENT": "✅ Sent", "CANCELLED": "🚫 Cancelled"}
    overdue_before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
    for s in sends:
        active = s["status"] in ("PENDING", "SENDING")
        with st.expander(
            f"{status_labels.get(s['status'], s['status'])} · "
            f"{local_time(s['send_at']):%d %b %Y, %H:%M} · {s['subject']}",
            expanded=active,
        ):
            st.write(
                f"**Recipients:** {s['recipient_count']} ({s['retailer_type']}) · "
                f"**sent so far:** {s['sent_count']} · **scheduled by:** {s['created_by']}"
            )
            if s["result_summary"]:
                st.caption(s["result_summary"])
            if s["status"] == "PENDING" and not s["result_summary"] and s["send_at"] < overdue_before:
                st.warning("This is overdue. Check that scheduler_worker.py is running.")
            if s["status"] == "PENDING" and st.button("Cancel this send", key=f"cancel_sched_{s['id']}"):
                if db.cancel_scheduled_send(s["id"]):
                    st.session_state.scheduled_flash = f"Cancelled “{s['subject']}”."
                else:
                    st.session_state.scheduled_flash = (
                        f"Too late to cancel “{s['subject']}”: it has started sending.")
                st.rerun()

# ---------- Pending Sends ----------
elif page == "Pending Sends":
    st.header("Pending Sends")

    if "confirm_remainder" not in st.session_state:
        st.session_state.confirm_remainder = None

    remainders = db.get_pending_remainders()
    if not remainders:
        st.info("No pending sends. You're all caught up.")
    else:
        for r in remainders:
            recipients = json.loads(r["recipients"])
            over_cap = len(recipients) > DAILY_CAP
            with st.expander(f"📬 {r['label']} — saved {r['created_at']:%Y-%m-%d %H:%M}"):
                st.write(f"**Subject:** {r['subject']}")
                st.write(f"**Recipients:** {len(recipients)}")
                st.write(f"**Saved by:** {r['created_by']}")
                with st.expander("Preview recipients"):
                    st.write(recipients)

                if over_cap:
                    st.warning(
                        f"Still {len(recipients)} recipients — over cap. "
                        f"Will send first {DAILY_CAP} and re-save the rest."
                    )

                btn_label = (
                    f"Send first {DAILY_CAP} & save remainder"
                    if over_cap else f"Send all {len(recipients)} now"
                )

                if st.session_state.confirm_remainder != r["id"]:
                    if st.button(btn_label, key=f"send_rem_{r['id']}", type="primary"):
                        st.session_state.confirm_remainder = r["id"]
                        st.rerun()
                else:
                    send_count = min(len(recipients), DAILY_CAP)
                    st.warning(
                        f"About to send **{send_count}** emails. This cannot be undone."
                    )
                    col1, col2 = st.columns(2)
                    if col1.button("✅ Yes, send", key=f"confirm_yes_{r['id']}", type="primary"):
                        st.session_state.confirm_remainder = None
                        send_now = recipients[:DAILY_CAP]
                        leftover = recipients[DAILY_CAP:]

                        batch_id = str(uuid.uuid4())
                        bar = st.progress(0, text="Sending…")
                        att_bytes = bytes(r["attachment_data"]) if r["attachment_data"] else None
                        results = send_bulk(
                            send_now, r["subject"], r["body"],
                            r["attachment_name"], att_bytes,
                            progress_cb=lambda i, n: bar.progress(i / n, text=f"Sent {i}/{n}"),
                        )

                        # --- bulk log ---
                        records = [
                            (None, r["retailer_type"], addr, r["subject"],
                             status, err, user["username"], batch_id)
                            for addr, status, err in results
                        ]
                        try:
                            db.log_emails_bulk(records)
                        except Exception as e:
                            st.warning(f"Emails were sent, but writing to the activity log failed: {e}")

                        sent = sum(1 for _, s, _ in results if s == "SENT")
                        st.success(f"Done. {sent}/{len(send_now)} sent.")
                        failed = [(a, e) for a, s, e in results if s == "FAILED"]
                        if failed:
                            st.error("Failures:")
                            st.table(failed)

                        db.mark_remainder_sent(r["id"])

                        if leftover:
                            db.save_remainder(
                                label=f"{r['retailer_type']} — remainder ({len(leftover)} recipients)",
                                retailer_type=r["retailer_type"],
                                subject=r["subject"],
                                body=r["body"],
                                attachment_name=r["attachment_name"],
                                attachment_bytes=att_bytes,
                                recipients=leftover,
                                created_by=user["username"],
                            )
                            st.info(f"{len(leftover)} still remaining — saved back to Pending Sends.")
                        st.rerun()

                    if col2.button("❌ Cancel", key=f"confirm_no_{r['id']}"):
                        st.session_state.confirm_remainder = None
                        st.rerun()

# ---------- Activity Log ----------
elif page == "Activity Log":
    st.header("Activity Log")
    conn = db.get_conn()
    try:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT TOP 500 sent_at, sent_by, retailer_type, recipient, subject, status, error "
            "FROM EMAIL_LOG ORDER BY sent_at DESC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    if rows:
        st.dataframe(rows, width='stretch')
    else:
        st.info("No activity yet.")

# ---------- Admin ----------
elif page == "Admin":
    st.header("Admin — User Management")
    st.subheader("Add user")
    with st.form("adduser"):
        nu = st.text_input("New username", key="new_username")
        npw = st.text_input("Temp password", type="password", key="new_temp_pw")
        nrole = st.selectbox("Role", ["user", "admin"])
        if st.form_submit_button("Create"):
            try:
                auth.create_user(nu, npw, nrole)
                st.success(f"Created {nu}.")
            except Exception as e:
                st.error(str(e))

    st.subheader("Existing users")
    for u in auth.list_users():
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        c1.write(f"**{u['username']}** — {u['role']} — {'active' if u['is_active'] else 'disabled'}")
        if c2.button("Toggle active", key=f"act_{u['username']}"):
            auth.set_active(u['username'], not u['is_active'])
            st.rerun()
        newpw = c3.text_input("Reset pw", key=f"pw_{u['username']}", type="password",
                               label_visibility="collapsed")
        if c3.button("Reset", key=f"rst_{u['username']}") and newpw:
            auth.reset_password(u['username'], newpw)
            st.success("Reset.")
        if c4.button("Delete", key=f"del_{u['username']}"):
            if u['username'] == user["username"]:
                st.error("Can't delete yourself.")
            else:
                auth.delete_user(u['username'])
                st.rerun()