from __future__ import annotations

import random
from datetime import timedelta

from hypothesis import given
from hypothesis import strategies as st

from settle.outbox.relay import RetryPolicy

POLICY = RetryPolicy(base_delay=timedelta(seconds=2), max_delay=timedelta(minutes=5))


@given(attempt=st.integers(min_value=1, max_value=50), seed=st.integers())
def test_delay_is_between_zero_and_the_capped_exponential(attempt: int, seed: int) -> None:
    delay = POLICY.delay_for(attempt, random.Random(seed))
    ceiling = min(POLICY.max_delay.total_seconds(), 2.0 * 2 ** (attempt - 1))
    assert timedelta(0) <= delay <= timedelta(seconds=ceiling)
