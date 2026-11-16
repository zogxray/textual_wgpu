"""Command palette Provider / Hit contracts — Textual ``textual.command`` parity."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Hit:
    """Command palette match."""

    score: float
    match_display: Any
    command: Callable[[], Any]
    text: str = ""
    help: str | None = None


@dataclass
class DiscoveryHit:
    """Command palette discovery item (empty query)."""

    display: Any
    command: Callable[[], Any]
    text: str = ""
    help: str | None = None


Hits = AsyncIterator[Hit | DiscoveryHit]


class Provider:
    """Base class for command palette providers."""

    def __init__(self, screen: Any, match_style: Any = None) -> None:
        self.screen = screen
        self.match_style = match_style

    @property
    def app(self) -> Any:
        return getattr(self.screen, "app", None)

    async def startup(self) -> None:
        return None

    async def search(self, query: str) -> Hits:
        if False:
            yield  # pragma: no cover

    async def discover(self) -> Hits:
        if False:
            yield  # pragma: no cover

    async def shutdown(self) -> None:
        return None


class SimpleProvider(Provider):
    """Provider over an explicit list of ``(name, callback, help)`` commands."""

    def __init__(
        self,
        screen: Any,
        commands: list[tuple[str, Callable[[], Any], str]] | None = None,
    ) -> None:
        super().__init__(screen)
        self._commands = list(commands or [])

    async def discover(self) -> Hits:
        for name, cb, help_txt in self._commands:
            yield DiscoveryHit(name, cb, text=name, help=help_txt)

    async def search(self, query: str) -> Hits:
        from .fuzzy import Matcher

        matcher = Matcher(query)
        for name, cb, help_txt in self._commands:
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), cb, text=name, help=help_txt)
