"""Click-to-focus, Panel focus, wheel routing, and mouse button events in ModeApp."""

from __future__ import annotations

from textual_wgpu import events
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import (
    DataTable,
    Horizontal,
    KeyBar,
    ModeBar,
    Option,
    OptionList,
    Panel,
    RichText,
    StatusBar,
    TextArea,
    Tree,
    VerticalScroll,
)


def _theme() -> Theme:
    return Theme.system()


def test_click_to_focus_scroll_richtext_panel_and_lists():
    class Body(Screen):
        DEFAULT_CSS = """
        #main { height: 1fr; }
        #left-box { width: 28; border: round $primary; }
        #mid-box { width: 1fr; border: round $primary; }
        #right-box { width: 32; border: round $primary; }
        """

        def compose(self):
            yield ModeBar("m", [("m", "Mode")])
            ol = OptionList(Option("One", id="1"), id="list")
            left = Panel(ol, title="LIST", id="left-box")

            body = RichText("\n".join(f"Line {i}" for i in range(60)), id="body", can_focus=False)
            scroll = VerticalScroll(body, id="scroll")
            mid = Panel(scroll, title="CHAT", id="mid-box")

            notes = TextArea("\n".join(f"Note {i}" for i in range(40)), id="notes")
            right = Panel(notes, title="NOTES", id="right-box")

            yield Horizontal(left, mid, right, id="main")
            yield KeyBar([("Tab", "Panel", lambda: self.app.focus_next() if self.app else None)])
            yield StatusBar("ok")

    app = ModeApp([("m", "Mode", Body)], theme=_theme(), start="m")
    app._pixel_size = (1000, 600)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 1000, 600))

    ol = screen.query_one("#list", OptionList)
    scroll = screen.query_one("#scroll", VerticalScroll)
    body = screen.query_one("#body", RichText)
    mid_box = screen.query_one("#mid-box", Panel)
    notes = screen.query_one("#notes", TextArea)
    keybar = screen.query_one(KeyBar)

    # Start focused on notes
    notes.focus()
    assert app._focus is notes

    # Click inside RichText body inside VerticalScroll -> focuses VerticalScroll
    app.on_pointer(body.rect.x + 20, body.rect.y + 20, 1)
    app.on_pointer_up(body.rect.x + 20, body.rect.y + 20, 1)
    assert app._focus is scroll

    # Click back on notes, then click CHAT panel title bar -> focuses VerticalScroll
    notes.focus()
    app.on_pointer(mid_box.rect.x + 20, mid_box.rect.y + 6, 1)
    app.on_pointer_up(mid_box.rect.x + 20, mid_box.rect.y + 6, 1)
    assert app._focus is scroll

    # Click empty space below OptionList items -> focuses OptionList
    app.on_pointer(ol.rect.x + 15, ol.rect.y1 - 10, 1)
    app.on_pointer_up(ol.rect.x + 15, ol.rect.y1 - 10, 1)
    assert app._focus is ol

    # Click KeyBar Tab chip -> cycles focus rather than stealing focus to KeyBar
    kb_rect, _ = keybar.hits()[0]
    app.on_pointer(kb_rect.x + 4, kb_rect.y + 4, 1)
    assert app._focus is scroll


def test_wheel_routes_to_vertical_scroll_and_textarea_not_inner_richtext():
    class Body(Screen):
        DEFAULT_CSS = """
        #main { height: 1fr; }
        #mid-box { width: 1fr; }
        #notes-box { width: 40; }
        """

        def compose(self):
            body = RichText("\n".join(f"Line {i}" for i in range(80)), id="body", can_focus=False)
            scroll = VerticalScroll(body, id="scroll")
            notes = TextArea("\n".join(f"Note {i}" for i in range(80)), id="notes")
            yield Horizontal(Panel(scroll, title="CHAT", id="mid-box"), Panel(notes, title="NOTES", id="notes-box"), id="main")

    app = ModeApp([("m", "Mode", Body)], theme=_theme(), start="m")
    app._pixel_size = (1000, 600)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 1000, 600))

    scroll = screen.query_one("#scroll", VerticalScroll)
    body = screen.query_one("#body", RichText)
    notes = screen.query_one("#notes", TextArea)

    # Wheel over RichText inside VerticalScroll scrolls VerticalScroll, not inner RichText
    app.on_wheel(body.rect.x + 20, body.rect.y + 20, 0.0, 120.0)
    assert scroll.scroll_py > 0
    assert body.scroll_y == 0

    prev_py = scroll.scroll_py
    # Wheel over TextArea scrolls TextArea, not VerticalScroll
    app.on_wheel(notes.rect.x + 20, notes.rect.y + 20, 0.0, 120.0)
    assert notes.scroll_y > 0
    assert scroll.scroll_py == prev_py


def test_click_to_focus_datatable_tree_and_right_click_events():
    seen: list[tuple[str, int]] = []

    class Body(Screen):
        def compose(self):
            tbl = DataTable(id="tbl")
            tbl.add_columns("A", "B")
            tbl.add_rows([("1", "2"), ("3", "4")])
            tree = Tree("root", id="tree")
            tree.root.add_leaf("leaf")
            tree.root.expand()
            yield Horizontal(tbl, tree, id="main")

        def on_mouse_down(self, ev: events.MouseDown) -> None:
            seen.append(("down", ev.button))

        def on_mouse_up(self, ev: events.MouseUp) -> None:
            seen.append(("up", ev.button))

    app = ModeApp([("m", "Mode", Body)], theme=_theme(), start="m")
    app._pixel_size = (800, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 800, 400))

    tbl = screen.query_one("#tbl", DataTable)
    tree = screen.query_one("#tree", Tree)

    app.on_pointer(tbl.rect.x + 10, tbl.rect.y + 25, 1)
    assert app._focus is tbl
    app.on_pointer(tree.rect.x + 10, tree.rect.y + 10, 1)
    assert app._focus is tree

    app.on_pointer(tree.rect.x + 10, tree.rect.y + 10, 2)
    app.on_pointer_up(tree.rect.x + 10, tree.rect.y + 10, 2)
    assert ("down", 2) in seen
    assert ("up", 2) in seen
