import os
import pymssql
import streamlit as st

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

def get_retailer_types():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT Retailer_Type FROM RETAILER_MASTER "
        "WHERE Retailer_Type IS NOT NULL ORDER BY Retailer_Type"
    )
    return [r[0] for r in cursor.fetchall()]

def get_emails_for_type(retailer_type):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT Email FROM RETAILER_MASTER "
        "WHERE Retailer_Type = %s "
        "AND UPPER(LTRIM(RTRIM(Dead_Alive))) = 'Y' "
        "AND Email IS NOT NULL AND LTRIM(RTRIM(Email)) <> ''",
        (retailer_type,)
    )
    return sorted({r[0].strip() for r in cursor.fetchall() if r[0] and "@" in r[0]})

def log_email(job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id=None):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO EMAIL_LOG (job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (job_id, retailer_type, recipient, subject, status, error, sent_by, batch_id)
    )
    conn.commit()

def get_campaign_log():
    conn = get_conn()
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
    conn = get_conn()
    cursor = conn.cursor(as_dict=True)
    cursor.execute(
        "SELECT sent_at, recipient, status, error FROM EMAIL_LOG "
        "WHERE batch_id = %s ORDER BY recipient",
        (batch_id,)
    )
    return cursor.fetchall()