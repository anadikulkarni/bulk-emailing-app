import streamlit as st
import auth
import db
import os
import json
import uuid
import time
from emailer import send_bulk

# Push Streamlit secrets into os.environ
for key, value in st.secrets.items():
    os.environ[key] = str(value)

st.set_page_config(page_title="Bulk Email Sender", page_icon="📧")

DAILY_CAP = 1800

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
            db.log_email(None, "TEST", addr, "Test email", status, err, user["username"])
            if status == "SENT":
                st.success(f"Test sent to {addr}.")
            else:
                st.error(f"Failed: {err}")

pages = ["Compose & Send", "Pending Sends", "Activity Log"]
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
    if "recipient_selections" not in st.session_state:
        st.session_state.recipient_selections = {}
    if "last_rtype" not in st.session_state:
        st.session_state.last_rtype = None
    if "recipient_page" not in st.session_state:
        st.session_state.recipient_page = 0
    if "last_search" not in st.session_state:
        st.session_state.last_search = ""

    recipient_mode = st.radio("Recipients", ["Retailer Type", "Custom"], horizontal=True)

    if recipient_mode == "Retailer Type":
        rtype = st.selectbox("Retailer Type", db.get_retailer_types())

        # reset checkboxes and pagination when retailer type changes
        if rtype != st.session_state.last_rtype:
            st.session_state.last_rtype = rtype
            st.session_state.recipient_selections = {}
            st.session_state.recipient_page = 0
            st.session_state.last_search = ""

        all_recipients = db.get_emails_for_type(rtype) if rtype else []

        # initialise selections for any new entries (default all checked)
        for r in all_recipients:
            if r["email"] not in st.session_state.recipient_selections:
                st.session_state.recipient_selections[r["email"]] = True

        total = len(all_recipients)
        selected_count = sum(
            1 for r in all_recipients
            if st.session_state.recipient_selections.get(r["email"], True)
        )
        st.info(f"{selected_count} of {total} recipients selected.")

        with st.expander("Preview & select recipients"):
            # select all / deselect all
            col_a, col_b, _ = st.columns([1, 1, 4])
            if col_a.button("Select all", key="sel_all"):
                for r in all_recipients:
                    st.session_state.recipient_selections[r["email"]] = True
                st.rerun()
            if col_b.button("Deselect all", key="desel_all"):
                for r in all_recipients:
                    st.session_state.recipient_selections[r["email"]] = False
                st.rerun()

            # search filter
            search = st.text_input("Search by name or email", key="recipient_search")
            filtered = [
                r for r in all_recipients
                if search.lower() in r["name"].lower()
                or search.lower() in r["email"].lower()
            ] if search else all_recipients

            # reset to page 0 when search changes
            if search != st.session_state.last_search:
                st.session_state.recipient_page = 0
                st.session_state.last_search = search

            # pagination
            PAGE_SIZE = 100
            total_pages = max(1, -(-len(filtered) // PAGE_SIZE))

            # clamp page in case filtered results shrank
            st.session_state.recipient_page = min(
                st.session_state.recipient_page, total_pages - 1
            )
            page_idx = st.session_state.recipient_page
            page_slice = filtered[page_idx * PAGE_SIZE: (page_idx + 1) * PAGE_SIZE]

            # pagination controls
            pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
            if pcol1.button("← Prev", key="pg_prev", disabled=page_idx == 0):
                st.session_state.recipient_page -= 1
                st.rerun()
            pcol2.markdown(
                f"<div style='text-align:center; padding-top:6px'>"
                f"Page {page_idx + 1} of {total_pages} "
                f"({len(filtered)} result{'s' if len(filtered) != 1 else ''})"
                f"</div>",
                unsafe_allow_html=True,
            )
            if pcol3.button("Next →", key="pg_next", disabled=page_idx >= total_pages - 1):
                st.session_state.recipient_page += 1
                st.rerun()

            # render checkboxes for this page only
            for r in page_slice:
                checked = st.session_state.recipient_selections.get(r["email"], True)
                label = f"{r['name']}  —  {r['email']}" if r["name"] else r["email"]
                new_val = st.checkbox(label, value=checked, key=f"chk_{r['email']}")
                if new_val != checked:
                    st.session_state.recipient_selections[r["email"]] = new_val

        # final recipient list = only checked emails
        recipients = [
            r["email"] for r in all_recipients
            if st.session_state.recipient_selections.get(r["email"], True)
        ]

    else:
        rtype = "CUSTOM"
        raw = st.text_area(
            "Enter email addresses",
            placeholder="One per line, or comma-separated",
            height=150,
        )
        recipients = list({
            e.strip()
            for e in raw.replace(",", "\n").splitlines()
            if e.strip() and "@" in e.strip()
        })
        if raw and recipients:
            st.info(f"{len(recipients)} valid email address(es) entered.")
        elif raw and not recipients:
            st.warning("No valid email addresses found — check formatting.")

    subject = st.text_input("Subject")
    body = st.text_area("Message body", height=200)
    up = st.file_uploader("Attachment (optional)")
    att_name = up.name if up else None
    att_bytes = up.getvalue() if up else None

    over_cap = len(recipients) > DAILY_CAP
    can_send = bool(recipients and subject and body)

    if over_cap:
        st.warning(
            f"⚠️ {len(recipients)} recipients exceeds the daily cap of {DAILY_CAP}. "
            f"The first **{DAILY_CAP}** will be sent now and the remaining "
            f"**{len(recipients) - DAILY_CAP}** will be saved to Pending Sends."
        )

    btn_label = f"Send first {DAILY_CAP} now & save remainder" if over_cap else "Send now"

    if not st.session_state.confirm_send and not st.session_state.sending:
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
        s = st.session_state.staged
        first_batch = s["recipients"][:DAILY_CAP]
        remainder = s["recipients"][DAILY_CAP:]

        batch_id = str(uuid.uuid4())
        bar = st.progress(0, text="Sending…")
        results = send_bulk(
            first_batch, s["subject"], s["body"], s["att_name"], s["att_bytes"],
            progress_cb=lambda i, n: bar.progress(i / n, text=f"Sent {i}/{n}"),
        )
        for addr, status, err in results:
            db.log_email(None, s["rtype"], addr, s["subject"],
                         status, err, user["username"], batch_id)

        sent = sum(1 for _, st_, _ in results if st_ == "SENT")
        st.success(f"Done. {sent}/{len(first_batch)} sent.")
        failed = [(a, e) for a, st_, e in results if st_ == "FAILED"]
        if failed:
            st.error("Failures:")
            st.table(failed)

        if remainder:
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
            st.info(f"✅ {len(remainder)} remaining recipients saved to Pending Sends.")

        st.session_state.sending = False
        st.session_state.staged = None

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
                        for addr, status, err in results:
                            db.log_email(None, r["retailer_type"], addr, r["subject"],
                                         status, err, user["username"], batch_id)

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
        st.dataframe(rows, use_container_width=True)
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