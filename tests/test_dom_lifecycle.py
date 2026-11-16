"""DOM lifecycle + CSS auto-layout (fr / content-size / no ModeBar convention)."""

from __future__ import annotations

from textual_wgpu.fonts import cell_height, rows_to_px
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import (
    Grid,
    Horizontal,
    KeyBar,
    ModeBar,
    Panel,
    Static,
    StatusBar,
    Vertical,
)


def _app_theme():
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


class _ChromeScreen(Screen):
    """Typical app chrome: ModeBar + body(1fr) + KeyBar + Status — CSS only."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #status { height: 1; }
    """

    def compose(self):
        yield ModeBar("x", [("x", "X"), ("y", "Y")], id="modes")
        yield Panel(Static("body"), title="BODY", id="main")
        yield KeyBar([("Q", "Quit", None)], id="keys")
        yield StatusBar("ok", id="status")


def test_screen_auto_layout_fr_chrome():
    theme = _app_theme()
    app = ModeApp([("x", "X", _ChromeScreen)], theme=theme, start="x")
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 900, 600))

    modes = screen.query_one("#modes")
    main = screen.query_one("#main")
    keys = screen.query_one("#keys")
    status = screen.query_one("#status")

    # Chrome rows: CSS height:1 → padded band (layout_band_h), not bare cell.
    assert modes.rect.h == modes.layout_band_h == cell_height(13) + 8
    assert status.rect.h == status.layout_band_h == cell_height(13) + 8
    assert keys.rect.h == keys.layout_band_h == cell_height(13) + 8
    # Body takes remaining via 1fr
    assert main.layout_height_fr == 1.0
    assert main.rect.h > 400
    assert modes.rect.y == 0
    assert main.rect.y == modes.rect.y1
    assert keys.rect.y == main.rect.y1
    assert status.rect.y == keys.rect.y1
    assert status.rect.y1 == 600


def test_vertical_fr_and_fixed():
    class Body(Screen):
        DEFAULT_CSS = """
        #top { height: 2; }
        #mid { height: 1fr; }
        #bot { height: 3; }
        """

        def compose(self):
            yield Vertical(
                Static("t", id="top"),
                Static("m", id="mid"),
                Static("b", id="bot"),
                id="col",
            )

    screen = Body()
    app = ModeApp([("x", "X", lambda: screen)], theme=_app_theme(), start="x")
    # Mount via ensure
    app._factories["x"] = lambda: screen
    app._screens.clear()
    mounted = app._ensure_screen()
    mounted.layout(Rect(0, 0, 400, 500))
    col = mounted.query_one("#col")
    col.layout(Rect(0, 0, 400, 500))
    top = mounted.query_one("#top")
    mid = mounted.query_one("#mid")
    bot = mounted.query_one("#bot")
    assert top.rect.h == rows_to_px(2)
    assert bot.rect.h == rows_to_px(3)
    assert mid.rect.h == 500 - top.rect.h - bot.rect.h
    assert mid.layout_height_fr == 1.0


def test_width_fr_weights():
    class Body(Screen):
        DEFAULT_CSS = """
        #a { width: 1fr; }
        #b { width: 2fr; }
        """

        def compose(self):
            yield Horizontal(
                Static("a", id="a"),
                Static("b", id="b"),
                id="row",
            )

    screen = Body()
    screen.build()
    screen.app = type("A", (), {
        "theme": _app_theme(),
        "stylesheet": None,
        "_extra_stylesheet": None,
        "CSS": "",
        "_focus": None,
        "_hover": None,
        "_layout_size": (0, 0),
        "request_draw": lambda self: None,
    })()
    screen.apply_styles()
    row = screen.query_one("#row")
    row.layout(Rect(0, 0, 300, 40))
    a = screen.query_one("#a")
    b = screen.query_one("#b")
    # 1fr : 2fr → ~100 : 200
    assert abs(a.rect.w - 100) <= 2
    assert abs(b.rect.w - 200) <= 2


def test_content_size_auto():
    class Body(Screen):
        DEFAULT_CSS = """
        #label { height: auto; }
        #fill { height: 1fr; }
        """

        def compose(self):
            yield Vertical(
                Static("one\ntwo\nthree", id="label"),
                Static("fill", id="fill"),
                id="col",
            )

    screen = Body()
    screen.build()
    screen.app = type("A", (), {
        "theme": _app_theme(),
        "stylesheet": None,
        "_extra_stylesheet": None,
        "CSS": "",
        "_focus": None,
        "_hover": None,
        "_layout_size": (0, 0),
        "request_draw": lambda self: None,
    })()
    # Need parents for walk
    for w in screen.walk_descendants(include_self=True):
        for c in w.children:
            c.parent = w
    screen.mount(screen.app)
    screen.layout(Rect(0, 0, 400, 400))
    col = screen.query_one("#col")
    col.layout(Rect(0, 0, 400, 400))
    label = screen.query_one("#label")
    fill = screen.query_one("#fill")
    assert label.rect.h == rows_to_px(3)
    assert fill.rect.h == 400 - label.rect.h


def test_mount_child_and_remove():
    class Body(Screen):
        def compose(self):
            yield Vertical(id="col")

    screen = Body()
    app = ModeApp([("x", "X", lambda: screen)], theme=_app_theme(), start="x")
    app._factories["x"] = lambda: screen
    app._screens.clear()
    s = app._ensure_screen()
    col = s.query_one("#col", Vertical)
    a = Static("a", id="a")
    b = Static("b", id="b")
    col.mount_child(a)
    col.mount_child(b)
    assert [c.id for c in col.children] == ["a", "b"]
    assert a.is_mounted and a.app is app
    a.remove()
    assert [c.id for c in col.children] == ["b"]
    assert not a.is_mounted


def test_recompose_rebuilds_tree():
    class Body(Screen):
        def __init__(self) -> None:
            super().__init__()
            self.flag = False

        def compose(self):
            if self.flag:
                yield Static("new", id="n")
            else:
                yield Static("old", id="o")

    screen = Body()
    app = ModeApp([("x", "X", lambda: screen)], theme=_app_theme(), start="x")
    app._factories["x"] = lambda: screen
    app._screens.clear()
    s = app._ensure_screen()
    assert s.query_one("#o").id == "o"
    s.flag = True
    s.recompose()
    assert s.query_one("#n").id == "n"
    try:
        s.query_one("#o")
        raise AssertionError("old node should be gone")
    except KeyError:
        pass


def test_grid_layout():
    class Body(Screen):
        def compose(self):
            yield Grid(
                Static("1", id="c1"),
                Static("2", id="c2"),
                Static("3", id="c3"),
                Static("4", id="c4"),
                id="g",
                columns=2,
            )

    screen = Body()
    screen.build()
    screen.app = type("A", (), {
        "theme": _app_theme(),
        "stylesheet": None,
        "_extra_stylesheet": None,
        "CSS": "",
        "_focus": None,
        "_hover": None,
        "_layout_size": (0, 0),
        "request_draw": lambda self: None,
    })()
    for w in screen.walk_descendants(include_self=True):
        for c in w.children:
            c.parent = w
    screen.mount(screen.app)
    g = screen.query_one("#g")
    g.layout(Rect(0, 0, 200, 200))
    c1 = screen.query_one("#c1")
    c2 = screen.query_one("#c2")
    c3 = screen.query_one("#c3")
    assert c1.rect.x == 0 and c1.rect.y == 0
    assert c2.rect.x == 100 and c2.rect.y == 0
    assert c3.rect.x == 0 and c3.rect.y == 100
