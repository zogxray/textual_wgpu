"""ModeBar / KeyBar / StatusBar — Content → Strip → compositor."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


def _chrome_band_h(font_size: int) -> int:
    """One CSS ``height: 1`` row for chrome = glyph cell + vertical pad."""
    return cell_height(font_size) + 8


class ModeBar(Widget):
    """F1–Fn mode strip. Active tab uses accent cells (Tabs-style)."""

    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        active: str,
        labels: Sequence[tuple[str, str]],
        *,
        on_select: Callable[[int], None] | None = None,
        item_formatter: Callable[[int, str, str], tuple[str, tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.active = active
        self.labels = list(labels)
        self.on_select = on_select
        self._item_formatter = item_formatter
        band = _chrome_band_h(self.RENDER_FONT_SIZE)
        self.layout_hint_h = band
        self.layout_band_h = band  # CSS height:1 → band, not bare cell_height
        self.layout_hint_rows = 1

    def _format_item(
        self,
        index: int,
        key: str,
        title: str,
    ) -> tuple[str, tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
        if self._item_formatter is not None:
            return self._item_formatter(index, key, title)
        return f" F{index + 1} {title} ", None, None

    def _tab_spans(self) -> list[tuple[int, int, int]]:
        """(index, start_col, width_cols) for hit testing."""
        spans: list[tuple[int, int, int]] = []
        col = 0
        for i, (key, title) in enumerate(self.labels):
            label, _fg, _bg = self._format_item(i, key, title)
            w = len(label)
            spans.append((i, col, w))
            col += w + 1
        return spans

    def render_lines(self) -> list[Strip]:
        t = self.theme
        boost = t.rgba("boost")
        muted = t.rgba("muted")
        if not self.labels:
            return [Strip.from_text("(no modes)", muted, boost)]
        segs: list[Segment] = []
        for i, (key, title) in enumerate(self.labels):
            if i:
                # Separator must stay boost — never accent (avoids ghost “empty tab”).
                segs.append(Segment(" ", muted, boost))
            label, fg_ov, bg_ov = self._format_item(i, key, title)
            if key == self.active:
                fg = t.rgba("background")
                bg = bg_ov if bg_ov is not None else (fg_ov if fg_ov is not None else t.rgba("accent"))
                segs.append(Segment(label, fg, bg, bold=True))
            elif fg_ov is not None or bg_ov is not None:
                segs.append(Segment(label, fg_ov or muted, bg_ov or boost, bold=True))
            else:
                segs.append(Segment(label, muted, boost))
        # Pad so crop/overflow never leaves a bare accent cell looking like a tab.
        segs.append(Segment(" " * 8, muted, boost))
        return [Strip(segs)]

    def _chrome_pad_y(self) -> int:
        """Center the Strip row inside the bar height."""
        ch = cell_height(self.RENDER_FONT_SIZE)
        return max(0, (self.rect.h - ch) // 2)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_widget_strips

        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("boost"),
        )
        paint_widget_strips(
            draw, self, font_size=self.RENDER_FONT_SIZE,
            fill_bg=False, pad_x=8, pad_y=self._chrome_pad_y(),
        )

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.on_select is None or self.rect.w < 2 or not self.labels:
            return []
        cw = cell_width(self.RENDER_FONT_SIZE)
        pad_x = 8
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        for i, start, width in self._tab_spans():
            r = Rect(
                self.rect.x + pad_x + start * cw,
                self.rect.y,
                width * cw,
                self.rect.h,
            )

            def _pick(_x: int, _y: int, idx: int = i) -> None:
                if self.on_select is not None:
                    self.on_select(idx)

            out.append((r, _pick))
        return out


class KeyBar(Widget):
    """Footer hints. items: (key, label, action|None). Actionable chips are clickable."""

    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        items: Sequence[tuple[str, str, Callable[[], None] | None]],
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.items = list(items)
        band = _chrome_band_h(self.RENDER_FONT_SIZE)
        self.layout_hint_h = band
        self.layout_band_h = band
        self.layout_hint_rows = 1

    def set_items(
        self,
        items: Sequence[tuple[str, str, Callable[[], None] | None]],
    ) -> None:
        self.items = list(items)
        self.refresh()

    def _item_spans(self) -> list[tuple[int, int, int, Callable[[], None] | None]]:
        """(index, start_col, width_cols, action)."""
        spans: list[tuple[int, int, int, Callable[[], None] | None]] = []
        col = 0
        for i, (key, label, action) in enumerate(self.items):
            text = f" {key} {label} "
            w = len(text)
            spans.append((i, col, w, action))
            col += w + 1
        return spans

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self.items:
            return [Strip([Segment(" ", t.rgba("muted"), t.rgba("boost"))])]
        segs: list[Segment] = []
        for i, (key, label, action) in enumerate(self.items):
            if i:
                segs.append(Segment(" ", t.rgba("muted"), t.rgba("boost")))
            text = f" {key} {label} "
            if action:
                segs.append(Segment(text, t.rgba("accent"), t.rgba("boost")))
            else:
                segs.append(Segment(text, t.rgba("muted"), t.rgba("boost")))
        return [Strip(segs)]

    def _chrome_pad_y(self) -> int:
        ch = cell_height(self.RENDER_FONT_SIZE)
        return max(0, (self.rect.h - ch) // 2)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_widget_strips

        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("boost"),
        )
        pad_y = self._chrome_pad_y()
        paint_widget_strips(
            draw, self, font_size=self.RENDER_FONT_SIZE,
            fill_bg=False, pad_x=8, pad_y=pad_y,
        )
        # Outline actionable chips.
        cw = cell_width(self.RENDER_FONT_SIZE)
        ch = cell_height(self.RENDER_FONT_SIZE)
        pad_x = 8
        for _i, start, width, action in self._item_spans():
            if action is None:
                continue
            box = Rect(
                self.rect.x + pad_x + start * cw,
                self.rect.y + pad_y,
                width * cw,
                ch,
            )
            draw.rectangle(
                [box.x, box.y, box.x1 - 1, box.y1 - 1],
                outline=t.rgba("accent"),
            )

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.rect.w < 2:
            return []
        cw = cell_width(self.RENDER_FONT_SIZE)
        pad_x = 8
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        for _i, start, width, action in self._item_spans():
            if action is None:
                continue
            r = Rect(
                self.rect.x + pad_x + start * cw,
                self.rect.y,
                width * cw,
                self.rect.h,
            )
            out.append((r, lambda _x, _y, a=action: a()))
        return out


class StatusBar(Widget):
    """Status strip — Content → Strip → compositor (accent on boost)."""

    RENDER_FONT_SIZE = 13

    def __init__(self, text: str = "", *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._text = text
        band = _chrome_band_h(self.RENDER_FONT_SIZE)
        self.layout_hint_h = band
        self.layout_band_h = band
        self.layout_hint_rows = 1

    def update(self, text: str) -> None:
        self._text = text
        self.refresh()

    def render_lines(self) -> list[Strip]:
        t = self.theme
        return [Strip([Segment(f" {self._text}", t.rgba("accent"), t.rgba("boost"))])]

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
            fill_bg=False, pad_x=8, pad_y=pad_y,
        )
