"""ProgressBar — determinate / indeterminate Strip bar."""

from __future__ import annotations

from ..fonts import cell_height, cell_width
from ..reactive import reactive
from ..renderables.bar import Bar
from ..strip import Segment, Strip
from ..widget import Widget


class ProgressBar(Widget):
    """Cell bar: filled ``█`` + empty ``░`` (+ optional percentage).

    ``progress`` is absolute progress toward ``total`` (Textual-style).
    Set ``show_eta=False``; percentage via ``show_percentage``.
    """

    progress = reactive(0.0, repaint=True)
    total = reactive(100.0, repaint=True)
    show_percentage = reactive(True, repaint=True)
    BAR_RENDERABLE = Bar
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        total: float | None = 100.0,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        show_bar: bool = True,
        show_percentage: bool = True,
        show_eta: bool = True,
        bar_width: int | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.total = float(total) if total else 100.0
        self.progress = 0.0
        self.show_bar = bool(show_bar)
        self.show_percentage = show_percentage
        self.show_eta = bool(show_eta)
        self._bar_width = bar_width  # cells; None → fill rect
        self.layout_hint_h = cell_height(self.RENDER_FONT_SIZE) + 6
        self.layout_hint_rows = 1

    def advance(self, advance: float = 1.0) -> None:
        self.update(advance=advance)

    @property
    def percentage(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.progress / self.total))

    def update(
        self,
        progress: float | None = None,
        *,
        total: float | None = None,
        advance: float | None = None,
    ) -> None:
        if total is not None:
            self.total = float(total)
        if progress is not None:
            self.progress = float(progress)
        elif advance is not None:
            self.progress = float(self.progress) + float(advance)

    def render_lines(self) -> list[Strip]:
        t = self.theme
        cw = cell_width(self.RENDER_FONT_SIZE)
        pad_cells = 1
        pct_cells = 5 if self.show_percentage else 0
        avail = max(4, (self.rect.w // cw) - 2 * pad_cells - pct_cells) if self.rect.w else 20
        width = self._bar_width if self._bar_width is not None else avail
        width = max(4, int(width))
        filled = int(round(width * self.percentage))
        filled = max(0, min(width, filled))
        bar = "█" * filled + "░" * (width - filled)
        segs = [Segment(bar, t.rgba("accent"), t.rgba("boost"))]
        if self.show_percentage:
            label = f" {int(self.percentage * 100):3d}%"
            segs.append(Segment(label, t.rgba("muted")))
        return [Strip(segs)]
