"""ScrollView Content→Strip contract (Tree / DataTable / custom views)."""

from __future__ import annotations

from PIL import Image, ImageDraw

from textual_wgpu.geometry import Rect
from textual_wgpu.strip import Segment, Strip
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import DataTable, ScrollView, Tree


def _theme() -> Theme:
    return Theme.from_mapping({
        "background": "#1E1923",
        "foreground": "#F5F0E8",
        "accent": "#D4AD72",
        "muted": "#9A9088",
        "panel": "#2A2430",
        "boost": "#342C3C",
        "surface": "#241E2A",
        "primary": "#D4AD72",
    })


def _app():
    class A:
        theme = _theme()
        _layout_size = (0, 0)
        _focus = None

        def request_draw(self) -> None:
            return None

        def set_focus(self, w) -> None:
            self._focus = w

    return A()


def test_scrollview_prefers_render_lines():
    class Lines(ScrollView):
        def get_content_height(self) -> int:
            return 3

        def render_lines(self) -> list[Strip]:
            t = self.theme
            return [
                Strip([Segment(f"row{i}", t.rgba("foreground"))])
                for i in range(3)
            ]

    app = _app()
    v = Lines(id="v")
    v.mount(app)
    v.layout(Rect(0, 0, 200, 80))
    strips = v.render_lines()
    assert [s.plain() for s in strips] == ["row0", "row1", "row2"]
    img = Image.new("RGBA", (200, 80), (0, 0, 0, 255))
    v.paint(ImageDraw.Draw(img))
    assert img.getpixel((10, 10)) != (0, 0, 0, 255)


def test_tree_render_lines_and_cursor_band():
    app = _app()
    tree = Tree("root", id="t")
    tree.mount(app)
    a = tree.root.add("alpha")
    a.add_leaf("a1")
    tree.root.add_leaf("beta")
    tree.layout(Rect(0, 0, 300, 160))
    strips = tree.render_lines()
    plain = [s.plain() for s in strips]
    assert any("alpha" in p for p in plain)
    assert any("beta" in p for p in plain)
    # Cursor on first visible → accent bgcolor on strip
    tree.cursor_line = 0
    assert any(
        seg.bgcolor == app.theme.rgba("accent")
        for seg in tree.render_lines()[0].segments
    )
    img = Image.new("RGBA", (300, 160))
    tree.paint(ImageDraw.Draw(img))
    assert tree.hits()


def test_datatable_render_lines():
    app = _app()
    table = DataTable(id="dt")
    table.mount(app)
    table.add_columns("A", "B")
    table.add_row("1", "2")
    table.add_row("3", "4")
    table.layout(Rect(0, 0, 400, 200))
    strips = table.render_lines()
    # header + 2 rows
    assert len(strips) == 3
    assert "A" in strips[0].plain()
    assert "1" in strips[1].plain()
    table.cursor_row = 1
    assert any(
        seg.bgcolor == app.theme.rgba("accent")
        for seg in table.render_lines()[2].segments
    )
    img = Image.new("RGBA", (400, 200))
    table.paint(ImageDraw.Draw(img))
