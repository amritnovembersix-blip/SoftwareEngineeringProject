"""Login helpers: password hashing, login tokens, and the "who is logged in" dependency.

Passwords are stored as bcrypt hashes, never as plain text. After a successful login the
backend hands out a signed JWT token; the frontend sends it back on every request as
`Authorization: Bearer <token>`.

To protect an endpoint, add the dependency:

    @router.get("/something")
    def something(user: CurrentUser = Depends(current_user)): ...
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import db
from app.config import settings

_ALGORITHM = "HS256"
# Compared against when the email is unknown, so a wrong email takes as long as a wrong password
# and an attacker can't tell which emails have accounts.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: int
    email: str
    role: str  # "teacher" or "admin"
    teacher_id: str | None
    display_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # malformed hash in the database
        return False


def _secret() -> str:
    if not settings.jwt_secret:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "JWT_SECRET is not set in Backend/.env")
    return settings.jwt_secret


def create_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def authenticate(email: str, password: str) -> CurrentUser | None:
    """Return the user if the email and password match an active account, otherwise None."""
    rows = db.query(
        "SELECT id, email, password_hash, role, teacher_id, display_name, is_active "
        "FROM app_user WHERE email = %s",
        (email.strip().lower(),),
    )
    if not rows:
        verify_password(password, _DUMMY_HASH)
        return None
    row = rows[0]
    if not verify_password(password, row["password_hash"]) or not row["is_active"]:
        return None
    db.execute("UPDATE app_user SET last_login_at = now() WHERE id = %s", (row["id"],))
    return CurrentUser(row["id"], row["email"], row["role"], row["teacher_id"], row["display_name"])


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> CurrentUser:
    """FastAPI dependency: the logged-in user, or 401 if the token is missing, invalid or expired."""
    if creds is None:
        raise _unauthorized("not logged in")
    try:
        payload = jwt.decode(creds.credentials, _secret(), algorithms=[_ALGORITHM])
        user_id = int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("login expired, please log in again")
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise _unauthorized("invalid token")
    # Re-read the account so a deactivated user or a changed role takes effect immediately.
    rows = db.query(
        "SELECT id, email, role, teacher_id, display_name FROM app_user WHERE id = %s AND is_active",
        (user_id,),
    )
    if not rows:
        raise _unauthorized("account not found or disabled")
    r = rows[0]
    return CurrentUser(r["id"], r["email"], r["role"], r["teacher_id"], r["display_name"])


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """FastAPI dependency for admin-only endpoints: 403 for teachers."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admins only")
    return user
