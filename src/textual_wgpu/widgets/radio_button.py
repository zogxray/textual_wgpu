"""RadioButton — exclusive selection item (Textual RadioButton subset)."""

from __future__ import annotations

from ..message import Message
from ._toggle_button import ToggleButton


class RadioButton(ToggleButton):
    """A radio button widget with ``(●)`` / ``( )`` indicator."""

    class Changed(Message):
        """Posted when the radio button value changes."""

        def __init__(self, radio_button: RadioButton, value: bool) -> None:
            super().__init__()
            self.radio_button = radio_button
            self.control = radio_button
            self.value = value

    BUTTON_LEFT = "("
    BUTTON_INNER_OFF = " "
    BUTTON_INNER_ON = "●"
    BUTTON_RIGHT = ")"

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

    def toggle(self) -> RadioButton:
        if self.disabled:
            return self
        parent = getattr(self, "parent", None)
        handler = getattr(parent, "_handle_button_toggle", None)
        if callable(handler):
            handler(self)
            return self
        super().toggle()
        return self
