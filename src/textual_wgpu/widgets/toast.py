"""Toast — transient notification strip (Textual notify surface)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..fonts import cell_height
from ..geometry import Rect
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw

_SEVERITY_FALLBACK = {
    "information": None,
    "info": None,
    "warning": (212, 173, 114, 255),
    "error": (176, 74, 74, 255),
}


class Toast(Widget):
    """Bottom (or docked) toast. App ``notify`` can drive one instance."""

    message = reactive("", repaint=True)
    severity = reactive("information", repaint=True)
    RENDER_FONT_SIZE = 13

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._until: float | None = None
        self.layout_hint_h = cell_height(self.RENDER_FONT_SIZE) + 10
        self.layout_dock = "bottom"

    @property
    def visible(self) -> bool:
        if not self.message:
            return False
        if self._until is not None and time.monotonic() > self._until:
            return False
        return True

    def show(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 3.0,
    ) -> None:
        self.message = str(message or "")
        self.severity = (severity or "information").lower()
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = 3.0
        timeout_f = max(0.4, timeout_f)
        self._until = time.monotonic() + timeout_f
        self.styles["display"] = "block"
        self.refresh(layout=True)
        # Schedule auto-hide only when the UI loop can defer; App.set_timer
        # fires synchronously pre-run, which would clear the toast immediately.
        app = self.app
        running = bool(getattr(app, "_running", False) or getattr(app, "_loop", None))
        if running and hasattr(app, "set_timer"):
            until = self._until
            msg = self.message

            def _clear() -> None:
                if self.message == msg and self._until == until:
                    self.hide()

            app.set_timer(timeout_f, _clear)

    def hide(self) -> None:
        self.message = ""
        self._until = None
        self.styles["display"] = "none"
        self.refresh(layout=True)

    def _fg(self):
        t = self.theme
        fill = _SEVERITY_FALLBACK.get(self.severity)
        if fill is None:
            return t.rgba("accent")
        return fill

    def render_lines(self) -> list[Strip]:
        if not self.visible:
            return []
        t = self.theme
        return [Strip([
            Segment(f" {self.message} ", self._fg(), t.rgba("boost")),
        ])]

    def paint_at(self, draw: ImageDraw.ImageDraw, bounds: Rect) -> None:
        """Paint as overlay into ``bounds`` (ModeApp bottom strip)."""
        if not self.visible:
            return
        from ..compositor import paint_strips

        t = self.theme
        h = min(28, bounds.h)
        rect = Rect(bounds.x, bounds.y1 - h, bounds.w, h)
        draw.rectangle(
            [rect.x, rect.y, rect.x1 - 1, rect.y1 - 1],
            fill=t.rgba("boost"),
        )
        paint_strips(
            draw, rect, self.render_lines(),
            theme=t, styles={}, font_size=self.RENDER_FONT_SIZE,
            pad_x=8, pad_y=4, fill_bg=False,
        )

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if not self.visible or self.rect.w < 2:
            return
        self.paint_at(draw, self.rect)
