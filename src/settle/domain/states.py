"""The booking payment lifecycle as an explicit state machine.

Any transition not listed in ``TRANSITIONS`` is rejected. This is what makes duplicate and
out-of-order provider events harmless: replaying "captured" on a booking that is already
``held`` is refused instead of moving money twice.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from settle.core.errors import InvalidTransition


class BookingStatus(StrEnum):
    PENDING = "pending"  # created, guest has not paid
    AUTHORIZED = "authorized"  # guest's funds reserved
    CAPTURED = "captured"  # guest charged (momentary; becomes held)
    HELD = "held"  # we hold the money until after check-in
    DISPUTED = "disputed"  # a claim is open; payout frozen until it is decided
    PARTIALLY_REFUNDED = "partially_refunded"  # part refunded; host gets the rest later
    RELEASED = "released"  # hold ended; payout queued or in flight
    PAID_OUT = "paid_out"  # host paid; done
    PAYOUT_FAILED = "payout_failed"  # provider refused the payout; can be retried
    VOIDED = "voided"  # cancelled before any charge; done
    REFUNDED = "refunded"  # guest refunded in full; done


S = BookingStatus

TRANSITIONS: Final[dict[BookingStatus, frozenset[BookingStatus]]] = {
    S.PENDING: frozenset({S.AUTHORIZED, S.VOIDED}),
    S.AUTHORIZED: frozenset({S.CAPTURED, S.VOIDED}),
    S.CAPTURED: frozenset({S.HELD, S.REFUNDED}),
    S.HELD: frozenset({S.RELEASED, S.DISPUTED, S.PARTIALLY_REFUNDED, S.REFUNDED}),
    S.DISPUTED: frozenset({S.HELD, S.PARTIALLY_REFUNDED, S.REFUNDED}),
    S.PARTIALLY_REFUNDED: frozenset({S.RELEASED}),
    S.RELEASED: frozenset({S.PAID_OUT, S.PAYOUT_FAILED}),
    S.PAYOUT_FAILED: frozenset({S.RELEASED}),
    S.PAID_OUT: frozenset(),
    S.VOIDED: frozenset(),
    S.REFUNDED: frozenset(),
}

TERMINAL_STATES: Final = frozenset(state for state, nxt in TRANSITIONS.items() if not nxt)

# States in which the platform holds the guest's money and the host has not been paid.
FUNDS_HELD_STATES: Final = frozenset({S.HELD, S.DISPUTED, S.PARTIALLY_REFUNDED})

# States from which held funds may be released to the host.
RELEASABLE_STATES: Final = frozenset({S.HELD, S.PARTIALLY_REFUNDED})


def can_transition(current: BookingStatus, target: BookingStatus) -> bool:
    return target in TRANSITIONS[current]


def ensure_transition(current: BookingStatus, target: BookingStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"Booking cannot move from {current} to {target}")
