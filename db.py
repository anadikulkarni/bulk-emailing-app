import os
import json
import pymssql
import streamlit as st
from contextlib import contextmanager

for key, value in st.secrets.items():
    os.environ[key] = str(value)

def get_conn():
    return pymssql.connect(
        server=os.environ["DB_SERVER"],
        port=os.environ["DB_PORT"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
    )

@contextmanager
def get_conn_ctx():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()

def get_retailer_types():
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT Retailer_Type FROM RETAILER_MASTER "
            "WHERE Retailer_Type IS NOT NULL ORDER BY Retailer_Type"
        )
        return [r[0] for r in cursor.fetchall()]

def get_emails_for_type(retailer_type):
    with get_conn_ctx() as conn:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT DISTINCT Customer_Name, Email FROM RETAILER_MASTER "
            "WHERE Retailer_Type = %s "
            "AND UPPER(LTRIM(RTRIM(Dead_Alive))) = 'Y' "
            "AND Email IS NOT NULL AND LTRIM(RTRIM(Email)) <> ''",
            (retailer_type,)
        )
        rows = cursor.fetchall()
        # dedupe by email, keep name
        seen = {}
        for r in rows:
            email = r["Email"].strip()
            name = (r["Customer_Name"] or "").strip()
            if email and "@" in email and email not in seen:
                seen[email] = name
        return [{"name": v, "email": k} for k, v in sorted(seen.items())]

def log_email(job_id, retailer_type, recipient, subject, status,
              error, sent_by, batch_id=None):
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO EMAIL_LOG "
            "(job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id)
        )
        conn.commit()

def get_campaign_log():
    with get_conn_ctx() as conn:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT MIN(sent_at) AS sent_at, sent_by, retailer_type, subject, batch_id, "
            "       COUNT(*) AS total, "
            "       SUM(CASE WHEN status='SENT' THEN 1 ELSE 0 END) AS sent_ok, "
            "       SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed "
            "FROM EMAIL_LOG "
            "WHERE batch_id IS NOT NULL "
            "GROUP BY batch_id, sent_by, retailer_type, subject "
            "ORDER BY MIN(sent_at) DESC"
        )
        return cursor.fetchall()

def get_batch_detail(batch_id):
    with get_conn_ctx() as conn:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT sent_at, recipient, status, error FROM EMAIL_LOG "
            "WHERE batch_id = %s ORDER BY recipient",
            (batch_id,)
        )
        return cursor.fetchall()

def save_remainder(label, retailer_type, subject, body,
                   attachment_name, attachment_bytes, recipients, created_by):
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO PENDING_REMAINDER "
            "(label, retailer_type, subject, body, attachment_name, "
            "attachment_data, recipients, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (label, retailer_type, subject, body, attachment_name,
             bytearray(attachment_bytes) if attachment_bytes else None,
             json.dumps(recipients), created_by)
        )
        conn.commit()

def get_pending_remainders():
    with get_conn_ctx() as conn:
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT id, label, retailer_type, subject, body, "
            "attachment_name, attachment_data, recipients, created_by, created_at "
            "FROM PENDING_REMAINDER WHERE status = 'PENDING' ORDER BY created_at ASC"
        )
        return cursor.fetchall()

def mark_remainder_sent(remainder_id):
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE PENDING_REMAINDER SET status='SENT', sent_at=SYSDATETIME() "
            "WHERE id=%s", (remainder_id,)
        )
        conn.commit()
        
def log_emails_bulk(records):
    """records: list of 8-tuples matching the EMAIL_LOG columns."""
    if not records:
        return
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO EMAIL_LOG "
            "(job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            records,
        )
        conn.commit()

# ---------- Verified distributors list ----------
# Every upload is kept; the newest one is the list in use.
VERIFIED_LISTS_DDL = (
    "IF OBJECT_ID('VERIFIED_DISTRIBUTOR_LISTS', 'U') IS NULL "
    "CREATE TABLE VERIFIED_DISTRIBUTOR_LISTS ("
    "id INT IDENTITY(1,1) PRIMARY KEY, "
    "filename NVARCHAR(260) NOT NULL, "
    "file_data VARBINARY(MAX) NOT NULL, "
    "row_count INT NOT NULL, "
    "uploaded_by NVARCHAR(100) NOT NULL, "
    "uploaded_at DATETIME2 NOT NULL DEFAULT SYSDATETIME())"
)
_verified_table_ready = False

