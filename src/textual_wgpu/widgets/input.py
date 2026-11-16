"""Input — single-line text field (Textual Input subset)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from ..message import Message
from ..validation import ValidationResult, Validator
from .textarea import TextArea


class Input(TextArea):
    """Single-line field with Textual-like ``value`` / ``cursor_position`` API.

    Messages: ``Input.Changed`` / ``Input.Submitted`` / ``Input.Blurred``.
    """

    # Textual-compatible aliases (same Message subclasses).
    Changed = TextArea.Changed
    Submitted = TextArea.Submitted

    class Blurred(Message):
        """Posted when the input loses focus."""

        def __init__(
            self,
            input_widget: Input,
            value: str,
            validation_result: ValidationResult | None = None,
        ) -> None:
            super().__init__()
            self.input = input_widget
            self.control = input_widget
            self.value = value
            self.validation_result = validation_result

    def __init__(
        self,
        value: str = "",
        *,
        id: str | None = None,
        placeholder: str = "",
        password: bool = False,
        validators: Callable[[str], Any] | Iterable[Callable[[str], Any]] | None = None,
        valid_empty: bool = False,
        select_on_focus: bool = True,
        name: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: str | None = None,
        compact: bool = False,
        suggester: Any = None,
        on_change: Callable[[str], None] | None = None,
        on_submit: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            value,
            id=id,
            placeholder=placeholder,
            on_change=on_change,
            on_submit=on_submit,
            single_line=True,
        )
        self.name = name
        self.disabled = bool(disabled)
        self.password = bool(password)
        self.valid_empty = bool(valid_empty)
        self.select_on_focus = bool(select_on_focus)
        self.tooltip = tooltip
        self.compact = bool(compact)
        self.suggester = suggester
        if classes:
            self.classes.update(c.strip().lstrip(".") for c in classes.split() if c.strip())
        if validators is None:
            self.validators: list[Any] = []
        elif callable(validators) or isinstance(validators, Validator):
            self.validators = [validators]
        else:
            self.validators = list(validators)
        self.layout_hint_h = 28

    @property
    def value(self) -> str:
        return self._text

    @value.setter
    def value(self, text: str) -> None:
        prev = self._text
        self.load_text(text)
        if text != prev:
            self._emit_change()

    @property
    def cursor_position(self) -> int:
        return self.cursor

    @cursor_position.setter
    def cursor_position(self, pos: int) -> None:
        self.cursor = max(0, min(len(self._text), int(pos)))
        self._clear_selection()
        if self.app is not None:
            self.app.request_draw()

    def validate(self, value: str) -> ValidationResult:
        if not value and self.valid_empty:
            return ValidationResult.success()
        failures: list[Any] = []
        for v in self.validators:
            res = v.validate(value) if isinstance(v, Validator) else v(value)
            if isinstance(res, ValidationResult):
                failures.extend(res.failures)
            elif res is False:
                failures.append("Validation failed")
        return ValidationResult(failures)

    @property
    def is_valid(self) -> bool:
        return self.validate(self._text).is_valid

    def blur(self):
        was = self.focused
        res = super().blur()
        if was:
            self.post_message(self.Blurred(self, self._text, self.validate(self._text)))
        return res

    def action_select_all(self) -> None:
        """Textual Input.action_select_all analogue."""
        self.select_all()

    def action_home(self) -> None:
        self.cursor_position = 0

    def action_end(self) -> None:
        self.cursor_position = len(self._text)

    def action_cursor_left_word(self) -> None:
        self.action_cursor_word_left()

    def action_cursor_right_word(self) -> None:
        self.action_cursor_word_right()

    def action_delete_left_word(self) -> None:
        self.action_delete_word_left()

    def action_delete_right_word(self) -> None:
        self.action_delete_word_right()

    def action_submit(self) -> None:
        self._emit_submit()

    def delete_selection(self) -> None:
        sel = self._selection_range()
        if sel is not None:
            self.delete(sel[0], sel[1])

    def insert_text_at_cursor(self, text: str) -> None:
        self.insert(text)

    def restricted(self) -> bool:
        return False

    def validate_selection(self, selection: Any) -> Any:
        return selection
