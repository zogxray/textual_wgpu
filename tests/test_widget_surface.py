"""DOM query, Tree, DirectoryTree, DataTable, ScrollView, Strip."""

from __future__ import annotations

from pathlib import Path

from textual_wgpu.dom import DOMQuery, matches_selector, query, query_one
from textual_wgpu.geometry import Rect
from textual_wgpu.screen import Screen
from textual_wgpu.strip import Strip
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import (
    DataTable,
    DirectoryTree,
    OptionList,
    Panel,
    ScrollView,
    Static,
    Tree,
)


def _app():
    class A:
        theme = Theme.system()
        _layout_size = (0, 0)

        def request_draw(self) -> None:
            return None

    return A()


def test_strip_plain():
    s = Strip.from_text("hello")
    assert s.plain() == "hello"
    assert len(s) == 5


def test_query_id_type_descendant():
    class Body(Screen):
        def compose(self):
            yield Panel(OptionList(id="jobs"), id="list-box")
            yield Static("x", id="status")

    screen = Body()
    screen.build()
    # Wire parents (mount normally does this).
    for w in screen.walk_descendants(include_self=True):
        for c in w.children:
            c.parent = w

    assert query_one(screen, "#jobs").id == "jobs"
    assert query_one(screen, "OptionList").id == "jobs"
    assert query_one(screen, "OptionList#jobs").id == "jobs"
    assert len(query(screen, "Panel")) == 1
    assert len(screen.query("Static#status")) == 1
    q = screen.query("Panel OptionList")
    assert len(q) == 1
    assert isinstance(q, DOMQuery)


def test_matches_selector():
    w = Static("a", id="s")
    assert matches_selector(w, "#s")
    assert matches_selector(w, "Static")
    assert matches_selector(w, "Static#s")
    assert not matches_selector(w, "#other")


def test_tree_expand_and_messages():
    events = []

    class Body(Screen):
        def compose(self):
            yield Tree("root", id="tree")

    screen = Body()
    screen.build()
    tree = screen.query_one("#tree", Tree)
    tree.app = _app()
    tree.parent = screen

    from textual_wgpu.on import on

    # Attach handler via post capture
    orig = tree.post_message

    def capture(msg):
        events.append(type(msg).__name__)
        orig(msg)

    tree.post_message = capture  # type: ignore[method-assign]

    child = tree.root.add("child")
    leaf = child.add_leaf("leaf")
    assert not child.is_expanded
    child.expand()
    assert child.is_expanded
    assert "NodeExpanded" in events
    tree.select_node(leaf)
    assert tree.cursor_node is leaf
    assert "NodeHighlighted" in events


def test_directory_tree_lists_tmpdir(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("b")

    dt = DirectoryTree(tmp_path, id="dir")
    dt.app = _app()
    # Root children loaded
    names = [c.label for c in dt.root.children]
    assert any("a.txt" in n for n in names)
    assert any(n.endswith("/") or "sub" in n for n in names)
    sub = next(c for c in dt.root.children if c.data and c.data.name == "sub")
    assert sub.allow_expand
    dt._load_children(sub)
    sub.expand()
    assert any(c.data and c.data.name == "b.txt" for c in sub.children)


def test_data_table_rows_and_cursor():
    from PIL import Image, ImageDraw

    table = DataTable(id="t")
    table.app = _app()
    table.add_columns("A", "B")
    table.add_row("1", "2", key="r1")
    table.add_row("3", "4", key="r2")
    assert table.row_count == 2
    assert table.get_row(0) == ["1", "2"]
    table.cursor_row = 1
    table.cursor_column = 1
    assert table.cursor_coordinate == (1, 1)
    assert table.get_cell(1, 1) == "4"
    table.layout(Rect(0, 0, 400, 200))
    img = Image.new("RGBA", (400, 200))
    table.paint(ImageDraw.Draw(img))


def test_scroll_view_scroll_clamp():
    class Tiny(ScrollView):
        def get_content_height(self) -> int:
            return 10

    sv = Tiny(id="sv")
    sv.app = _app()
    sv.layout(Rect(0, 0, 100, 36))  # ~2 lines
    sv.scroll(100)
    assert sv.scroll_y == sv._max_scroll()
    sv.scroll_home()
    assert sv.scroll_y == 0
