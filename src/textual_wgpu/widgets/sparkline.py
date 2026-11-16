"""Sparkline — inline bar chart using block glyphs (Textual Sparkline subset)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..fonts import cell_height, cell_width, columns_for_width
from ..reactive import reactive
from ..strip import RGBA, Segment, Strip
from ..widget import Widget

_BARS = "▁▂▃▄▅▆▇█"


def _lerp_rgba(a: RGBA, b: RGBA, t: float) -> RGBA:
    u = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * u),
        int(a[1] + (b[1] - a[1]) * u),
        int(a[2] + (b[2] - a[2]) * u),
        255,
    )


def _bucket_sample(data: Sequence[float], width: int, fn: Callable[[Sequence[float]], float]) -> list[float]:
    n = len(data)
    if n <= width or width <= 0:
        return [float(x) for x in data]
    out: list[float] = []
    for i in range(width):
        lo = int(i * n / width)
        hi = max(lo + 1, int((i + 1) * n / width))
        out.append(float(fn(data[lo:hi])))
    return out


class Sparkline(Widget):
    """Render numeric data as a single-row ``▁▂▃▄▅▆▇█`` sparkline."""

    data = reactive(None, repaint=True)
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        data: Sequence[float] | None = None,
        *,
        min_color: RGBA | None = None,
        max_color: RGBA | None = None,
        summary_function: Callable[[Sequence[float]], float] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.data = list(data) if data is not None else []
        self.min_color = min_color
        self.max_color = max_color
        self.summary_function = summary_function or max
        self.layout_hint_rows = 1

    def render_lines(self) -> list[Strip]:
        t = self.theme
        vals = [float(x) for x in (self.data or ())]
        if not vals:
            return [Strip([Segment(" ", t.rgba("muted"))])]
        cols = columns_for_width(self.rect.w, self.RENDER_FONT_SIZE) if self.rect.w > 0 else len(vals)
        samples = _bucket_sample(vals, max(1, cols), self.summary_function)
        lo = min(samples)
        hi = max(samples)
        span = hi - lo
        c_min = self.min_color or t.rgba("muted")
        c_max = self.max_color or t.rgba("accent")
        segs: list[Segment] = []
        for v in samples:
            frac = 0.5 if span <= 0 else (v - lo) / span
            idx = min(len(_BARS) - 1, max(0, int(round(frac * (len(_BARS) - 1)))))
            segs.append(Segment(_BARS[idx], _lerp_rgba(c_min, c_max, frac)))
        return [Strip(segs)]

    def get_content_width(self) -> int | None:
        n = len(self.data or ())
        return cell_width(self.RENDER_FONT_SIZE) * max(1, n)

    def get_content_height(self) -> int | None:
        return cell_height(self.RENDER_FONT_SIZE) + 2
