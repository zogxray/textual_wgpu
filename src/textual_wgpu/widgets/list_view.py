"""ListView / ListItem — scrollable widget-item list (Textual ListView subset)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget
from .scroll_view import ScrollView
from .static import Static

if TYPE_CHECKING:
    from PIL import ImageDraw


class ListItem(Widget):
    """Row container inside a ``ListView``."""

    highlighted = reactive(False, repaint=True)
    ALLOW_SELECT = False

    def __init__(
        self,
        *children: Widget | str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self._children: list[Widget] = [
            Static(c, markup=markup) if isinstance(c, str) else c
            for c in children
        ]
        self.styles.setdefault("layout", "horizontal")

    @property
    def children(self) -> list[Widget]:
        return self._children

    def watch_highlighted(self, _old: bool, new: bool) -> None:
        self.set_class("--highlight", bool(new))

    def on_enter_or_leave(self, _event: Any = None) -> None:
        self.refresh()

    def get_content_height(self) -> int | None:
        if not self._children:
            return cell_height(13) + 6
        heights = [
            (c.get_content_height() or (cell_height(13) + 4))
            for c in self._children
        ]
        return max(cell_height(13) + 6, max(heights) + 4)

    def render_lines(self) -> list[Strip]:
        t = self.theme
        fg = t.rgba("background") if self.highlighted else (
            t.rgba("muted") if self.disabled else t.rgba("foreground")
        )
        bg = t.rgba("accent") if self.highlighted else None
        if not self._children:
            return [Strip([Segment(" ", fg, bg)])]
        parts: list[str] = []
        for c in self._children:
            if hasattr(c, "render_lines"):
                lines = c.render_lines()
                if lines:
                    parts.append(" ".join(s.plain() for s in lines))
        text = " ".join(p for p in parts if p) or " "
        return [Strip([Segment(f" {text}", fg, bg)])]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        t = self.theme
        if self.highlighted and not self.disabled:
            draw.rectangle(
                [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
                fill=t.rgba("accent"),
            )
        from ..compositor import paint_strips

        paint_strips(
            draw,
            self.rect,
            self.render_lines(),
            theme=t,
            styles=self.styles or {},
            font_size=13,
            pad_x=4,
            pad_y=2,
            fill_bg=False,
        )


class ListView(ScrollView):
    """Keyboard & pointer selectable list of ``ListItem`` widgets."""

    class Highlighted(Message):
        """Posted when the highlighted item changes."""

        def __init__(self, list_view: ListView, item: ListItem | None) -> None:
            super().__init__()
            self.list_view = list_view
            self.control = list_view
            self.item = item

    class Selected(Message):
        """Posted when a ``ListItem`` is activated (Enter / click)."""

        def __init__(self, list_view: ListView, item: ListItem, index: int) -> None:
            super().__init__()
            self.list_view = list_view
            self.control = list_view
            self.item = item
            self.index = index

    index = reactive(None, repaint=True)
    can_focus = True
    ALLOW_SELECT = False

    def __init__(
        self,
        *children: ListItem,
        initial_index: int | None = 0,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id)
        self.name = name
        self.disabled = bool(disabled)
        if classes:
            self.classes.update(c.strip().lstrip(".") for c in classes.split() if c.strip())
        self._children: list[Widget] = list(children)
        self._item_heights: list[int] = []
        self._sync_initial_index(initial_index)

    def _sync_initial_index(self, initial_index: int | None) -> None:
        items = self._items
        if initial_index is None or not items:
            self.index = None
        else:
            self.index = max(0, min(len(items) - 1, int(initial_index)))
        self._apply_highlight_flags()

    @property
    def children(self) -> list[Widget]:
        return self._children

    @property
    def _items(self) -> list[ListItem]:
        return [c for c in self._children if isinstance(c, ListItem)]

    @property
    def highlighted_child(self) -> ListItem | None:
        items = self._items
        if self.index is None or not (0 <= self.index < len(items)):
            return None
        return items[self.index]

    def _apply_highlight_flags(self) -> None:
        items = self._items
        for i, item in enumerate(items):
            item.highlighted = (self.index == i and not item.disabled)

    def watch_index(self, _old: int | None, new: int | None) -> None:
        items = self._items
        if new is not None and items:
            clamped = max(0, min(len(items) - 1, int(new)))
            if clamped != new:
                self.index = clamped
                return
        self._apply_highlight_flags()
        if self.index is not None:
            self.ensure_visible_line(self.index)
        self.post_message(self.Highlighted(self, self.highlighted_child))

    def append(self, item: ListItem) -> ListView:
        self.mount_child(item)
        if self.index is None and len(self._items) == 1:
            self.index = 0
        self._apply_highlight_flags()
        return self

    def extend(self, items: Iterable[ListItem]) -> ListView:
        item_list = list(items)
        if item_list:
            self.mount_all(item_list)
            if self.index is None and self._items:
                self.index = 0
            self._apply_highlight_flags()
        return self

    def insert(self, index: int, items: Iterable[ListItem]) -> ListView:
        before = self._items[index] if 0 <= index < len(self._items) else None
        for item in items:
            self.mount_child(item, before=before)
        self._apply_highlight_flags()
        return self

    def pop(self, index: int | None = None) -> ListItem:
        items = self._items
        idx = (len(items) - 1) if index is None else int(index)
        target = items[idx]
        target.remove()
        self._apply_highlight_flags()
        return target

    def validate_index(self, index: int | None) -> int | None:
        items = self._items
        if index is None or not items:
            return None
        return max(0, min(len(items) - 1, int(index)))

    def clear(self) -> ListView:
        self.remove_children()
        self.index = None
        self.scroll_y = 0
        self.refresh(layout=True)
        return self

    def remove_items(self, indices: Iterable[int]) -> ListView:
        to_drop = {int(i) for i in indices}
        items = self._items
        for i in sorted(to_drop, reverse=True):
            if 0 <= i < len(items):
                items[i].remove()
        remaining = self._items
        if not remaining:
            self.index = None
        elif self.index is not None:
            self.index = max(0, min(len(remaining) - 1, self.index))
        self._apply_highlight_flags()
        return self

    def scroll_to_item(self, item: ListItem) -> None:
        items = self._items
        if item in items:
            self.ensure_visible_line(items.index(item))
            self.refresh()

    def get_content_height(self) -> int:
        return len(self._items)

    def _row_height(self) -> int:
        return self.LINE_H + 6

    def _visible_lines(self) -> int:
        return max(1, (self.content_region().h - 4) // self._row_height())

    def layout_children(self) -> None:
        region = self.content_region()
        rh = self._row_height()
        items = self._items
        for i, item in enumerate(items):
            if i < self.scroll_y or i >= self.scroll_y + self._visible_lines() + 1:
                item.layout(Rect(0, 0, 0, 0))
            else:
                y = region.y + 2 + (i - self.scroll_y) * rh
                item.layout(Rect(region.x, y, region.w, rh))
        self._layout_scrollbar()

    def _move(self, delta: int) -> bool:
        items = self._items
        if not items or self.disabled:
            return False
        cur = 0 if self.index is None else self.index
        step = 1 if delta >= 0 else -1
        target = cur
        for _ in range(abs(delta)):
            probe = target + step
            while 0 <= probe < len(items) and items[probe].disabled:
                probe += step
            if not (0 <= probe < len(items)):
                break
            target = probe
        if target != self.index:
            self.index = target
            self.refresh()
        return True

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_select_cursor(self) -> None:
        item = self.highlighted_child
        if item is None or item.disabled or self.disabled or self.index is None:
            return
        self.post_message(self.Selected(self, item, self.index))

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key == "up":
            return self._move(-1)
        if key == "down":
            return self._move(1)
        if key == "pageup":
            return self._move(-self._visible_lines())
        if key == "pagedown":
            return self._move(self._visible_lines())
        if key == "home":
            for i, it in enumerate(self._items):
                if not it.disabled:
                    self.index = i
                    self.refresh()
                    return True
        if key == "end":
            items = self._items
            for i in range(len(items) - 1, -1, -1):
                if not items[i].disabled:
                    self.index = i
                    self.refresh()
                    return True
        if key in ("enter", "return"):
            if self.highlighted_child is not None and not self.highlighted_child.disabled:
                self.action_select_cursor()
                return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        out = list(super().hits())
        region = self.content_region()
        rh = self._row_height()
        items = self._items
        vis = self._visible_lines()
        for i in range(self.scroll_y, min(len(items), self.scroll_y + vis + 1)):
            item = items[i]
            if item.disabled or self.disabled:
                continue
            y = region.y + 2 + (i - self.scroll_y) * rh
            box = Rect(region.x, y, region.w, rh)

            def _pick(_x: int, _y: int, idx: int = i, target: ListItem = item) -> None:
                self.focus()
                self.index = idx
                self.post_message(self.Selected(self, target, idx))
                self.refresh()

            out.append((box, _pick))
        return out

    def render_lines(self) -> list[Strip]:
        items = self._items
        if not items:
            return [Strip([Segment("(empty)", self.theme.rgba("muted"))])]
        out: list[Strip] = []
        for item in items:
            out.extend(item.render_lines())
        return out

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=self.theme.rgba("panel"),
        )
        self.layout_children()
        items = self._items
        vis = self._visible_lines()
        for i in range(self.scroll_y, min(len(items), self.scroll_y + vis + 1)):
            items[i].paint(draw)
        self._layout_scrollbar()
        self._scrollbar.paint(draw)
