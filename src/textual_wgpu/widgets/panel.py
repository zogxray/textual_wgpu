"""Bordered panel with optional title — title/subtitle via Content→Strip."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..fonts import cell_height
from ..geometry import Rect
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


def _hex_rgba(value: str | None, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not value:
        return fallback
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return fallback
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


class Panel(Widget):
    TITLE_SIZE = 12
    SUBTITLE_SIZE = 11

    def __init__(
        self,
        child: Widget,
        *,
        title: str = "",
        subtitle: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.child = child
        self.title = title
        self.subtitle = subtitle

    @property
    def children(self) -> list[Widget]:
        return [self.child]

    def _title_h(self) -> int:
        return cell_height(self.TITLE_SIZE) + 8 if self.title else 4

    def _subtitle_h(self) -> int:
        return cell_height(self.SUBTITLE_SIZE) + 8 if self.subtitle else 4

    def layout_children(self) -> None:
        top = self._title_h()
        bottom = self._subtitle_h()
        inner = self.rect.inset(8, top, 8, bottom)
        self.child.layout(inner)

    def _title_strip(self, fill) -> list[Strip]:
        return [Strip([Segment(f" {self.title}", fill)])]

    def _subtitle_strip(self, fill) -> list[Strip]:
        return [Strip([Segment(f" {self.subtitle}", fill)])]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_strips

        t = self.theme
        styles = self.styles or {}
        show_border = styles.get("border", True)
        fill = _hex_rgba(styles.get("background"), t.rgba("panel"))
        outline = _hex_rgba(styles.get("border_color"), t.rgba("primary"))
        title_fill = _hex_rgba(styles.get("color"), t.rgba("muted"))

        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=fill,
            outline=outline if show_border else fill,
        )
        if self.title:
            th = self._title_h()
            ch = cell_height(self.TITLE_SIZE)
            pad_y = max(1, (th - ch) // 2)
            title_rect = Rect(self.rect.x + 6, self.rect.y + 1, self.rect.w - 12, th - 1)
            paint_strips(
                draw, title_rect, self._title_strip(title_fill),
                theme=t, font_size=self.TITLE_SIZE,
                pad_x=4, pad_y=pad_y, fill_bg=False,
            )
        if self.subtitle:
            sh = self._subtitle_h()
            ch = cell_height(self.SUBTITLE_SIZE)
            pad_y = max(1, (sh - ch) // 2)
            sub_rect = Rect(
                self.rect.x + 6,
                self.rect.y1 - sh,
                self.rect.w - 12,
                sh,
            )
            paint_strips(
                draw, sub_rect, self._subtitle_strip(t.rgba("muted")),
                theme=t, font_size=self.SUBTITLE_SIZE,
                pad_x=4, pad_y=pad_y, fill_bg=False,
            )
        self.child.paint(draw)
