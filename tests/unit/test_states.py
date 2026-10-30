from __future__ import annotations

from collections import deque
from itertools import pairwise

import pytest

from settle.core.errors import InvalidTransition
from settle.domain.states import (
    TERMINAL_STATES,
    TRANSITIONS,
    BookingStatus,
    can_transition,
    ensure_transition,
)

S = BookingStatus

HAPPY_PATH = [S.PENDING, S.AUTHORIZED, S.CAPTURED, S.HELD, S.RELEASED, S.PAID_OUT]


def test_every_status_has_a_transition_rule() -> None:
    assert set(TRANSITIONS) == set(BookingStatus)


def test_terminal_states() -> None:
    assert {S.PAID_OUT, S.VOIDED, S.REFUNDED} == TERMINAL_STATES


def test_happy_path_is_allowed() -> None:
    for current, nxt in pairwise(HAPPY_PATH):
        ensure_transition(current, nxt)


def test_every_status_is_reachable_from_pending() -> None:
    seen, queue = {S.PENDING}, deque([S.PENDING])
    while queue:
        for nxt in TRANSITIONS[queue.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    assert seen == set(BookingStatus)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.CAPTURED),  # can't charge without authorizing
        (S.HELD, S.PAID_OUT),  # can't pay out before release
        (S.PAID_OUT, S.REFUNDED),  # money already left; needs a dispute flow
        (S.REFUNDED, S.HELD),  # terminal
        (S.HELD, S.HELD),  # no self-loops: replays are rejected
    ],
)
def test_illegal_transitions_are_rejected(current: S, target: S) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransition):
        ensure_transition(current, target)
