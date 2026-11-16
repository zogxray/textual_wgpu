"""ModeApp modal overlays — Content → Strip paint helpers (no draw.text)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compositor import paint_strips
from .geometry import Rect
from .strip import Segment, Strip

if TYPE_CHECKING:
    from PIL import ImageDraw

    from .theme import Theme

RGBA = tuple[int, int, int, int]


def veil(draw: ImageDraw.ImageDraw, w: int, h: int, *, alpha: int = 160) -> None:
    draw.rectangle([0, 0, w, h], fill=(0, 0, 0, alpha))


def dialog(
    draw: ImageDraw.ImageDraw,
    mx: int,
    my: int,
    mw: int,
    mh: int,
    theme: Theme,
) -> Rect:
    draw.rectangle(
        [mx, my, mx + mw, my + mh],
        fill=theme.rgba("surface"),
        outline=theme.rgba("accent"),
        width=2,
    )
    return Rect(mx, my, mw, mh)


def strip_line(
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    text: str,
    theme: Theme,
    *,
    fg: RGBA | None = None,
    bg: RGBA | None = None,
    font_size: int = 13,
    pad_x: int = 4,
    pad_y: int = 2,
) -> None:
    """Blit one text line into ``rect`` via the cell compositor."""
    if rect.w < 2 or rect.h < 2 or not text:
        return
    paint_strips(
        draw,
        rect,
        [Strip([Segment(text, fg or theme.rgba("foreground"), bg)])],
        theme=theme,
        font_size=font_size,
        pad_x=pad_x,
        pad_y=pad_y,
        fill_bg=False,
    )


def strip_lines(
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    lines: list[str],
    theme: Theme,
    *,
    fg: RGBA | None = None,
    font_size: int = 13,
    pad_x: int = 4,
    pad_y: int = 2,
    scroll_y: int = 0,
) -> None:
    if rect.w < 2 or rect.h < 2:
        return
    color = fg or theme.rgba("foreground")
    strips = [Strip([Segment(line, color)]) for line in lines]
    paint_strips(
        draw,
        rect,
        strips,
        theme=theme,
        font_size=font_size,
        pad_x=pad_x,
        pad_y=pad_y,
        scroll_y=scroll_y,
        fill_bg=False,
    )


def chip(
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    label: str,
    theme: Theme,
    *,
    active: bool = False,
    outlined: bool = False,
    font_size: int = 13,
) -> None:
    """Button-like chip: accent fill when active, optional muted outline."""
    if rect.w < 2 or rect.h < 2:
        return
    if active:
        fill = theme.rgba("accent")
        fg = theme.rgba("background")
        outline = fill
    else:
        fill = theme.rgba("panel")
        fg = theme.rgba("foreground")
        outline = theme.rgba("accent") if outlined else theme.rgba("muted")
    draw.rectangle(
        [rect.x, rect.y, rect.x1 - 1, rect.y1 - 1],
        fill=fill,
        outline=outline,
    )
    strip_line(
        draw, rect, f" {label} ", theme,
        fg=fg, font_size=font_size, pad_x=6, pad_y=4,
    )


def field_box(
    draw: ImageDraw.ImageDraw,
    rect: Rect,
    text: str,
    theme: Theme,
    *,
    focused: bool = False,
    muted: bool = False,
    font_size: int = 13,
) -> None:
    """Input-like panel box with Strip text."""
    outline = theme.rgba("accent") if focused else theme.rgba("muted")
    draw.rectangle(
        [rect.x, rect.y, rect.x1 - 1, rect.y1 - 1],
        fill=theme.rgba("panel"),
        outline=outline,
    )
    fg = theme.rgba("muted") if muted else theme.rgba("foreground")
    strip_line(
        draw, rect, text, theme,
        fg=fg, font_size=font_size, pad_x=8, pad_y=4,
    )
