"""Cache utilities — ``FIFOCache`` / ``LRUCache`` (Textual ``textual.cache``)."""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class FIFOCache(Generic[K, V]):
    """Bounded FIFO key-value cache."""

    def __init__(self, maxsize: int = 100) -> None:
        self.maxsize = max(1, int(maxsize))
        self._data: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K, default: V | None = None) -> V | None:
        return self._data.get(key, default)

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        if key not in self._data and len(self._data) >= self.maxsize:
            self._data.popitem(last=False)
        self._data[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()


class LRUCache(FIFOCache[K, V]):
    """Bounded LRU key-value cache."""

    def get(self, key: K, default: V | None = None) -> V | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return default

    def __getitem__(self, key: K) -> V:
        self._data.move_to_end(key)
        return self._data[key]
