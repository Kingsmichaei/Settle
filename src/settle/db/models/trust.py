"""Trust and safety: risk assessments and disputes."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from settle.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from settle.db.enum_type import str_enum
from settle.db.types import UTCDateTime


class RiskAction(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"  # flag for an operator; money flows normally
    HOLD = "hold"  # block the payout until an operator approves it


class RiskAssessment(UUIDPrimaryKey, Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="score_range"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id"), unique=True, nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[RiskAction] = mapped_column(str_enum(RiskAction), nullable=False)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    # Which model and prompt wrote the explanation ("rules" when no model was used).
    explained_by: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class DisputeStatus(StrEnum):
    OPEN = "open"  # collecting evidence
    RECOMMENDED = "recommended"  # the mediator has proposed an outcome
    RESOLVED = "resolved"  # an operator decided


class DisputeParty(StrEnum):
    GUEST = "guest"
    HOST = "host"


class EvidenceKind(StrEnum):
    STATEMENT = "statement"
    PHOTO = "photo"  # a photo URL plus a description of what it shows


class Dispute(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "disputes"
    __table_args__ = (CheckConstraint("claim_minor > 0", name="positive_claim"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id"), index=True, nullable=False
    )
    # Disputes are guest refund claims; the host responds with evidence.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # The refund the claimant asks for, in the booking currency.
    claim_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[DisputeStatus] = mapped_column(
        str_enum(DisputeStatus), default=DisputeStatus.OPEN, nullable=False
    )
    recommendation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recommended_by: Mapped[str | None] = mapped_column(String(100))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_refund_minor: Mapped[int | None] = mapped_column(Integer)
    decision_note: Mapped[str | None] = mapped_column(Text)

    evidence: Mapped[list[DisputeEvidence]] = relationship(
        back_populates="dispute", order_by="DisputeEvidence.created_at"
    )


class DisputeEvidence(UUIDPrimaryKey, Base):
    __tablename__ = "dispute_evidence"

    dispute_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("disputes.id"), index=True, nullable=False
    )
    party: Mapped[DisputeParty] = mapped_column(str_enum(DisputeParty), nullable=False)
    kind: Mapped[EvidenceKind] = mapped_column(str_enum(EvidenceKind), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    dispute: Mapped[Dispute] = relationship(back_populates="evidence")
