"""Host earnings and the AI earnings assistant."""

from __future__ import annotations

from fastapi import APIRouter

from settle.api.deps import ContainerDep, HostUser, SessionDep
from settle.api.schemas import AssistantIn, AssistantOut, HostPayoutOut, HostSummaryOut, MoneyOut
from settle.api.serializers import payout_out
from settle.services.hosts import assistant_context, host_balances, host_payouts

router = APIRouter(prefix="/v1/hosts/me", tags=["hosts"])


@router.get("/summary", response_model=HostSummaryOut)
def summary(session: SessionDep, host: HostUser) -> HostSummaryOut:
    """What we owe the host (from the ledger) and every payout, newest first."""
    return HostSummaryOut(
        owed=[MoneyOut.of(balance) for balance in host_balances(session, host)],
        payouts=[
            HostPayoutOut(
                booking_id=row.booking.id,
                listing_title=row.listing.title,
                check_in=row.booking.check_in,
                payout=payout_out(row.payout),
            )
            for row in host_payouts(session, host)
        ],
    )


@router.post("/assistant", response_model=AssistantOut)
def ask_assistant(
    body: AssistantIn, session: SessionDep, container: ContainerDep, host: HostUser
) -> AssistantOut:
    """Answers questions about the host's own payouts, grounded only in their records."""
    reply = container.services.assistant.answer(body.question, assistant_context(session, host))
    return AssistantOut(answer=reply.answer, answered_by=reply.answered_by)
