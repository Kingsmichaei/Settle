"""Wraps a write endpoint so a retried request replays the original response."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from settle.bootstrap import Container
from settle.db.models import User
from settle.db.session import transaction
from settle.idempotency.store import StoredResponse, fingerprint

REPLAY_HEADER = "Idempotent-Replayed"

Operation = Callable[[Session], BaseModel]


def run_idempotent(
    container: Container,
    *,
    user: User,
    operation_name: str,
    key: str,
    request_body: Any,
    status_code: int,
    operation: Operation,
) -> JSONResponse:
    """Run ``operation`` at most once per (operation, caller, key).

    Keys are scoped to the caller, so two users can never collide or replay each other's
    responses. The operation's own transaction commits before the response is stored. If the
    operation raises, the claim is released so the client can fix the request and retry.
    """
    store = container.idempotency
    scope = f"{operation_name}|{user.id}"
    outcome = store.claim(scope, key, fingerprint(request_body))
    if isinstance(outcome, StoredResponse):
        return JSONResponse(outcome.body, outcome.status_code, headers={REPLAY_HEADER: "true"})

    try:
        with transaction(container.session_factory) as session:
            body = operation(session).model_dump(mode="json")
    except BaseException:
        store.release(outcome)
        raise
    store.complete(outcome, status_code, body)
    return JSONResponse(body, status_code)
