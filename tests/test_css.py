"""TCSS engine: parse, cascade, combinators, dock, classes, transitions."""

from __future__ import annotations

import time

from textual_wgpu.css import Stylesheet, parse_size, resolve_color, tick_transitions
from textual_wgpu.geometry import Rect
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Horizontal, Panel, Static, Vertical


def _app(**kw):
    theme = kw.pop("theme", Theme.system())

    class A:
        stylesheet = None
        _extra_stylesheet = None
        CSS = ""
        _hover = None
        _focus = None

        def __init__(self) -> None:
            self.theme = theme
            for k, v in kw.items():
                setattr(self, k, v)

    return A()


def test_parse_size_units():
    assert parse_size("34").kind == "cells"
    assert parse_size("34").value == 34
    assert parse_size("48%").kind == "percent"
    assert parse_size("48%").value == 48
    assert parse_size("1fr").kind == "fr"
    assert parse_size("auto").kind == "auto"
    assert parse_size("200ms").kind == "ms"
    assert parse_size("0.5s").value == 500


def test_parse_rules_and_comma_selectors():
    sheet = Stylesheet.parse("""
    /* comment */
    #a, #b:focus-within { width: 34; border: round $primary; }
    #c { width: 48%; min-width: 36; }
    Panel { height: 1fr; }
    """)
    assert len(sheet.rules) == 3
    assert len(sheet.rules[0].selectors) == 2
    last = sheet.rules[0].selectors[1].parts[-1][1]
    assert last.pseudo == "focus-within"
    decls = {d.prop: d.value for d in sheet.rules[1].decls}
    assert decls["min-width"] == "36"


def test_combinators_child_and_descendant():
    sheet = Stylesheet.parse("""
    #main > Panel { width: 20; }
    #main Panel#deep { width: 40; }
    """)
    theme = Theme.system()

    class Body(Screen):
        DEFAULT_CSS = sheet.source

        def compose(self):
            inner = Panel(Static("d"), id="deep")
            mid = Vertical(inner, id="mid")
            yield Horizontal(Panel(Static("a"), id="a"), mid, id="main")

    screen = Body()
    screen.build()
    # parents
    for w in screen.walk_descendants(include_self=True):
        for c in w.children:
            c.parent = w
    screen.app = _app(theme=theme)
    screen.apply_styles()
    assert screen.query_one("#a").layout_hint_cells == 20  # direct child Panel
    assert screen.query_one("#deep").layout_hint_cells == 40  # descendant


def test_sibling_combinators():
    sheet = Stylesheet.parse("""
    #a + #b { width: 15; }
    #a ~ #c { width: 25; }
    """)

    class Body(Screen):
        DEFAULT_CSS = sheet.source

        def compose(self):
            yield Horizontal(
                Static("a", id="a"),
                Static("b", id="b"),
                Static("c", id="c"),
                id="row",
            )

    screen = Body()
    screen.build()
    for w in screen.walk_descendants(include_self=True):
        for c in w.children:
            c.parent = w
    screen.app = _app()
    screen.apply_styles()
    assert screen.query_one("#b").layout_hint_cells == 15
    assert screen.query_one("#c").layout_hint_cells == 25


def test_class_selector_and_important():
    class Body(Screen):
        DEFAULT_CSS = """
        .wide { width: 10; }
        #x.wide { width: 30 !important; }
        #x { width: 20; }
        """

        def compose(self):
            yield Static("x", id="x")

    screen = Body()
    screen.build()
    x = screen.query_one("#x")
    x.parent = screen
    screen.app = _app()
    screen.apply_styles()
    assert x.layout_hint_cells == 20
    x.add_class("wide")
    # add_class triggers refresh but need re-apply
    screen.apply_styles()
    assert x.layout_hint_cells == 30


def test_theme_vars():
    theme = Theme.from_mapping({"primary": "#AABBCC", "accent": "#112233", "muted": "#999999"})
    assert resolve_color("$primary", theme) == "#AABBCC"
    assert resolve_color("$text-muted", theme) == "#999999"
    assert resolve_color("transparent", theme) is None
    assert resolve_color("#FF00AA", theme) == "#FF00AA"


