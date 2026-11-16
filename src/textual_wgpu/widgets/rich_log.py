"""RichLog — rich renderable scrollable log (Textual RichLog subset)."""

from __future__ import annotations

from typing import Any

from ..content import markup_to_strips, rich_text_to_strips
from ..reactive import reactive
from ..strip import Strip
from .log import _LogBase


class RichLog(_LogBase):
    """Scrollable log supporting Rich renderables, markup, and optional wrapping."""

    wrap = reactive(False, repaint=True)
    highlight = reactive(False, repaint=True)
    markup = reactive(False, repaint=True)

    def __init__(
        self,
        *,
        max_lines: int | None = None,
        min_width: int = 78,
        wrap: bool = False,
        highlight: bool = False,
        markup: bool = False,
        auto_scroll: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, max_lines=max_lines, auto_scroll=auto_scroll)
        self.name = name
        self.min_width = max(8, int(min_width))
        self.disabled = bool(disabled)
        if classes:
            self.classes.update(c.strip().lstrip(".") for c in classes.split() if c.strip())
        self._entries: list[tuple[Any, int | None, bool, bool]] = []
        self._last_cols: int = 0
        self.wrap = bool(wrap)
        self.highlight = bool(highlight)
        self.markup = bool(markup)

    def _effective_width(self, width: int | None) -> int:
        if width is not None and width > 0:
            return max(8, int(width))
        if self.wrap and self.rect.w > 0:
            return self._target_columns()
        return max(self.min_width, self._target_columns() if self.rect.w > 0 else self.min_width)

    def _render_entry(
        self,
        content: Any,
        width: int | None,
        use_markup: bool,
        use_highlight: bool,
    ) -> list[Strip]:
        _ = use_highlight
        fg = self._default_fg()
        cols = self._effective_width(width)
        if use_markup and isinstance(content, str):
            return markup_to_strips(content, width=cols, default_fg=fg)
        if not self.wrap and isinstance(content, str):
            return [Strip.from_text(line, fg) for line in (content.splitlines() or [""])]
        return rich_text_to_strips(content, width=cols, default_fg=fg)

    def _rewrap_all(self) -> None:
        self._strips.clear()
        for content, w_ov, mk, hl in self._entries:
            self._strips.extend(self._render_entry(content, w_ov, mk, hl))
        if self.MAX_LINES is not None and len(self._strips) > self.MAX_LINES:
            self._strips = self._strips[-self.MAX_LINES:]

    def watch_wrap(self, _old: bool, _new: bool) -> None:
        if self._entries:
            self._rewrap_all()

    def on_resize(self, _event: Any = None) -> None:
        if self.wrap and self._entries:
            self._rewrap_all()

    def layout(self, rect) -> None:  # noqa: ANN001
        super().layout(rect)
        if self.wrap and self._entries:
            cols = self._target_columns()
            if cols != self._last_cols:
                self._last_cols = cols
                self._rewrap_all()

    def clear(self) -> RichLog:
        self._entries.clear()
        super().clear()
        return self

    def write(
        self,
        content: Any,
        width: int | None = None,
        expand: bool = False,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
        markup: bool | None = None,
        highlight: bool | None = None,
    ) -> RichLog:
        _ = expand, shrink, animate
        use_markup = self.markup if markup is None else bool(markup)
        use_highlight = self.highlight if highlight is None else bool(highlight)
        self._entries.append((content, width, use_markup, use_highlight))
        if self.MAX_LINES is not None and len(self._entries) > self.MAX_LINES:
            self._entries = self._entries[-self.MAX_LINES:]
        self._last_cols = self._target_columns()
        lines = self._render_entry(content, width, use_markup, use_highlight)
        self._append_strips(lines, scroll_end=scroll_end)
        return self