def _ensure_verified_table(conn):
    global _verified_table_ready
    if not _verified_table_ready:
        conn.cursor().execute(VERIFIED_LISTS_DDL)
        conn.commit()
        _verified_table_ready = True

def get_verified_list_meta():
    """Newest verified distributors upload (without the file), or None."""
    with get_conn_ctx() as conn:
        _ensure_verified_table(conn)
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT TOP 1 id, filename, row_count, uploaded_by, uploaded_at "
            "FROM VERIFIED_DISTRIBUTOR_LISTS ORDER BY id DESC"
        )
        return cursor.fetchone()

def get_verified_list_data(list_id):
    """The stored file, or None if that upload no longer exists."""
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_data FROM VERIFIED_DISTRIBUTOR_LISTS WHERE id = %s", (list_id,)
        )
        row = cursor.fetchone()
        return bytes(row[0]) if row else None

def save_verified_list(filename, file_bytes, row_count, uploaded_by):
    with get_conn_ctx() as conn:
        _ensure_verified_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO VERIFIED_DISTRIBUTOR_LISTS "
            "(filename, file_data, row_count, uploaded_by) VALUES (%s,%s,%s,%s)",
            # bytearray: pymssql sends plain ASCII bytes as text, which SQL
            # Server won't put into a VARBINARY column
            (filename, bytearray(file_bytes), row_count, uploaded_by)
        )
        conn.commit()

# ---------- Scheduled sends ----------
# Times in this table are UTC. status goes PENDING → SENDING (claimed by a
# scheduler_worker run) → SENT, or back to PENDING while recipients remain;
# CANCELLED if someone cancels it first. Each recipient tried is logged in
# EMAIL_LOG with job_id = the send's id, which is how a run resumes.
SCHEDULED_SENDS_DDL = (
    "IF OBJECT_ID('SCHEDULED_SENDS', 'U') IS NULL "
    "CREATE TABLE SCHEDULED_SENDS ("
    "id INT IDENTITY(1,1) PRIMARY KEY, "
    "retailer_type NVARCHAR(100) NOT NULL, "
    "subject NVARCHAR(500) NOT NULL, "
    "body NVARCHAR(MAX) NOT NULL, "
    "attachment_name NVARCHAR(260) NULL, "
    "attachment_data VARBINARY(MAX) NULL, "
    "recipients NVARCHAR(MAX) NOT NULL, "
    "recipient_count INT NOT NULL, "
    "send_at DATETIME2 NOT NULL, "
    "status NVARCHAR(20) NOT NULL DEFAULT 'PENDING', "
    "claimed_at DATETIME2 NULL, "
    "created_by NVARCHAR(100) NOT NULL, "
    "created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(), "
    "processed_at DATETIME2 NULL, "
    "result_summary NVARCHAR(400) NULL)"
)
_scheduled_table_ready = False

def _ensure_scheduled_table(conn):
    global _scheduled_table_ready
    if not _scheduled_table_ready:
        conn.cursor().execute(SCHEDULED_SENDS_DDL)
        conn.commit()
        _scheduled_table_ready = True

def schedule_send(retailer_type, subject, body, attachment_name, attachment_bytes,
                  recipients, send_at_utc, created_by):
    with get_conn_ctx() as conn:
        _ensure_scheduled_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO SCHEDULED_SENDS "
            "(retailer_type, subject, body, attachment_name, attachment_data, "
            "recipients, recipient_count, send_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (retailer_type, subject, body, attachment_name,
             bytearray(attachment_bytes) if attachment_bytes else None,
             json.dumps(recipients), len(recipients), send_at_utc, created_by)
        )
        conn.commit()

