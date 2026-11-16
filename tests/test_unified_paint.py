"""Unified Content→Strip paint: chrome, TextArea, ModeApp modals."""

from __future__ import annotations

from PIL import Image, ImageDraw

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import KeyBar, ModeBar, StatusBar, TextArea


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


def test_modebar_keybar_render_lines():
    app = _app()
    bar = ModeBar("chat", [("chat", "Chat"), ("help", "Help")], on_select=lambda i: None)
    bar.mount(app)
    bar.layout(Rect(0, 0, 600, 28))
    lines = bar.render_lines()
    assert lines and "Chat" in lines[0].plain() and "Help" in lines[0].plain()
    # Active tab has accent bgcolor
    assert any(seg.bgcolor == app.theme.rgba("accent") for seg in lines[0].segments)
    assert bar.hits()

    keys = KeyBar([("Q", "Quit", lambda: None), ("↑↓", "Scroll", None)])
    keys.mount(app)
    keys.layout(Rect(0, 0, 600, 28))
    kl = keys.render_lines()
    assert "Quit" in kl[0].plain()
    assert keys.hits()  # actionable Quit chip


def test_textarea_render_lines_and_selection():
    app = _app()
    ta = TextArea("hello world", id="t")
    ta.mount(app)
    ta.layout(Rect(0, 0, 300, 80))
    ta._focused = True
    ta._sel_anchor = 0
    ta.cursor = 5
    lines = ta.render_lines()
    assert lines
    plain = lines[0].plain()
    assert plain.startswith("hello")
    # Selection mid has accent bgcolor
    assert any(seg.bgcolor == app.theme.rgba("accent") for seg in lines[0].segments)
    img = Image.new("RGBA", (300, 80), (0, 0, 0, 255))
    ta.paint(ImageDraw.Draw(img))
    assert img.getpixel((10, 10))[3] == 255


def test_mode_app_quit_modal_strip_paint():
    app = ModeApp([("x", "X", None)], theme=_theme())
    app.ask_quit()
    assert app.quit_modal
    img = Image.new("RGBA", (640, 400), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    app._paint_quit_modal(draw, 640, 400)
    assert len(app._hits) >= 3
    # Dialog surface should tint center
    px = img.getpixel((320, 200))
    assert px != (0, 0, 0, 255)


def test_mode_app_prompt_strip_paint():
    app = ModeApp([("x", "X", None)], theme=_theme())
    app._prompt = {
        "title": "Name",
        "value": "abc",
        "placeholder": "",
        "password": False,
        "hint": "Enter",
        "callback": lambda _v: None,
    }
    img = Image.new("RGBA", (640, 400), (0, 0, 0, 255))
    app._paint_prompt(ImageDraw.Draw(img), 640, 400)
    assert app._hits


def test_statusbar_still_strip():
    app = _app()
    s = StatusBar("ok")
    s.mount(app)
    s.layout(Rect(0, 0, 200, 24))
    assert "ok" in s.render_lines()[0].plain()
