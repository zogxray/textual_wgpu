"""Tabs / Tab — horizontal tab strip via Strip compositor."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget


class Tab(Widget):
    """One tab entry (Textual ``Tab(label, id=...)``)."""

    class TabMessage(Message):
        def __init__(self, tab: Tab) -> None:
            super().__init__()
            self.tab = tab
            self.control = tab

    class Clicked(TabMessage):
        pass

    class Disabled(TabMessage):
        pass

    class Enabled(TabMessage):
        pass

    class Relabelled(TabMessage):
        pass

    label = reactive("", repaint=True)

    def __init__(
        self,
        label: str,
        *,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.label = str(label)
        self.layout_hint_rows = 1

    def update(self, label: str) -> None:
        self.label = str(label)
        self.post_message(self.Relabelled(self))
        self.refresh(layout=True)


class Tabs(Widget):
    """One-row tab bar. Active tab uses accent background cells."""

    class TabMessage(Message):
        def __init__(self, tabs: Tabs, tab: Any) -> None:
            super().__init__()
            self.tabs = tabs
            self.control = tabs
            self.tab = tab

    class TabActivated(Message):
        def __init__(self, tabs: Tabs, index: int, name: Any) -> None:
            super().__init__()
            self.tabs = tabs
            self.index = index
            self.tab = name

    class TabDisabled(TabMessage):
        pass

    class TabEnabled(TabMessage):
        pass

    class TabHidden(TabMessage):
        pass

    class TabShown(TabMessage):
        pass

    class TabError(TabMessage):
        pass

    class Cleared(Message):
        def __init__(self, tabs: Tabs) -> None:
            super().__init__()
            self.tabs = tabs

    active = reactive(0, repaint=True)
    can_focus = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        *tabs: Tab | str | Sequence[Tab | str],
        id: str | None = None,
        active: int | str = 0,
        classes: str | None = None,
        disabled: bool = False,
        on_change: Callable[[int, str], None] | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        items: list[Tab | str] = []
        if len(tabs) == 1 and isinstance(tabs[0], (list, tuple)):
            items = list(tabs[0])
        else:
            items = list(tabs)  # type: ignore[arg-type]
        self._tab_objs: list[Tab] = [
            t if isinstance(t, Tab) else Tab(str(t)) for t in items
        ]
        self._tabs: list[str] = [t.label for t in self._tab_objs]
        self.on_change = on_change
        if isinstance(active, str):
            idx = self._index_of_id(active)
            self.active = idx if idx is not None else 0
        else:
            self.active = max(0, min(int(active), max(0, len(self._tabs) - 1)))
        self.layout_hint_h = cell_height(self.RENDER_FONT_SIZE) + 8
        self.layout_hint_rows = 1

    def _index_of_id(self, tab_id: str) -> int | None:
        for i, t in enumerate(self._tab_objs):
            if t.id == tab_id:
                return i
        return None

    @property
    def tab_names(self) -> list[str]:
        return list(self._tabs)

    @property
    def active_tab(self) -> Tab | None:
        if 0 <= self.active < len(self._tab_objs):
            return self._tab_objs[self.active]
        return None

    def set_tabs(self, tabs: Iterable[Tab | str], *, active: int | None = None) -> None:
        self._tab_objs = [t if isinstance(t, Tab) else Tab(str(t)) for t in tabs]
        self._tabs = [t.label for t in self._tab_objs]
        if active is not None:
            self.active = active
        else:
            self.active = max(0, min(self.active, max(0, len(self._tabs) - 1)))
        self.refresh(layout=True)

    def add_tab(self, tab: Tab | str) -> Tab:
        obj = tab if isinstance(tab, Tab) else Tab(str(tab))
        self._tab_objs.append(obj)
        self._tabs.append(obj.label)
        self.refresh(layout=True)
        return obj

    def get_tab(self, tab_id: str) -> Tab:
        idx = self._index_of_id(tab_id)
        if idx is None:
            raise KeyError(f"No tab with id {tab_id!r}")
        return self._tab_objs[idx]

    def disable(self, tab_id: str) -> Tabs:
        t = self.get_tab(tab_id)
        t.disabled = True
        self.post_message(self.TabDisabled(self, t))
        self.refresh()
        return self

    def enable(self, tab_id: str) -> Tabs:
        t = self.get_tab(tab_id)
        t.disabled = False
        self.post_message(self.TabEnabled(self, t))
        self.refresh()
        return self

    def hide(self, tab_id: str) -> Tabs:
        t = self.get_tab(tab_id)
        t.styles["display"] = "none"
        self.post_message(self.TabHidden(self, t))
        self.refresh(layout=True)
        return self

    def show(self, tab_id: str) -> Tabs:
        t = self.get_tab(tab_id)
        t.styles["display"] = "block"
        self.post_message(self.TabShown(self, t))
        self.refresh(layout=True)
        return self

    def remove_tab(self, tab_id: str) -> Tabs:
        idx = self._index_of_id(tab_id)
        if idx is not None:
            self._tab_objs.pop(idx)
            self._tabs.pop(idx)
            self.active = max(0, min(self.active, max(0, len(self._tabs) - 1)))
            self.refresh(layout=True)
        return self

    def validate_active(self, active: int | str) -> int:
        if isinstance(active, str):
            idx = self._index_of_id(active)
            return idx if idx is not None else 0
        return max(0, min(int(active), max(0, len(self._tabs) - 1)))

    def watch_active(self, _old: int, _new: int) -> None:
        self.refresh()

    def action_next_tab(self) -> None:
        if self._tabs:
            self.activate((self.active + 1) % len(self._tabs))

    def action_previous_tab(self) -> None:
        if self._tabs:
            self.activate((self.active - 1) % len(self._tabs))

    def clear(self) -> None:
        self._tab_objs.clear()
        self._tabs.clear()
        self.active = 0
        self.post_message(self.Cleared(self))
        self.refresh(layout=True)

    def activate(self, index: int | str) -> None:
        if not self._tabs:
            return
        if isinstance(index, str):
            resolved = self._index_of_id(index)
            if resolved is None:
                return
            index = resolved
        index = max(0, min(len(self._tabs) - 1, int(index)))
        if index == self.active or self._tab_objs[index].disabled:
            return
        self.active = index
        name = self._tabs[index]
        if self.on_change is not None:
            self.on_change(index, name)
        self.post_message(self.TabActivated(self, index, name))

    def on_key(self, key: str) -> bool:
        if not self._tabs or self.disabled:
            return False
        if key == "left":
            self.activate((self.active - 1) % len(self._tabs))
            return True
        if key == "right":
            self.activate((self.active + 1) % len(self._tabs))
            return True
        if key in ("home",):
            self.activate(0)
            return True
        if key in ("end",):
            self.activate(len(self._tabs) - 1)
            return True
        return False

    def _tab_spans(self) -> list[tuple[int, int, int]]:
        """(index, start_col, width_cols) for hit testing."""
        spans: list[tuple[int, int, int]] = []
        col = 0
        for i, name in enumerate(self._tabs):
            w = len(name) + 2  # space padding
            spans.append((i, col, w))
            col += w + 1  # gap
        return spans

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2 or not self._tabs:
            return []
        cw = cell_width(self.RENDER_FONT_SIZE)
        pad_x = 4
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        for i, start, width in self._tab_spans():
            if self._tab_objs[i].disabled:
                continue
            r = Rect(
                self.rect.x + pad_x + start * cw,
                self.rect.y,
                width * cw,
                self.rect.h,
            )

            def _pick(_x: int, _y: int, idx: int = i) -> None:
                self.focus()
                self.activate(idx)

            out.append((r, _pick))
        return out

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self._tabs:
            return [Strip.from_text("(no tabs)", t.rgba("muted"))]
        segs: list[Segment] = []
        for i, name in enumerate(self._tabs):
            if i:
                segs.append(Segment(" ", t.rgba("muted")))
            label = f" {name} "
            if self._tab_objs[i].disabled:
                segs.append(Segment(label, t.rgba("muted"), t.rgba("panel")))
            elif i == self.active:
                segs.append(Segment(label, t.rgba("background"), t.rgba("accent")))
            else:
                segs.append(Segment(label, t.rgba("foreground"), t.rgba("panel")))
        return [Strip(segs)]
