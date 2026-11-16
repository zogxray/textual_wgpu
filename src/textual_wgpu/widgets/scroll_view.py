"""ScrollView — scrollable viewport with Content→Strip contract.

Preferred subclass API::

    class MyView(ScrollView):
        def get_content_height(self) -> int: ...
        def render_lines(self) -> list[Strip]: ...
        def paint_row_backgrounds(self, draw, region): ...  # optional bands

Legacy ``paint_content`` remains as a fallback when ``render_lines`` is empty.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..fonts import cell_height
from ..geometry import Rect
from ..strip import Strip
from ..widget import Widget
from .scroll import VerticalScroll
from .scrollbar import Scrollbar

if TYPE_CHECKING:
    from PIL import ImageDraw

Hit = tuple[Rect, Callable[[int, int], None]]


class ScrollView(Widget):
    """Scrollable region: ``render_lines`` → cell compositor + scrollbar gutter."""

    can_focus = True
    FONT_SIZE = 13
    LINE_H = cell_height(FONT_SIZE)
    RENDER_FONT_SIZE = FONT_SIZE
    BAR_W = Scrollbar.BAR_W

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.scroll_y = 0  # line offset into render_lines()
        self.scroll_x = 0  # character / pixel offset (subclass-defined)
        self._scrollbar = Scrollbar(vertical=True, on_scroll=self._on_bar_scroll)

    def get_content_height(self) -> int:
        """Total content height in lines. Default: ``len(render_lines())``."""
        return len(self.render_lines())

    def render_lines(self) -> list[Strip]:
        """All content strips (preferred paint path). Override in subclasses."""
        return []

    def paint_content(self, draw: ImageDraw.ImageDraw, region: Rect) -> None:
        """Legacy paint hook when ``render_lines`` is empty. Prefer strips."""

    def paint_row_backgrounds(
        self, draw: ImageDraw.ImageDraw, region: Rect,
    ) -> None:
        """Optional full-width row bands (cursor / zebra) before strips."""

    def _visible_lines(self) -> int:
        # Match compositor: pad_y=2 top+bottom ≈ one LINE_H slack.
        return max(1, (self.content_region().h - 4) // self.LINE_H)

    def _max_scroll(self) -> int:
        return max(0, self.get_content_height() - self._visible_lines())

    def _layout_scrollbar(self) -> None:
        gutter = Rect(
            self.rect.x1 - self.BAR_W,
            self.rect.y,
            self.BAR_W,
            self.rect.h,
        )
        self._scrollbar.app = self.app
        self._scrollbar.layout(gutter)
        max_s = self._max_scroll()
        total = max(1, self.get_content_height())
        visible = self._visible_lines()
        if max_s <= 0:
            self._scrollbar.set_metrics(position=0.0, window=1.0)
        else:
            self._scrollbar.set_metrics(
                position=self.scroll_y / max_s,
                window=min(1.0, visible / total),
            )

    def _on_bar_scroll(self, frac: float) -> None:
        self.scroll_to_fraction(frac)

    def scroll(self, delta: int) -> None:
        self.scroll_y = max(0, min(self._max_scroll(), self.scroll_y + delta))
        self._layout_scrollbar()
        self.refresh()

    def scroll_to(
        self,
        x: int | None = None,
        y: int | None = None,
        *,
        animate: bool = True,
    ) -> None:
        _ = animate
        if x is not None:
            self.scroll_x = max(0, int(x))
        if y is not None:
            self.scroll_y = max(0, min(self._max_scroll(), int(y)))
        self._layout_scrollbar()
        self.refresh()

    @property
    def virtual_size(self):
        from ..geometry import Size

        return Size(self.rect.w, self.get_content_height() * self.LINE_H)

    @virtual_size.setter
    def virtual_size(self, value) -> None:  # noqa: ANN001
        self._virtual_size = value

    def scroll_home(self, *, animate: bool = True) -> None:
        _ = animate
        self.scroll_y = 0
        self._layout_scrollbar()
        self.refresh()

    def scroll_end(self, *, animate: bool = True) -> None:
        _ = animate
        self.scroll_y = self._max_scroll()
        self._layout_scrollbar()
        self.refresh()

    def scroll_to_fraction(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        self.scroll_y = int(round(self._max_scroll() * frac))
        self._layout_scrollbar()
        self.refresh()

    def ensure_visible_line(self, line: int) -> None:
        vis = self._visible_lines()
        if line < self.scroll_y:
            self.scroll_y = max(0, line)
        elif line >= self.scroll_y + vis:
            self.scroll_y = max(0, line - vis + 1)
        self._layout_scrollbar()

    def line_y(self, line: int, region: Rect | None = None) -> int | None:
        """Pixel y of absolute ``line`` if visible, else None."""
        region = region or self.content_region()
        if line < self.scroll_y:
            return None
        row = line - self.scroll_y
        if row >= self._visible_lines() + 1:
            return None
        return region.y + 2 + row * self.LINE_H

    def on_key(self, key: str) -> bool:
        if key == "up":
            self.scroll(-1)
            return True
        if key == "down":
            self.scroll(1)
            return True
        if key == "pageup":
            self.scroll(-self._visible_lines())
            return True
        if key == "pagedown":
            self.scroll(self._visible_lines())
            return True
        if key == "home":
            self.scroll_home()
            return True
        if key == "end":
            self.scroll_end()
            return True
        return False

    def _bar_geometry(self) -> tuple[Rect, Rect] | None:
        """Track/thumb rects (compat for tests / callers)."""
        self._layout_scrollbar()
        return self._scrollbar._geometry()

    def on_pointer_move(self, x: int, y: int) -> bool:
        return self._scrollbar.on_pointer_move(x, y)

    def on_pointer_up(self, _x: int, _y: int) -> bool:
        return self._scrollbar.on_pointer_up(_x, _y)

    def content_region(self) -> Rect:
        return Rect(
            self.rect.x,
            self.rect.y,
            max(0, self.rect.w - self.BAR_W - 2),
            self.rect.h,
        )

    def hits(self) -> list[Hit]:
        self._layout_scrollbar()
        return list(self._scrollbar.hits())

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_strips

        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("panel"),
        )
        region = self.content_region()
        strips = list(self.render_lines())
        if strips:
            self.paint_row_backgrounds(draw, region)
            paint_strips(
                draw,
                region,
                strips,
                theme=t,
                font_size=self.FONT_SIZE,
                pad_x=4,
                pad_y=2,
                scroll_y=self.scroll_y,
                fill_bg=False,
            )
        else:
            self.paint_content(draw, region)
        self._layout_scrollbar()
        self._scrollbar.paint(draw)


# Re-export for callers that expect scroll bits together.
__all__ = ["ScrollView", "VerticalScroll"]
