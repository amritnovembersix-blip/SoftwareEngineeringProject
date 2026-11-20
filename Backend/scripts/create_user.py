"""Create or update a login account.

Run from the Backend folder:

    python scripts/create_user.py --email admin@sass.test --name "Admin" --role admin
    python scripts/create_user.py --email teacher@sass.test --name "Ms. Rivera" --role teacher --teacher-id <teacher id>

It asks for the password (nothing is shown while you type). Running it again for the same email
resets that account's password, role and name.

To see the teacher ids: psql "$DATABASE_URL" -c "SELECT id, name FROM teacher;"
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.auth import hash_password  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Create or update a SASS login account.")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True, help="name shown in the top bar")
    p.add_argument("--role", required=True, choices=["teacher", "admin"])
    p.add_argument("--teacher-id", help="required for --role teacher (an id from the teacher table)")
    p.add_argument("--password", help="for scripts only; leave it out to be asked instead")
    a = p.parse_args()

    if a.role == "teacher" and not a.teacher_id:
        p.error("--teacher-id is required for a teacher account")
    if a.role == "admin" and a.teacher_id:
        p.error("an admin account has no --teacher-id")
    if a.teacher_id and not db.query("SELECT 1 FROM teacher WHERE id = %s", (a.teacher_id,)):
        p.error(f"no teacher with id {a.teacher_id!r}")

    password = a.password or getpass.getpass("Password: ")
    if len(password) < 8:
        p.error("use a password of at least 8 characters")

    row = db.execute(
        """
        INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE
           SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role,
               teacher_id = EXCLUDED.teacher_id, display_name = EXCLUDED.display_name, is_active = true
        RETURNING id, (xmax = 0) AS created
        """,
        (a.email.strip().lower(), hash_password(password), a.role, a.teacher_id, a.name),
    )[0]
    email = a.email.strip().lower()
    print(f"{'Created' if row['created'] else 'Updated'} {a.role} account {email} (id {row['id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
