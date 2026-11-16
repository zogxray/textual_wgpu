"""ETA estimator (Textual ``textual.eta``)."""

from __future__ import annotations

import time


class ETA:
    def __init__(self) -> None:
        self._start = time.monotonic()

    def get_eta(self, progress: float) -> float | None:
        if progress <= 0.0:
            return None
        elapsed = time.monotonic() - self._start
        return max(0.0, elapsed * (1.0 - progress) / progress)
