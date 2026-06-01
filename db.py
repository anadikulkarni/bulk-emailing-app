import os
import pyodbc
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

def _conn_str():
    base = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.environ['DB_SERVER']};"
        f"DATABASE={os.environ['DB_NAME']};"
        "Encrypt=yes;TrustServerCertificate=yes;"
    )
    if os.environ.get("DB_TRUSTED", "yes").lower() == "yes":
        return base + "Trusted_Connection=yes;"
    return base + f"UID={os.environ['DB_USER']};PWD={os.environ['DB_PASSWORD']};"

@contextmanager
def get_conn():
    conn = pyodbc.connect(_conn_str())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# --- Retailer queries (EDIT 'Email' to your real column name) ---
def get_retailer_types():
    with get_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT Retailer_Type FROM RETAILER_MASTER "
            "WHERE Retailer_Type IS NOT NULL ORDER BY Retailer_Type"
        ).fetchall()
    return [r[0] for r in rows]

def get_emails_for_type(retailer_type):
    with get_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT Email FROM RETAILER_MASTER "
            "WHERE Retailer_Type = ? "
            "AND Dead_Alive = 'Y' "
            "AND Email IS NOT NULL AND LTRIM(RTRIM(Email)) <> ''",
            retailer_type,
        ).fetchall()
    return sorted({r[0].strip() for r in rows if r[0] and "@" in r[0]})

# --- Logging ---
def log_email(job_id, retailer_type, recipient, subject, status, error, sent_by):
    with get_conn() as c:
        c.execute(
            "INSERT INTO EMAIL_LOG (job_id, retailer_type, recipient, subject, status, error, sent_by) "
            "VALUES (?,?,?,?,?,?,?)",
            job_id, retailer_type, recipient, subject, status, error, sent_by,
        )