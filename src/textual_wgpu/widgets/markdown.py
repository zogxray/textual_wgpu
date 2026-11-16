"""Markdown widget — Content (Rich MD) → Strip → compositor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..content import Content, markdown_to_strips
from ..fonts import cell_height, cell_width, columns_for_width
from ..geometry import Rect
from ..message import Message
from ..strip import Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


# Back-compat alias used by tests / callers.
def bake_markdown(md: str, columns: int, *, default_fill: tuple[int, int, int, int]):
    """Return Strip rows (also exposes ``.runs``-like via Segment for tests)."""
    strips = markdown_to_strips(md, columns, default_fg=default_fill)

    # Lightweight shim so existing tests can still do ``r.text`` / ``r.bold``.
    class _Run:
        __slots__ = ("text", "fill", "bold", "italic", "underline", "dim")

        def __init__(self, seg) -> None:  # noqa: ANN001
            self.text = seg.text
            self.fill = seg.style or default_fill
            self.bold = seg.bold
            self.italic = seg.italic
            self.underline = seg.underline
            self.dim = False

    class _Line:
        __slots__ = ("runs",)

        def __init__(self, strip: Strip) -> None:
            self.runs = [_Run(s) for s in strip.segments]

    return [_Line(s) for s in strips]


class Markdown(Widget):
    """Markdown body via Content → Strip. Wrap in ``VerticalScroll``."""

    class LinkClicked(Message):
        def __init__(self, markdown: Markdown, href: str) -> None:
            super().__init__()
            self.markdown = markdown
            self.control = markdown
            self.href = href

    class TableOfContentsUpdated(Message):
        def __init__(self, markdown: Markdown, table_of_contents: list) -> None:
            super().__init__()
            self.markdown = markdown
            self.table_of_contents = table_of_contents

    class TableOfContentsSelected(Message):
        def __init__(self, markdown: Markdown, block_id: str) -> None:
            super().__init__()
            self.markdown = markdown
            self.block_id = block_id

    ALLOW_SELECT = True
    LINE_H = 18
    FONT_SIZE = 14
    RENDER_FONT_SIZE = 14

    def __init__(self, markup: str = "", *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.markup = markup
        self._content = Content.from_markdown(markup)
        self._strips: list[Strip] = []
        self._baked_columns = 0

    def get_block_class(self, block_name: str):
        _ = block_name
        return None

    def get_stream(self):
        return None

    def on_markdown_link_clicked(self, event: LinkClicked) -> None:
        if self.app is not None and hasattr(self.app, "open_url"):
            self.app.open_url(event.href)

    def sanitize_location(self, location: str) -> tuple[str, str]:
        if "#" in location:
            path, _, anchor = location.partition("#")
            return path, anchor
        return location, ""

    def unhandled_token(self, token) -> None:
        _ = token

    def load(self, path: str | Path) -> None:
        p = Path(path)
        self.update(p.read_text(encoding="utf-8"))

    def append(self, markdown: str) -> None:
        self.update((self.markup or "") + markdown)

    def goto_anchor(self, anchor: str) -> bool:
        _ = anchor
        return False

    def update(self, markup: str) -> None:
        self.markup = markup
        self._content.update(markup, kind="markdown")
        self.invalidate()
        self.refresh()

    def ensure_baked(self, pixel_width: int) -> None:
        cols = columns_for_width(max(40, int(pixel_width)), self.FONT_SIZE)
        if self._strips and cols == self._baked_columns:
            return
        self._baked_columns = cols
        fg = self.theme.rgba("foreground") if self.app else (240, 230, 216, 255)
        self._strips = self._content.render(cols, default_fg=fg)

    def invalidate(self) -> None:
        self._strips = []
        self._baked_columns = 0
        self._content.invalidate()

    @property
    def line_count(self) -> int:
        return len(self._strips)

    def render_lines(self) -> list[Strip]:
        w = self.rect.w if self.rect.w > 0 else max(40, self._baked_columns * cell_width(self.FONT_SIZE))
        self.ensure_baked(w)
        return list(self._strips)

    def _strips_for_paint(self) -> list[Strip]:
        from ..selection import apply_selection_bg, selection_style_rgba

        strips = list(self._strips)
        sel = self.text_selection
        if sel is not None:
            strips = apply_selection_bg(strips, sel, selection_style_rgba(self))
        return strips

    def paint_lines(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        scroll: int,
        scroll_px: int = 0,
        line_h: int = 18,
    ) -> None:
        """VerticalScroll path — blit strips into a sub-rect with scroll offset."""
        from ..compositor import paint_strips

        self.ensure_baked(max(40, width))
        if not self._strips or self.app is None:
            return
        # Temporary styles: fill panel bg under markdown body.
        styles = dict(self.styles or {})
        if "background" not in styles:
            styles["background"] = self.theme.primary and None
        # Use theme panel as bg via explicit fill.
        draw.rectangle([x, y, x + width - 1, y + height - 1], fill=self.theme.rgba("panel"))
        paint_strips(
            draw,
            Rect(x, y, width, height),
            self._strips_for_paint(),
            theme=self.theme,
            styles={"color": None},
            font_size=self.FONT_SIZE,
            pad_x=0,
            pad_y=0,
            scroll_y=scroll,
            scroll_px=scroll_px,
            fill_bg=False,
        )

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        # Prefer compositor via render_lines (Widget.paint).
        self.ensure_baked(max(40, self.rect.w))
        from ..compositor import paint_widget_strips

        # Panel-ish background.
        if self.rect.w > 1 and self.rect.h > 1:
            draw.rectangle(
                [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
                fill=self.theme.rgba("panel"),
            )
        paint_widget_strips(
            draw, self, self._strips_for_paint(),
            font_size=self.FONT_SIZE, fill_bg=False, pad_x=4, pad_y=2,
        )

    def get_content_height(self) -> int | None:
        if not self._strips:
            self.ensure_baked(max(40, self.rect.w or 400))
        return max(self.LINE_H, len(self._strips) * cell_height(self.FONT_SIZE))
