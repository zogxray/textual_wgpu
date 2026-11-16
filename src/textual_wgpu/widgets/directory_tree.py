"""DirectoryTree — filesystem browser on top of ``Tree``."""

from __future__ import annotations

from pathlib import Path

from ..message import Message
from .tree import Tree, TreeNode


class DirectoryTree(Tree[Path]):
    """Load directory entries lazily as nodes expand (Textual DirectoryTree subset)."""

    PATH = Path

    class FileSelected(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    class DirectorySelected(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(
        self,
        path: str | Path,
        *,
        id: str | None = None,
        show_root: bool = True,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        super().__init__(
            label=str(self.path.name or self.path),
            data=self.path,
            id=id,
            show_root=show_root,
        )
        self.root.allow_expand = True
        self._loaded: set[int] = set()
        self._load_children(self.root)
        self.root.expand()

    def filter_paths(self, paths: list[Path]) -> list[Path]:
        return [p for p in paths if not p.name.startswith(".")]

    def clear_node(self, node: TreeNode[Path]) -> None:
        node.remove_children()
        self._loaded.discard(id(node))

    def reload_node(self, node: TreeNode[Path]) -> None:
        self.clear_node(node)
        self._load_children(node)
        self._invalidate()

    def reload(self) -> None:
        self.reload_node(self.root)

    def reset_node(self, node: TreeNode[Path]) -> None:
        self.reload_node(node)

    def validate_path(self, path: str | Path) -> Path:
        return Path(path).expanduser().resolve()

    def watch_path(self, _o: Any, _n: Any) -> None:
        self.reload()

    def _load_children(self, node: TreeNode[Path]) -> None:
        key = id(node)
        if key in self._loaded:
            return
        self._loaded.add(key)
        folder = node.data
        if folder is None or not folder.is_dir():
            return
        try:
            kids = sorted(
                self.filter_paths(list(folder.iterdir())),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            kids = []
        # Clear placeholder children if re-loading (first load only via set).
        if node.children:
            return
        for p in kids:
            if p.is_dir():
                child = node.add(f"{p.name}/", p)
                child.allow_expand = True
            else:
                node.add_leaf(p.name, p)

    def _prepare_expand(self, node: TreeNode[Path] | None) -> None:
        if node is not None and node.allow_expand:
            self._load_children(node)

    def on_key(self, key: str) -> bool:
        node = self.cursor_node
        if key in ("right", "space"):
            self._prepare_expand(node)
        if key in ("enter", "return"):
            if node is not None and node.data is not None:
                path = node.data
                if path.is_dir():
                    self._prepare_expand(node)
                    if not node.is_expanded:
                        node.expand()
                    self.post_message(self.DirectorySelected(path))
                else:
                    self.post_message(self.FileSelected(path))
                self.refresh()
                return True
        handled = super().on_key(key)
        # After Tree toggles expand, populate children.
        node = self.cursor_node
        if node is not None and node.is_expanded:
            before = len(node.children)
            self._load_children(node)
            if len(node.children) != before:
                self._invalidate()
        return handled

    def hits(self) -> list:
        from collections.abc import Callable

        from ..geometry import Rect

        out = list(super(Tree, self).hits())  # scrollbar from ScrollView
        region = self.content_region()
        vis = self._visible_lines()
        start = self.scroll_y
        end = min(len(self._lines), start + vis + 1)

        def make_pick(idx: int) -> Callable[[int, int], None]:
            def _pick(_x: int, _y: int, j: int = idx) -> None:
                self.cursor_line = j
                self.ensure_visible_line(j)
                node = self.cursor_node
                if node is None:
                    return
                self.post_message(self.NodeHighlighted(node))
                guide_w = 24 + self._lines[j][1] * 16
                if node.allow_expand and _x < region.x + guide_w:
                    self._prepare_expand(node)
                    node.toggle()
                else:
                    if node.data is not None and node.data.is_dir():
                        self._prepare_expand(node)
                        self.post_message(self.DirectorySelected(node.data))
                    elif node.data is not None:
                        self.post_message(self.FileSelected(node.data))
                    self.post_message(self.NodeSelected(node))
                self.refresh()

            return _pick

        for i in range(start, end):
            row_i = i - start
            y = region.y + 2 + row_i * self.LINE_H
            if y >= region.y1:
                break
            out.append((Rect(region.x, y, region.w, self.LINE_H), make_pick(i)))
        return out
