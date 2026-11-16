"""Select — dropdown / pick-list control via Strip + ask_pick."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget


class Select(Widget):
    """Collapsed value row; Enter/click opens ``ask_pick`` (or cycles if no modal)."""

    class Changed(Message):
        def __init__(self, select: Select, value: Any) -> None:
            super().__init__()
            self.select = select
            self.value = value

    value = reactive(None, repaint=True)
    prompt = reactive("Select…", repaint=True)
    can_focus = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        options: Sequence[tuple[Any, str]] | None = None,
        *,
        id: str | None = None,
        value: Any = None,
        prompt: str = "Select…",
        allow_blank: bool = False,
        disabled: bool = False,
        on_change: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(id=id)
        self._options: list[tuple[Any, str]] = [(v, str(lbl)) for v, lbl in (options or ())]
        self.value = value
        self.prompt = prompt
        self.allow_blank = allow_blank
        self.disabled = disabled
        self.on_change = on_change
        self.layout_hint_h = cell_height(self.RENDER_FONT_SIZE) + 8
        self.layout_hint_cells = 24

    @classmethod
    def from_values(cls, values: Sequence[Any], **kwargs: Any) -> Select:
        return cls([(v, str(v)) for v in values], **kwargs)

    def is_blank(self) -> bool:
        return self.value is None

    def clear(self) -> None:
        self.set_value(None)
        self.refresh()

    def action_show_overlay(self) -> None:
        self.open_picker()

    def set_options(
        self,
        options: Sequence[tuple[Any, str]],
        *,
        value: Any = None,
    ) -> None:
        self._options = [(v, str(lbl)) for v, lbl in options]
        if value is not None or self.value not in {v for v, _ in self._options}:
            self.value = value
        self.refresh()

    def _label_for(self, value: Any) -> str:
        for v, lbl in self._options:
            if v == value:
                return lbl
        return str(self.prompt)

    def set_value(self, value: Any) -> None:
        if value == self.value:
            return
        self.value = value
        if self.on_change is not None:
            self.on_change(value)
        self.post_message(self.Changed(self, value))

    def open_picker(self) -> None:
        if self.disabled or not self._options:
            return
        app = self.app
        opts = [(str(i), lbl) for i, (_v, lbl) in enumerate(self._options)]
        current = None
        for i, (v, _) in enumerate(self._options):
            if v == self.value:
                current = str(i)
                break

        def _done(picked: str | None) -> None:
            if picked is None:
                return
            try:
                idx = int(picked)
            except ValueError:
                return
            if 0 <= idx < len(self._options):
                self.set_value(self._options[idx][0])

        if app is not None and hasattr(app, "ask_pick"):
            app.ask_pick(str(self.prompt), opts, _done, current=current)
            return
        # Headless / no modal: cycle forward.
        if not self._options:
            return
        idx = 0
        for i, (v, _) in enumerate(self._options):
            if v == self.value:
                idx = (i + 1) % len(self._options)
                break
        self.set_value(self._options[idx][0])

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key in ("enter", "return", "space"):
            self.open_picker()
            return True
        if key in ("down", "right") and self._options:
            idx = 0
            for i, (v, _) in enumerate(self._options):
                if v == self.value:
                    idx = (i + 1) % len(self._options)
                    break
            self.set_value(self._options[idx][0])
            return True
        if key in ("up", "left") and self._options:
            idx = len(self._options) - 1
            for i, (v, _) in enumerate(self._options):
                if v == self.value:
                    idx = (i - 1) % len(self._options)
                    break
            self.set_value(self._options[idx][0])
            return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2:
            return []

        def _click(_x: int, _y: int) -> None:
            self.focus()
            self.open_picker()

        return [(self.rect, _click)]

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if self.disabled:
            fg, bg = t.rgba("muted"), t.rgba("boost")
        else:
            fg, bg = t.rgba("foreground"), t.rgba("panel")
        label = self._label_for(self.value) if self.value is not None else str(self.prompt)
        # Truncate + chevron.
        text = f" {label} "
        chev = " ▾"
        return [Strip([
            Segment(text, fg, bg),
            Segment(chev, t.rgba("muted"), bg),
        ])]
