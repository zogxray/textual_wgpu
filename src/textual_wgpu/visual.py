"""Visual — Textual ``textual.visual`` parity."""

from __future__ import annotations

from typing import Any, Protocol


class SupportsVisual(Protocol):
    def render_strips(self, width: int) -> list[Any]: ...


class Visual:
    def __init__(self, content: Any = "") -> None:
        self.content = content


VisualType = Visual | str | Any


def visualize(obj: Any) -> Visual:
    return obj if isinstance(obj, Visual) else Visual(obj)
