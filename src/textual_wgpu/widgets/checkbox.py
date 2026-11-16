"""Checkbox — boolean toggle with check glyph (Textual Checkbox subset)."""

from __future__ import annotations

from ..message import Message
from ._toggle_button import ToggleButton


class Checkbox(ToggleButton):
    """A checkbox widget with ``[✓]`` / ``[ ]`` indicator."""

    class Changed(Message):
        """Posted when the checkbox value changes."""

        def __init__(self, checkbox: Checkbox, value: bool) -> None:
            super().__init__()
            self.checkbox = checkbox
            self.control = checkbox
            self.value = value

    BUTTON_LEFT = "["
    BUTTON_INNER_OFF = " "
    BUTTON_INNER_ON = "✓"
    BUTTON_RIGHT = "]"

    def _make_changed_message(self) -> Message:
        return self.Changed(self, self.value)

    def update(self, label: str | None = None, value: bool | None = None) -> None:
        if label is not None:
            self.label = str(label)
        if value is not None:
            self.value = bool(value)
        self.refresh()

    def watch_value(self, _old: bool, _new: bool) -> None:
        self.refresh()
