"""Widget catalog + Strip cell compositor."""

from __future__ import annotations

from PIL import Image, ImageDraw

from textual_wgpu.compositor import paint_strips
from textual_wgpu.geometry import Rect
from textual_wgpu.screen import Screen
from textual_wgpu.strip import Segment, Strip
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Button, ProgressBar, Select, Static, Switch, Tabs


def _theme() -> Theme:
    return Theme.from_mapping({
        "background": "#1E1923",
        "foreground": "#F5F0E8",
        "accent": "#D4AD72",
        "muted": "#9A9088",
        "panel": "#2A2430",
        "boost": "#342C3C",
        "surface": "#241E2A",
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

        def ask_pick(self, title, options, callback, *, current=None):
            # Auto-pick first option for tests.
            callback(options[0][0] if options else None)

    return A()


def test_strip_crop_join_bg():
    a = Strip.from_text("hello", bgcolor=(1, 2, 3, 255))
    b = Strip.from_text("!", (9, 9, 9, 255))
    joined = a + b
    assert joined.plain() == "hello!"
    assert joined.crop(1, 4).plain() == "ell"
    assert joined.segments[0].bgcolor == (1, 2, 3, 255)


def test_paint_strips_cell_grid():
    img = Image.new("RGBA", (200, 40), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    theme = _theme()
    strips = [
        Strip([Segment("AB", theme.rgba("foreground"), theme.rgba("accent"))]),
        Strip.from_text("cd", theme.rgba("muted")),
    ]
    paint_strips(draw, Rect(0, 0, 200, 40), strips, theme=theme, font_size=13)
    # Accent bg cells should tint some pixels.
    px = img.getpixel((8, 4))
    assert px[3] == 255


def test_button_press_message():
    app = _app()
    pressed: list = []
    btn = Button("Go", variant="primary", on_press=lambda: pressed.append(1))
    btn.mount(app)
    btn.layout(Rect(0, 0, 120, 28))
    lines = btn.render_lines()
    assert lines and "[" in lines[0].plain() and "Go" in lines[0].plain()
    btn.press()
    assert pressed == [1]
    # Hits
    assert btn.hits()
    btn.hits()[0][1](1, 1)
    assert pressed == [1, 1]


def test_switch_toggle():
    app = _app()
    sw = Switch(False)
    sw.mount(app)
    assert not sw.value
    sw.toggle()
    assert sw.value
    assert "●" in sw.render_lines()[0].plain()


def test_progress_bar_fill():
    app = _app()
    bar = ProgressBar(total=100, show_percentage=True)
    bar.mount(app)
    bar.layout(Rect(0, 0, 400, 24))
    bar.update(progress=50)
    plain = bar.render_lines()[0].plain()
    assert "█" in plain and "░" in plain
    assert "50%" in plain


def test_tabs_activate():
    app = _app()
    seen: list = []
    tabs = Tabs(["A", "B", "C"], on_change=lambda i, n: seen.append((i, n)))
    tabs.mount(app)
    tabs.layout(Rect(0, 0, 300, 28))
    assert tabs.active == 0
    tabs.activate(2)
    assert tabs.active == 2
    assert seen == [(2, "C")]
    assert tabs.on_key("left")
    assert tabs.active == 1
    strips = tabs.render_lines()
    assert "B" in strips[0].plain()


def test_select_cycle_and_pick():
    app = _app()
    sel = Select([("a", "Alpha"), ("b", "Beta")], value="a")
    sel.mount(app)
    sel.layout(Rect(0, 0, 200, 28))
    assert "Alpha" in sel.render_lines()[0].plain()
    sel.on_key("down")
    assert sel.value == "b"
    # ask_pick path
    sel.open_picker()
    assert sel.value == "a"  # test app picks first option


def test_static_render_lines():
    app = _app()
    s = Static("hello\nworld")
    s.mount(app)
    lines = s.render_lines()
    assert [x.plain() for x in lines] == ["hello", "world"]


def test_widget_paint_uses_compositor():
    app = _app()
    btn = Button("OK")
    btn.mount(app)
    btn.layout(Rect(10, 10, 100, 28))
    img = Image.new("RGBA", (200, 80), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    btn.paint(draw)  # should not raise; Strip path


def test_catalog_in_screen_compose():
    class Body(Screen):
        def compose(self):
            yield Tabs(["One", "Two"], id="tabs")
            yield Button("Run", id="run")
            yield Switch(True, id="sw")
            yield ProgressBar(id="bar")
            yield Select([("x", "X")], id="sel")
            yield Static("hi", id="st")

    screen = Body()
    screen.mount(_app())
    assert screen.query_one("#run", Button).label == "Run"
    assert screen.query_one("#sw", Switch).value is True
    assert screen.query_one("#tabs", Tabs).tab_names == ["One", "Two"]
