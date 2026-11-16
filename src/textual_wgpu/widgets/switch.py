"""Switch — boolean toggle rendered via Strip."""

from __future__ import annotations

from collections.abc import Callable

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget


class Switch(Widget):
    """On/off toggle: ``●══`` / ``══●`` cell glyphs."""

    class Changed(Message):
        def __init__(self, switch: Switch, value: bool) -> None:
            super().__init__()
            self.switch = switch
            self.value = value

    value = reactive(False, repaint=True)
    can_focus = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        value: bool = False,
        *,
        id: str | None = None,
        disabled: bool = False,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(id=id)
        self.value = bool(value)
        self.disabled = disabled
        self.on_change = on_change
        self.layout_hint_h = cell_height(self.RENDER_FONT_SIZE) + 6
        self.layout_hint_cells = 6

    def toggle(self) -> None:
        if self.disabled:
            return
        self.value = not self.value
        if self.on_change is not None:
            self.on_change(self.value)
        self.post_message(self.Changed(self, self.value))

    def action_toggle_switch(self) -> None:
        self.toggle()

    def watch_value(self, _old: bool, _new: bool) -> None:
        self.refresh()

    def watch__slider_position(self, _old: float, _new: float) -> None:
        self.refresh()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key in ("enter", "return", "space"):
            self.toggle()
            return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2:
            return []

        def _click(_x: int, _y: int) -> None:
            self.focus()
            self.toggle()

        return [(self.rect, _click)]

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if self.disabled:
            fg, bg = t.rgba("muted"), t.rgba("boost")
        elif self.value:
            fg, bg = t.rgba("background"), t.rgba("accent")
        else:
            fg, bg = t.rgba("muted"), t.rgba("panel")
        # 4-cell track + knob.
        if self.value:
            body = "══● "
        else:
            body = " ●══"
        return [Strip([Segment(body, fg, bg)])]
