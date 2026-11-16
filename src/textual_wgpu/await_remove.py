"""AwaitRemove — awaitable returned by DOM removals."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any


class AwaitRemove:
    """Optionally-awaitable removal handle (Textual ``AwaitRemove``)."""

    def __init__(self, *items: Any) -> None:
        self._items = list(items)

    async def __call__(self) -> None:
        return None

    def __await__(self) -> Generator[None, None, None]:
        async def _done() -> None:
            return None

        return _done().__await__()
