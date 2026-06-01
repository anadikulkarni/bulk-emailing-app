import streamlit as st
import auth
import db
from emailer import send_bulk
import pyodbc

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

pages = ["Compose & Send", "Scheduled Jobs", "Activity Log"]
if user["role"] == "admin":
    pages.append("Admin")
page = st.sidebar.radio("Navigate", pages)

# ---------- Compose & Send ----------
if page == "Compose & Send":
    st.header("Compose & Send")
    rtype = st.selectbox("Retailer Type", db.get_retailer_types())
    recipients = db.get_emails_for_type(rtype) if rtype else []
    st.info(f"{len(recipients)} email address(es) found for **{rtype}**.")
    with st.expander("Preview recipients"):
        st.write(recipients)

    subject = st.text_input("Subject")
    body = st.text_area("Message body", height=200)
    up = st.file_uploader("Attachment (optional)")
    att_name = up.name if up else None
    att_bytes = up.getvalue() if up else None

    mode = st.radio("When", ["Send now", "Schedule for later"], horizontal=True)

    if mode == "Schedule for later":
        import datetime
        col1, col2 = st.columns(2)
        d = col1.date_input("Date", datetime.date.today())
        t = col2.time_input("Time", datetime.time(9, 0))
        send_at = datetime.datetime.combine(d, t)
        if st.button("Schedule", type="primary", disabled=not (recipients and subject and body)):
            with db.get_conn() as c:
                c.execute(
                    "INSERT INTO SCHEDULED_EMAILS "
                    "(retailer_type, subject, body, attachment_name, attachment_data, send_at, created_by) "
                    "VALUES (?,?,?,?,?,?,?)",
                    rtype, subject, body, att_name,
                    pyodbc_bytes(att_bytes), send_at, user["username"],
                )
            st.success(f"Scheduled for {send_at}. The worker will pick it up.")
    else:
        if st.button("Send now", type="primary", disabled=not (recipients and subject and body)):
            bar = st.progress(0, text="Sending…")
            results = send_bulk(
                recipients, subject, body, att_name, att_bytes,
                progress_cb=lambda i, n: bar.progress(i / n, text=f"Sent {i}/{n}"),
            )
            for addr, status, err in results:
                db.log_email(None, rtype, addr, subject, status, err, user["username"])
            sent = sum(1 for _, s, _ in results if s == "SENT")
            st.success(f"Done. {sent}/{len(results)} sent.")
            failed = [(a, e) for a, s, e in results if s == "FAILED"]
            if failed:
                st.error("Failures:")
                st.table(failed)

# ---------- Scheduled Jobs ----------
elif page == "Scheduled Jobs":
    st.header("Scheduled Jobs")
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT id, retailer_type, subject, send_at, status, created_by, processed_at "
            "FROM SCHEDULED_EMAILS ORDER BY send_at DESC"
        ).fetchall()
    st.dataframe([tuple(r) for r in rows],
                 column_config=None, use_container_width=True)
    cancel_id = st.number_input("Cancel a PENDING job by ID", min_value=0, step=1)
    if st.button("Cancel job") and cancel_id:
        with db.get_conn() as c:
            c.execute("UPDATE SCHEDULED_EMAILS SET status='FAILED', "
                      "result_summary='Cancelled by user' "
                      "WHERE id=? AND status='PENDING'", cancel_id)
        st.rerun()

# ---------- Activity Log ----------
elif page == "Activity Log":
    st.header("Activity Log")
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT TOP 500 sent_at, sent_by, retailer_type, recipient, subject, status, error "
            "FROM EMAIL_LOG ORDER BY sent_at DESC"
        ).fetchall()
    st.dataframe([tuple(r) for r in rows], use_container_width=True)

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


# helper for binary param
def pyodbc_bytes(b):
    return pyodbc.Binary(b) if b else None