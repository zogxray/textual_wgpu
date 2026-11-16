"""ContentSwitcher — show exactly one child at a time (by id)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


class ContentSwitcher(Widget):
    """Visibility shell: ``visible`` holds the id of the active child."""

    class Changed(Message):
        def __init__(self, switcher: ContentSwitcher, visible: str | None) -> None:
            super().__init__()
            self.switcher = switcher
            self.visible = visible

    visible = reactive(None, repaint=True, layout=True)

    def __init__(
        self,
        *children: Widget,
        initial: str | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._children = list(children)
        if initial is not None:
            self.visible = initial
        elif self._children:
            first = self._children[0]
            if first.id is None:
                first.id = "_0"
            self.visible = first.id
        self._apply_visibility()

    @property
    def children(self) -> list[Widget]:
        return list(self._children)

    def watch_visible(self, _value: str | None) -> None:
        self._apply_visibility()

    def watch_current(self, _value: str | None) -> None:
        self._apply_visibility()

    def add_content(self, widget: Widget, *, id: str | None = None) -> ContentSwitcher:
        if id is not None:
            widget.id = id
        self.mount_child(widget)
        self._apply_visibility()
        return self

    def _apply_visibility(self) -> None:
        target = self.visible
        for i, child in enumerate(self._children):
            cid = child.id if child.id is not None else f"_{i}"
            show = target is not None and cid == target
            child.styles["display"] = "block" if show else "none"

    def show(self, child_id: str) -> None:
        if child_id == self.visible:
            return
        self.visible = child_id
        self._apply_visibility()
        self.post_message(self.Changed(self, child_id))
        self.refresh(layout=True)

    def current(self) -> Widget | None:
        target = self.visible
        for i, child in enumerate(self._children):
            cid = child.id if child.id is not None else f"_{i}"
            if cid == target:
                return child
        return None

    def layout_children(self) -> None:
        for child in self._children:
            if (child.styles or {}).get("display") == "none":
                child.layout(Rect(0, 0, 0, 0))
                continue
            child.layout(self.rect)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        cur = self.current()
        if cur is not None:
            cur.paint(draw)

    def hits(self):
        cur = self.current()
        return list(cur.hits()) if cur is not None else []

    def on_key(self, key: str) -> bool:
        cur = self.current()
        return cur.on_key(key) if cur is not None else False
