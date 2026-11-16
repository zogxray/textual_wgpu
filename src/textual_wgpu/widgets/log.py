"""Log / _LogBase — append-only Strip view via Content → Strip → compositor."""

from __future__ import annotations

from typing import Any

from ..content import markup_to_strips, rich_text_to_strips
from ..fonts import cell_height, columns_for_width
from ..strip import Strip
from ..widget import Widget


class _LogBase(Widget):
    """Shared scrollable Strip log backing ``Log`` and ``RichLog``."""

    ALLOW_SELECT = True
    can_focus = True
    RENDER_FONT_SIZE = 13
    LINE_H = 18
    MAX_LINES = 5000

    def __init__(
        self,
        *,
        id: str | None = None,
        max_lines: int | None = 5000,
        auto_scroll: bool = True,
    ) -> None:
        super().__init__(id=id)
        self.MAX_LINES = max(100, int(max_lines)) if max_lines is not None else None
        self._strips: list[Strip] = []
        self.scroll_y = 0
        self.auto_scroll = bool(auto_scroll)

    def clear(self) -> _LogBase:
        self._strips.clear()
        self.scroll_y = 0
        self.refresh()
        return self

    def refresh_line(self, y: int) -> None:
        _ = y
        self.refresh()

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        _ = y_start, line_count
        self.refresh()

    def _default_fg(self) -> tuple[int, int, int, int]:
        t = self.theme if self.app else None
        return t.rgba("foreground") if t else (240, 230, 216, 255)

    def _target_columns(self, width: int | None = None) -> int:
        if width is not None and width > 0:
            return max(8, int(width))
        w = self.rect.w if self.rect.w > 0 else 400
        return columns_for_width(w, self.RENDER_FONT_SIZE)

    def _append_strips(
        self,
        lines: list[Strip],
        *,
        scroll_end: bool | None = None,
    ) -> None:
        self._strips.extend(lines)
        if self.MAX_LINES is not None and len(self._strips) > self.MAX_LINES:
            self._strips = self._strips[-self.MAX_LINES:]
        do_end = self.auto_scroll if scroll_end is None else bool(scroll_end)
        if do_end:
            self.scroll_end()
        else:
            self.refresh()

    def _visible_lines(self) -> int:
        return max(1, self.rect.h // self.LINE_H) if self.rect.h else 20

    def scroll(self, delta: int) -> None:
        max_s = max(0, len(self._strips) - self._visible_lines())
        self.scroll_y = max(0, min(max_s, self.scroll_y + delta))
        self.refresh()

    def scroll_home(self) -> None:
        self.scroll_y = 0
        self.refresh()

    def scroll_end(self) -> None:
        vis = self._visible_lines()
        self.scroll_y = max(0, len(self._strips) - vis)
        self.refresh()

    def render_lines(self) -> list[Strip]:
        return list(self._strips)

    def paint(self, draw) -> None:  # noqa: ANN001
        from ..compositor import paint_strips
        from ..selection import apply_selection_bg, selection_style_rgba

        if self.rect.w < 2 or self.rect.h < 2:
            return
        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("panel"),
        )
        strips = self.render_lines()
        sel = self.text_selection
        if sel is not None:
            strips = apply_selection_bg(strips, sel, selection_style_rgba(self))
        paint_strips(
            draw,
            self.rect,
            strips,
            theme=t,
            styles=self.styles or {},
            font_size=self.RENDER_FONT_SIZE,
            pad_x=4,
            pad_y=2,
            scroll_y=self.scroll_y,
            fill_bg=False,
        )

    def on_key(self, key: str) -> bool:
        vis = self._visible_lines()
        max_s = max(0, len(self._strips) - vis)
        if key == "up":
            self.scroll_y = max(0, self.scroll_y - 1)
            self.refresh()
            return True
        if key == "down":
            self.scroll_y = min(max_s, self.scroll_y + 1)
            self.refresh()
            return True
        if key == "pageup":
            self.scroll_y = max(0, self.scroll_y - vis)
            self.refresh()
            return True
        if key == "pagedown":
            self.scroll_y = min(max_s, self.scroll_y + vis)
            self.refresh()
            return True
        if key == "home":
            self.scroll_home()
            return True
        if key == "end":
            self.scroll_end()
            return True
        return False

    def get_content_height(self) -> int | None:
        return max(self.LINE_H, len(self._strips) * cell_height(self.RENDER_FONT_SIZE))


class Log(_LogBase):
    """Scrollable log of Strip lines (plain / markup / rich)."""

    def write(
        self,
        text: Any = "",
        *,
        markup: bool = False,
        scroll_end: bool | None = None,
    ) -> Log:
        """Append one or more lines."""
        fg = self._default_fg()
        cols = self._target_columns()
        if markup and isinstance(text, str):
            lines = markup_to_strips(text, width=cols, default_fg=fg)
        else:
            lines = rich_text_to_strips(text, width=cols, default_fg=fg)
        self._append_strips(lines, scroll_end=scroll_end)
        return self

    def write_line(self, text: Any = "", *, markup: bool = False) -> Log:
        return self.write(text, markup=markup)

    def write_lines(self, lines: Any, *, scroll_end: bool | None = None) -> Log:
        for ln in lines:
            self.write(ln, scroll_end=False)
        if scroll_end or (scroll_end is None and self.auto_scroll):
            self.scroll_end()
        return self
