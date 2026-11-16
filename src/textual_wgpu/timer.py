"""Timer — Textual ``textual.timer.Timer`` parity."""

from __future__ import annotations


class Timer:
    """Cancelable / pausable interval or one-shot timer handle."""

    def __init__(self, interval: float = 0.0, *, name: str | None = None) -> None:
        self.interval = float(interval)
        self.name = name
        self.active = True
        self.paused = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def stop(self) -> None:
        self.active = False

    def cancel(self) -> None:
        self.stop()

    def reset(self) -> None:
        self.paused = False
        self.active = True
