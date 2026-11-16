"""Toast / Tooltip / LoadingIndicator / Scrollbar / Collapsible / switchers."""

from __future__ import annotations

from PIL import Image, ImageDraw

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import (
    Collapsible,
    ContentSwitcher,
    LoadingIndicator,
    Scrollbar,
    Static,
    TabPane,
    TabbedContent,
    Toast,
    Tooltip,
    VerticalScroll,
)


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
        _timers: list = []

        def request_draw(self) -> None:
            return None

        def set_focus(self, w) -> None:
            self._focus = w

        def set_timer(self, seconds: float, callback) -> None:
            self._timers.append((seconds, callback))

    return A()


def test_toast_show_hide():
    app = _app()
    toast = Toast()
    toast.mount(app)
    assert not toast.visible
    toast.show("Saved", severity="information", timeout=2.0)
    assert toast.visible
    assert "Saved" in toast.render_lines()[0].plain()
    # Pre-run apps do not schedule set_timer (would fire sync and clear).
    assert not app._timers
    toast.hide()
    assert not toast.visible


def test_mode_app_notify_uses_toast():
    app = ModeApp([("demo", "Demo", None)], theme=_theme())
    app.notify("Hello", severity="warning", timeout=1.5)
    assert app._toast.visible
    assert app._notify == "Hello"
    img = Image.new("RGBA", (320, 200), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    app._toast.paint_at(draw, Rect(0, 0, 320, 200))


def test_tooltip_and_loading():
    app = _app()
    tip = Tooltip()
    tip.mount(app)
    tip.show_at("Hint", 10, 20)
    assert tip.visible
    assert tip.rect.w > 0
    tip.hide()
    assert not tip.visible

    load = LoadingIndicator("Working")
    load.mount(app)
    load.layout(Rect(0, 0, 200, 24))
    load.show()
    assert load.loading
    lines = load.render_lines()
    assert lines and "Working" in lines[0].plain()
    load.hide()
    assert not load.loading


def test_scrollbar_metrics_and_hits():
    app = _app()
    moved: list[float] = []
    bar = Scrollbar(vertical=True, on_scroll=moved.append)
    bar.mount(app)
    bar.layout(Rect(100, 0, 10, 200))
    bar.set_metrics(position=0.0, window=0.25)
    geo = bar._geometry()
    assert geo is not None
    hits = bar.hits()
    assert hits
    # Jump toward bottom of track.
    track, _ = geo
    hits[0][1](track.x + 1, track.y + track.h - 5)
    assert moved and moved[-1] > 0.5


def test_vertical_scroll_uses_scrollbar_widget():
    app = _app()

    class Body(Static):
        line_count = 40

        def ensure_baked(self, _w: int) -> None:
            return None

    scroll = VerticalScroll(Body("x" * 20))
    scroll.mount(app)
    scroll.layout(Rect(0, 0, 200, 100))
    assert isinstance(scroll._scrollbar, Scrollbar)
    hits = scroll.hits()
    # Overflow → scrollbar hit region present.
    assert any(True for _ in hits)
    scroll.scroll_to_fraction(1.0)
    assert scroll.scroll_y == scroll._max_scroll()


def test_collapsible_toggle():
    app = _app()
    body = Static("inner", id="inner")
    col = Collapsible(body, title="More", collapsed=True)
    col.mount(app)
    col.layout(Rect(0, 0, 240, 120))
    assert col.collapsed
    assert "▶" in col.render_lines()[0].plain()
    col.toggle()
    assert not col.collapsed
    assert "▼" in col.render_lines()[0].plain()
    # Expanded body laid out with height.
    col.layout(Rect(0, 0, 240, 120))
    assert body.rect.h > 0
    hits = col.hits()
    assert hits
    hits[0][1](10, 10)  # title click collapses again
    assert col.collapsed


def test_content_switcher_and_tabbed():
    app = _app()
    a = Static("A", id="a")
    b = Static("B", id="b")
    sw = ContentSwitcher(a, b, initial="a")
    sw.mount(app)
    sw.layout(Rect(0, 0, 200, 80))
    assert sw.current() is a
    assert a.styles.get("display") == "block"
    assert b.styles.get("display") == "none"
    sw.show("b")
    assert sw.current() is b

    panes = [
        TabPane("One", Static("p1"), id="one"),
        TabPane("Two", Static("p2"), id="two"),
    ]
    tabs = TabbedContent(*panes, initial=0)
    tabs.mount(app)
    tabs.layout(Rect(0, 0, 300, 160))
    assert tabs._switcher.current() is panes[0]
    tabs.activate(1)
    assert tabs._switcher.current() is panes[1]
    assert tabs.hits()
