"""The booking lifecycle end to end, at the service level."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.core.errors import InvalidTransition, ValidationFailed
from settle.db.models import PayoutStatus, Refund, RefundStatus, User
from settle.db.session import transaction
from settle.domain.money import Money
from settle.domain.states import BookingStatus as S
from settle.ledger.accounts import AccountRef
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult
from settle.services.bookings import get_booking
from settle.services.payouts import release_due_payouts, retry_failed_payout
from tests.support.data import CHECK_IN, CHECK_OUT
from tests.support.flow import (
    balance,
    book,
    book_and_pay,
    cancel,
    checkout_and_approve,
    host_owed,
    ledger_is_balanced,
    payout_of,
    release_payouts_after_check_in,
    status_of,
)

USD_CASH = AccountRef.provider_cash("USD")
USD_FEES = AccountRef.platform_revenue("USD")
MODERATE, FLEXIBLE, STRICT = 0, 1, 2  # listing indexes in the seed data


def test_happy_path_pays_the_host_in_their_currency(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)

    assert status_of(container, booking_id) is S.HELD
    assert balance(container, USD_CASH) == 50_000
    assert host_owed(container, demo) == 42_500
    assert balance(container, USD_FEES) == 7_500

    assert release_payouts_after_check_in(container, clock) == 1
    container.relay.drain()

    payout = payout_of(container, booking_id)
    assert status_of(container, booking_id) is S.PAID_OUT
    assert payout.status is PayoutStatus.PAID
    assert payout.payout_amount == Money(39_100, "EUR")  # USD 425.00 at 0.92
    assert payout.fx_rate == "0.92"
    assert host_owed(container, demo) == 0
    assert balance(container, USD_CASH) == 7_500  # only the fee remains with us
    assert ledger_is_balanced(container)


def test_payout_is_released_only_once_the_hold_ends(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    release_at = payout_of(container, booking_id).release_at
    assert release_at.date() == CHECK_IN + timedelta(days=1)

    clock.set(release_at - timedelta(minutes=1))
    with transaction(container.session_factory) as session:
        assert release_due_payouts(session, clock) == 0
    clock.set(release_at)
    with transaction(container.session_factory) as session:
        assert release_due_payouts(session, clock) == 1
    assert status_of(container, booking_id) is S.RELEASED


def test_listing_cannot_be_double_booked(container: Container, demo: SeedResult) -> None:
    book(container, demo)
    with pytest.raises(InvalidTransition):
        book(container, demo, check_in=CHECK_IN + timedelta(days=2), check_out=CHECK_OUT)
    # Adjacent stays are fine: check-out day is the next guest's check-in day.
    book(container, demo, check_in=CHECK_OUT, check_out=CHECK_OUT + timedelta(days=2))


def test_cancelled_booking_frees_the_dates(container: Container, demo: SeedResult) -> None:
    cancel(container, book(container, demo))
    book(container, demo)  # same dates, no clash


# ---- cancellation policies --------------------------------------------------------------


def test_cancel_before_payment_voids(container: Container, demo: SeedResult) -> None:
    booking_id = book(container, demo)
    cancel(container, booking_id)
    assert status_of(container, booking_id) is S.VOIDED


def test_cancel_early_under_moderate_refunds_in_full(
    container: Container, demo: SeedResult
) -> None:
    booking_id = book_and_pay(container, demo, listing_index=MODERATE)  # 41 days' notice
    quote = cancel(container, booking_id)
    container.relay.drain()

    assert quote.is_full
    assert status_of(container, booking_id) is S.REFUNDED
    assert balance(container, USD_CASH) == 0
    assert host_owed(container, demo) == 0
    assert balance(container, USD_FEES) == 0
    assert payout_of(container, booking_id).status is PayoutStatus.CANCELLED


def test_cancel_late_under_moderate_refunds_half_and_pays_the_host_the_rest(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo, listing_index=MODERATE)
    clock.set(clock.now().replace(year=2026, month=12, day=9))  # 3 days before check-in
    quote = cancel(container, booking_id)
    container.relay.drain()

    assert quote.refund == Money(25_000, "USD")
    assert status_of(container, booking_id) is S.PARTIALLY_REFUNDED
    # Half the payment went back, funded proportionally from the host share and our fee.
    assert balance(container, USD_CASH) == 25_000
    assert host_owed(container, demo) == 21_250
    assert balance(container, USD_FEES) == 3_750
    assert payout_of(container, booking_id).amount_minor == 21_250

    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    assert status_of(container, booking_id) is S.PAID_OUT
    assert balance(container, USD_CASH) == 3_750
    assert ledger_is_balanced(container)


def test_cancel_inside_the_strict_window_refunds_nothing(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo, listing_index=STRICT)
    clock.set(clock.now().replace(month=12, day=8))  # 4 days before check-in
    quote = cancel(container, booking_id)
    container.relay.drain()

    assert quote.refund.is_zero
    assert status_of(container, booking_id) is S.HELD  # host is still paid after check-in
    with pytest.raises(InvalidTransition):
        cancel(container, booking_id)  # can't cancel twice


def test_cancel_after_payout_is_refused(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    with pytest.raises(InvalidTransition):
        cancel(container, booking_id)


def test_payout_waits_while_a_refund_is_in_flight(
    container: Container, demo: SeedResult, clock: FrozenClock, provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo, listing_index=FLEXIBLE)
    clock.set(clock.now().replace(month=12, day=10))
    provider.fail_next_transient("refund")
    cancel(container, booking_id)
    container.relay.run_once()  # the refund fails transiently and is rescheduled

    assert release_payouts_after_check_in(container, clock) == 0  # not released mid-refund
    container.relay.drain()
    assert status_of(container, booking_id) is S.REFUNDED


# ---- provider failures ------------------------------------------------------------------


def test_declined_authorization_voids_the_booking(
    container: Container, demo: SeedResult, provider: FakeProvider
) -> None:
    provider.fail_next_permanent("authorize", "card declined")
    booking_id = book(container, demo)
    checkout_and_approve(container, booking_id)
    container.relay.drain()

    assert status_of(container, booking_id) is S.VOIDED
    assert balance(container, USD_CASH) == 0


def test_failed_capture_voids_the_authorization(
    container: Container, demo: SeedResult, provider: FakeProvider
) -> None:
    provider.fail_next_permanent("capture", "authorization expired")
    booking_id = book(container, demo)
    checkout_and_approve(container, booking_id)
    container.relay.drain()

    assert status_of(container, booking_id) is S.VOIDED
    assert provider.executions["void"] == 1
    assert balance(container, USD_CASH) == 0


def test_refused_refund_blocks_the_payout(
    container: Container, demo: SeedResult, clock: FrozenClock, provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    provider.fail_next_permanent("refund", "capture too old to refund")
    cancel(container, booking_id)
    container.relay.drain()

    with transaction(container.session_factory) as session:
        refund = session.scalars(select(Refund).where(Refund.booking_id == booking_id)).one()
        assert refund.status is RefundStatus.FAILED
    assert payout_of(container, booking_id).status is PayoutStatus.BLOCKED
    assert release_payouts_after_check_in(container, clock) == 0


def test_failed_payout_can_be_retried_after_the_host_fixes_details(
    container: Container, demo: SeedResult, clock: FrozenClock, provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    provider.fail_next_permanent("payout", "receiver account is restricted")
    release_payouts_after_check_in(container, clock)
    container.relay.drain()

    assert status_of(container, booking_id) is S.PAYOUT_FAILED
    assert host_owed(container, demo) == 42_500  # still owed; nothing left our account

    with transaction(container.session_factory) as session:
        retry_failed_payout(session, clock, get_booking(session, booking_id))
    container.relay.drain()

    assert status_of(container, booking_id) is S.PAID_OUT
    assert host_owed(container, demo) == 0
    assert provider.executions["payout"] == 1


def test_host_without_payout_method_fails_safely(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    with transaction(container.session_factory) as session:
        session.get_one(User, demo.host.id).payout_email = None
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()

    assert status_of(container, booking_id) is S.PAYOUT_FAILED
    assert host_owed(container, demo) == 42_500


def test_every_status_change_is_recorded_in_order(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    with transaction(container.session_factory) as session:
        history = [change.to_status for change in get_booking(session, booking_id).history]
    assert history == [S.PENDING, S.AUTHORIZED, S.CAPTURED, S.HELD, S.RELEASED, S.PAID_OUT]


def test_booking_in_the_past_is_rejected(container: Container, demo: SeedResult) -> None:
    with pytest.raises(ValidationFailed):
        book(container, demo, check_in=date(2026, 1, 1), check_out=date(2026, 1, 3))
