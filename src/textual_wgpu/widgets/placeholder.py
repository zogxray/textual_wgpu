"""Placeholder — layout / design mockup block (Textual Placeholder subset)."""

from __future__ import annotations

from collections.abc import Callable

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget

_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
)
_VARIANTS = ("default", "size", "text")


class Placeholder(Widget):
    """Simple placeholder widget with ``default`` / ``size`` / ``text`` variants."""

    variant = reactive("default", repaint=True)
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        label: str | None = None,
        variant: str = "default",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.label = label
        self.variant = str(variant or "default").lower()
        self.styles.setdefault("background", "$surface")

    def validate_variant(self, variant: str) -> str:
        v = str(variant or "default").lower()
        return v if v in _VARIANTS else "default"

    def watch_variant(self, _old: str, _new: str) -> None:
        self.refresh()

    def cycle_variant(self) -> None:
        try:
            idx = (_VARIANTS.index(self.variant) + 1) % len(_VARIANTS)
        except ValueError:
            idx = 0
        self.variant = _VARIANTS[idx]

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2 or self.rect.h < 2:
            return []

        def _click(_x: int, _y: int) -> None:
            self.cycle_variant()

        return [(self.rect, _click)]

    def _text_lines(self) -> list[str]:
        if self.variant == "size":
            cw = max(1, cell_width(self.RENDER_FONT_SIZE))
            ch = max(1, cell_height(self.RENDER_FONT_SIZE))
            cols = max(0, self.rect.w // cw)
            rows = max(0, self.rect.h // ch)
            return [f"{cols} x {rows}" if cols and rows else f"{self.rect.w}x{self.rect.h}"]
        if self.variant == "text":
            cw = max(1, cell_width(self.RENDER_FONT_SIZE))
            cols = max(16, (self.rect.w - 16) // cw) if self.rect.w > 24 else 40
            words = _LOREM.split()
            lines: list[str] = []
            cur = ""
            for w in words:
                trial = f"{cur} {w}".strip() if cur else w
                if cur and len(trial) > cols:
                    lines.append(cur)
                    cur = w
                else:
                    cur = trial
            if cur:
                lines.append(cur)
            return lines
        if self.label is not None:
            return [str(self.label)]
        if self.id:
            return [f"#{self.id}"]
        return ["Placeholder"]

    def render_lines(self) -> list[Strip]:
        t = self.theme
        fg = t.rgba("muted") if self.disabled else t.rgba("foreground")
        bg = t.rgba("surface")
        lines = self._text_lines()
        if self.variant == "text":
            return [Strip([Segment(ln, fg, bg)]) for ln in lines]
        cw = max(1, cell_width(self.RENDER_FONT_SIZE))
        cols = max(len(lines[0]) + 2, (self.rect.w - 8) // cw) if self.rect.w > 16 else len(lines[0]) + 4
        text = lines[0].center(cols)
        return [Strip([Segment(text, fg, bg)])]
