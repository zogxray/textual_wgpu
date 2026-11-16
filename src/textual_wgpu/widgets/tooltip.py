"""Tooltip — floating hint near a point / widget."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


class Tooltip(Widget):
    """Lightweight overlay tip. Call ``show_at`` / ``hide`` from the app."""

    RENDER_FONT_SIZE = 12

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._text = ""
        self._anchor = (0, 0)
        self.styles["display"] = "none"
        self.layout_layer = 40  # tooltip layer

    @property
    def visible(self) -> bool:
        return bool(self._text) and self.styles.get("display") != "none"

    def update(self, content: Any = "") -> None:
        self._text = str(content or "")
        self.refresh()

    def show_at(self, text: str, x: int, y: int) -> None:
        self._text = str(text or "").strip()
        self._anchor = (int(x), int(y))
        if not self._text:
            self.hide()
            return
        self.styles["display"] = "block"
        # Size from text.
        cw = cell_width(self.RENDER_FONT_SIZE)
        ch = cell_height(self.RENDER_FONT_SIZE)
        w = min(420, max(40, len(self._text) * cw + 16))
        h = ch + 8
        ax, ay = self._anchor
        self.layout(Rect(ax + 12, ay + 14, w, h))
        self.refresh()

    def show_for(self, widget: Widget, text: str) -> None:
        r = widget.rect
        self.show_at(text, r.x + r.w // 2, r.y1)

    def hide(self) -> None:
        self._text = ""
        self.styles["display"] = "none"
        self.refresh()

    def render_lines(self) -> list[Strip]:
        if not self.visible:
            return []
        t = self.theme
        return [Strip([Segment(f" {self._text} ", t.rgba("foreground"), t.rgba("surface"))])]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if not self.visible or self.rect.w < 2:
            return
        from ..compositor import paint_widget_strips

        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("surface"),
            outline=t.rgba("accent"),
        )
        paint_widget_strips(
            draw, self, font_size=self.RENDER_FONT_SIZE,
            fill_bg=False, pad_x=4, pad_y=2,
        )
