"""Scrollbar — first-class vertical/horizontal scroll control."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..geometry import Rect
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


class Scrollbar(Widget):
    """Track + thumb. Parent supplies ``position`` (0..1) and ``window`` (visible frac)."""

    BAR_W = 10
    BAR_PAD = 2

    def __init__(
        self,
        *,
        id: str | None = None,
        vertical: bool = True,
        on_scroll: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(id=id)
        self.vertical = vertical
        self.on_scroll = on_scroll
        self.position = 0.0  # 0 = start, 1 = end
        self.window = 1.0  # visible fraction of content (1 = no overflow)
        self._dragging = False
        self._drag_off = 0
        if vertical:
            self.layout_hint_w = self.BAR_W
        else:
            self.layout_hint_h = self.BAR_W

    def set_metrics(self, *, position: float, window: float) -> None:
        self.position = max(0.0, min(1.0, float(position)))
        self.window = max(0.05, min(1.0, float(window)))
        self.refresh()

    def _geometry(self) -> tuple[Rect, Rect] | None:
        if self.window >= 0.999:
            return None
        pad = self.BAR_PAD
        if self.vertical:
            track = Rect(
                self.rect.x + 1,
                self.rect.y + pad,
                max(1, self.rect.w - 2),
                max(1, self.rect.h - 2 * pad),
            )
            thumb_h = max(18, int(track.h * self.window))
            thumb_h = min(thumb_h, track.h)
            travel = max(0, track.h - thumb_h)
            thumb_y = track.y + int(travel * self.position)
            thumb = Rect(track.x, thumb_y, track.w, thumb_h)
        else:
            track = Rect(
                self.rect.x + pad,
                self.rect.y + 1,
                max(1, self.rect.w - 2 * pad),
                max(1, self.rect.h - 2),
            )
            thumb_w = max(18, int(track.w * self.window))
            thumb_w = min(thumb_w, track.w)
            travel = max(0, track.w - thumb_w)
            thumb_x = track.x + int(travel * self.position)
            thumb = Rect(thumb_x, track.y, thumb_w, track.h)
        return track, thumb

    def _emit(self, frac: float) -> None:
        self.position = max(0.0, min(1.0, frac))
        if self.on_scroll is not None:
            self.on_scroll(self.position)
        self.refresh()

    def _click(self, x: int, y: int) -> None:
        geo = self._geometry()
        if geo is None:
            return
        track, thumb = geo
        if self.vertical:
            if thumb.contains(x, y):
                self._dragging = True
                self._drag_off = y - thumb.y
                return
            travel = max(1, track.h - thumb.h)
            self._emit((y - track.y - thumb.h / 2) / travel)
        else:
            if thumb.contains(x, y):
                self._dragging = True
                self._drag_off = x - thumb.x
                return
            travel = max(1, track.w - thumb.w)
            self._emit((x - track.x - thumb.w / 2) / travel)

    def on_pointer_move(self, x: int, y: int) -> bool:
        if not self._dragging:
            return False
        geo = self._geometry()
        if geo is None:
            self._dragging = False
            return True
        track, thumb = geo
        if self.vertical:
            travel = max(1, track.h - thumb.h)
            self._emit((y - self._drag_off - track.y) / travel)
        else:
            travel = max(1, track.w - thumb.w)
            self._emit((x - self._drag_off - track.x) / travel)
        return True

    def on_pointer_up(self, _x: int, _y: int) -> bool:
        if self._dragging:
            self._dragging = False
            return True
        return False

    def hits(self):
        geo = self._geometry()
        if geo is None:
            return []
        track, _ = geo
        return [(track, self._click)]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("boost"),
        )
        geo = self._geometry()
        if geo is None:
            return
        track, thumb = geo
        draw.rectangle(
            [track.x, track.y, track.x1 - 1, track.y1 - 1],
            fill=t.rgba("panel"),
        )
        draw.rectangle(
            [thumb.x, thumb.y, thumb.x1 - 1, thumb.y1 - 1],
            fill=t.rgba("accent"),
        )
