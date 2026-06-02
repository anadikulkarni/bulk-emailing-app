import streamlit as st
import auth
import db
import os
from emailer import send_bulk
import json

# Push Streamlit secrets into os.environ so all modules pick them up
for key, value in st.secrets.items():
    os.environ[key] = str(value)

st.set_page_config(page_title="Bulk Email Sender", page_icon="📧")

# ---------- Session / login ----------
def login_screen():
    st.title("📧 Bulk Email Sender — Login")
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Log in"):
            user = auth.verify_user(u, p)
            if user:
                st.session_state.user = user
                st.rerun()
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

DAILY_CAP = 1800  # safely under Workspace's 2000/day limit

# ---------- Compose & Send ----------
if page == "Compose & Send":
    st.header("Compose & Send")

    recipient_mode = st.radio("Recipients", ["Retailer Type", "Custom"], horizontal=True)

    if recipient_mode == "Retailer Type":
        rtype = st.selectbox("Retailer Type", db.get_retailer_types())
        recipients = db.get_emails_for_type(rtype) if rtype else []
        st.info(f"{len(recipients)} email address(es) found for **{rtype}**.")
        with st.expander("Preview recipients"):
            st.write(recipients)
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

    if over_cap:
        first_batch = recipients[:DAILY_CAP]
        remainder = recipients[DAILY_CAP:]
        st.warning(
            f"⚠️ {len(recipients)} recipients exceeds the daily send cap of {DAILY_CAP}. "
            f"The first **{len(first_batch)}** will be sent now and the remaining "
            f"**{len(remainder)}** will be saved to **Pending Sends** for tomorrow."
        )
        if st.button("Send first 1,800 now & save remainder", type="primary",
                     disabled=not (recipients and subject and body)):
            import uuid

            # --- Send first batch ---
            batch_id = str(uuid.uuid4())
            bar = st.progress(0, text="Sending…")
            results = send_bulk(
                first_batch, subject, body, att_name, att_bytes,
                progress_cb=lambda i, n: bar.progress(i / n, text=f"Sent {i}/{n}"),
            )
            for addr, status, err in results:
                db.log_email(None, rtype, addr, subject, status, err, user["username"], batch_id)
            sent = sum(1 for _, s, _ in results if s == "SENT")
            st.success(f"First batch done. {sent}/{len(first_batch)} sent.")
            failed = [(a, e) for a, s, e in results if s == "FAILED"]
            if failed:
                st.error("Failures in first batch:")
                st.table(failed)

            # --- Save remainder ---
            db.save_remainder(
                label=f"{rtype} — remainder ({len(remainder)} recipients)",
                retailer_type=rtype,
                subject=subject,
                body=body,
                attachment_name=att_name,
                attachment_bytes=att_bytes,
                recipients=remainder,
                created_by=user["username"],
            )
            st.info(f"✅ {len(remainder)} remaining recipients saved to Pending Sends.")
    else:
        if st.button("Send now", type="primary",
                     disabled=not (recipients and subject and body)):
            import uuid
            batch_id = str(uuid.uuid4())
            bar = st.progress(0, text="Sending…")
            results = send_bulk(
                recipients, subject, body, att_name, att_bytes,
                progress_cb=lambda i, n: bar.progress(i / n, text=f"Sent {i}/{n}"),
            )
            for addr, status, err in results:
                db.log_email(None, rtype, addr, subject, status, err, user["username"], batch_id)
            sent = sum(1 for _, s, _ in results if s == "SENT")
            st.success(f"Done. {sent}/{len(results)} sent.")
            failed = [(a, e) for a, s, e in results if s == "FAILED"]
            if failed:
                st.error("Failures:")
                st.table(failed)

# ---------- Pending Sends ----------
elif page == "Pending Sends":
    st.header("Pending Sends")
    remainders = db.get_pending_remainders()
    if not remainders:
        st.info("No pending sends. You're all caught up.")
    else:
        for r in remainders:
            recipients = json.loads(r["recipients"])
            with st.expander(f"📬 {r['label']} — saved {r['created_at']:%Y-%m-%d %H:%M}"):
                st.write(f"**Subject:** {r['subject']}")
                st.write(f"**Recipients:** {len(recipients)}")
                st.write(f"**Saved by:** {r['created_by']}")
                with st.expander("Preview recipients"):
                    st.write(recipients)

                over_cap = len(recipients) > DAILY_CAP
                if over_cap:
                    st.warning(
                        f"This batch still has {len(recipients)} recipients — over the {DAILY_CAP} cap. "
                        f"Sending first {DAILY_CAP} and re-saving the rest."
                    )

                btn_label = (f"Send first {DAILY_CAP} & save remainder"
                             if over_cap else f"Send all {len(recipients)} now")

                if st.button(btn_label, key=f"send_rem_{r['id']}", type="primary"):
                    import uuid
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

                    # mark this one done
                    db.mark_remainder_sent(r["id"])

                    # if still more left, save again
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

# ---------- Activity Log ----------
elif page == "Activity Log":
    st.header("Activity Log")
    conn = db.get_conn()
    cursor = conn.cursor(as_dict=True)
    cursor.execute(
        "SELECT TOP 500 sent_at, sent_by, retailer_type, recipient, subject, status, error "
        "FROM EMAIL_LOG ORDER BY sent_at DESC"
    )
    rows = cursor.fetchall()
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No activity yet.")

# ---------- Admin ----------
elif page == "Admin":
    st.header("Admin — User Management")
    st.subheader("Add user")
    with st.form("adduser"):
        nu = st.text_input("New username")
        npw = st.text_input("Temp password", type="password")
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
        c1.write(f"**{u.username}** — {u.role} — {'active' if u.is_active else 'disabled'}")
        if c2.button("Toggle active", key=f"act{u.username}"):
            auth.set_active(u.username, not u.is_active); st.rerun()
        newpw = c3.text_input("Reset pw", key=f"pw{u.username}", type="password", label_visibility="collapsed")
        if c3.button("Reset", key=f"rst{u.username}") and newpw:
            auth.reset_password(u.username, newpw); st.success("Reset.")
        if c4.button("Delete", key=f"del{u.username}"):
            if u.username == user["username"]:
                st.error("Can't delete yourself.")
            else:
                auth.delete_user(u.username); st.rerun()