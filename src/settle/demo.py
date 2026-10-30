"""The end-to-end story, run on a throwaway database: ``settle demo``.

A US guest books a host in Portugal. The approval arrives by webhook, the risk agent scores the
booking, the money is captured and held. During the stay the guest disputes a broken boiler;
the AI mediator recommends a partial refund and an operator approves it. 24 hours after
check-in the host is paid in EUR. The payout webhook is delivered twice and processed once,
and reconciliation confirms every account ties out.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

from settle.bootstrap import Container, build_container
from settle.config import Settings
from settle.core.clock import FrozenClock
from settle.db.models import (
    Booking,
    Dispute,
    DisputeParty,
    EvidenceKind,
    PaymentIntent,
    Payout,
    RiskAssessment,
)
from settle.db.session import transaction
from settle.domain.money import Money
from settle.ledger.accounts import AccountRef
from settle.ledger.posting import Ledger
from settle.providers.base import EventKind
from settle.providers.fake import FakeProvider
from settle.seed import seed_demo_data
from settle.services.bookings import create_booking, get_booking
from settle.services.disputes import (
    add_evidence,
    decide_dispute,
    open_dispute,
    request_recommendation,
)
from settle.services.payments import PaymentService
from settle.services.payouts import release_due_payouts
from settle.services.reconciliation import run_reconciliation
from settle.services.webhooks import receive_webhook

Migrate = Callable[[str], None]


class Story:
    def __init__(self, container: Container, clock: FrozenClock, provider: FakeProvider) -> None:
        self.container = container
        self.clock = clock
        self.provider = provider
        self.step = 0

    def say(self, text: str) -> None:
        self.step += 1
        print(f"{self.step:>2}. {text}")

    def webhook(self, kind: EventKind, *, event_id: str | None = None, **data: str) -> None:
        body, headers = self.provider.build_webhook(kind, event_id=event_id, **data)
        with transaction(self.container.session_factory) as session:
            receive_webhook(session, self.container.services, headers=headers, body=body)

    def work(self) -> None:
        """Let the background worker run until it has nothing left to do."""
        self.container.relay.drain()

    def balances(self, host_id: str) -> tuple[Money, Money, Money]:
        with transaction(self.container.session_factory) as session:
            ledger = Ledger(session)
            return (
                ledger.balance(AccountRef.provider_cash("USD")),
                ledger.balance(AccountRef.host_payable(host_id, "USD")),
                ledger.balance(AccountRef.platform_revenue("USD")),
            )


def run_demo(migrate: Migrate) -> bool:
    """Returns True when the story ends with balanced books and no critical findings."""
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'demo.db'}"
        migrate(url)
        clock = FrozenClock(datetime(2026, 11, 1, 9, 0, tzinfo=UTC))
        provider = FakeProvider(async_payouts=True)
        container = build_container(Settings(database_url=url), clock=clock, provider=provider)
        try:
            return _tell(Story(container, clock, provider))
        finally:
            container.close()


def _tell(story: Story) -> bool:
    factory, services = story.container.session_factory, story.container.services

    with transaction(factory) as session:
        data = seed_demo_data(session)
        booking = create_booking(
            session,
            guest_id=data.guest.id,
            listing_id=data.listing.id,
            check_in=date(2026, 12, 12),
            check_out=date(2026, 12, 16),
            today=story.clock.now().date(),
        )
        order_id = PaymentService(session, services).start_checkout(booking).provider_order_id
        booking_id, host_id, admin = booking.id, str(data.host.id), data.admin
        story.say(f"Ada (US) books 4 nights at '{data.listing.title}', Lisbon: {booking.total}")

    story.webhook(EventKind.ORDER_APPROVED, order_id=order_id)
    story.work()
    with transaction(factory) as session:
        risk = session.scalars(
            select(RiskAssessment).where(RiskAssessment.booking_id == booking_id)
        ).one()
        story.say(f"Approval arrives by webhook. Risk score {risk.score} ({risk.action}).")
    cash, owed, fees = story.balances(host_id)
    story.say(f"Captured and held: cash {cash}, owed to host {owed}, platform fee {fees}")

    story.clock.set(datetime(2026, 12, 12, 20, 0, tzinfo=UTC))
    dispute_id = _guest_disputes(story, booking_id)
    story.work()  # the mediator runs in the background
    with transaction(factory) as session:
        dispute = session.get_one(Dispute, dispute_id)
        recommendation = dispute.recommendation or {}
        refund = Money(int(recommendation.get("refund_minor", 0)), "USD")
        story.say(
            f"Mediator ({dispute.recommended_by}) recommends refunding {refund}: "
            f"{recommendation.get('reasoning', '')}"
        )
        decide_dispute(
            session,
            services,
            dispute=dispute,
            decided_by=admin,
            refund=refund,
            note="Accepted the mediator's recommendation",
        )
    story.work()
    cash, owed, fees = story.balances(host_id)
    story.say(f"Operator approves; partial refund sent. Host now owed {owed}, platform fee {fees}")

    story.clock.set(datetime(2026, 12, 13, 0, 5, tzinfo=UTC))
    with transaction(factory) as session:
        release_due_payouts(session, story.clock)
    story.work()
    with transaction(factory) as session:
        payout = session.scalars(select(Payout).where(Payout.booking_id == booking_id)).one()
        payout_id = payout.id
        story.say(
            f"24h after check-in: payout of {payout.amount} sent as {payout.payout_amount} "
            f"(rate {payout.fx_rate})"
        )

    for _ in range(2):  # providers retry webhooks; the second delivery must change nothing
        story.webhook(EventKind.PAYOUT_SUCCEEDED, event_id="EVT-PAYOUT-1", payout_id=str(payout_id))
    story.work()
    cash, owed, fees = story.balances(host_id)
    with transaction(factory) as session:
        status = session.get_one(Booking, booking_id).status
        refunded = session.scalars(
            select(PaymentIntent.refunded_minor).where(PaymentIntent.booking_id == booking_id)
        ).one()
    story.say(f"Payout webhook delivered twice, processed once. Booking is '{status}'.")
    story.say(
        f"Books: cash {cash} = owed {owed} + fees {fees}; guest refunded {Money(refunded, 'USD')}"
    )

    with transaction(factory) as session:
        run = run_reconciliation(session, services)
    story.say(f"Reconciliation: {run.summary}")
    critical = any(finding["severity"] == "critical" for finding in run.findings)
    return cash == owed + fees and not critical


def _guest_disputes(story: Story, booking_id: uuid.UUID) -> uuid.UUID:
    with transaction(story.container.session_factory) as session:
        dispute = open_dispute(
            session,
            booking=get_booking(session, booking_id),
            reason="No hot water for the first two nights",
            claim=Money(10_000, "USD"),
            statement="The boiler showed error E9 and the host didn't reply to my messages.",
        )
        add_evidence(
            session,
            dispute=dispute,
            party=DisputeParty.GUEST,
            kind=EvidenceKind.PHOTO,
            content="Boiler control panel showing error code E9, photographed on arrival",
            url="https://example.com/e9.jpg",
        )
        request_recommendation(session, story.container.services, dispute)
        story.say("During the stay Ada disputes USD 100.00 for a broken boiler, with a photo")
        return dispute.id
