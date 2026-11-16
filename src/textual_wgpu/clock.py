"""Clock — wall & mock clocks (Textual ``textual.clock``)."""

from __future__ import annotations

import time


class Clock:
    def get_time(self) -> float:
        return time.monotonic()

    @property
    def time(self) -> float:
        return self.get_time()


class MockClock(Clock):
    def __init__(self, initial_time: float = 0.0) -> None:
        self._time = float(initial_time)

    def set_time(self, value: float) -> None:
        self._time = float(value)

    def advance(self, delta: float) -> None:
        self._time += float(delta)

    def get_time(self) -> float:
        return self._time
