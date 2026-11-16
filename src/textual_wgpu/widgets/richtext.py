"""RichText — ``rich.text.Text`` via Content → Strip → compositor."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..content import Content
from ..fonts import cell_height, cell_width, columns_for_width
from ..geometry import Rect
from ..strip import Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw

Hit = tuple[Rect, Callable[[int, int], None]]

_CLICK_KEYS = ("ydna_click", "@click")


class RichText(Widget):
    """Multiline rich/plain body. Paint path: Content → Strip → compositor."""

    ALLOW_SELECT = True
    can_focus = True
    LINE_H = 18
    FONT_SIZE = 13
    RENDER_FONT_SIZE = 13

    def __init__(self, content: Any = "", *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._content = Content(content, kind="auto")
        self.scroll_y = 0
        self.layout_hint_h: int | None = None
        self.fill_bg: bool = True
        self.hit_builder: Callable[[], list[Hit]] | None = None
        self.on_action: Callable[[str], None] | None = None
        self._click_spans: list[tuple[int, int, int, int, str]] = []
        self._strips: list[Strip] = []

    def update(self, content: Any, *, home: bool = True) -> None:
        self._content.update(content, kind="auto")
        if home:
            self.scroll_y = 0
        self._strips = []
        self.refresh()

    def _rebuild(self) -> list[Strip]:
        t = self.theme
        w = self.rect.w if self.rect.w > 0 else 400
        cols = columns_for_width(w, self.FONT_SIZE)
        strips = self._content.render(cols, default_fg=t.rgba("foreground"))
        self._strips = strips
        self._click_spans = []
        for row, strip in enumerate(strips):
            col = 0
            for seg in strip.segments:
                n = len(seg.text)
                meta = seg.meta or {}
                action = None
                for key in _CLICK_KEYS:
                    act = meta.get(key)
                    if isinstance(act, str) and act:
                        action = act
                        break
                if action:
                    self._click_spans.append((row, col, col + n, col + n, action))
                col += n
        return strips

    def _lines(self) -> list[list[tuple[str, tuple[int, int, int, int]]]]:
        """Compat for ModeApp overlays that still call ``_lines()``."""
        strips = self.render_lines()
        out: list[list[tuple[str, tuple[int, int, int, int]]]] = []
        default = self.theme.rgba("foreground")
        for strip in strips:
            runs = [
                (seg.text, seg.style or default)
                for seg in strip.segments
            ]
            out.append(runs or [("", default)])
        return out

    def render_lines(self) -> list[Strip]:
        return self._rebuild()

    def hits(self) -> list[Hit]:
        out = list(super().hits())
        if self.hit_builder is not None:
            try:
                out.extend(self.hit_builder())
            except Exception:  # noqa: BLE001
                pass
        if self.on_action is not None:
            self._rebuild()
            cw = cell_width(self.FONT_SIZE)
            vis = max(1, self.rect.h // self.LINE_H)
            start = self.scroll_y
            for row, col0, col1, _end, action in self._click_spans:
                if row < start or row >= start + vis + 1:
                    continue
                y = self.rect.y + 2 + (row - start) * self.LINE_H
                box = Rect(
                    self.rect.x + 6 + col0 * cw,
                    y,
                    max(8, (col1 - col0) * cw),
                    self.LINE_H,
                )

                def _pick(_x: int, _y: int, act: str = action) -> None:
                    if self.on_action is not None:
                        self.on_action(act)

                out.append((box, _pick))
        return out

    def _max_scroll(self) -> int:
        lines = self.render_lines()
        vis = max(1, self.rect.h // self.LINE_H)
        return max(0, len(lines) - vis)

    def scroll(self, steps: int) -> None:
        if steps == 0:
            return
        self.scroll_y = max(0, min(self._max_scroll(), self.scroll_y + steps))
        self.refresh()

    def on_key(self, key: str) -> bool:
        max_s = self._max_scroll()
        if key == "up":
            self.scroll_y = max(0, self.scroll_y - 1)
            return True
        if key == "down":
            self.scroll_y = min(max_s, self.scroll_y + 1)
            return True
        if key == "pageup":
            self.scroll_y = max(0, self.scroll_y - max(1, self.rect.h // self.LINE_H))
            return True
        if key == "pagedown":
            self.scroll_y = min(max_s, self.scroll_y + max(1, self.rect.h // self.LINE_H))
            return True
        return False

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        from ..compositor import paint_strips
        from ..selection import apply_selection_bg, selection_style_rgba

        if self.rect.w < 2 or self.rect.h < 2:
            return
        t = self.theme
        strips = self.render_lines()
        sel = self.text_selection
        if sel is not None:
            strips = apply_selection_bg(strips, sel, selection_style_rgba(self))
        styles = dict(self.styles or {})
        if self.fill_bg:
            draw.rectangle(
                [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
                fill=t.rgba("panel"),
            )
        paint_strips(
            draw,
            self.rect,
            strips,
            theme=t,
            styles=styles,
            font_size=self.FONT_SIZE,
            pad_x=6,
            pad_y=2,
            scroll_y=self.scroll_y,
            fill_bg=False,
        )

    def get_content_height(self) -> int | None:
        return max(self.LINE_H, len(self.render_lines()) * cell_height(self.FONT_SIZE))
