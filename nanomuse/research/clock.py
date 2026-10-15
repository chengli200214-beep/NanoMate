"""Injectable wall clock, always UTC."""

from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> float:
        return time.time()


class FakeClock:
    def __init__(self, value: float = 1800000000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()
