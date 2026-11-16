"""Bar renderable — Textual ``textual.renderables.bar`` parity."""

from __future__ import annotations

from rich.text import Text


class Bar:
    def __init__(self, highlight_range=(0, 0), width: int = 20) -> None:
        self.highlight_range = highlight_range
        self.width = max(1, int(width))

    def __rich__(self) -> Text:
        return Text("█" * self.width)
