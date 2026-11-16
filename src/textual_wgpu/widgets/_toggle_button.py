"""ToggleButton — shared base for Checkbox and RadioButton."""

from __future__ import annotations

from collections.abc import Callable

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget


class ToggleButton(Widget):
    """Focusable binary toggle with label + indicator glyphs."""

    class Changed(Message):
        def __init__(self, toggle_button: ToggleButton, value: bool) -> None:
            super().__init__()
            self.toggle_button = toggle_button
            self.control = toggle_button
            self.value = value

    value = reactive(False, repaint=True)
    label = reactive("", repaint=True)

    can_focus = True
    ALLOW_SELECT = False
    RENDER_FONT_SIZE = 13

    BUTTON_LEFT: str = "["
    BUTTON_INNER_OFF: str = " "
    BUTTON_INNER_ON: str = "X"
    BUTTON_RIGHT: str = "]"

    def __init__(
        self,
        label: str = "",
        value: bool = False,
        button_first: bool = True,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: str | None = None,
        compact: bool = False,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.button_first = bool(button_first)
        self.tooltip = tooltip
        self.compact = bool(compact)
        self.on_change = on_change
        self.label = str(label or "")
        self.value = bool(value)
        self.layout_hint_rows = 1
        self._update_width_hint()

    def watch_label(self, _old: str, _new: str) -> None:
        self._update_width_hint()

    def _update_width_hint(self) -> None:
        pad = 2 if self.compact else 4
        self.layout_hint_cells = max(5, len(self.label) + 4 + pad)

    def _make_changed_message(self) -> Message:
        return self.Changed(self, self.value)

    def toggle(self) -> ToggleButton:
        if self.disabled:
            return self
        self.value = not self.value
        if self.on_change is not None:
            self.on_change(self.value)
        self.post_message(self._make_changed_message())
        self.refresh()
        return self

    def action_toggle_button(self) -> None:
        self.toggle()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key in ("space", "enter", "return"):
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
        focused = self.focused or getattr(self.app, "_focus", None) is self
        inner = self.BUTTON_INNER_ON if self.value else self.BUTTON_INNER_OFF
        box = f"{self.BUTTON_LEFT}{inner}{self.BUTTON_RIGHT}"

        if self.disabled:
            box_fg = t.rgba("muted")
            lbl_fg = t.rgba("muted")
            bg = None
        elif focused:
            box_fg = t.rgba("background")
            lbl_fg = t.rgba("background")
            bg = t.rgba("accent")
        elif self.value:
            box_fg = t.rgba("accent")
            lbl_fg = t.rgba("foreground")
            bg = None
        else:
            box_fg = t.rgba("muted")
            lbl_fg = t.rgba("foreground")
            bg = None

        pad = "" if self.compact else " "
        segs: list[Segment] = []
        if self.button_first:
            segs.append(Segment(f"{pad}{box}", box_fg, bg, bold=bool(self.value)))
            if self.label:
                segs.append(Segment(f" {self.label}{pad}", lbl_fg, bg))
            elif pad:
                segs.append(Segment(pad, lbl_fg, bg))
        else:
            if self.label:
                segs.append(Segment(f"{pad}{self.label} ", lbl_fg, bg))
            elif pad:
                segs.append(Segment(pad, lbl_fg, bg))
            segs.append(Segment(f"{box}{pad}", box_fg, bg, bold=bool(self.value)))
        return [Strip(segs)]

    def get_content_width(self) -> int | None:
        pad = 0 if self.compact else 2
        extra = (len(self.label) + 1) if self.label else 0
        return cell_width(self.RENDER_FONT_SIZE) * max(3, 3 + extra + pad)

    def get_content_height(self) -> int | None:
        return cell_height(self.RENDER_FONT_SIZE) + (2 if self.compact else 4)
