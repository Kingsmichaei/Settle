"""How a guest's payment, and any refund of it, is split between the host and the platform."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from settle.core.errors import CurrencyMismatch, ValidationFailed
from settle.domain.money import Money


@dataclass(frozen=True, slots=True)
class FeeSplit:
    gross: Money
    platform_fee: Money
    host_amount: Money

    def __post_init__(self) -> None:
        if self.platform_fee + self.host_amount != self.gross:
            raise ValidationFailed("Fee and host share must add up to the gross amount")


@dataclass(frozen=True, slots=True)
class RefundSplit:
    """Which part of a refund comes out of the host's share and which out of our fee."""

    total: Money
    from_host: Money
    from_fee: Money


def split_payment(gross: Money, platform_fee_bps: int) -> FeeSplit:
    """Split ``gross`` so that ``platform_fee + host_amount == gross`` exactly.

    The fee is rounded and the host receives the remainder, so no minor unit is ever lost.
    """
    if not gross.is_positive:
        raise ValidationFailed("A payment must be a positive amount")
    platform_fee = gross.portion_bps(platform_fee_bps)
    return FeeSplit(gross=gross, platform_fee=platform_fee, host_amount=gross - platform_fee)


def split_refund(payment: FeeSplit, refund: Money) -> RefundSplit:
    """Refund proportionally: the platform gives back the same fraction of its fee.

    ``from_host + from_fee == refund`` exactly, and neither part exceeds what is left of it.
    """
    if refund.currency != payment.gross.currency:
        raise CurrencyMismatch("A refund must be in the currency of the payment")
    if not Money.zero(refund.currency) < refund <= payment.gross:
        raise ValidationFailed("A refund must be positive and no more than the payment")
    exact_fee = Decimal(payment.platform_fee.amount) * refund.amount / payment.gross.amount
    from_fee = Money(int(exact_fee.quantize(Decimal(1), ROUND_HALF_EVEN)), refund.currency)
    from_host = refund - from_fee
    if from_host > payment.host_amount:  # only possible through rounding at the extremes
        from_host, from_fee = payment.host_amount, refund - payment.host_amount
    return RefundSplit(total=refund, from_host=from_host, from_fee=from_fee)


def remaining_after(payment: FeeSplit, refunded: RefundSplit | None) -> FeeSplit:
    """What is left of a payment after a refund."""
    if refunded is None:
        return payment
    return FeeSplit(
        gross=payment.gross - refunded.total,
        platform_fee=payment.platform_fee - refunded.from_fee,
        host_amount=payment.host_amount - refunded.from_host,
    )
