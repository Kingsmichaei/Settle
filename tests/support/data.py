"""Fixed dates used across the suite, so tests never depend on the real clock."""

from __future__ import annotations

from datetime import UTC, date, datetime

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CHECK_IN = date(2026, 12, 12)
CHECK_OUT = date(2026, 12, 16)  # 4 nights at USD 125.00 = USD 500.00
