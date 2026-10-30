"""Cancellation policies: how much a guest gets back, depending on how early they cancel.

Times are measured to the start of the check-in day (00:00 UTC), the same instant the payout
hold is measured from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from settle.domain.money import BPS_DENOMINATOR, Money


class CancellationPolicy(StrEnum):
    FLEXIBLE = "flexible"
    MODERATE = "moderate"
    STRICT = "strict"


@dataclass(frozen=True, slots=True)
class RefundTier:
    """Refund ``refund_bps`` if the guest cancels at least ``notice`` before check-in."""

    notice: timedelta
    refund_bps: int


# Tiers are checked in order; the first one the guest qualifies for applies.
POLICY_TIERS: Final[dict[CancellationPolicy, tuple[RefundTier, ...]]] = {
    CancellationPolicy.FLEXIBLE: (RefundTier(timedelta(days=1), BPS_DENOMINATOR),),
    CancellationPolicy.MODERATE: (
        RefundTier(timedelta(days=5), BPS_DENOMINATOR),
        RefundTier(timedelta(days=1), BPS_DENOMINATOR // 2),
    ),
    CancellationPolicy.STRICT: (RefundTier(timedelta(days=7), BPS_DENOMINATOR // 2),),
}

POLICY_SUMMARIES: Final[dict[CancellationPolicy, str]] = {
    CancellationPolicy.FLEXIBLE: "Full refund up to 1 day before check-in.",
    CancellationPolicy.MODERATE: (
        "Full refund up to 5 days before check-in, 50% up to 1 day before."
    ),
    CancellationPolicy.STRICT: "50% refund up to 7 days before check-in.",
}


@dataclass(frozen=True, slots=True)
class RefundQuote:
    policy: CancellationPolicy
    refund_bps: int
    refund: Money
    explanation: str

    @property
    def is_full(self) -> bool:
        return self.refund_bps == BPS_DENOMINATOR


def quote_refund(
    policy: CancellationPolicy, paid: Money, *, cancelled_at: datetime, check_in_at: datetime
) -> RefundQuote:
    notice = check_in_at - cancelled_at
    for tier in POLICY_TIERS[policy]:
        if notice >= tier.notice:
            refund = paid.portion_bps(tier.refund_bps)
            percent = tier.refund_bps * 100 // BPS_DENOMINATOR
            return RefundQuote(
                policy, tier.refund_bps, refund, f"{percent}% refund under the {policy} policy"
            )
    return RefundQuote(
        policy, 0, Money.zero(paid.currency), f"No refund this close to check-in ({policy} policy)"
    )
