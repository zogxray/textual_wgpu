"""Button — clickable Strip-rendered control."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget


class Button(Widget):
    """``[ label ]`` button. Paint path: ``render_lines`` → cell compositor."""

    class Pressed(Message):
        def __init__(self, button: Button) -> None:
            super().__init__()
            self.button = button

    label = reactive("Button", repaint=True)
    variant = reactive("default", repaint=True)  # default | primary | success | warning | error

    can_focus = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        label: str = "Button",
        *,
        id: str | None = None,
        variant: str = "default",
        disabled: bool = False,
        on_press: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(id=id)
        self.label = str(label)
        self.variant = variant
        self.disabled = disabled
        self.on_press = on_press
        self._pressed = False
        self.layout_hint_h = cell_height(self.RENDER_FONT_SIZE) + 8
        self.layout_hint_cells = max(8, len(self.label) + 4)

    def press(self) -> None:
        if self.disabled:
            return
        if self.on_press is not None:
            self.on_press()
        self.post_message(self.Pressed(self))
        self.refresh()

    def action_press(self) -> None:
        self.press()

    @classmethod
    def success(cls, label: str = "Button", **kwargs: Any) -> Button:
        return cls(label, variant="success", **kwargs)

    @classmethod
    def warning(cls, label: str = "Button", **kwargs: Any) -> Button:
        return cls(label, variant="warning", **kwargs)

    @classmethod
    def error(cls, label: str = "Button", **kwargs: Any) -> Button:
        return cls(label, variant="error", **kwargs)

    def validate_label(self, label: str) -> str:
        return str(label)

    def validate_variant(self, variant: str) -> str:
        return str(variant)

    def watch_flat(self, _old: bool, _new: bool) -> None:
        self.refresh()

    def watch_variant(self, _old: str, _new: str) -> None:
        self.refresh()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key in ("enter", "return", "space"):
            self.press()
            return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2:
            return []

        def _click(_x: int, _y: int) -> None:
            self.focus()
            self.press()

        return [(self.rect, _click)]

    def _palette(self) -> tuple[Any, Any]:
        t = self.theme
        if self.disabled:
            return t.rgba("muted"), t.rgba("boost")
        v = (self.variant or "default").lower()
        focused = getattr(self.app, "_focus", None) is self
        if v == "primary" or focused or self._pressed:
            return t.rgba("background"), t.rgba("accent")
        if v == "success":
            return t.rgba("background"), (70, 140, 90, 255)
        if v == "warning":
            return t.rgba("background"), (180, 140, 60, 255)
        if v == "error":
            return t.rgba("background"), (160, 70, 70, 255)
        return t.rgba("foreground"), t.rgba("panel")

    def render_lines(self) -> list[Strip]:
        fg, bg = self._palette()
        label = f" {self.label} "
        # Bracket chrome.
        left = Segment("[", fg, bg)
        mid = Segment(label, fg, bg)
        right = Segment("]", fg, bg)
        return [Strip([left, mid, right])]
