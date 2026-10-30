"""Payment provider notifications. Authenticated by signature, not by a user token."""

from __future__ import annotations

from fastapi import APIRouter, Request

from settle.api.deps import ContainerDep
from settle.api.schemas import ProblemOut, WebhookAckOut
from settle.core.errors import NotFound
from settle.db.session import transaction
from settle.services.webhooks import receive_webhook

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post(
    "/{provider}",
    response_model=WebhookAckOut,
    responses={401: {"model": ProblemOut}, 404: {"model": ProblemOut}, 503: {"model": ProblemOut}},
)
async def receive(provider: str, request: Request, container: ContainerDep) -> WebhookAckOut:
    """Verify, store once, acknowledge. Processing happens in the worker.

    A 401 (bad signature) is never retried by the provider; a 503 (we couldn't verify right
    now) is, which is what we want.
    """
    if provider != container.provider.name:
        raise NotFound(f"No webhook endpoint for provider {provider!r}")
    body = await request.body()
    headers = dict(request.headers)
    with transaction(container.session_factory) as session:
        receipt = receive_webhook(session, container.services, headers=headers, body=body)
    return WebhookAckOut(received=True, duplicate=receipt.duplicate)
