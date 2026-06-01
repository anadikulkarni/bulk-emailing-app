import bcrypt
from db import get_conn

def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def check_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False

def verify_user(username, password):
    with get_conn() as c:
        row = c.execute(
            "SELECT password_hash, role, is_active FROM APP_USERS WHERE username = ?",
            username,
        ).fetchone()
    if not row or not row.is_active:
        return None
    if check_pw(password, row.password_hash):
        return {"username": username, "role": row.role}
    return None

def create_user(username, password, role="user"):
    with get_conn() as c:
        c.execute(
            "INSERT INTO APP_USERS (username, password_hash, role) VALUES (?,?,?)",
            username, hash_pw(password), role,
        )

def list_users():
    with get_conn() as c:
        return c.execute(
            "SELECT username, role, is_active, created_at FROM APP_USERS ORDER BY username"
        ).fetchall()

def set_role(username, role):
    with get_conn() as c:
        c.execute("UPDATE APP_USERS SET role = ? WHERE username = ?", role, username)

def set_active(username, is_active):
    with get_conn() as c:
        c.execute("UPDATE APP_USERS SET is_active = ? WHERE username = ?", 1 if is_active else 0, username)

def reset_password(username, new_password):
    with get_conn() as c:
        c.execute("UPDATE APP_USERS SET password_hash = ? WHERE username = ?",
                  hash_pw(new_password), username)

def delete_user(username):
    with get_conn() as c:
        c.execute("DELETE FROM APP_USERS WHERE username = ?", username)