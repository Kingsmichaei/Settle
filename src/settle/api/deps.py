"""FastAPI dependencies: the container, database sessions and the authenticated user."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from settle.auth import InvalidToken, read_token
from settle.bootstrap import Container
from settle.core.errors import Forbidden, Unauthenticated
from settle.db.models import User, UserRole
from settle.db.session import transaction


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_session(container: ContainerDep) -> Iterator[Session]:
    """A session for read endpoints; commits on success."""
    with transaction(container.session_factory) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /v1/auth/token")


def get_current_user(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the bearer token to a user. Loaded in its own short session and detached, so
    write endpoints can use it alongside their own transaction."""
    if credentials is None:
        raise Unauthenticated("Sign in to continue")
    try:
        claims = read_token(
            credentials.credentials,
            secret=container.settings.jwt_secret.get_secret_value(),
            now=container.clock.now(),
        )
    except InvalidToken as exc:
        raise Unauthenticated("Your session has expired; sign in again") from exc
    with container.session_factory() as session:
        user = session.get(User, claims.user_id)
        if user is None:
            raise Unauthenticated("Account not found")
        session.expunge(user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole) -> Callable[[User], User]:
    def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise Forbidden(f"This needs the {' or '.join(role.value for role in roles)} role")
        return user

    return check


GuestUser = Annotated[User, Depends(require_role(UserRole.GUEST))]
HostUser = Annotated[User, Depends(require_role(UserRole.HOST))]
AdminUser = Annotated[User, Depends(require_role(UserRole.ADMIN))]

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        description="A unique key per logical request (a UUID is ideal). Retries reuse it.",
    ),
]
