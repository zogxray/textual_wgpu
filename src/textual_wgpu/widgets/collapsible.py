"""Collapsible — titled section that expands / collapses its children."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw

Hit = tuple[Rect, Callable[[int, int], None]]


class Collapsible(Widget):
    """Title bar + optional body. UX shell for nested settings / help blocks."""

    class Toggled(Message):
        def __init__(self, collapsible: Collapsible, collapsed: bool) -> None:
            super().__init__()
            self.collapsible = collapsible
            self.collapsed = collapsed

    class Collapsed(Message):
        def __init__(self, collapsible: Collapsible) -> None:
            super().__init__()
            self.collapsible = collapsible

    class Expanded(Message):
        def __init__(self, collapsible: Collapsible) -> None:
            super().__init__()
            self.collapsible = collapsible

    class Contents(Widget):
        """Collapsible body container."""

        def __init__(self, *children: Widget, id: str | None = None) -> None:
            super().__init__(id=id)
            self._children = list(children)

        @property
        def children(self) -> list[Widget]:
            return self._children

    title = reactive("Details", repaint=True)
    collapsed = reactive(False, repaint=True, layout=True)
    can_focus = True
    RENDER_FONT_SIZE = 13
    TITLE_H = 26

    def __init__(
        self,
        *children: Widget,
        title: str = "Details",
        collapsed: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._children = list(children)
        self.title = str(title)
        self.collapsed = bool(collapsed)
        self.layout_hint_h = self.TITLE_H

    @property
    def children(self) -> list[Widget]:
        return list(self._children)

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.post_message(self.Toggled(self, self.collapsed))
        self.post_message(self.Collapsed(self) if self.collapsed else self.Expanded(self))
        self.refresh(layout=True)

    def expand(self) -> None:
        if self.collapsed:
            self.toggle()

    def collapse(self) -> None:
        if not self.collapsed:
            self.toggle()

    def on_key(self, key: str) -> bool:
        if key in ("enter", "return", "space"):
            self.toggle()
            return True
        if key == "right" and self.collapsed:
            self.toggle()
            return True
        if key == "left" and not self.collapsed:
            self.toggle()
            return True
        if self.collapsed:
            return False
        return super().on_key(key)

    def _title_rect(self) -> Rect:
        return Rect(self.rect.x, self.rect.y, self.rect.w, min(self.TITLE_H, self.rect.h))

    def layout_children(self) -> None:
        if self.collapsed or not self._children:
            for child in self._children:
                child.layout(Rect(0, 0, 0, 0))
            return
        body = Rect(
            self.rect.x,
            self.rect.y + self.TITLE_H,
            self.rect.w,
            max(0, self.rect.h - self.TITLE_H),
        )
        if len(self._children) == 1:
            self._children[0].layout(body)
            return
        saved = self.rect
        self.rect = body
        try:
            from ..layout import layout_children as _layout

            _layout(self, self._children)
        finally:
            self.rect = saved

    def hits(self) -> list[Hit]:
        if self.rect.w < 2:
            return []

        def _toggle(_x: int, _y: int) -> None:
            self.focus()
            self.toggle()

        out: list[Hit] = [(self._title_rect(), _toggle)]
        if not self.collapsed:
            for child in self._children:
                out.extend(child.hits())
        return out

    def render_lines(self) -> list[Strip]:
        t = self.theme
        mark = "▶" if self.collapsed else "▼"
        label = f" {mark} {self.title} "
        return [Strip([Segment(label, t.rgba("foreground"), t.rgba("boost"))])]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_strips

        t = self.theme
        title = self._title_rect()
        draw.rectangle(
            [title.x, title.y, title.x1 - 1, title.y1 - 1],
            fill=t.rgba("boost"),
        )
        paint_strips(
            draw, title, self.render_lines(),
            theme=t, styles={}, font_size=self.RENDER_FONT_SIZE,
            pad_x=6, pad_y=4, fill_bg=False,
        )
        if self.collapsed:
            return
        for child in self._children:
            if (child.styles or {}).get("display") == "none":
                continue
            child.paint(draw)

    def get_content_height(self) -> int | None:
        if self.collapsed:
            return self.TITLE_H
        h = self.TITLE_H
        for child in self._children:
            ch = child.get_content_height()
            if ch is not None:
                h += int(ch)
            elif child.layout_hint_h:
                h += int(child.layout_hint_h)
            else:
                h += cell_height(self.RENDER_FONT_SIZE) * 2
        return h
