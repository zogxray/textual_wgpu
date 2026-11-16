"""Strip — one painted line of styled segments (Textual Strip analogue).

The cell compositor (``compositor.paint_strips``) is the preferred paint path:
widgets emit ``list[Strip]``; the compositor maps them onto a mono cell grid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import ImageDraw

RGBA = tuple[int, int, int, int]


@dataclass
class Segment:
    """Text run with optional foreground / background RGBA + emphasis."""

    text: str
    style: RGBA | None = None  # foreground; None → theme default
    bgcolor: RGBA | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    meta: dict | None = None  # e.g. click actions from Rich


@dataclass
class Strip:
    """A single row of segments. Sole content unit for the cell compositor."""

    segments: list[Segment] = field(default_factory=list)

    @classmethod
    def from_text(
        cls,
        text: str,
        style: RGBA | None = None,
        *,
        bgcolor: RGBA | None = None,
    ) -> Strip:
        return cls([Segment(text, style, bgcolor)])

    @classmethod
    def blank(cls, width: int = 0, *, bgcolor: RGBA | None = None) -> Strip:
        if width <= 0:
            return cls([])
        return cls([Segment(" " * width, bgcolor=bgcolor)])

    def __len__(self) -> int:
        return sum(len(s.text) for s in self.segments)

    def plain(self) -> str:
        return "".join(s.text for s in self.segments)

    def join(self, other: Strip) -> Strip:
        return Strip(list(self.segments) + list(other.segments))

    def __add__(self, other: object) -> Strip:
        if isinstance(other, Strip):
            return self.join(other)
        if isinstance(other, Segment):
            return Strip(list(self.segments) + [other])
        if isinstance(other, str):
            return self.join(Strip.from_text(other))
        return NotImplemented

    def crop(self, start: int, end: int | None = None) -> Strip:
        """Slice by character columns (Textual ``Strip.crop`` analogue)."""
        plain = self.plain()
        if end is None:
            end = len(plain)
        start = max(0, start)
        end = max(start, min(len(plain), end))
        if start == 0 and end == len(plain):
            return Strip(list(self.segments))
        out: list[Segment] = []
        pos = 0
        for seg in self.segments:
            seg_end = pos + len(seg.text)
            if seg_end <= start:
                pos = seg_end
                continue
            if pos >= end:
                break
            lo = max(0, start - pos)
            hi = min(len(seg.text), end - pos)
            if lo < hi:
                out.append(Segment(
                    seg.text[lo:hi], seg.style, seg.bgcolor,
                    bold=seg.bold, italic=seg.italic,
                    underline=seg.underline, meta=seg.meta,
                ))
            pos = seg_end
        return Strip(out)

    def paint(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        *,
        font,
        default_fill: RGBA,
        max_chars: int | None = None,
    ) -> int:
        """Draw left→right (proportional advance). Return characters rendered."""
        shown = 0
        cx = x
        for seg in self.segments:
            text = seg.text
            if max_chars is not None:
                remain = max_chars - shown
                if remain <= 0:
                    break
                text = text[:remain]
            if not text:
                continue
            fill = seg.style if seg.style is not None else default_fill
            draw.text((cx, y), text, font=font, fill=fill)
            try:
                cx += int(round(font.getlength(text)))
            except Exception:  # noqa: BLE001
                cx += len(text) * 8
            shown += len(text)
        return shown

    def paint_cells(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        *,
        cell_w: int,
        cell_h: int,
        font,
        default_fg: RGBA,
        default_bg: RGBA | None = None,
        max_cells: int | None = None,
        font_size: int | None = None,
    ) -> int:
        """Draw on a fixed mono cell grid (bg fills + glyphs). Return cells used."""
        from .fonts import _is_braille, mono, mono_braille

        shown = 0
        cx = x
        size = font_size
        if size is None:
            size = getattr(font, "size", None) or 13
        size_i = int(size)
        for seg in self.segments:
            text = seg.text
            if max_cells is not None:
                remain = max_cells - shown
                if remain <= 0:
                    break
                text = text[:remain]
            if not text:
                continue
            n = len(text)
            bg = seg.bgcolor if seg.bgcolor is not None else default_bg
            if bg is not None:
                draw.rectangle(
                    [cx, y, cx + n * cell_w - 1, y + cell_h - 1],
                    fill=bg,
                )
            fill = seg.style if seg.style is not None else default_fg
            if seg.bold or seg.italic:
                f = mono(size_i, bold=seg.bold, italic=seg.italic)
            else:
                f = font
            # Vertically center glyphs in the cell (avoids “stuck to top” chrome).
            descent = 2
            try:
                ascent, descent = f.getmetrics()
                text_h = max(1, int(ascent) + int(descent))
                descent = int(descent)
            except Exception:  # noqa: BLE001
                text_h = max(1, size_i)
            ty = y + max(0, (cell_h - text_h) // 2)
            # Braille (world map) is missing from many monos — FreeMono fallback.
            if any(_is_braille(ch) for ch in text):
                fb = mono_braille(size_i, bold=bool(seg.bold))
                i = 0
                while i < n:
                    braille = _is_braille(text[i])
                    j = i + 1
                    while j < n and _is_braille(text[j]) == braille:
                        j += 1
                    chunk = text[i:j]
                    face = fb if braille else f
                    if braille:
                        for k, ch in enumerate(chunk):
                            draw.text(
                                (cx + (i + k) * cell_w, ty), ch, font=face, fill=fill,
                            )
                    else:
                        draw.text((cx + i * cell_w, ty), chunk, font=face, fill=fill)
                    i = j
            else:
                draw.text((cx, ty), text, font=f, fill=fill)
            if seg.underline:
                uy = ty + text_h - max(1, descent)
                draw.line(
                    [(cx, uy), (cx + n * cell_w - 1, uy)],
                    fill=fill, width=1,
                )
            cx += n * cell_w
            shown += n
        return shown
