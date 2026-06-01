import getpass
from auth import create_user, list_users

if any(u.role == "admin" for u in list_users()):
    print("An admin already exists. Aborting.")
else:
    username = input("Admin username: ").strip()
    pw = getpass.getpass("Admin password: ")
    create_user(username, pw, role="admin")
    print(f"Admin '{username}' created.")