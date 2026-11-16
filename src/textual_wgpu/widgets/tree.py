"""Tree — expandable node list (Textual Tree subset)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from ..fonts import cell_width
from ..geometry import Rect
from ..message import Message
from ..strip import Segment, Strip
from .scroll_view import ScrollView

if TYPE_CHECKING:
    from PIL import ImageDraw

T = TypeVar("T")


class TreeNode(Generic[T]):
    """One node in a ``Tree``."""

    def __init__(
        self,
        tree: Tree[T],
        parent: TreeNode[T] | None,
        label: str,
        data: T | None = None,
    ) -> None:
        self._tree = tree
        self.parent = parent
        self.label = str(label)
        self.data = data
        self._children: list[TreeNode[T]] = []
        self._expanded = False
        self.allow_expand = True

    @property
    def children(self) -> list[TreeNode[T]]:
        return self._children

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    @property
    def is_root(self) -> bool:
        return self.parent is None

    def add(self, label: str, data: T | None = None) -> TreeNode[T]:
        """Add an expandable child and return it."""
        node = TreeNode(self._tree, self, label, data)
        self._children.append(node)
        self._tree._invalidate()
        return node

    def add_leaf(self, label: str, data: T | None = None) -> TreeNode[T]:
        """Add a non-expandable leaf."""
        node = self.add(label, data)
        node.allow_expand = False
        return node

    def set_label(self, label: str) -> TreeNode[T]:
        self.label = str(label)
        self._tree._invalidate()
        return self

    def remove_children(self) -> TreeNode[T]:
        self._children.clear()
        self._tree._invalidate()
        return self

    def remove(self) -> None:
        if self.parent is not None and self in self.parent._children:
            self.parent._children.remove(self)
            self._tree._invalidate()

    def expand(self) -> None:
        if not self.allow_expand:
            return
        if not self._expanded:
            self._expanded = True
            self._tree.post_message(Tree.NodeExpanded(self))
            self._tree._invalidate()

    def collapse(self) -> None:
        if self._expanded:
            self._expanded = False
            self._tree.post_message(Tree.NodeCollapsed(self))
            self._tree._invalidate()

    def toggle(self) -> None:
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def expand_all(self) -> None:
        self.expand()
        for c in self._children:
            c.expand_all()

    def collapse_all(self) -> None:
        for c in self._children:
            c.collapse_all()
        self.collapse()


class Tree(ScrollView, Generic[T]):
    """Hierarchical list with expand/collapse (Textual ``Tree`` subset)."""

    class NodeHighlighted(Message):
        def __init__(self, node: TreeNode[Any]) -> None:
            super().__init__()
            self.node = node

    class NodeSelected(Message):
        def __init__(self, node: TreeNode[Any]) -> None:
            super().__init__()
            self.node = node

    class NodeExpanded(Message):
        def __init__(self, node: TreeNode[Any]) -> None:
            super().__init__()
            self.node = node

    class NodeCollapsed(Message):
        def __init__(self, node: TreeNode[Any]) -> None:
            super().__init__()
            self.node = node

    def __init__(
        self,
        label: str = "root",
        data: T | None = None,
        *,
        id: str | None = None,
        show_root: bool = True,
    ) -> None:
        super().__init__(id=id)
        self.show_root = show_root
        self.root = TreeNode(self, None, label, data)
        self.root._expanded = True
        self.cursor_line = 0
        self._lines: list[tuple[TreeNode[T], int]] = []  # node, depth
        self._invalidate()

    def clear(self) -> Tree[T]:
        self.root._children.clear()
        self.cursor_line = 0
        self._invalidate()
        return self

    def reset(self, label: str = "root", data: T | None = None) -> Tree[T]:
        self.root = TreeNode(self, None, label, data)
        self.root._expanded = True
        self.cursor_line = 0
        self._invalidate()
        return self

    def unselect(self) -> None:
        self.cursor_line = -1
        self.refresh()

    def move_cursor(self, node: TreeNode[T] | None, animate: bool = True) -> None:
        _ = animate
        self.select_node(node)

    def move_cursor_to_line(self, line: int, animate: bool = True) -> None:
        _ = animate
        if 0 <= line < len(self._lines):
            self.cursor_line = line
            self.ensure_visible_line(line)
            self.refresh()

    def scroll_to_line(self, line: int, animate: bool = True) -> None:
        _ = animate
        self.ensure_visible_line(line)
        self.refresh()

    def scroll_to_node(self, node: TreeNode[T], animate: bool = True) -> None:
        _ = animate
        for i, (n, _) in enumerate(self._lines):
            if n is node:
                self.ensure_visible_line(i)
                self.refresh()
                return

    def process_label(self, label: Any) -> str:
        return str(label)

    def render_label(self, node: TreeNode[T], base_style: Any = None, style: Any = None) -> str:
        _ = base_style, style
        return node.label

    def get_label_width(self, node: TreeNode[T]) -> int:
        return len(node.label)

    def refresh_line(self, y: int) -> None:
        _ = y
        self.refresh()

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        _ = y_start, line_count
        self.refresh()

    def action_cursor_next_sibling(self) -> None:
        cur = self.cursor_node
        if cur and cur.parent:
            sibs = cur.parent.children
            idx = sibs.index(cur)
            if idx + 1 < len(sibs):
                self.select_node(sibs[idx + 1])

    def action_cursor_previous_sibling(self) -> None:
        cur = self.cursor_node
        if cur and cur.parent:
            sibs = cur.parent.children
            idx = sibs.index(cur)
            if idx > 0:
                self.select_node(sibs[idx - 1])

    def action_cursor_parent_next_sibling(self) -> None:
        cur = self.cursor_node
        if cur and cur.parent and cur.parent.parent:
            sibs = cur.parent.parent.children
            idx = sibs.index(cur.parent)
            if idx + 1 < len(sibs):
                self.select_node(sibs[idx + 1])

    def validate_cursor_line(self, line: int) -> int:
        return max(-1, min(len(self._lines) - 1, int(line)))

    def validate_guide_depth(self, depth: int) -> int:
        return max(1, int(depth))

    def watch_cursor_line(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_guide_depth(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_hover_line(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_show_root(self, _o: Any, _n: Any) -> None:
        self._invalidate()

    def _invalidate(self) -> None:
        self._lines = list(self._iter_visible())
        if self.cursor_line >= len(self._lines):
            self.cursor_line = max(0, len(self._lines) - 1)
        self.refresh()

    def _iter_visible(self) -> Iterator[tuple[TreeNode[T], int]]:
        def walk(node: TreeNode[T], depth: int) -> Iterator[tuple[TreeNode[T], int]]:
            if depth > 0 or self.show_root:
                yield node, depth if self.show_root else max(0, depth - 1)
            if node.is_expanded:
                for child in node.children:
                    yield from walk(child, depth + 1)

        yield from walk(self.root, 0)

    def get_content_height(self) -> int:
        return len(self._lines)

    @property
    def cursor_node(self) -> TreeNode[T] | None:
        if not self._lines:
            return None
        i = max(0, min(self.cursor_line, len(self._lines) - 1))
        return self._lines[i][0]

    def select_node(self, node: TreeNode[T] | None) -> None:
        if node is None:
            return
        for i, (n, _) in enumerate(self._lines):
            if n is node:
                self.cursor_line = i
                self.ensure_visible_line(i)
                self.post_message(self.NodeHighlighted(node))
                self.refresh()
                return

    def get_node_at_line(self, line: int) -> TreeNode[T] | None:
        if 0 <= line < len(self._lines):
            return self._lines[line][0]
        return None

    def get_node_by_id(self, node_id: int) -> TreeNode[T]:
        def _find(node: TreeNode[T]) -> TreeNode[T] | None:
            if getattr(node, "id", id(node)) == node_id:
                return node
            for c in node.children:
                found = _find(c)
                if found is not None:
                    return found
            return None

        res = _find(self.root)
        if res is None:
            raise KeyError(f"No node with id {node_id!r}")
        return res

    def add_json(self, json_data: Any, node: TreeNode[T] | None = None) -> None:
        import json

        data = json.loads(json_data) if isinstance(json_data, str) else json_data
        target = node or self.root

        def _populate(parent: TreeNode[Any], obj: Any) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    child = parent.add(str(k))
                    _populate(child, v)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    child = parent.add(f"[{i}]")
                    _populate(child, v)
            else:
                parent.add_leaf(repr(obj))

        _populate(target, data)

    def action_cursor_up(self) -> None:
        self.on_key("up")

    def action_cursor_down(self) -> None:
        self.on_key("down")

    def action_cursor_parent(self) -> None:
        cur = self.cursor_node
        if cur is not None and cur.parent is not None:
            self.select_node(cur.parent)

    def action_select_cursor(self) -> None:
        self.on_key("enter")

    def action_toggle_expand_all(self) -> None:
        node = self.cursor_node or self.root
        if node.is_expanded:
            node.collapse_all()
        else:
            node.expand_all()

    def action_toggle_node(self) -> None:
        node = self.cursor_node
        if node is not None and node.allow_expand:
            node.toggle()

    def on_key(self, key: str) -> bool:
        if key == "up":
            if self.cursor_line > 0:
                self.cursor_line -= 1
                self.ensure_visible_line(self.cursor_line)
                node = self.cursor_node
                if node is not None:
                    self.post_message(self.NodeHighlighted(node))
                self.refresh()
            return True
        if key == "down":
            if self.cursor_line < len(self._lines) - 1:
                self.cursor_line += 1
                self.ensure_visible_line(self.cursor_line)
                node = self.cursor_node
                if node is not None:
                    self.post_message(self.NodeHighlighted(node))
                self.refresh()
            return True
        if key in ("left",):
            node = self.cursor_node
            if node is not None and node.is_expanded:
                node.collapse()
            elif node is not None and node.parent is not None:
                self.select_node(node.parent)
            return True
        if key in ("right",):
            node = self.cursor_node
            if node is not None and node.allow_expand and not node.is_expanded:
                node.expand()
            elif node is not None and node.is_expanded and node.children:
                self.select_node(node.children[0])
            return True
        if key in ("enter", "return", "space"):
            node = self.cursor_node
            if node is None:
                return True
            if key == "space" and node.allow_expand:
                node.toggle()
            else:
                self.post_message(self.NodeSelected(node))
            return True
        return super().on_key(key)

    def _guide(self, node: TreeNode[T], depth: int) -> str:
        if not node.allow_expand:
            return "  " * depth + "  "
        mark = "▼ " if node.is_expanded else "▶ "
        return "  " * depth + mark

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self._lines:
            return [Strip([Segment("(empty)", t.rgba("muted"))])]
        out: list[Strip] = []
        accent = t.rgba("accent")
        on_hi = t.rgba("background")
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        for i, (node, depth) in enumerate(self._lines):
            hi = i == self.cursor_line
            prefix = self._guide(node, depth)
            label = node.label
            if hi:
                out.append(Strip([
                    Segment(prefix, on_hi, accent),
                    Segment(label, on_hi, accent),
                ]))
            else:
                out.append(Strip([
                    Segment(prefix, muted),
                    Segment(label, fg),
                ]))
        return out

    def paint_row_backgrounds(self, draw: ImageDraw.ImageDraw, region: Rect) -> None:
        if not self._lines:
            return
        y = self.line_y(self.cursor_line, region)
        if y is None:
            return
        t = self.theme
        hy1 = min(y + self.LINE_H - 1, region.y1 - 1)
        if hy1 >= y:
            draw.rectangle(
                [region.x, y, region.x1 - 1, hy1],
                fill=t.rgba("accent"),
            )

    def hits(self) -> list:
        out = super().hits()
        region = self.content_region()
        vis = self._visible_lines()
        start = self.scroll_y
        end = min(len(self._lines), start + vis + 1)
        cw = cell_width(self.FONT_SIZE)

        def make_pick(idx: int) -> Callable[[int, int], None]:
            def _pick(_x: int, _y: int, j: int = idx) -> None:
                self.cursor_line = j
                self.ensure_visible_line(j)
                node = self.cursor_node
                if node is not None:
                    self.post_message(self.NodeHighlighted(node))
                    # Click on guide zone toggles; else select.
                    guide_w = 4 + len(self._guide(node, self._lines[j][1])) * cw
                    if node.allow_expand and _x < region.x + guide_w:
                        node.toggle()
                    else:
                        self.post_message(self.NodeSelected(node))
                self.refresh()

            return _pick

        for i in range(start, end):
            y = self.line_y(i, region)
            if y is None or y >= region.y1:
                break
            out.append((Rect(region.x, y, region.w, self.LINE_H), make_pick(i)))
        return out
