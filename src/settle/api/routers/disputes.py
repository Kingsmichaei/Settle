"""Disputes: guests open them, both sides add evidence, operators decide (see admin routes)."""

from __future__ import annotations

import uuid
from http import HTTPStatus

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from settle.api.deps import ContainerDep, CurrentUser, GuestUser, IdempotencyKey, SessionDep
from settle.api.idempotency import run_idempotent
from settle.api.routers.bookings import ERRORS
from settle.api.schemas import DisputeOut, EvidenceIn, OpenDisputeIn
from settle.api.serializers import dispute_out
from settle.db.models import Booking
from settle.services.access import ensure_can_view, ensure_guest_of, party_of
from settle.services.bookings import get_booking
from settle.services.disputes import add_evidence, get_dispute, open_dispute

router = APIRouter(prefix="/v1", tags=["disputes"])


@router.post(
    "/bookings/{booking_id}/disputes", status_code=201, response_model=DisputeOut, responses=ERRORS
)
def open_(
    booking_id: uuid.UUID,
    body: OpenDisputeIn,
    container: ContainerDep,
    user: GuestUser,
    key: IdempotencyKey,
) -> JSONResponse:
    """A guest claims a refund while the payment is still held. The payout freezes."""

    def operation(session: Session) -> BaseModel:
        booking = get_booking(session, booking_id)
        ensure_guest_of(user, booking)
        dispute = open_dispute(
            session,
            booking=booking,
            reason=body.reason,
            claim=body.claim.to_money(),
            statement=body.statement,
        )
        return dispute_out(session, dispute)

    return run_idempotent(
        container,
        user=user,
        operation_name=f"open-dispute:{booking_id}",
        key=key,
        request_body=body.model_dump(mode="json"),
        status_code=HTTPStatus.CREATED,
        operation=operation,
    )


@router.get("/disputes/{dispute_id}", response_model=DisputeOut, responses=ERRORS)
def read(dispute_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> DisputeOut:
    dispute = get_dispute(session, dispute_id)
    ensure_can_view(user, session.get_one(Booking, dispute.booking_id))
    return dispute_out(session, dispute)


@router.post(
    "/disputes/{dispute_id}/evidence", status_code=201, response_model=DisputeOut, responses=ERRORS
)
def submit_evidence(
    dispute_id: uuid.UUID,
    body: EvidenceIn,
    container: ContainerDep,
    user: CurrentUser,
    key: IdempotencyKey,
) -> JSONResponse:
    """The guest or the host adds a statement or a photo (URL plus what it shows)."""

    def operation(session: Session) -> BaseModel:
        dispute = get_dispute(session, dispute_id)
        booking = session.get_one(Booking, dispute.booking_id)
        party = party_of(user, booking)
        add_evidence(
            session,
            dispute=dispute,
            party=party,
            kind=body.kind,
            content=body.content,
            url=str(body.url) if body.url else None,
        )
        session.refresh(dispute)
        return dispute_out(session, dispute)

    return run_idempotent(
        container,
        user=user,
        operation_name=f"evidence:{dispute_id}",
        key=key,
        request_body=body.model_dump(mode="json"),
        status_code=HTTPStatus.CREATED,
        operation=operation,
    )
