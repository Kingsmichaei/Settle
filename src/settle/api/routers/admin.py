"""Operations endpoints, for admins only."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from settle.api.deps import AdminUser, ContainerDep, IdempotencyKey, SessionDep
from settle.api.idempotency import run_idempotent
from settle.api.routers.bookings import ERRORS
from settle.api.schemas import (
    AdminSummaryOut,
    BookingOut,
    BookingSummaryOut,
    CountOut,
    DecideDisputeIn,
    DisputeOut,
    LedgerIntegrityOut,
    MoneyOut,
    OutboxEventOut,
    ReconciliationRunOut,
)
from settle.api.serializers import booking_out, booking_summary_out, dispute_out
from settle.bootstrap import Container
from settle.db.models import (
    Booking,
    Dispute,
    DisputeStatus,
    LedgerAccount,
    OutboxEvent,
    OutboxStatus,
    Payout,
    PayoutStatus,
    ReconciliationRun,
    RiskAction,
    RiskAssessment,
    User,
)
from settle.domain.money import Money
from settle.domain.states import FUNDS_HELD_STATES, BookingStatus
from settle.ledger.accounts import AccountKind, AccountRef
from settle.ledger.posting import Ledger
from settle.services.bookings import get_booking, list_bookings
from settle.services.disputes import (
    decide_dispute,
    get_dispute,
    list_disputes,
    request_recommendation,
)
from settle.services.payouts import approve_blocked_payout, retry_failed_payout
from settle.services.reconciliation import run_reconciliation

router = APIRouter(prefix="/v1/admin", tags=["admin"], responses=ERRORS)


# ---- overview ---------------------------------------------------------------------------


@router.get("/summary", response_model=AdminSummaryOut)
def summary(session: SessionDep, _admin: AdminUser) -> AdminSummaryOut:
    counts = session.execute(select(Booking.status, func.count()).group_by(Booking.status)).all()
    held: dict[str, int] = defaultdict(int)
    for currency, total in session.execute(
        select(Booking.currency, func.sum(Booking.total_minor))
        .where(Booking.status.in_(FUNDS_HELD_STATES))
        .group_by(Booking.currency)
    ):
        held[currency] += int(total)

    ledger = Ledger(session)

    def totals(kind: AccountKind) -> list[MoneyOut]:
        per_currency: dict[str, int] = defaultdict(int)
        for account in session.scalars(select(LedgerAccount).where(LedgerAccount.kind == kind)):
            per_currency[account.currency] += ledger.balance(
                AccountRef(kind, account.owner, account.currency)
            ).amount
        return [MoneyOut.of(Money(amount, cur)) for cur, amount in sorted(per_currency.items())]

    def count(stmt: Select[Any]) -> int:
        return int(session.scalar(stmt) or 0)

    return AdminSummaryOut(
        bookings_by_status=[CountOut(label=str(status), count=n) for status, n in counts],
        funds_held=[MoneyOut.of(Money(amount, cur)) for cur, amount in sorted(held.items())],
        owed_to_hosts=totals(AccountKind.HOST_PAYABLE),
        platform_revenue=totals(AccountKind.PLATFORM_REVENUE),
        open_disputes=count(
            select(func.count())
            .select_from(Dispute)
            .where(Dispute.status != DisputeStatus.RESOLVED)
        ),
        blocked_payouts=count(
            select(func.count()).select_from(Payout).where(Payout.status == PayoutStatus.BLOCKED)
        ),
        risk_reviews=count(
            select(func.count())
            .select_from(RiskAssessment)
            .where(RiskAssessment.action != RiskAction.ALLOW, RiskAssessment.reviewed_at.is_(None))
        ),
        dead_events=count(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.DEAD)
        ),
    )


@router.get("/bookings", response_model=list[BookingSummaryOut])
def bookings(
    session: SessionDep, admin: AdminUser, status: BookingStatus | None = None
) -> list[BookingSummaryOut]:
    return [booking_summary_out(b) for b in list_bookings(session, admin, status=status, limit=200)]


@router.get("/risk", response_model=list[BookingSummaryOut])
def risk_queue(session: SessionDep, _admin: AdminUser) -> list[BookingSummaryOut]:
    """Bookings the risk agent flagged that no operator has reviewed yet."""
    flagged = session.scalars(
        select(Booking)
        .join(RiskAssessment, RiskAssessment.booking_id == Booking.id)
        .where(RiskAssessment.action != RiskAction.ALLOW, RiskAssessment.reviewed_at.is_(None))
        .order_by(RiskAssessment.score.desc())
    ).all()
    return [booking_summary_out(booking) for booking in flagged]


# ---- payouts ----------------------------------------------------------------------------


def _booking_action(
    *,
    container: Container,
    admin: User,
    key: str,
    name: str,
    booking_id: uuid.UUID,
    act: Callable[[Session, Booking], object],
) -> JSONResponse:
    def operation(session: Session) -> BaseModel:
        booking = get_booking(session, booking_id)
        act(session, booking)
        return booking_out(session, booking)

    return run_idempotent(
        container,
        user=admin,
        operation_name=f"{name}:{booking_id}",
        key=key,
        request_body={},
        status_code=HTTPStatus.ACCEPTED,
        operation=operation,
    )


@router.post("/bookings/{booking_id}/approve-payout", status_code=202, response_model=BookingOut)
def approve_payout(
    booking_id: uuid.UUID, container: ContainerDep, admin: AdminUser, key: IdempotencyKey
) -> JSONResponse:
    """Clear a payout held for review (risk, refused refund or chargeback)."""
    return _booking_action(
        container=container,
        admin=admin,
        key=key,
        name="approve-payout",
        booking_id=booking_id,
        act=lambda session, booking: approve_blocked_payout(
            session, container.clock, booking, admin.id
        ),
    )


@router.post("/bookings/{booking_id}/retry-payout", status_code=202, response_model=BookingOut)
def retry_payout(
    booking_id: uuid.UUID, container: ContainerDep, admin: AdminUser, key: IdempotencyKey
) -> JSONResponse:
    """Re-send a payout that failed (for example after the host fixed their details)."""
    return _booking_action(
        container=container,
        admin=admin,
        key=key,
        name="retry-payout",
        booking_id=booking_id,
        act=lambda session, booking: retry_failed_payout(session, container.clock, booking),
    )


# ---- disputes ---------------------------------------------------------------------------


@router.get("/disputes", response_model=list[DisputeOut])
def disputes(
    session: SessionDep, _admin: AdminUser, status: DisputeStatus | None = None
) -> list[DisputeOut]:
    return [dispute_out(session, dispute) for dispute in list_disputes(session, status=status)]


@router.post("/disputes/{dispute_id}/recommend", status_code=202, response_model=DisputeOut)
def recommend(
    dispute_id: uuid.UUID, container: ContainerDep, admin: AdminUser, key: IdempotencyKey
) -> JSONResponse:
    """Ask the AI mediator for a recommendation. It runs in the background."""

    def operation(session: Session) -> BaseModel:
        dispute = get_dispute(session, dispute_id)
        request_recommendation(session, container.services, dispute)
        return dispute_out(session, dispute)

    return run_idempotent(
        container,
        user=admin,
        operation_name=f"recommend:{dispute_id}",
        key=key,
        request_body={},
        status_code=HTTPStatus.ACCEPTED,
        operation=operation,
    )


@router.post("/disputes/{dispute_id}/decide", response_model=DisputeOut)
def decide(
    dispute_id: uuid.UUID,
    body: DecideDisputeIn,
    container: ContainerDep,
    admin: AdminUser,
    key: IdempotencyKey,
) -> JSONResponse:
    """The final decision. A positive amount is refunded; zero releases the payout to the host."""

    def operation(session: Session) -> BaseModel:
        dispute = get_dispute(session, dispute_id)
        decide_dispute(
            session,
            container.services,
            dispute=dispute,
            decided_by=admin,
            refund=body.refund.to_money(),
            note=body.note,
        )
        return dispute_out(session, dispute)

    return run_idempotent(
        container,
        user=admin,
        operation_name=f"decide:{dispute_id}",
        key=key,
        request_body=body.model_dump(mode="json"),
        status_code=HTTPStatus.OK,
        operation=operation,
    )


# ---- reliability ------------------------------------------------------------------------


@router.get("/outbox", response_model=list[OutboxEventOut])
def outbox(
    session: SessionDep,
    _admin: AdminUser,
    status: OutboxStatus = OutboxStatus.DEAD,
    limit: int = 100,
) -> list[OutboxEventOut]:
    """Queued background work. Defaults to dead events, the ones that need a human."""
    events = session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.status == status)
        .order_by(OutboxEvent.created_at.desc())
        .limit(min(limit, 500))
    )
    return [OutboxEventOut.model_validate(event) for event in events]


@router.get("/ledger/integrity", response_model=LedgerIntegrityOut)
def ledger_integrity(session: SessionDep, _admin: AdminUser) -> LedgerIntegrityOut:
    unbalanced = Ledger(session).unbalanced_entry_ids()
    return LedgerIntegrityOut(balanced=not unbalanced, unbalanced_entry_ids=unbalanced)


@router.get("/reconciliation/runs", response_model=list[ReconciliationRunOut])
def reconciliation_runs(session: SessionDep, _admin: AdminUser) -> list[ReconciliationRunOut]:
    runs = session.scalars(
        select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc()).limit(20)
    )
    return [ReconciliationRunOut.model_validate(run) for run in runs]


@router.post("/reconciliation/runs", status_code=201, response_model=ReconciliationRunOut)
def reconcile_now(
    session: SessionDep, container: ContainerDep, _admin: AdminUser
) -> ReconciliationRunOut:
    """Run every check now. Read-only apart from storing the report, so no idempotency key."""
    run = run_reconciliation(session, container.services)
    return ReconciliationRunOut.model_validate(run)
