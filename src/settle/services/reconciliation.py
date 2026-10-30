"""Deterministic reconciliation: the ledger against our records, and our records against the
provider. The checks decide; the AI explainer only summarises what they found."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from settle.ai.reconciliation_agent import FindingView
from settle.db.models import (
    JournalEntry,
    LedgerAccount,
    LedgerLine,
    OutboxEvent,
    OutboxStatus,
    PaymentIntent,
    Payout,
    PayoutStatus,
    ReconciliationRun,
    Refund,
    RefundStatus,
    WebhookEvent,
)
from settle.ledger.accounts import AccountKind, EntryDirection
from settle.ledger.posting import Ledger
from settle.providers.base import CaptureState, ProviderError
from settle.services.context import Services

logger = logging.getLogger(__name__)

STALE_PAYOUT_AFTER: Final = timedelta(hours=48)
MAX_PROVIDER_CHECKS: Final = 500


class Severity(StrEnum):
    CRITICAL = "critical"  # money may be wrong; act now
    WARNING = "warning"  # needs attention soon
    INFO = "info"  # worth knowing


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    message: str
    booking_id: str | None = None

    def view(self) -> FindingView:
        return FindingView(self.code, self.severity.value, self.message, self.booking_id)


class Reconciler:
    def __init__(self, session: Session, services: Services) -> None:
        self._session = session
        self._services = services
        self.checks_run = 0
        self.findings: list[Finding] = []

    def run(self) -> list[Finding]:
        self._ledger_entries_balance()
        self._accounts_tie_out()
        self._captures_are_recorded()
        self._payouts_are_recorded()
        self._refunds_are_recorded()
        self._provider_agrees()
        self._operational_health()
        return self.findings

    # ---- the checks ---------------------------------------------------------------------

    def _ledger_entries_balance(self) -> None:
        self.checks_run += 1
        for entry_id in Ledger(self._session).unbalanced_entry_ids():
            self._add(
                "unbalanced_entry", Severity.CRITICAL, f"Journal entry {entry_id} is unbalanced"
            )

    def _accounts_tie_out(self) -> None:
        """Cash at the provider must equal what we owe hosts plus the fees we earned."""
        totals: dict[tuple[str, AccountKind], int] = defaultdict(int)
        rows = self._session.execute(
            select(
                LedgerAccount.currency,
                LedgerAccount.kind,
                LedgerLine.direction,
                func.sum(LedgerLine.amount_minor),
            )
            .join(LedgerLine, LedgerLine.account_id == LedgerAccount.id)
            .group_by(LedgerAccount.currency, LedgerAccount.kind, LedgerLine.direction)
        ).all()
        for currency, kind, direction, amount in rows:
            sign = 1 if direction is EntryDirection.DEBIT else -1
            totals[(currency, kind)] += sign * int(amount)
        for currency in {currency for currency, _ in totals}:
            self.checks_run += 1
            cash = totals[(currency, AccountKind.PROVIDER_CASH)]
            owed_and_earned = -(
                totals[(currency, AccountKind.HOST_PAYABLE)]
                + totals[(currency, AccountKind.PLATFORM_REVENUE)]
            )
            if cash != owed_and_earned:
                self._add(
                    "accounts_do_not_tie_out",
                    Severity.CRITICAL,
                    f"{currency}: provider cash {cash} != host payable + revenue {owed_and_earned}",
                )

    def _captures_are_recorded(self) -> None:
        intents = self._session.scalars(
            select(PaymentIntent).where(PaymentIntent.provider_capture_id.is_not(None))
        ).all()
        for intent in intents:
            self.checks_run += 1
            debited = self._entry_debit(f"capture:{intent.id}", AccountKind.PROVIDER_CASH)
            if debited is None:
                self._add(
                    "capture_not_in_ledger",
                    Severity.CRITICAL,
                    f"Capture {intent.provider_capture_id} has no ledger entry",
                    intent.booking_id,
                )
            elif debited != intent.amount_minor:
                self._add(
                    "capture_amount_mismatch",
                    Severity.CRITICAL,
                    f"Ledger recorded {debited} for a capture of {intent.amount_minor}",
                    intent.booking_id,
                )

    def _payouts_are_recorded(self) -> None:
        for payout in self._session.scalars(
            select(Payout).where(Payout.status == PayoutStatus.PAID)
        ):
            self.checks_run += 1
            debited = self._entry_debit(f"payout:{payout.id}", AccountKind.HOST_PAYABLE)
            if debited is None:
                self._add(
                    "payout_not_in_ledger",
                    Severity.CRITICAL,
                    f"Paid payout {payout.provider_payout_id} has no ledger entry",
                    payout.booking_id,
                )
            elif debited != payout.amount_minor:
                self._add(
                    "payout_amount_mismatch",
                    Severity.CRITICAL,
                    f"Ledger recorded {debited} for a payout of {payout.amount_minor}",
                    payout.booking_id,
                )

    def _refunds_are_recorded(self) -> None:
        refunded_by_booking: dict[uuid.UUID, int] = defaultdict(int)
        for refund in self._session.scalars(
            select(Refund).where(Refund.status == RefundStatus.SUCCEEDED)
        ):
            self.checks_run += 1
            refunded_by_booking[refund.booking_id] += refund.amount_minor
            if self._entry_debit(f"refund:{refund.id}", None) is None:
                self._add(
                    "refund_not_in_ledger",
                    Severity.CRITICAL,
                    f"Refund {refund.provider_refund_id} has no ledger entry",
                    refund.booking_id,
                )
        for intent in self._session.scalars(
            select(PaymentIntent).where(PaymentIntent.refunded_minor > 0)
        ):
            self.checks_run += 1
            if refunded_by_booking[intent.booking_id] != intent.refunded_minor:
                self._add(
                    "refund_total_mismatch",
                    Severity.CRITICAL,
                    f"Refunds total {refunded_by_booking[intent.booking_id]} but the payment "
                    f"records {intent.refunded_minor}",
                    intent.booking_id,
                )

    def _provider_agrees(self) -> None:
        intents = self._session.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.provider_capture_id.is_not(None))
            .order_by(PaymentIntent.updated_at.desc())
            .limit(MAX_PROVIDER_CHECKS)
        ).all()
        for intent in intents:
            if intent.provider_capture_id is None:
                continue
            self.checks_run += 1
            try:
                info = self._services.provider.get_capture(intent.provider_capture_id)
            except ProviderError as exc:
                self._add(
                    "provider_unverified",
                    Severity.WARNING,
                    f"Could not verify capture {intent.provider_capture_id}: {exc}",
                    intent.booking_id,
                )
                continue
            if info.state is CaptureState.REVERSED:
                self._add(
                    "chargeback",
                    Severity.CRITICAL,
                    f"Capture {info.capture_id} was reversed (chargeback)",
                    intent.booking_id,
                )
            if info.amount.amount != intent.amount_minor:
                self._add(
                    "provider_amount_mismatch",
                    Severity.CRITICAL,
                    f"Provider captured {info.amount} but we recorded {intent.amount}",
                    intent.booking_id,
                )
            if info.refunded is not None and info.refunded.amount != intent.refunded_minor:
                self._add(
                    "provider_refund_mismatch",
                    Severity.CRITICAL,
                    f"Provider shows {info.refunded} refunded; we recorded {intent.refunded_minor}",
                    intent.booking_id,
                )

    def _operational_health(self) -> None:
        now = self._services.clock.now()
        self.checks_run += 1
        for payout in self._session.scalars(
            select(Payout).where(
                Payout.status == PayoutStatus.SENT, Payout.sent_at < now - STALE_PAYOUT_AFTER
            )
        ):
            self._add(
                "payout_unconfirmed",
                Severity.WARNING,
                f"Payout {payout.provider_payout_id} sent over 48h ago with no confirmation",
                payout.booking_id,
            )
        self.checks_run += 1
        dead = self._session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.DEAD)
        )
        if dead:
            self._add(
                "dead_events",
                Severity.WARNING,
                f"{dead} background job(s) gave up and need a human",
            )
        self.checks_run += 1
        for payout in self._session.scalars(
            select(Payout).where(Payout.status == PayoutStatus.BLOCKED)
        ):
            self._add(
                "payout_blocked",
                Severity.INFO,
                f"Payout held for review: {payout.review_reason}",
                payout.booking_id,
            )
        self.checks_run += 1
        duplicates = self._session.scalars(
            select(WebhookEvent).where(WebhookEvent.deliveries > 1)
        ).all()
        for webhook in duplicates:
            self._add(
                "duplicate_webhook",
                Severity.INFO,
                f"Webhook {webhook.provider_event_id} ({webhook.event_type}) was delivered "
                f"{webhook.deliveries} times and processed once",
            )

    # ---- helpers ------------------------------------------------------------------------

    def _entry_debit(self, idempotency_key: str, kind: AccountKind | None) -> int | None:
        """Total debited by an entry (to one kind of account, or to all). None if no entry."""
        entry_id = self._session.scalar(
            select(JournalEntry.id).where(JournalEntry.idempotency_key == idempotency_key)
        )
        if entry_id is None:
            return None
        query = (
            select(func.coalesce(func.sum(LedgerLine.amount_minor), 0))
            .join(LedgerAccount, LedgerAccount.id == LedgerLine.account_id)
            .where(LedgerLine.entry_id == entry_id, LedgerLine.direction == EntryDirection.DEBIT)
        )
        if kind is not None:
            query = query.where(LedgerAccount.kind == kind)
        return int(self._session.scalar(query) or 0)

    def _add(
        self, code: str, severity: Severity, message: str, booking_id: uuid.UUID | None = None
    ) -> None:
        self.findings.append(
            Finding(code, severity, message, str(booking_id) if booking_id else None)
        )


def run_reconciliation(session: Session, services: Services) -> ReconciliationRun:
    started = services.clock.now()
    reconciler = Reconciler(session, services)
    findings = reconciler.run()
    summary, summarized_by = services.explainer.summarize(
        [finding.view() for finding in findings], reconciler.checks_run
    )
    run = ReconciliationRun(
        started_at=started,
        finished_at=services.clock.now(),
        checks_run=reconciler.checks_run,
        findings=[{**asdict(finding), "severity": finding.severity.value} for finding in findings],
        summary=summary,
        summarized_by=summarized_by,
    )
    session.add(run)
    session.flush()
    if any(finding.severity is Severity.CRITICAL for finding in findings):
        logger.error("Reconciliation found critical issues: %s", summary)
    return run
