"""Digits — 3-row block-glyph numeric display (Textual Digits subset)."""

from __future__ import annotations

from ..fonts import cell_height, cell_width
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget

# 3-row x 3-col crisp box/block glyph table
_DIGITS_FONT: dict[str, tuple[str, str, str]] = {
    "0": ("┏━┓", "┃ ┃", "┗━┛"),
    "1": (" ┓ ", " ┃ ", " ┻ "),
    "2": ("━━┓", "┏━┛", "┗━━"),
    "3": ("━━┓", " ━┫", "━━┛"),
    "4": ("╻ ╻", "┗━┫", "  ╹"),
    "5": ("┏━━", "┗━┓", "━━┛"),
    "6": ("┏━━", "┣━┓", "┗━┛"),
    "7": ("━━┓", "  ┃", "  ╹"),
    "8": ("┏━┓", "┣━┫", "┗━┛"),
    "9": ("┏━┓", "┗━┫", "━━┛"),
    ".": ("   ", "   ", " ▪ "),
    ",": ("   ", "   ", " ╻ "),
    "-": ("   ", "━━━", "   "),
    "+": ("   ", "━╋━", "   "),
    ":": ("   ", " ▪ ", " ▪ "),
    "%": ("▫ ╱", " ╱ ", "╱ ▫"),
    "/": ("  ╱", " ╱ ", "╱  "),
    " ": ("   ", "   ", "   "),
}


class Digits(Widget):
    """Display numbers using tall 3-row block glyphs."""

    value = reactive("", repaint=True, layout=True)
    ALLOW_SELECT = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        value: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.value = str(value or "")
        self.layout_hint_rows = 3

    def update(self, value: str) -> None:
        self.value = str(value or "")
        self.refresh(layout=True)

    def render_lines(self) -> list[Strip]:
        t = self.theme
        fg = t.rgba("muted") if self.disabled else t.rgba("foreground")
        rows = ["", "", ""]
        text = self.value or " "
        for idx, ch in enumerate(text):
            glyph = _DIGITS_FONT.get(ch)
            if glyph is None:
                glyph = ("   ", f" {ch} ", "   ")
            sep = " " if idx > 0 else ""
            for r in range(3):
                rows[r] += sep + glyph[r]
        return [Strip([Segment(line, fg)]) for line in rows]

    def get_content_width(self) -> int | None:
        n = max(1, len(self.value or " "))
        cols = n * 3 + max(0, n - 1)
        return cell_width(self.RENDER_FONT_SIZE) * cols

    def get_content_height(self) -> int | None:
        return cell_height(self.RENDER_FONT_SIZE) * 3 + 4
