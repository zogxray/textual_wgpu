"""Static / Label — Content → Strip → compositor."""

from __future__ import annotations

from typing import Any

from ..content import Content
from ..fonts import cell_width, columns_for_width
from ..reactive import reactive
from ..strip import Strip
from ..widget import Widget


class Static(Widget):
    """Multi-line text via Content pipeline (plain / markup / rich)."""

    ALLOW_SELECT = True
    RENDER_FONT_SIZE = 13
    markup = reactive(False, repaint=True)

    def __init__(
        self,
        content: Any = "",
        *,
        id: str | None = None,
        markup: bool = False,
    ) -> None:
        super().__init__(id=id)
        self.markup = markup
        kind = "markup" if markup else "auto"
        self._content = Content(content, kind=kind)
        self.layout_hint_h: int | None = None

    @property
    def text(self) -> str:
        src = self._content.source
        return src if isinstance(src, str) else str(src or "")

    @text.setter
    def text(self, value: str) -> None:
        self.update(value)

    def update(self, content: Any, *, markup: bool | None = None) -> None:
        if markup is not None:
            self.markup = markup
        kind = "markup" if self.markup else "auto"
        self._content.update(content, kind=kind)
        self.refresh()

    def render_lines(self) -> list[Strip]:
        t = self.theme
        w = self.rect.w if self.rect.w > 0 else 400
        cols = columns_for_width(w, self.RENDER_FONT_SIZE)
        # Prefer muted for plain Static chrome labels; markup keeps Rich colors.
        fg = t.rgba("foreground") if self.markup else t.rgba("muted")
        return self._content.render(cols, default_fg=fg)

    def get_content_height(self) -> int | None:
        from ..fonts import rows_to_px

        lines = self.render_lines()
        return rows_to_px(max(1, len(lines)), self.RENDER_FONT_SIZE)

    def get_content_width(self) -> int | None:
        lines = self.render_lines()
        n = max((len(s) for s in lines), default=1)
        return cell_width(self.RENDER_FONT_SIZE) * max(1, n)


class Label(Static):
    """Single-purpose label — Content markup by default (Textual Label lite)."""

    ALLOW_SELECT = False  # chrome labels; not drag-select targets

    def __init__(
        self,
        content: Any = "",
        *,
        id: str | None = None,
        markup: bool = True,
    ) -> None:
        super().__init__(content, id=id, markup=markup)
        self.layout_hint_rows = 1
