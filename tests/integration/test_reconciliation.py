"""Reconciliation catches each kind of discrepancy it claims to."""

from __future__ import annotations

from sqlalchemy import delete, select

from settle.bootstrap import Container
from settle.core.clock import FrozenClock
from settle.db.models import JournalEntry, LedgerLine, PaymentIntent, Payout
from settle.db.session import transaction
from settle.providers.base import EventKind
from settle.providers.fake import FakeProvider
from settle.seed import SeedResult
from settle.services.reconciliation import Severity, run_reconciliation
from tests.support.flow import book_and_pay, deliver_webhook, release_payouts_after_check_in


def findings(container: Container) -> dict[str, Severity]:
    with transaction(container.session_factory) as session:
        run = run_reconciliation(session, container.services)
        return {item["code"]: Severity(item["severity"]) for item in run.findings}


def test_clean_books_have_no_findings(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    book_and_pay(container, demo)
    book_and_pay(container, demo, listing_index=1)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    assert findings(container) == {}


def test_missing_ledger_entry_is_critical(container: Container, demo: SeedResult) -> None:
    booking_id = book_and_pay(container, demo)
    with transaction(container.session_factory) as session:
        intent = session.scalars(
            select(PaymentIntent).where(PaymentIntent.booking_id == booking_id)
        ).one()
        entry = session.scalars(
            select(JournalEntry).where(JournalEntry.idempotency_key == f"capture:{intent.id}")
        ).one()
        session.execute(delete(LedgerLine).where(LedgerLine.entry_id == entry.id))
        session.delete(entry)
    assert findings(container)["capture_not_in_ledger"] is Severity.CRITICAL


def test_payout_amount_drift_is_critical(
    container: Container, demo: SeedResult, clock: FrozenClock
) -> None:
    booking_id = book_and_pay(container, demo)
    release_payouts_after_check_in(container, clock)
    container.relay.drain()
    with transaction(container.session_factory) as session:
        payout = session.scalars(select(Payout).where(Payout.booking_id == booking_id)).one()
        payout.amount_minor += 1
    assert findings(container)["payout_amount_mismatch"] is Severity.CRITICAL


def test_provider_chargeback_is_critical(
    container: Container, demo: SeedResult, provider: FakeProvider
) -> None:
    booking_id = book_and_pay(container, demo)
    with transaction(container.session_factory) as session:
        capture_id = session.scalars(
            select(PaymentIntent.provider_capture_id).where(PaymentIntent.booking_id == booking_id)
        ).one()
    assert capture_id is not None
    provider.reverse_capture(capture_id)
    assert findings(container)["chargeback"] is Severity.CRITICAL


def test_duplicate_webhooks_are_reported_for_information(
    container: Container, provider: FakeProvider
) -> None:
    for _ in range(3):
        deliver_webhook(container, provider, EventKind.IGNORED, event_id="EVT-DUP")
    assert findings(container) == {"duplicate_webhook": Severity.INFO}


def test_summary_explains_the_findings(container: Container, provider: FakeProvider) -> None:
    deliver_webhook(container, provider, EventKind.IGNORED, event_id="EVT-DUP")
    deliver_webhook(container, provider, EventKind.IGNORED, event_id="EVT-DUP")
    with transaction(container.session_factory) as session:
        run = run_reconciliation(session, container.services)
    assert "EVT-DUP" in run.summary
    assert run.summarized_by == "rules"
