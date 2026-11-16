"""Rule — horizontal or vertical divider strip."""

from __future__ import annotations

from ..fonts import cell_height, cell_width
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget

_H_GLYPHS: dict[str, str] = {
    "ascii": "-",
    "blank": " ",
    "dashed": "╌",
    "double": "═",
    "heavy": "━",
    "hidden": " ",
    "none": " ",
    "solid": "─",
    "thick": "█",
}

_V_GLYPHS: dict[str, str] = {
    "ascii": "|",
    "blank": " ",
    "dashed": "┆",
    "double": "║",
    "heavy": "┃",
    "hidden": " ",
    "none": " ",
    "solid": "│",
    "thick": "█",
}


class Rule(Widget):
    """A rule widget to separate content (Textual ``Rule`` subset)."""

    orientation = reactive("horizontal", repaint=True, layout=True)
    line_style = reactive("solid", repaint=True)
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        orientation: str = "horizontal",
        line_style: str = "solid",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.orientation = str(orientation or "horizontal").lower()
        self.line_style = str(line_style or "solid").lower()
        self._sync_hints()

    @classmethod
    def horizontal(cls, line_style: str = "solid", **kwargs) -> Rule:
        return cls(orientation="horizontal", line_style=line_style, **kwargs)

    @classmethod
    def vertical(cls, line_style: str = "solid", **kwargs) -> Rule:
        return cls(orientation="vertical", line_style=line_style, **kwargs)

    def validate_orientation(self, orientation: str) -> str:
        return str(orientation or "horizontal").lower()

    def validate_line_style(self, line_style: str) -> str:
        return str(line_style or "solid").lower()

    def watch_orientation(self, _old: str, _new: str) -> None:
        self._sync_hints()

    def _sync_hints(self) -> None:
        if self.orientation == "vertical":
            self.layout_hint_rows = None
            self.layout_hint_cells = 1
        else:
            self.layout_hint_cells = None
            self.layout_hint_rows = 1

    def render_lines(self) -> list[Strip]:
        t = self.theme
        fg = t.rgba("muted")
        if self.orientation == "vertical":
            ch = _V_GLYPHS.get(self.line_style, "│")
            rows = max(1, self.rect.h // max(1, cell_height(self.RENDER_FONT_SIZE)))
            return [Strip([Segment(ch, fg)]) for _ in range(rows)]
        ch = _H_GLYPHS.get(self.line_style, "─")
        cw = max(1, cell_width(self.RENDER_FONT_SIZE))
        cols = max(1, (self.rect.w - 8) // cw) if self.rect.w > 16 else 24
        return [Strip([Segment(ch * cols, fg)])]

    def get_content_width(self) -> int | None:
        if self.orientation == "vertical":
            return cell_width(self.RENDER_FONT_SIZE)
        return None

    def get_content_height(self) -> int | None:
        if self.orientation == "vertical":
            return None
        return cell_height(self.RENDER_FONT_SIZE) + 4
