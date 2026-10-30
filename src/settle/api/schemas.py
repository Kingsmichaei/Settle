"""Request and response bodies. Kept separate from ORM models so the API contract is explicit."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from settle.db.models import (
    DisputeParty,
    DisputeStatus,
    EvidenceKind,
    OutboxStatus,
    PaymentIntentStatus,
    PayoutStatus,
    RefundSource,
    RefundStatus,
    RiskAction,
    UserRole,
)
from settle.domain.money import Money
from settle.domain.policies import CancellationPolicy
from settle.domain.states import BookingStatus


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class MoneyOut(Schema):
    amount_minor: int = Field(description="Amount in minor units, e.g. cents")
    currency: str
    formatted: str

    @classmethod
    def of(cls, money: Money) -> MoneyOut:
        return cls(amount_minor=money.amount, currency=money.currency, formatted=str(money))


class MoneyIn(Schema):
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    def to_money(self) -> Money:
        return Money(self.amount_minor, self.currency)


# ---- auth -------------------------------------------------------------------------------


class LoginIn(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    country: str
    default_currency: str


class TokenOut(Schema):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


# ---- listings and bookings --------------------------------------------------------------


class ListingOut(Schema):
    id: uuid.UUID
    title: str
    city: str
    country: str
    image_url: str | None
    nightly_price: MoneyOut
    cancellation_policy: CancellationPolicy
    policy_summary: str
    host_name: str


class CreateBookingIn(Schema):
    listing_id: uuid.UUID
    check_in: date
    check_out: date


class CancelBookingIn(Schema):
    reason: str = Field(default="cancelled by guest", min_length=1, max_length=200)


class StatusChangeOut(Schema):
    from_status: BookingStatus | None
    to_status: BookingStatus
    reason: str
    created_at: datetime


class PaymentOut(Schema):
    status: PaymentIntentStatus
    amount: MoneyOut
    refunded: MoneyOut
    provider: str
    provider_order_id: str
    approval_url: str | None


class PayoutOut(Schema):
    status: PayoutStatus
    amount: MoneyOut
    host_receives: MoneyOut | None
    fx_rate: str | None
    release_at: datetime
    provider_payout_id: str | None
    failure_reason: str | None
    review_reason: str | None


class RefundOut(Schema):
    id: uuid.UUID
    status: RefundStatus
    source: RefundSource
    amount: MoneyOut
    from_host: MoneyOut
    from_fee: MoneyOut
    reason: str
    created_at: datetime


class RiskOut(Schema):
    score: int
    action: RiskAction
    signals: list[dict[str, Any]]
    explanation: str
    explained_by: str
    reviewed_at: datetime | None


class BookingOut(Schema):
    id: uuid.UUID
    status: BookingStatus
    listing: ListingOut
    guest_id: uuid.UUID
    host_id: uuid.UUID
    check_in: date
    check_out: date
    nights: int
    total: MoneyOut
    cancelled_at: datetime | None
    payment: PaymentOut | None
    payout: PayoutOut | None
    refunds: list[RefundOut]
    risk: RiskOut | None
    dispute_ids: list[uuid.UUID]
    history: list[StatusChangeOut]
    created_at: datetime


class BookingSummaryOut(Schema):
    id: uuid.UUID
    status: BookingStatus
    listing_title: str
    check_in: date
    check_out: date
    total: MoneyOut
    created_at: datetime


class CheckoutOut(Schema):
    booking_id: uuid.UUID
    provider_order_id: str
    approval_url: str | None


class CancellationQuoteOut(Schema):
    policy: CancellationPolicy
    refund: MoneyOut
    refund_percent: int
    explanation: str


# ---- disputes ---------------------------------------------------------------------------


class OpenDisputeIn(Schema):
    reason: str = Field(min_length=3, max_length=500)
    claim: MoneyIn
    statement: str | None = Field(default=None, max_length=4000)


class EvidenceIn(Schema):
    kind: EvidenceKind
    content: str = Field(min_length=1, max_length=4000, description="Statement, or photo caption")
    url: HttpUrl | None = None


class EvidenceOut(Schema):
    id: uuid.UUID
    party: DisputeParty
    kind: EvidenceKind
    content: str
    url: str | None
    created_at: datetime


class RecommendationOut(Schema):
    refund: MoneyOut
    reasoning: str
    cited_evidence_ids: list[str]
    confidence: float
    recommended_by: str


class DisputeOut(Schema):
    id: uuid.UUID
    booking_id: uuid.UUID
    status: DisputeStatus
    reason: str
    claim: MoneyOut
    max_refundable: MoneyOut | None
    evidence: list[EvidenceOut]
    recommendation: RecommendationOut | None
    decision_refund: MoneyOut | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime


class DecideDisputeIn(Schema):
    refund: MoneyIn
    note: str = Field(default="", max_length=1000)


# ---- hosts ------------------------------------------------------------------------------


class HostPayoutOut(Schema):
    booking_id: uuid.UUID
    listing_title: str
    check_in: date
    payout: PayoutOut


class HostSummaryOut(Schema):
    owed: list[MoneyOut]
    payouts: list[HostPayoutOut]


class AssistantIn(Schema):
    question: str = Field(min_length=1, max_length=500)


class AssistantOut(Schema):
    answer: str
    answered_by: str


# ---- admin ------------------------------------------------------------------------------


class OutboxEventOut(Schema):
    id: uuid.UUID
    topic: str
    aggregate_id: str
    status: OutboxStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None


class LedgerIntegrityOut(Schema):
    balanced: bool
    unbalanced_entry_ids: list[uuid.UUID]


class FindingOut(Schema):
    code: str
    severity: str
    message: str
    booking_id: str | None


class ReconciliationRunOut(Schema):
    id: uuid.UUID
    started_at: datetime
    finished_at: datetime
    checks_run: int
    findings: list[FindingOut]
    summary: str
    summarized_by: str


class CountOut(Schema):
    label: str
    count: int


class AdminSummaryOut(Schema):
    bookings_by_status: list[CountOut]
    funds_held: list[MoneyOut]
    owed_to_hosts: list[MoneyOut]
    platform_revenue: list[MoneyOut]
    open_disputes: int
    blocked_payouts: int
    risk_reviews: int
    dead_events: int


class HealthOut(Schema):
    status: str
    database: str
    provider: str
    ai: str


class WebhookAckOut(Schema):
    received: bool
    duplicate: bool


class ProblemOut(Schema):
    """Error body, modelled on RFC 9457 (Problem Details for HTTP APIs)."""

    type: str
    title: str
    status: int
    detail: str