def get_scheduled_sends(limit=50):
    """Upcoming and in-progress sends first, then the latest finished ones,
    each with how many recipients it has been sent to so far."""
    with get_conn_ctx() as conn:
        _ensure_scheduled_table(conn)
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT TOP (%s) s.id, s.retailer_type, s.subject, s.recipient_count, "
            "s.send_at, s.status, s.created_by, s.processed_at, s.result_summary, "
            "(SELECT COUNT(*) FROM EMAIL_LOG l "
            " WHERE l.job_id = s.id AND l.status = 'SENT') AS sent_count "
            "FROM SCHEDULED_SENDS s "
            "ORDER BY CASE WHEN s.status IN ('PENDING','SENDING') THEN 0 ELSE 1 END, "
            "CASE WHEN s.status IN ('PENDING','SENDING') THEN s.send_at END ASC, "
            "s.send_at DESC",
            (limit,)
        )
        return cursor.fetchall()

def cancel_scheduled_send(send_id):
    """Cancel a send that hasn't started. False if a worker already claimed it."""
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE SCHEDULED_SENDS SET status='CANCELLED', processed_at=SYSUTCDATETIME() "
            "WHERE id=%s AND status='PENDING'",
            (send_id,)
        )
        conn.commit()
        return cursor.rowcount == 1

# --- used by scheduler_worker.py ---

def release_stale_scheduled_sends(stale_minutes):
    """Return sends to PENDING when the run sending them has gone quiet
    (crashed or was killed). Running sends refresh claimed_at as they go."""
    with get_conn_ctx() as conn:
        _ensure_scheduled_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE SCHEDULED_SENDS SET status='PENDING', claimed_at=NULL "
            "WHERE status='SENDING' AND claimed_at < DATEADD(MINUTE, -%s, SYSUTCDATETIME())",
            (stale_minutes,)
        )
        conn.commit()
        return cursor.rowcount

def count_sent_last_24h():
    """Emails this app sent successfully in the last 24 hours (Gmail's limit is rolling)."""
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM EMAIL_LOG "
            "WHERE status='SENT' AND sent_at >= DATEADD(HOUR, -24, SYSDATETIME())"
        )
        return cursor.fetchone()[0]

def get_due_scheduled_sends():
    with get_conn_ctx() as conn:
        _ensure_scheduled_table(conn)
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT id, retailer_type, subject, body, attachment_name, attachment_data, "
            "recipients, created_by "
            "FROM SCHEDULED_SENDS WHERE status='PENDING' AND send_at <= SYSUTCDATETIME() "
            "ORDER BY send_at, id"
        )
        return cursor.fetchall()

def claim_scheduled_send(send_id):
    """Atomically mark a due send as SENDING. Only one run can win the claim,
    so overlapping runs never send the same campaign twice."""
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE SCHEDULED_SENDS SET status='SENDING', claimed_at=SYSUTCDATETIME() "
            "WHERE id=%s AND status='PENDING'",
            (send_id,)
        )
        conn.commit()
        return cursor.rowcount == 1

def get_scheduled_attempts(send_id):
    """{recipient: status} for every recipient already tried for this send."""
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT recipient, status FROM EMAIL_LOG WHERE job_id=%s", (send_id,)
        )
        return {recipient: status for recipient, status in cursor.fetchall()}

def log_scheduled_progress(send_id, records):
    """Log a batch of results and refresh the claim, in one transaction."""
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO EMAIL_LOG "
            "(job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            records,
        )
        cursor.execute(
            "UPDATE SCHEDULED_SENDS SET claimed_at=SYSUTCDATETIME() "
            "WHERE id=%s AND status='SENDING'",
            (send_id,)
        )
        conn.commit()

def finish_scheduled_send(send_id, done, summary):
    """Mark a claimed send SENT when every recipient has been tried, or put it
    back to PENDING so the next run carries on."""
    summary = summary[:400]  # the column's size; error messages can run long
    with get_conn_ctx() as conn:
        cursor = conn.cursor()
        if done:
            cursor.execute(
                "UPDATE SCHEDULED_SENDS SET status='SENT', processed_at=SYSUTCDATETIME(), "
                "result_summary=%s WHERE id=%s",
                (summary, send_id)
            )
        else:
            cursor.execute(
                "UPDATE SCHEDULED_SENDS SET status='PENDING', claimed_at=NULL, "
                "result_summary=%s WHERE id=%s",
                (summary, send_id)
            )
        conn.commit()
