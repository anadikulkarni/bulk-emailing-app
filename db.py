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
             attachment_bytes, json.dumps(recipients), created_by)
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