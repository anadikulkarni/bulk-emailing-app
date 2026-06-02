import bcrypt
import db

def hash_pw(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def check_pw(pw, hashed):
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False

def verify_user(username, password):
    conn = db.get_conn()
    cursor = conn.cursor(as_dict=True)
    cursor.execute(
        "SELECT password_hash, role, is_active FROM APP_USERS WHERE username = %s",
        (username,)
    )
    row = cursor.fetchone()
    if not row or not row["is_active"]:
        return None
    if check_pw(password, row["password_hash"]):
        return {"username": username, "role": row["role"]}
    return None

def create_user(username, password, role="user"):
    conn = db.get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO APP_USERS (username, password_hash, role) VALUES (%s,%s,%s)",
        (username, hash_pw(password), role)
    )
    conn.commit()

def list_users():
    conn = db.get_conn()
    cursor = conn.cursor(as_dict=True)
    cursor.execute(
        "SELECT username, role, is_active, created_at FROM APP_USERS ORDER BY username"
    )
    return cursor.fetchall()

def set_role(username, role):
    conn = db.get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE APP_USERS SET role = %s WHERE username = %s", (role, username))
    conn.commit()

def set_active(username, is_active):
    conn = db.get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE APP_USERS SET is_active = %s WHERE username = %s",
        (1 if is_active else 0, username)
    )
    conn.commit()

def reset_password(username, new_password):
    conn = db.get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE APP_USERS SET password_hash = %s WHERE username = %s",
        (hash_pw(new_password), username)
    )
    conn.commit()

def delete_user(username):
    conn = db.get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM APP_USERS WHERE username = %s", (username,))
    conn.commit()