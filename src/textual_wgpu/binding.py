"""Binding — Textual ``textual.binding.Binding`` subset."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Binding:
    """Key → action binding (Textual-compatible constructor)."""

    key: str
    action: str
    description: str = ""
    show: bool = True
    key_display: str | None = None
    priority: bool = False
    tooltip: str = ""
    id: str | None = None
    system: bool = False
    group: Any = None


BindingType = Binding | tuple[str, str] | tuple[str, str, str]


def iter_bindings(bindings: Iterable[Any], *, priority_only: bool | None = None) -> Iterator[tuple[str, str]]:
    """Yield ``(key, action)`` pairs from ``Binding`` objects or ``(key, action)`` tuples."""
    for item in bindings or ():
        if isinstance(item, Binding):
            if priority_only is True and not item.priority:
                continue
            if priority_only is False and item.priority:
                continue
            action = item.action
            for part in str(item.key).split(","):
                key = part.strip()
                if key:
                    yield key, action
            continue
        if priority_only is True:
            continue
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            key = str(item[0]).strip()
            action = str(item[1]).strip()
            if key and action:
                yield key, action
