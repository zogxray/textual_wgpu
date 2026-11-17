"""Cell compositor — Strip is the sole content paint path.

Widgets emit ``list[Strip]`` (via ``render_lines`` / ``render_line``).
This module maps strips onto a monospace cell grid inside a widget rect.
Not a terminal emulator: no ANSI, no screen buffer, no cursor protocol —
just shared cell metrics + Strip blit.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .fonts import cell_height, cell_width, mono
from .geometry import Rect
from .strip import Strip

if TYPE_CHECKING:
    from PIL import Image, ImageDraw

    from .theme import Theme

RGBA = tuple[int, int, int, int]

_IMAGE_CACHE: dict[tuple[str, int, int, int], Any] = {}
_IMAGE_CACHE_MAX = 32


def _load_scaled_image(path_str: str, w: int, h: int) -> Any | None:
    from PIL import Image

    if w < 1 or h < 1:
        return None
    p = Path(path_str)
    try:
        st = p.stat()
        mtime_ns = int(st.st_mtime_ns)
    except OSError:
        return None
    key = (str(p), w, h, mtime_ns)
    cached = _IMAGE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(p) as im:
            rgba = im.convert("RGBA")
            if rgba.size != (w, h):
                rgba = rgba.resize((w, h), Image.Resampling.LANCZOS)
    except OSError:
        return None
    if len(_IMAGE_CACHE) >= _IMAGE_CACHE_MAX:
        _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)), None)
    _IMAGE_CACHE[key] = rgba
    return rgba


def make_image_text(
    path: Path | str,
    *,
    max_width_px: int = 480,
    max_height_px: int = 320,
    row_height_px: int = 18,
    indent_px: int = 16,
) -> Any | None:
    """Build a ``rich.text.Text`` placeholder for native RGBA image compositing."""
    from PIL import Image
    from rich.style import Style
    from rich.text import Text

    p = Path(path)
    try:
        with Image.open(p) as im:
            iw, ih = im.size
    except OSError:
        return None
    if iw < 2 or ih < 2:
        return None
    max_w = max(32, int(max_width_px))
    max_h = max(32, int(max_height_px))
    scale = min(max_w / iw, max_h / ih, 1.0)
    tw = max(2, int(round(iw * scale)))
    th = max(2, int(round(ih * scale)))
    rh = max(1, int(row_height_px))
    rows = max(1, (th + rh - 1) // rh)

    out = Text()
    resolved = str(p.resolve())
    for r in range(rows):
        meta = {
            "@image": {
                "path": resolved,
                "w": tw,
                "h": th,
                "row": r,
                "rows": rows,
                "indent": int(indent_px),
            },
        }
        out.append(" ", style=Style.from_meta(meta))
        out.append("\n")
    return out


def _paint_inline_images(
    draw_or_img: Any,
    strips: list[Strip],
    *,
    start: int,
    end: int,
    origin_x: int,
    origin_y: int,
    clip_x0: int,
    clip_y0: int,
    clip_x1: int,
    clip_y1: int,
    ch: int,
) -> None:
    record_img = getattr(draw_or_img, "record_image", None)
    dest_img = getattr(draw_or_img, "_image", None) if record_img is None else None
    if record_img is None and dest_img is None:
        return
    if clip_x1 <= clip_x0 or clip_y1 <= clip_y0:
        return
    drawn: set[tuple[int, str]] = set()
    for i in range(start, end):
        strip = strips[i]
        for seg in strip.segments:
            meta = seg.meta
            if not meta:
                continue
            info = meta.get("@image")
            if not isinstance(info, dict):
                continue
            path_str = str(info.get("path") or "")
            if not path_str:
                continue
            row_idx = int(info.get("row") or 0)
            img_start = i - row_idx
            key = (img_start, path_str)
            if key in drawn:
                continue
            drawn.add(key)
            tw = int(info.get("w") or 0)
            th = int(info.get("h") or 0)
            indent = int(info.get("indent") or 0)
            if tw <= 0 or th <= 0:
                continue
            ix0 = origin_x + indent
            iy0 = origin_y + (img_start - start) * ch
            vx0 = max(ix0, clip_x0)
            vy0 = max(iy0, clip_y0)
            vx1 = min(ix0 + tw, clip_x1)
            vy1 = min(iy0 + th, clip_y1)
            if vx1 <= vx0 or vy1 <= vy0:
                continue
            crop_box = (vx0 - ix0, vy0 - iy0, vx1 - ix0, vy1 - iy0)
            if callable(record_img):
                record_img(path_str, tw, th, vx0, vy0, crop_box)
                continue
            rgba = _load_scaled_image(path_str, tw, th)
            if rgba is None:
                continue
            piece = rgba.crop(crop_box) if crop_box != (0, 0, tw, th) else rgba
            dest_img.alpha_composite(piece, (vx0, vy0))


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

    if sub_y > 0 and callable(getattr(draw, "push_clip", None)):
        draw.push_clip(rect.x, rect.y, rect.x1, rect.y1)
        end = min(len(strips), start + max_rows + 2)
        y = rect.y + pad_y - sub_y
        for i in range(start, end):
            strip = strips[i]
            if strip.segments:
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
        draw.pop_clip()
        _paint_inline_images(
            draw,
            strips,
            start=start,
            end=end,
            origin_x=rect.x + pad_x,
            origin_y=rect.y + pad_y - sub_y,
            clip_x0=rect.x + pad_x,
            clip_y0=rect.y,
            clip_x1=rect.x1 - pad_x,
            clip_y1=rect.y1,
            ch=ch,
        )
        return

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
        _paint_inline_images(
            draw,
            strips,
            start=start,
            end=end,
            origin_x=rect.x + pad_x,
            origin_y=rect.y + pad_y - sub_y,
            clip_x0=rect.x + pad_x,
            clip_y0=rect.y,
            clip_x1=rect.x1 - pad_x,
            clip_y1=rect.y1,
            ch=ch,
        )
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
    _paint_inline_images(
        draw,
        strips,
        start=start,
        end=min(len(strips), start + max_rows + 2),
        origin_x=rect.x + pad_x,
        origin_y=rect.y + pad_y,
        clip_x0=rect.x + pad_x,
        clip_y0=rect.y,
        clip_x1=rect.x1 - pad_x,
        clip_y1=rect.y1,
        ch=ch,
    )


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
