from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from settle.domain.money import Money
from settle.domain.policies import CancellationPolicy, quote_refund

CHECK_IN = datetime(2026, 12, 12, tzinfo=UTC)
PAID = Money(50_000, "USD")


@pytest.mark.parametrize(
    ("policy", "notice", "expected"),
    [
        (CancellationPolicy.FLEXIBLE, timedelta(days=1), 50_000),
        (CancellationPolicy.FLEXIBLE, timedelta(hours=23), 0),
        (CancellationPolicy.MODERATE, timedelta(days=5), 50_000),
        (CancellationPolicy.MODERATE, timedelta(days=4), 25_000),
        (CancellationPolicy.MODERATE, timedelta(hours=1), 0),
        (CancellationPolicy.STRICT, timedelta(days=30), 25_000),
        (CancellationPolicy.STRICT, timedelta(days=6), 0),
        (CancellationPolicy.MODERATE, -timedelta(days=1), 0),  # after check-in
    ],
)
def test_refund_depends_on_policy_and_notice(
    policy: CancellationPolicy, notice: timedelta, expected: int
) -> None:
    quote = quote_refund(policy, PAID, cancelled_at=CHECK_IN - notice, check_in_at=CHECK_IN)
    assert quote.refund == Money(expected, "USD")
    assert quote.policy is policy
    assert quote.explanation


def test_full_refund_is_flagged() -> None:
    quote = quote_refund(
        CancellationPolicy.FLEXIBLE,
        PAID,
        cancelled_at=CHECK_IN - timedelta(days=9),
        check_in_at=CHECK_IN,
    )
    assert quote.is_full
