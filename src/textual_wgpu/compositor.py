"""Cell compositor — Strip is the sole content paint path.

Widgets emit ``list[Strip]`` (via ``render_lines`` / ``render_line``).
This module maps strips onto a monospace cell grid inside a widget rect.
Not a terminal emulator: no ANSI, no screen buffer, no cursor protocol —
just shared cell metrics + Strip blit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .fonts import cell_height, cell_width, mono
from .geometry import Rect
from .strip import Strip

if TYPE_CHECKING:
    from PIL import ImageDraw

    from .theme import Theme

RGBA = tuple[int, int, int, int]


def paint_strips(
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    strips: list[Strip],
    *,
    theme: Theme,
    styles: dict[str, Any] | None = None,
    font_size: int = 13,
    pad_x: int = 4,
    pad_y: int = 2,
    scroll_y: int = 0,
    scroll_px: int = 0,
    fill_bg: bool = True,
) -> None:
    """Blit ``strips`` into ``rect`` on a mono cell grid.

    Supports either line-level ``scroll_y`` or pixel-smooth ``scroll_px``.
    """
    if rect.w < 2 or rect.h < 2:
        return
    styles = styles or {}
    cw = cell_width(font_size)
    ch = cell_height(font_size)
    f = mono(font_size)

    bg = _resolve_bg(styles, theme)
    if fill_bg and bg is not None:
        draw.rectangle(
            [rect.x, rect.y, rect.x1 - 1, rect.y1 - 1],
            fill=bg,
        )

    fg = _resolve_fg(styles, theme)
    max_cols = max(1, (rect.w - 2 * pad_x) // cw)
    max_rows = max(1, (rect.h - 2 * pad_y) // ch)

    if scroll_px > 0:
        start = max(0, int(scroll_px) // ch)
        sub_y = int(scroll_px) % ch
    else:
        start = max(0, int(scroll_y))
        sub_y = 0

    target_img = getattr(draw, "_image", None)
    if sub_y > 0 and target_img is not None:
        from PIL import Image, ImageDraw as _ImageDraw

        layer = Image.new("RGBA", (rect.w, rect.h), (0, 0, 0, 0))
        ldraw = _ImageDraw.Draw(layer)
        end = min(len(strips), start + max_rows + 2)
        y = pad_y - sub_y
        for i in range(start, end):
            strip = strips[i]
            if strip.segments:
                visible = strip.crop(0, max_cols) if len(strip) > max_cols else strip
                visible.paint_cells(
                    ldraw,
                    pad_x,
                    y,
                    cell_w=cw,
                    cell_h=ch,
                    font=f,
                    default_fg=fg,
                    default_bg=None,
                    max_cells=max_cols,
                    font_size=font_size,
                )
            y += ch
            if y >= rect.h:
                break
        target_img.alpha_composite(layer, (rect.x, rect.y))
        return

    end = min(len(strips), start + max_rows)
    y = rect.y + pad_y
    for i in range(start, end):
        strip = strips[i]
        if strip.segments:
            # Crop to visible columns.
            visible = strip.crop(0, max_cols) if len(strip) > max_cols else strip
            visible.paint_cells(
                draw,
                rect.x + pad_x,
                y,
                cell_w=cw,
                cell_h=ch,
                font=f,
                default_fg=fg,
                default_bg=None,
                max_cells=max_cols,
                font_size=font_size,
            )
        y += ch
        if y >= rect.y1:
            break


def paint_widget_strips(
    draw: ImageDraw.ImageDraw,
    widget: Any,
    strips: list[Strip] | None = None,
    **kwargs: Any,
) -> None:
    """Convenience: paint ``widget.render_lines()`` (or given strips) into ``widget.rect``."""
    if strips is None:
        strips = list(widget.render_lines())
    if not strips:
        return
    theme = widget.theme
    styles = getattr(widget, "styles", None) or {}
    paint_strips(
        draw,
        widget.rect,
        strips,
        theme=theme,
        styles=styles,
        **kwargs,
    )


def meta_at_point(
    widget: Any,
    ix: int,
    iy: int,
    *,
    pad_x: int = 4,
    pad_y: int = 2,
    scroll_y: int = 0,
) -> dict[str, Any] | None:
    """Return ``Segment.meta`` under pixel ``(ix, iy)`` for a Strip-painted widget."""
    rect = getattr(widget, "rect", None)
    if rect is None or not rect.contains(ix, iy) or not hasattr(widget, "render_lines"):
        return None
    font_size = int(getattr(widget, "RENDER_FONT_SIZE", getattr(widget, "FONT_SIZE", 13)))
    cw = max(1, cell_width(font_size))
    ch = max(1, int(getattr(widget, "LINE_H", cell_height(font_size))))
    row = int(scroll_y) + (iy - rect.y - pad_y) // ch
    col = (ix - rect.x - pad_x) // cw
    if row < 0 or col < 0:
        return None
    strips = list(widget.render_lines())
    if row >= len(strips):
        return None
    pos = 0
    for seg in strips[row].segments:
        n = len(seg.text)
        if pos <= col < pos + n:
            return dict(seg.meta) if seg.meta else None
        pos += n
    return None


def _resolve_bg(styles: dict[str, Any], theme: Theme) -> RGBA | None:
    raw = styles.get("background")
    if isinstance(raw, tuple) and len(raw) >= 3:
        return _as_rgba(raw)
    # CSS may store token names.
    if isinstance(raw, str) and raw.startswith("$"):
        try:
            return theme.rgba(raw[1:])
        except Exception:  # noqa: BLE001
            pass
    return None


def _resolve_fg(styles: dict[str, Any], theme: Theme) -> RGBA:
    raw = styles.get("color") or styles.get("tint")
    if isinstance(raw, tuple) and len(raw) >= 3:
        return _as_rgba(raw)
    if isinstance(raw, str) and raw.startswith("$"):
        try:
            return theme.rgba(raw[1:])
        except Exception:  # noqa: BLE001
            pass
    return theme.rgba("foreground")


def _as_rgba(c: tuple) -> RGBA:
    if len(c) == 4:
        return int(c[0]), int(c[1]), int(c[2]), int(c[3])
    return int(c[0]), int(c[1]), int(c[2]), 255
