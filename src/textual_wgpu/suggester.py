"""Suggester — autocomplete suggestions for Input (Textual suggester parity)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable


class Suggester(ABC):
    """Abstract base for ``Input(..., suggester=...)``."""

    def __init__(self, *, use_cache: bool = True, case_sensitive: bool = False) -> None:
        self.use_cache = bool(use_cache)
        self.case_sensitive = bool(case_sensitive)
        self._cache: dict[str, str | None] = {}

    async def get_suggestion(self, value: str) -> str | None:
        return self._lookup(value)

    @abstractmethod
    def _lookup(self, value: str) -> str | None:
        """Synchronously resolve a suggestion for ``value``."""


class SuggestFromList(Suggester):
    """Suggest prefix matches from an iterable of strings."""

    def __init__(
        self,
        suggestions: Iterable[str],
        *,
        case_sensitive: bool = False,
    ) -> None:
        super().__init__(case_sensitive=case_sensitive)
        self._suggestions = [str(s) for s in suggestions]
        self._for_cmp = (
            self._suggestions
            if case_sensitive
            else [s.casefold() for s in self._suggestions]
        )

    def _lookup(self, value: str) -> str | None:
        if not value:
            return None
        needle = value if self.case_sensitive else value.casefold()
        for idx, cand in enumerate(self._for_cmp):
            if cand.startswith(needle):
                return self._suggestions[idx]
        return None
