"""SelectionList — multi-select OptionList (Textual SelectionList subset)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..message import Message
from ..strip import Segment, Strip
from .option_list import Option, OptionList


@dataclass
class Selection:
    """One selectable item (Textual ``Selection(prompt, value, initial_state=False)``)."""

    prompt: str
    value: Any
    initial_state: bool = False
    id: str | None = None
    disabled: bool = False


class SelectionList(OptionList):
    """Multi-selection list built on ``OptionList`` with ``[✓]`` / ``[ ]`` markers."""

    class SelectionMessage(Message):
        def __init__(self, selection_list: SelectionList, index: int) -> None:
            super().__init__()
            self.selection_list = selection_list
            self.control = selection_list
            self.index = index

    class SelectionHighlighted(SelectionMessage):
        """Posted when the highlighted selection changes."""

    class SelectionToggled(SelectionMessage):
        """Posted when an item is toggled."""

    class SelectedChanged(Message):
        """Posted whenever the set of selected values changes."""

        def __init__(self, selection_list: SelectionList) -> None:
            super().__init__()
            self.selection_list = selection_list
            self.control = selection_list

    def __init__(
        self,
        *selections: Selection | tuple[str, Any] | tuple[str, Any, bool],
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        compact: bool = False,
    ) -> None:
        super().__init__(id=id, click_activates=True)
        self.name = name
        self.disabled = bool(disabled)
        self.compact = bool(compact)
        if classes:
            self.classes.update(c.strip().lstrip(".") for c in classes.split() if c.strip())
        self._selected_indices: set[int] = set()
        self._values: list[Any] = []
        if selections:
            self.add_options(selections)

    def _coerce_selection(
        self,
        item: Selection | tuple[str, Any] | tuple[str, Any, bool],
    ) -> Selection:
        if isinstance(item, Selection):
            return item
        if isinstance(item, tuple):
            if len(item) == 2:
                return Selection(str(item[0]), item[1], False)
            if len(item) >= 3:
                return Selection(str(item[0]), item[1], bool(item[2]))
        raise TypeError(f"Invalid selection item: {item!r}")

    def add_option(
        self,
        item: Selection | tuple[str, Any] | tuple[str, Any, bool],
    ) -> SelectionList:
        sel = self._coerce_selection(item)
        idx = len(self._options)
        self._options.append(
            Option(
                label=str(sel.prompt),
                id=sel.id,
                data=sel.value,
                disabled=sel.disabled,
            )
        )
        self._values.append(sel.value)
        if sel.initial_state:
            self._selected_indices.add(idx)
        self.refresh()
        return self

    def add_options(
        self,
        items: Iterable[Selection | tuple[str, Any] | tuple[str, Any, bool]],
    ) -> SelectionList:
        for item in items:
            self.add_option(item)
        return self

    def clear_options(self) -> None:
        super().clear_options()
        self._values.clear()
        self._selected_indices.clear()

    @property
    def selected(self) -> list[Any]:
        """Selected values in option order (Textual ``SelectionList.selected``)."""
        return [
            self._values[i]
            for i in range(len(self._values))
            if i in self._selected_indices
        ]

    @property
    def selected_values(self) -> list[Any]:
        return self.selected

    def _index_for_value(self, value: Any) -> int | None:
        for i, v in enumerate(self._values):
            if v == value:
                return i
        return None

    def _toggle_index(self, idx: int, *, state: bool | None = None) -> bool:
        if idx < 0 or idx >= len(self._options):
            return False
        if self._options[idx].disabled or self.disabled:
            return False
        was = idx in self._selected_indices
        want = (not was) if state is None else bool(state)
        if want == was:
            return False
        if want:
            self._selected_indices.add(idx)
        else:
            self._selected_indices.discard(idx)
        self.post_message(self.SelectionToggled(self, idx))
        self.post_message(self.SelectedChanged(self))
        self.refresh()
        return True

    def toggle(self, value: Any) -> SelectionList:
        idx = self._index_for_value(value)
        if idx is not None:
            self._toggle_index(idx, state=None)
        return self

    def select(self, value: Any) -> SelectionList:
        idx = self._index_for_value(value)
        if idx is not None:
            self._toggle_index(idx, state=True)
        return self

    def deselect(self, value: Any) -> SelectionList:
        idx = self._index_for_value(value)
        if idx is not None:
            self._toggle_index(idx, state=False)
        return self

    def select_all(self) -> SelectionList:
        changed = False
        for i, opt in enumerate(self._options):
            if not opt.disabled and i not in self._selected_indices:
                self._selected_indices.add(i)
                changed = True
        if changed:
            self.post_message(self.SelectedChanged(self))
            self.refresh()
        return self

    def deselect_all(self) -> SelectionList:
        if not self._selected_indices:
            return self
        self._selected_indices.clear()
        self.post_message(self.SelectedChanged(self))
        self.refresh()
        return self

    def toggle_all(self) -> SelectionList:
        changed = False
        for i, opt in enumerate(self._options):
            if opt.disabled:
                continue
            if i in self._selected_indices:
                self._selected_indices.discard(i)
            else:
                self._selected_indices.add(i)
            changed = True
        if changed:
            self.post_message(self.SelectedChanged(self))
            self.refresh()
        return self

    def _emit_highlight(self) -> None:
        super()._emit_highlight()
        if self._options:
            self.post_message(self.SelectionHighlighted(self, self.highlighted))

    def activate_highlighted(self) -> None:
        opt = self.highlighted_option
        if opt is None or opt.disabled or self.disabled:
            return
        self._toggle_index(self.highlighted, state=None)
        super().activate_highlighted()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key == "space":
            self.activate_highlighted()
            return True
        return super().on_key(key)

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self._options:
            return [Strip([Segment("(empty)", t.rgba("muted"))])]
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        accent = t.rgba("accent")
        on_hi = t.rgba("background")
        out: list[Strip] = []
        for i, opt in enumerate(self._options):
            hi = i == self.highlighted and not opt.disabled
            checked = i in self._selected_indices
            box = "[✓] " if checked else "[ ] "
            if hi:
                box_seg = Segment(box, on_hi, accent, bold=checked)
                lbl_seg = Segment(opt.label, on_hi, accent)
            elif opt.disabled:
                box_seg = Segment(box, muted)
                lbl_seg = Segment(opt.label, muted)
            else:
                box_seg = Segment(box, accent if checked else muted, bold=checked)
                lbl_seg = Segment(opt.label, fg)
            out.append(Strip([box_seg, lbl_seg]))
        return out
