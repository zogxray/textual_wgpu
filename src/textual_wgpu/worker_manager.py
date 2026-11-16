"""WorkerManager — Textual ``textual.worker_manager`` parity."""

from __future__ import annotations

from typing import Any

from .worker import Worker


class WorkerManager:
    """Tracks active workers for an App."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._workers: list[Worker] = []

    def add(self, worker: Worker) -> None:
        self._workers.append(worker)

    def cancel_all(self) -> None:
        for w in list(self._workers):
            if not w.is_finished:
                w.cancel()

    def __iter__(self):
        return iter(self._workers)

    def __len__(self) -> int:
        return len(self._workers)
