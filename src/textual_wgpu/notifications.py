"""Notifications — Textual ``textual.notifications`` parity."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Notification:
    message: str
    title: str = ""
    severity: str = "information"
    timeout: float = 3.0
    created: float = field(default_factory=time.monotonic)


class Notifications:
    """Collection of active notifications."""

    def __init__(self) -> None:
        self._items: list[Notification] = []

    def add(self, notification: Notification) -> None:
        self._items.append(notification)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)
