"""Signal — reactive pub/sub signal (Textual ``textual.signal``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class Signal(Generic[T]):
    def __init__(self, owner: object, name: str) -> None:
        self.owner = owner
        self.name = name
        self._listeners: list[Callable[[T], None]] = []

    def subscribe(self, owner: object, callback: Callable[[T], None]) -> None:
        _ = owner
        if callback not in self._listeners:
            self._listeners.append(callback)

    def publish(self, data: T) -> None:
        for cb in list(self._listeners):
            cb(data)