def test_apply_width_cells_percent_fr():
    theme = Theme.system()

    class Body(Screen):
        DEFAULT_CSS = """
        #left { width: 34; border: round $primary; }
        #mid { width: 1fr; border: round $primary; }
        #right { width: 38%; min-width: 40; border: round $accent; }
        """

        def compose(self):
            yield Horizontal(
                Panel(Static("L"), id="left"),
                Panel(Static("M"), id="mid"),
                Panel(Static("R"), id="right"),
                id="main",
            )

    screen = Body()
    screen.build()
    screen.app = _app(theme=theme)
    screen.apply_styles()
    left = screen.query_one("#left")
    mid = screen.query_one("#mid")
    right = screen.query_one("#right")
    assert left.layout_hint_cells == 34
    assert mid.layout_hint_cells is None
    assert mid.layout_width_fr == 1.0  # 1fr
    assert mid.layout_frac is None
    assert right.layout_frac == 0.38
    assert right.layout_min_cells == 40
    assert left.styles.get("border") is True
    assert left.styles.get("border_color") == theme.primary


def test_focus_within_border_accent():
    theme = Theme.from_mapping({"primary": "#111111", "accent": "#FFAA00"})

    class Body(Screen):
        DEFAULT_CSS = """
        #box { border: round $primary; }
        #box:focus-within { border: round $accent; }
        """

        def compose(self):
            inner = Static("x", id="inner")
            yield Panel(inner, id="box")

    screen = Body()
    screen.build()
    inner = screen.query_one("#inner")
    box = screen.query_one("#box")
    box.parent = screen
    inner.parent = box
    screen.app = _app(theme=theme)
    screen.apply_styles()
    assert box.styles.get("border_color") == "#111111"

    screen.app._focus = inner
    screen.apply_styles()
    assert box.styles.get("border_color") == "#FFAA00"


def test_dock_right_layout():
    class Body(Screen):
        DEFAULT_CSS = """
        #side { dock: right; width: 20; }
        #main { width: 1fr; }
        """

        def compose(self):
            yield Horizontal(
                Panel(Static("m"), id="main"),
                Panel(Static("s"), id="side"),
                id="row",
            )

    screen = Body()
    screen.build()
    for w in screen.walk_descendants(include_self=True):
        for c in w.children:
            c.parent = w
    screen.app = _app()
    screen.layout(Rect(0, 0, 800, 400))
    row = screen.query_one("#row")
    row.layout(Rect(0, 40, 800, 300))
    side = screen.query_one("#side")
    main = screen.query_one("#main")
    from textual_wgpu.fonts import cells_to_px
    assert side.rect.w == cells_to_px(20)
    assert side.rect.x + side.rect.w == row.rect.x + row.rect.w
    assert main.rect.w > 0
    assert main.rect.x + main.rect.w <= side.rect.x + 1


def test_app_and_screen_cascade():
    class Body(Screen):
        DEFAULT_CSS = """
        #x { width: 40; }
        """

        def compose(self):
            yield Static("x", id="x")

    screen = Body()
    screen.build()
    x = screen.query_one("#x")
    x.parent = screen
    app = _app()
    app.CSS = "#x { width: 10; }"
    screen.app = app
    screen.apply_styles()
    # Screen layer (1) beats app layer (0) at equal specificity… actually both #x
    # same spec; higher source_layer wins in our sort key.
    assert x.layout_hint_cells == 40


def test_transition_arms_and_ticks():
    class Body(Screen):
        DEFAULT_CSS = """
        #box { background: #000000; transition: background 100ms; }
        #box.hot { background: #ffffff; }
        """

        def compose(self):
            yield Panel(Static("x"), id="box")

    screen = Body()
    screen.build()
    box = screen.query_one("#box")
    box.parent = screen
    screen.app = _app()
    screen.apply_styles()
    assert box.styles.get("background") == "#000000"
    box.add_class("hot")
    screen.apply_styles()
    # Should arm transition — still near start color
    assert "_transition_background" in box.styles
    # Force complete
    tr = box.styles["_transition_background"]
    tr["t0"] = time.monotonic() - 2.0
    assert tick_transitions(screen) is False
    assert box.styles.get("background") == "#ffffff"


def test_layout_uses_css_cells():
    theme = Theme.system()

    class Body(Screen):
        DEFAULT_CSS = """
        #list-box { width: 48%; min-width: 36; }
        #det-box { width: 1fr; }
        """

        def compose(self):
            yield Horizontal(
                Panel(Static("jobs"), id="list-box"),
                Panel(Static("det"), id="det-box"),
                id="main",
            )

    screen = Body()
    screen.build()
    screen.app = _app(theme=theme)
    screen.layout(Rect(0, 0, 800, 400))
    main = screen.query_one("#main")
    main.layout(Rect(0, 40, 800, 300))
    lst = screen.query_one("#list-box")
    det = screen.query_one("#det-box")
    from textual_wgpu.fonts import cells_to_px

    assert lst.rect.w >= cells_to_px(36)
    assert det.rect.w > 0
    assert lst.rect.w + det.rect.w >= 800
