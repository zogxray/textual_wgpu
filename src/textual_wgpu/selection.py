"""Text selection — Textual ``Selection`` subset for screen-level copy UX.

Widgets that opt in via ``ALLOW_SELECT`` expose plain text through
``get_selection``; ``Screen.selections`` maps widget → range; Ctrl+C copies
via ``Screen.get_selected_text``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from .geometry import Offset, Rect
from .strip import Segment, Strip

if TYPE_CHECKING:
    from .widget import Widget

RGBA = tuple[int, int, int, int]


class Selection(NamedTuple):
    """A selected range of lines (content offsets)."""

    start: Offset | None
    end: Offset | None

    @classmethod
    def from_offsets(cls, offset1: Offset, offset2: Offset) -> Selection:
        offsets = sorted([offset1, offset2], key=lambda o: (o.y, o.x))
        return cls(*offsets)

    def extract(self, text: str) -> str:
        """Extract selection from newline-joined widget text."""
        lines = text.splitlines()
        if not lines:
            return ""
        if self.start is None:
            start_line_index = 0
            start_offset = 0
        else:
            start_line_index, start_offset = self.start.transpose

        if self.end is None:
            end_line = len(lines) - 1
            end_offset = len(lines[-1])
        else:
            end_line, end_offset = self.end.transpose
            end_line = min(len(lines) - 1, end_line)

        if start_line_index > end_line:
            return ""
        if start_line_index == end_line:
            line = lines[start_line_index] if start_line_index < len(lines) else ""
            return line[start_offset:end_offset]

        selected: list[str] = []
        selected_lines = lines[start_line_index : end_line + 1]
        if len(selected_lines) >= 2:
            first_line, *mid_lines, last_line = selected_lines
            selected.append(first_line[start_offset:])
            selected.extend(mid_lines)
            selected.append(last_line[:end_offset])
        elif selected_lines:
            selected.append(selected_lines[0][start_offset:end_offset])
        return "\n".join(selected)

    def get_span(self, y: int) -> tuple[int, int] | None:
        """Selected ``(x_start, x_end)`` on line ``y``; ``x_end=-1`` → end of line."""
        start, end = self
        if start is None and end is None:
            return 0, -1
        if start is not None and end is not None:
            if y < start.y or y > end.y:
                return None
            if y == start.y and start.y == end.y:
                return start.x, end.x
            if y == end.y:
                return 0, end.x
            if y == start.y:
                return start.x, -1
            return 0, -1
        if start is None and end is not None:
            if y == end.y:
                return 0, end.x
            if y > end.y:
                return None
            return 0, -1
        if end is None and start is not None:
            if y == start.y:
                return start.x, -1
            if y > start.y:
                return 0, -1
            return None
        return 0, -1


SELECT_ALL = Selection(None, None)


def strips_plain(strips: list[Strip]) -> str:
    return "\n".join(s.plain() for s in strips)


def apply_selection_bg(
    strips: list[Strip],
    selection: Selection | None,
    bgcolor: RGBA,
) -> list[Strip]:
    """Return strips with ``bgcolor`` on the selected spans (paint highlight)."""
    if selection is None or not strips:
        return strips
    out: list[Strip] = []
    for y, strip in enumerate(strips):
        span = selection.get_span(y)
        if span is None:
            out.append(strip)
            continue
        x0, x1 = span
        plain = strip.plain()
        if x1 < 0:
            x1 = len(plain)
        x0 = max(0, min(len(plain), x0))
        x1 = max(x0, min(len(plain), x1))
        if x0 >= x1 and x1 != len(plain):
            out.append(strip)
            continue
        if x0 == 0 and x1 >= len(plain):
            out.append(_recolor_strip(strip, bgcolor))
            continue
        parts: list[Segment] = []
        if x0 > 0:
            parts.extend(strip.crop(0, x0).segments)
        mid = strip.crop(x0, x1 if x1 > x0 else len(plain))
        parts.extend(_recolor_strip(mid, bgcolor).segments)
        if x1 < len(plain):
            parts.extend(strip.crop(x1, len(plain)).segments)
        out.append(Strip(parts))
    return out


def _recolor_strip(strip: Strip, bgcolor: RGBA) -> Strip:
    return Strip([
        Segment(
            seg.text, seg.style, bgcolor,
            bold=seg.bold, italic=seg.italic,
            underline=seg.underline, meta=seg.meta,
        )
        for seg in strip.segments
    ])


def pointer_to_offset(
    x: int,
    y: int,
    *,
    rect: Rect,
    scroll_y: int = 0,
    pad_x: int = 4,
    pad_y: int = 2,
    font_size: int = 13,
    line_h: int | None = None,
) -> Offset | None:
    """Map screen pointer to a content ``Offset`` inside ``rect``."""
    if rect.w < 2 or rect.h < 2:
        return None
    if not (rect.x <= x < rect.x1 and rect.y <= y < rect.y1):
        return None
    from .fonts import cell_height, cell_width

    cw = cell_width(font_size)
    ch = line_h if line_h is not None else cell_height(font_size)
    col = max(0, (x - rect.x - pad_x) // max(1, cw))
    row = max(0, (y - rect.y - pad_y) // max(1, ch)) + max(0, scroll_y)
    return Offset(col, row)


def word_span_at(text_line: str, col: int) -> tuple[int, int]:
    """Inclusive-exclusive word bounds around ``col`` on a single line."""
    if not text_line:
        return 0, 0
    col = max(0, min(len(text_line), col))
    if col >= len(text_line):
        col = len(text_line) - 1
    if text_line[col].isspace():
        return col, col + 1
    a = col
    while a > 0 and not text_line[a - 1].isspace():
        a -= 1
    b = col + 1
    while b < len(text_line) and not text_line[b].isspace():
        b += 1
    return a, b


def selection_style_rgba(widget: Widget) -> RGBA:
    """Accent-tinted selection background (Textual ``screen--selection`` lite)."""
    t = widget.theme
    r, g, b, _ = t.rgba("accent")
    return (r, g, b, 110)
