"""Header — top application title bar (Textual Header subset)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..fonts import cell_height, columns_for_width
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget
from .chrome import _chrome_band_h

if TYPE_CHECKING:
    from PIL import ImageDraw


class Header(Widget):
    """Docked top title bar displaying app/screen title, optional sub-title & clock."""

    RENDER_FONT_SIZE = 13
    show_clock = reactive(False, repaint=True)
    title = reactive("", repaint=True)
    sub_title = reactive("", repaint=True)

    def __init__(
        self,
        show_clock: bool = False,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        icon: str | None = None,
        time_format: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.name = name
        self.show_clock = bool(show_clock)
        self.icon = icon if icon is not None else "◆"
        self.time_format = time_format or "%H:%M:%S"
        band = _chrome_band_h(self.RENDER_FONT_SIZE)
        self.layout_dock = "top"
        self.layout_hint_h = band
        self.layout_band_h = band
        self.layout_hint_rows = 1

    def format_title(self) -> str:
        title, sub = self._resolved_titles()
        return f"{title} — {sub}" if sub else title

    def watch_tall(self, _old: bool, _new: bool) -> None:
        self.refresh(layout=True)

    def _resolved_titles(self) -> tuple[str, str]:
        screen = getattr(self.app, "screen", None) if self.app else None
        t = self.title or getattr(screen, "TITLE", "") or getattr(self.app, "TITLE", "") or "App"
        st = self.sub_title or getattr(screen, "SUB_TITLE", "") or getattr(self.app, "SUB_TITLE", "") or ""
        return str(t), str(st)

    def render_lines(self) -> list[Strip]:
        t = self.theme
        boost = t.rgba("boost")
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        accent = t.rgba("accent")
        title, sub = self._resolved_titles()
        left_parts: list[Segment] = []
        if self.icon:
            left_parts.append(Segment(f" {self.icon} ", accent, boost, bold=True))
        else:
            left_parts.append(Segment(" ", accent, boost))
        left_parts.append(Segment(title, fg, boost, bold=True))
        if sub:
            left_parts.append(Segment(f" — {sub}", muted, boost))

        if not self.show_clock:
            return [Strip(left_parts)]

        clock = datetime.now().strftime(self.time_format)
        cols = columns_for_width(self.rect.w, self.RENDER_FONT_SIZE) if self.rect.w > 0 else 80
        used = sum(len(s.text) for s in left_parts)
        gap = max(2, cols - used - len(clock) - 2)
        left_parts.append(Segment(" " * gap, muted, boost))
        left_parts.append(Segment(f"{clock} ", accent, boost))
        return [Strip(left_parts)]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        from ..compositor import paint_widget_strips

        if self.rect.w < 2 or self.rect.h < 2:
            return
        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("boost"),
        )
        ch = cell_height(self.RENDER_FONT_SIZE)
        pad_y = max(0, (self.rect.h - ch) // 2)
        paint_widget_strips(
            draw, self, font_size=self.RENDER_FONT_SIZE,
            fill_bg=False, pad_x=6, pad_y=pad_y,
        )
