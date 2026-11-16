"""TabbedContent — Tabs header + ContentSwitcher body."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..widget import Widget
from .content_switcher import ContentSwitcher
from .tabs import Tabs

if TYPE_CHECKING:
    from PIL import ImageDraw


class TabPane(Widget):
    """One pane inside ``TabbedContent``. ``title`` is the tab label."""

    class TabPaneMessage(Message):
        def __init__(self, tab_pane: TabPane) -> None:
            super().__init__()
            self.tab_pane = tab_pane
            self.control = tab_pane

    class Disabled(TabPaneMessage):
        pass

    class Enabled(TabPaneMessage):
        pass

    class Focused(TabPaneMessage):
        pass

    def __init__(
        self,
        title: str,
        *children: Widget,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.pane_title = str(title)
        self._children = list(children)
        self.styles.setdefault("layout", "vertical")

    @property
    def children(self) -> list[Widget]:
        return list(self._children)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        for child in self._children:
            if (child.styles or {}).get("display") == "none":
                continue
            child.paint(draw)


class TabbedContent(Widget):
    """Tab strip + switching panes. Thin UX shell over ``Tabs`` + ``ContentSwitcher``."""

    class TabActivated(Message):
        def __init__(self, tabbed: TabbedContent, index: int, name: str) -> None:
            super().__init__()
            self.tabbed_content = tabbed
            self.index = index
            self.tab = name

    class Cleared(Message):
        def __init__(self, tabbed: TabbedContent) -> None:
            super().__init__()
            self.tabbed_content = tabbed

    active = reactive(0, repaint=True, layout=True)

    def __init__(
        self,
        *panes: TabPane,
        id: str | None = None,
        initial: str | int | None = None,
    ) -> None:
        super().__init__(id=id)
        self._panes = list(panes)
        # Ensure each pane has a stable id for the switcher.
        for i, pane in enumerate(self._panes):
            if pane.id is None:
                pane.id = f"tab-{i}"
        titles = [p.pane_title for p in self._panes]
        start = 0
        if isinstance(initial, int):
            start = max(0, min(initial, max(0, len(self._panes) - 1)))
        elif isinstance(initial, str):
            for i, p in enumerate(self._panes):
                if p.id == initial or p.pane_title == initial:
                    start = i
                    break
        self.active = start
        initial_id = self._panes[start].id if self._panes else None
        self._tabs = Tabs(titles, active=start, on_change=self._on_tab)
        for i, p in enumerate(self._panes):
            if i < len(self._tabs._tab_objs):
                self._tabs._tab_objs[i].id = p.id
        self._switcher = ContentSwitcher(*self._panes, initial=initial_id)
        self._tabs.layout_hint_h = cell_height(Tabs.RENDER_FONT_SIZE) + 8

    @property
    def children(self) -> list[Widget]:
        return [self._tabs, self._switcher]

    def _on_tab(self, index: int, name: str) -> None:
        self.active = index
        if 0 <= index < len(self._panes):
            self._switcher.show(self._panes[index].id or f"tab-{index}")
        self.post_message(self.TabActivated(self, index, name))

    def activate(self, index: int) -> None:
        self._tabs.activate(index)

    def _sync_tabs(self) -> None:
        self._tabs.set_tabs([p.pane_title for p in self._panes], active=self.active if self._panes else 0)
        for i, p in enumerate(self._panes):
            if i < len(self._tabs._tab_objs):
                self._tabs._tab_objs[i].id = p.id

    def add_pane(self, pane: TabPane, *, before: str | TabPane | None = None, after: str | TabPane | None = None) -> None:
        if pane.id is None:
            pane.id = f"tab-{len(self._panes)}"
        idx = len(self._panes)
        if before is not None:
            bid = before.id if isinstance(before, TabPane) else str(before)
            for i, p in enumerate(self._panes):
                if p.id == bid:
                    idx = i
                    break
        elif after is not None:
            aid = after.id if isinstance(after, TabPane) else str(after)
            for i, p in enumerate(self._panes):
                if p.id == aid:
                    idx = i + 1
                    break
        self._panes.insert(idx, pane)
        self._switcher.mount_child(pane)
        self._sync_tabs()
        self.refresh(layout=True)

    def remove_pane(self, pane_id: str) -> None:
        for i, p in enumerate(list(self._panes)):
            if p.id == pane_id:
                self._panes.pop(i)
                p.remove()
                break
        self._sync_tabs()
        self.refresh(layout=True)

    def clear_panes(self) -> None:
        for p in list(self._panes):
            p.remove()
        self._panes.clear()
        self._tabs.clear()
        self.post_message(self.Cleared(self))
        self.refresh(layout=True)

    def get_pane(self, pane_id: str) -> TabPane:
        for p in self._panes:
            if p.id == pane_id:
                return p
        raise KeyError(f"No pane {pane_id!r}")

    def get_tab(self, tab_id: str):
        return self._tabs.get_tab(tab_id)

    def disable_tab(self, tab_id: str) -> None:
        self._tabs.disable(tab_id)

    def enable_tab(self, tab_id: str) -> None:
        self._tabs.enable(tab_id)

    def hide_tab(self, tab_id: str) -> None:
        self.disable_tab(tab_id)

    def show_tab(self, tab_id: str) -> None:
        self.enable_tab(tab_id)

    def layout_children(self) -> None:
        th = self._tabs.layout_hint_h or 28
        self._tabs.layout(Rect(self.rect.x, self.rect.y, self.rect.w, th))
        body = Rect(
            self.rect.x,
            self.rect.y + th,
            self.rect.w,
            max(0, self.rect.h - th),
        )
        self._switcher.layout(body)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        self._tabs.paint(draw)
        self._switcher.paint(draw)

    def hits(self):
        out = list(self._tabs.hits())
        out.extend(self._switcher.hits())
        return out

    def on_key(self, key: str) -> bool:
        if self._tabs.on_key(key):
            return True
        return self._switcher.on_key(key)
