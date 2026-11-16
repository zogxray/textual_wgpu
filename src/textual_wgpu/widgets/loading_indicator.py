"""LoadingIndicator — busy spinner / status strip."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw

_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class LoadingIndicator(Widget):
    """Animated busy indicator. Toggle with ``loading`` or ``show`` / ``hide``."""

    message = reactive("Loading…", repaint=True)
    loading = reactive(False, repaint=True, layout=True)

    def __init__(
        self,
        message: str = "Loading…",
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.message = message
        self._t0 = time.monotonic()
        self.styles["display"] = "none"

    def show(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        self.loading = True
        self.styles["display"] = "block"
        self._t0 = time.monotonic()
        self.refresh(layout=True)

    def hide(self) -> None:
        self.loading = False
        self.styles["display"] = "none"
        self.refresh(layout=True)

    def watch_loading(self, value: bool) -> None:
        self.styles["display"] = "block" if value else "none"

    def on_input(self, _event: Any = None) -> None:
        pass

    def _frame(self) -> str:
        i = int((time.monotonic() - self._t0) * 10) % len(_FRAMES)
        return _FRAMES[i]

    def render_lines(self) -> list[Strip]:
        if not self.loading and self.styles.get("display") == "none":
            return []
        t = self.theme
        text = f" {self._frame()} {self.message} "
        return [Strip([Segment(text, t.rgba("accent"), t.rgba("surface"))])]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.styles.get("display") == "none" and not self.loading:
            return
        if self.rect.w < 2:
            return
        from ..compositor import paint_widget_strips

        paint_widget_strips(draw, self, font_size=13, fill_bg=True, pad_x=6, pad_y=4)
        if self.loading and self.app is not None and hasattr(self.app, "refresh"):
            self.app.refresh()
        elif self.loading and self.app is not None and hasattr(self.app, "request_draw"):
            self.app.request_draw()
