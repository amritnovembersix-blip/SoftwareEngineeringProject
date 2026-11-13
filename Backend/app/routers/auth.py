"""Login: POST /auth/login gives a token, GET /auth/me says who the token belongs to."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import CurrentUser, authenticate, create_token, current_user
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    teacher_id: str | None
    display_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserOut


def _out(u: CurrentUser) -> UserOut:
    return UserOut(id=u.id, email=u.email, role=u.role, teacher_id=u.teacher_id, display_name=u.display_name)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    """Check the email and password. On success, return a token for the Authorization header."""
    user = authenticate(body.email, body.password)
    if user is None:
        # Same message for a wrong email and a wrong password, so emails can't be guessed.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    return LoginResponse(
        access_token=create_token(user.id, user.role),
        expires_in=settings.jwt_expire_minutes * 60,
        user=_out(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(current_user)) -> UserOut:
    """Who is logged in. The frontend calls this on page load to restore the session."""
    return _out(user)
