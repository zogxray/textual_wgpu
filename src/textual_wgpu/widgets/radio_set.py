"""RadioSet — mutually exclusive group of RadioButtons."""

from __future__ import annotations

from collections.abc import Callable

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..on import on
from ..widget import Widget
from .radio_button import RadioButton


class RadioSet(Widget):
    """Container enforcing single selection among child ``RadioButton``s."""

    class Changed(Message):
        """Posted when the selected radio button changes."""

        def __init__(self, radio_set: RadioSet, pressed: RadioButton, index: int = 0) -> None:
            super().__init__()
            self.radio_set = radio_set
            self.control = radio_set
            self.pressed = pressed
            self.index = index

    can_focus = True
    ALLOW_SELECT = False

    def __init__(
        self,
        *buttons: str | RadioButton,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: str | None = None,
        compact: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.tooltip = tooltip
        self.compact = bool(compact)
        self.styles.setdefault("layout", "vertical")
        self._children: list[Widget] = []
        self._selected_index: int = -1
        for item in buttons:
            btn = item if isinstance(item, RadioButton) else RadioButton(str(item), compact=self.compact)
            btn.can_focus = False  # RadioSet manages keyboard navigation across items
            if disabled:
                btn.disabled = True
            self._children.append(btn)
        # Ensure at most one initially pressed button.
        for i, b in enumerate(self._buttons):
            if b.value:
                if self._selected_index == -1:
                    self._selected_index = i
                else:
                    b.value = False
        self._sync_height_hint()

    @property
    def _buttons(self) -> list[RadioButton]:
        return [c for c in self._children if isinstance(c, RadioButton)]

    @property
    def children(self) -> list[Widget]:
        return self._children

    def _sync_height_hint(self) -> None:
        row_h = cell_height(13) + (2 if self.compact else 4)
        self.layout_hint_h = max(row_h, len(self._buttons) * row_h)

    @property
    def pressed_index(self) -> int:
        for i, b in enumerate(self._buttons):
            if b.value:
                return i
        return -1

    @property
    def pressed_button(self) -> RadioButton | None:
        for b in self._buttons:
            if b.value:
                return b
        return None

    def _select_button(self, target: RadioButton, *, emit: bool = True) -> None:
        buttons = self._buttons
        if target not in buttons or target.disabled or self.disabled:
            return
        idx = buttons.index(target)
        changed = (self._selected_index != idx) or not target.value
        for b in buttons:
            b.value = b is target
        self._selected_index = idx
        if emit and changed:
            self.post_message(self.Changed(self, target, idx))
        self.refresh()

    def _handle_button_toggle(self, button: RadioButton) -> None:
        self._select_button(button, emit=True)

    @on(RadioButton.Changed)
    def _on_radio_button_changed(self, event: RadioButton.Changed) -> None:
        event.stop()
        if event.value:
            self._select_button(event.radio_button, emit=True)
        else:
            # Do not allow deselecting the active button in a RadioSet.
            if event.radio_button is self.pressed_button:
                event.radio_button.value = True

    def _move(self, step: int) -> bool:
        buttons = self._buttons
        if not buttons or self.disabled:
            return False
        enabled_indices = [i for i, b in enumerate(buttons) if not b.disabled]
        if not enabled_indices:
            return False
        cur = self.pressed_index
        if cur not in enabled_indices:
            target_idx = enabled_indices[0] if step >= 0 else enabled_indices[-1]
        else:
            pos = enabled_indices.index(cur)
            target_idx = enabled_indices[(pos + step) % len(enabled_indices)]
        self._select_button(buttons[target_idx], emit=True)
        return True

    def action_next_button(self) -> None:
        self._move(1)

    def action_previous_button(self) -> None:
        self._move(-1)

    def action_toggle_button(self) -> None:
        if self.pressed_button is None:
            self._move(1)

    def watch__selected(self, _old: Any, _new: Any) -> None:
        self.refresh()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key in ("down", "right"):
            return self._move(1)
        if key in ("up", "left"):
            return self._move(-1)
        if key == "home":
            for b in self._buttons:
                if not b.disabled:
                    self._select_button(b, emit=True)
                    return True
        if key == "end":
            for b in reversed(self._buttons):
                if not b.disabled:
                    self._select_button(b, emit=True)
                    return True
        if key in ("space", "enter", "return"):
            if self.pressed_button is None:
                return self._move(1)
            return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        for btn in self._buttons:
            if btn.disabled or self.disabled:
                continue

            def _pick(_x: int, _y: int, target: RadioButton = btn) -> None:
                self.focus()
                self._select_button(target, emit=True)

            if btn.rect.w >= 2 and btn.rect.h >= 2:
                out.append((btn.rect, _pick))
        return out

    def get_content_height(self) -> int | None:
        row_h = cell_height(13) + (2 if self.compact else 4)
        return max(row_h, len(self._buttons) * row_h)
