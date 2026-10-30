"""Property test: whatever happens to a batch of bookings, the money always reconciles.

Hypothesis generates a random story per booking: which listing (so which cancellation policy),
whether and when the guest cancels, a dispute and its outcome, and provider faults (declines,
lost responses, refused payouts). After the worker settles everything we check the books:

* every journal entry balances;
* cash held at the provider == what we owe hosts + the fees we earned;
* every reconciliation check passes;
* every booking ends in a sensible final state.
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from settle.bootstrap import Container, build_container
from settle.config import Settings
from settle.core.clock import FrozenClock
from settle.db.base import Base
from settle.db.models import Dispute
from settle.db.session import make_engine, transaction
from settle.domain.money import Money
from settle.domain.states import BookingStatus as S
from settle.ledger.accounts import AccountRef
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult, seed_demo_data
from settle.services.bookings import get_booking
from settle.services.disputes import decide_dispute, open_dispute
from settle.services.payouts import release_due_payouts
from settle.services.reconciliation import Severity, run_reconciliation
from tests.support.data import NOW
from tests.support.flow import balance, book, cancel, checkout_and_approve, status_of

Cancel = Literal["never", "before_payment", "early", "late"]
Fault = Literal["none", "decline", "capture_timeout", "payout_timeout", "payout_refused"]

CHECK_IN = datetime(2026, 12, 12, tzinfo=UTC)
FINAL_STATES = {S.PAID_OUT, S.PAYOUT_FAILED, S.VOIDED, S.REFUNDED, S.HELD, S.PARTIALLY_REFUNDED}


@dataclass(frozen=True)
class Story:
    listing: int
    cancel: Cancel
    fault: Fault
    dispute_refund_pct: int | None  # None = no dispute


stories = st.builds(
    Story,
    listing=st.integers(min_value=0, max_value=2),
    cancel=st.sampled_from(["never", "before_payment", "early", "late"]),
    fault=st.sampled_from(
        ["none", "decline", "capture_timeout", "payout_timeout", "payout_refused"]
    ),
    dispute_refund_pct=st.one_of(st.none(), st.integers(min_value=0, max_value=100)),
)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(batch=st.lists(stories, min_size=1, max_size=3))
def test_books_always_reconcile(batch: list[Story]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'prop.db'}"
        engine = make_engine(url)
        Base.metadata.create_all(engine)
        clock, provider = FrozenClock(NOW), FakeProvider()
        container = build_container(
            Settings(database_url=url, outbox_max_attempts=5),
            clock=clock,
            provider=provider,
            engine=engine,
        )
        try:
            _play(container, clock, provider, batch)
        finally:
            container.close()


def _play(
    container: Container, clock: FrozenClock, provider: FakeProvider, batch: list[Story]
) -> None:
    with transaction(container.session_factory) as session:
        demo = seed_demo_data(session)

    booked = []
    for index, story in enumerate(batch):
        # Each story books its own week so listings never clash.
        start = (CHECK_IN + timedelta(days=7 * index)).date()
        booking_id = book(
            container,
            demo,
            listing_index=story.listing,
            check_in=start,
            check_out=start + timedelta(days=4),
        )
        booked.append(booking_id)
        if story.cancel == "before_payment":
            cancel(container, booking_id)
            continue
        if story.fault == "decline":
            provider.fail_next_permanent("authorize")
        if story.fault == "capture_timeout":
            provider.fail_next_transient("capture", lose_response=True)
        checkout_and_approve(container, booking_id)
        _settle(container, clock)

    for booking_id, story in zip(booked, batch, strict=True):
        if status_of(container, booking_id) is not S.HELD:
            continue
        if story.cancel in {"early", "late"}:
            when = CHECK_IN - (
                timedelta(days=30) if story.cancel == "early" else timedelta(hours=60)
            )
            clock.set(max(clock.now(), when))
            cancel(container, booking_id)
        elif story.dispute_refund_pct is not None:
            _dispute(container, demo, booking_id, story.dispute_refund_pct)
        _settle(container, clock)

    for story in batch:
        if story.fault == "payout_timeout":
            provider.fail_next_transient("payout", lose_response=True)
        if story.fault == "payout_refused":
            provider.fail_next_permanent("payout")
    clock.set(CHECK_IN + timedelta(days=7 * len(batch) + 2))
    _settle(container, clock, release=True)

    cash = balance(container, AccountRef.provider_cash("USD"))
    owed = sum(
        balance(container, AccountRef.host_payable(str(host.id), "USD"))
        for host in (demo.host, demo.host_ng)
    )
    fees = balance(container, AccountRef.platform_revenue("USD"))
    assert cash == owed + fees

    with transaction(container.session_factory) as session:
        run = run_reconciliation(session, container.services)
        critical = [f for f in run.findings if f["severity"] == Severity.CRITICAL]
    assert not critical, critical

    for booking_id in booked:
        assert status_of(container, booking_id) in FINAL_STATES


def _dispute(container: Container, demo: SeedResult, booking_id: uuid.UUID, pct: int) -> None:
    with transaction(container.session_factory) as session:
        booking = get_booking(session, booking_id)
        dispute = open_dispute(
            session, booking=booking, reason="test", claim=booking.total, statement=None
        )
        dispute_id = dispute.id
    with transaction(container.session_factory) as session:
        dispute = session.get_one(Dispute, dispute_id)
        refund = Money(dispute.claim_minor * pct // 100, dispute.currency)
        decide_dispute(
            session,
            container.services,
            dispute=dispute,
            decided_by=demo.admin,
            refund=refund,
            note="",
        )


def _settle(container: Container, clock: FrozenClock, *, release: bool = False) -> None:
    """Run the worker (and the payout release job), letting time pass for retries."""
    for _ in range(8):
        if release:
            with transaction(container.session_factory) as session:
                release_due_payouts(session, clock)
        container.relay.drain()
        clock.advance(timedelta(minutes=10))
