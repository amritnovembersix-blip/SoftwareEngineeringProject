"""Login tests. The password and token tests need no database; the endpoint tests need Postgres
with the app_user table (Backend/migrations/001_app_user.sql) and skip without it."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.config import settings
from app.main import app

settings.jwt_secret = settings.jwt_secret or "test-secret-" + "x" * 40
client = TestClient(app)
PASSWORD = "correct horse battery"


def _has_app_user() -> bool:
    try:
        return bool(db.query("SELECT to_regclass('app_user') IS NOT NULL AS ok")[0]["ok"])
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _has_app_user(), reason="Postgres with app_user table not available")


# ---- no database needed -----------------------------------------------------------------------

def test_password_is_hashed_not_stored_plain():
    h = auth.hash_password(PASSWORD)
    assert h != PASSWORD and h.startswith("$2")
    assert auth.verify_password(PASSWORD, h)
    assert not auth.verify_password("wrong", h)


def test_malformed_hash_does_not_crash():
    assert auth.verify_password(PASSWORD, "not-a-bcrypt-hash") is False


def test_me_without_token_is_401():
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_me_with_garbage_token_is_401():
    r = client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid token"


def test_token_signed_with_another_secret_is_401():
    forged = jwt.encode({"sub": "1", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        "someone-elses-secret-" + "y" * 40, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_expired_token_is_401():
    old = jwt.encode({"sub": "1", "role": "admin", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                     settings.jwt_secret, algorithm="HS256")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_login_rejects_missing_fields():
    assert client.post("/auth/login", json={"email": "a@b.c"}).status_code == 422


# ---- with the database ------------------------------------------------------------------------

@pytest.fixture
def admin():
    """A throwaway admin account, removed after the test."""
    email = f"test-{uuid.uuid4().hex[:8]}@sass.test"
    db.execute(
        "INSERT INTO app_user (email, password_hash, role, display_name) VALUES (%s, %s, 'admin', 'Test Admin')",
        (email, auth.hash_password(PASSWORD)),
    )
    yield email
    db.execute("DELETE FROM app_user WHERE email = %s", (email,))


@needs_db
def test_login_returns_token_and_user(admin):
    r = client.post("/auth/login", json={"email": admin, "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert body["expires_in"] == settings.jwt_expire_minutes * 60
    assert body["user"]["email"] == admin and body["user"]["role"] == "admin"
    assert "password_hash" not in body["user"]


@needs_db
def test_login_email_is_case_insensitive(admin):
    r = client.post("/auth/login", json={"email": "  " + admin.upper() + " ", "password": PASSWORD})
    assert r.status_code == 200


@needs_db
def test_wrong_password_and_unknown_email_give_the_same_401(admin):
    wrong_pw = client.post("/auth/login", json={"email": admin, "password": "nope"})
    no_user = client.post("/auth/login", json={"email": "nobody@sass.test", "password": "nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json() == {"detail": "wrong email or password"}


@needs_db
def test_me_returns_the_logged_in_user(admin):
    token = client.post("/auth/login", json={"email": admin, "password": PASSWORD}).json()["access_token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["email"] == admin


@needs_db
def test_disabled_account_cannot_log_in_or_use_old_token(admin):
    token = client.post("/auth/login", json={"email": admin, "password": PASSWORD}).json()["access_token"]
    db.execute("UPDATE app_user SET is_active = false WHERE email = %s", (admin,))
    assert client.post("/auth/login", json={"email": admin, "password": PASSWORD}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@needs_db
def test_password_is_stored_hashed(admin):
    stored = db.query("SELECT password_hash FROM app_user WHERE email = %s", (admin,))[0]["password_hash"]
    assert stored != PASSWORD and auth.verify_password(PASSWORD, stored)


@needs_db
def test_teacher_account_needs_a_teacher_row():
    """The table refuses a teacher account without a teacher, and an admin with one."""
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO app_user (email, password_hash, role, display_name) "
                   "VALUES ('bad@sass.test', 'x', 'teacher', 'Bad')")
