"""Sign-in. Accounts are created by the seed command or by operators, not self-service here."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from settle.api.deps import ContainerDep, CurrentUser, SessionDep
from settle.api.schemas import LoginIn, ProblemOut, TokenOut, UserOut
from settle.auth import hash_password, issue_token, verify_password
from settle.core.errors import Unauthenticated
from settle.db.models import User

router = APIRouter(prefix="/v1", tags=["auth"])

# Verifying against a dummy hash for unknown emails keeps response times equal, so an
# attacker can't learn which emails have accounts.
_DUMMY_HASH = hash_password("not-a-real-password")


@router.post("/auth/token", response_model=TokenOut, responses={401: {"model": ProblemOut}})
def login(body: LoginIn, session: SessionDep, container: ContainerDep) -> TokenOut:
    user = session.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_ok:
        raise Unauthenticated("Email or password is incorrect")
    now = container.clock.now()
    ttl = timedelta(minutes=container.settings.jwt_ttl_minutes)
    token = issue_token(
        user.id,
        user.role,
        secret=container.settings.jwt_secret.get_secret_value(),
        now=now,
        ttl=ttl,
    )
    return TokenOut(access_token=token, expires_at=now + ttl, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
