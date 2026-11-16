"""AwaitMount / AwaitComplete — optionally-awaitable DOM lifecycle handles."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Generator, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .widget import Widget


class AwaitComplete:
    """Optionally-awaitable wrapper around one or more awaitables (gather)."""

    def __init__(self, *awaitables: Awaitable[Any]) -> None:
        self._awaitables = awaitables

    async def __call__(self) -> Any:
        return await self

    def __await__(self) -> Generator[Any, None, Any]:
        async def _run() -> Any:
            if not self._awaitables:
                return None
            return await asyncio.gather(*self._awaitables)

        return _run().__await__()

    @classmethod
    def nothing(cls) -> AwaitComplete:
        return cls()


class AwaitMount:
    """Optionally awaitable returned by ``mount_child`` / ``mount_widgets``.

    Example::

        await container.mount_widgets(Static("ready"))
        # or
        handle = container.mount_child(Static("x"))
        await handle
    """

    def __init__(self, parent: Widget, widgets: Sequence[Widget]) -> None:
        self._parent = parent
        self._widgets = list(widgets)

    async def __call__(self) -> None:
        await self

    def __await__(self) -> Generator[None, None, None]:
        async def await_mount() -> None:
            if not self._widgets:
                return
            await asyncio.gather(*(w._wait_mounted() for w in self._widgets))
            self._parent.refresh(layout=True)

        return await_mount().__await__()

    @property
    def widgets(self) -> list[Widget]:
        return list(self._widgets)
