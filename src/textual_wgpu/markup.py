"""Markup helpers — Textual ``textual.markup`` parity."""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text


class Markup:
    def __init__(self, text: str = "") -> None:
        self.text = str(text)

    def to_rich_text(self) -> Text:
        return Text.from_markup(self.text)


__all__ = ["Markup", "escape"]
