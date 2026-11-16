"""Pretty — pretty-print any Python object via Rich → Strip."""

from __future__ import annotations

from typing import Any

from ..content import rich_text_to_strips
from ..fonts import cell_width, columns_for_width, rows_to_px
from ..strip import Strip
from ..widget import Widget


class Pretty(Widget):
    """Display a pretty-printed Python object (Textual ``Pretty`` subset)."""

    ALLOW_SELECT = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        object: Any,  # noqa: A002 — Textual signature
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.name = name
        self._renderable: Any = object

    def update(self, object: Any) -> None:  # noqa: A002
        self._renderable = object
        self.refresh(layout=True)

    def render_lines(self) -> list[Strip]:
        from rich.pretty import Pretty as RichPretty

        t = self.theme if self.app else None
        fg = t.rgba("foreground") if t else (240, 230, 216, 255)
        w = self.rect.w if self.rect.w > 0 else 400
        cols = columns_for_width(w, self.RENDER_FONT_SIZE)
        return rich_text_to_strips(
            RichPretty(self._renderable),
            width=cols,
            default_fg=fg,
        )

    def get_content_width(self) -> int | None:
        lines = self.render_lines()
        n = max((len(s) for s in lines), default=1)
        return cell_width(self.RENDER_FONT_SIZE) * max(1, n)

    def get_content_height(self) -> int | None:
        lines = self.render_lines()
        return rows_to_px(max(1, len(lines)), self.RENDER_FONT_SIZE)
