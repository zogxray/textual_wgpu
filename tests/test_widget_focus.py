"""Widget.focus / blur — Textual-style base API via ModeApp.set_focus."""

from __future__ import annotations

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widget import Widget
from textual_wgpu.widgets import OptionList, RichText, ScrollView, TextArea


def _theme() -> Theme:
    return Theme.from_mapping(
        {"background": "#1E1923", "accent": "#D4AD72", "panel": "#2A2430"}
    )


def test_base_widget_focus_and_blur():
    class Body(Screen):
        def compose(self):
            yield TextArea("", id="a")
            yield OptionList(id="b")

    app = ModeApp([("m", "M", Body)], theme=_theme(), start="m")
    app._pixel_size = (640, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 400))
    a = screen.query_one("#a", TextArea)
    b = screen.query_one("#b", OptionList)

    assert a.can_focus and b.can_focus
    assert not a.focused and not b.focused

    a.focus()
    assert app._focus is a
    assert a.focused and a._focused
    assert not b.focused

    b.focus()
    assert app._focus is b
    assert b.focused and not a.focused

    b.blur()
    assert app._focus is None
    assert not b.focused


def test_blur_only_clears_own_focus():
    class Body(Screen):
        def compose(self):
            yield TextArea("", id="a")
            yield TextArea("", id="b")

    app = ModeApp([("m", "M", Body)], theme=_theme(), start="m")
    app._pixel_size = (640, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 400))
    a = screen.query_one("#a", TextArea)
    b = screen.query_one("#b", TextArea)
    a.focus()
    b.blur()  # not focused — no-op
    assert app._focus is a
    assert a.focused


def test_focusables_include_optionlist_richtext_scrollview():
    class Body(Screen):
        def compose(self):
            yield OptionList(id="ol")
            yield RichText("hello", id="rt")
            yield ScrollView(id="sv")
            yield Widget(id="plain")  # can_focus=False

    app = ModeApp([("m", "M", Body)], theme=_theme(), start="m")
    app._pixel_size = (640, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 400))
    ids = {getattr(w, "id", None) for w in app._focusables()}
    assert "ol" in ids
    assert "rt" in ids
    assert "sv" in ids
    assert "plain" not in ids


def test_scrollview_focus_sets_focused_flag():
    """MDGA chroms.focus() path — ScrollView gets _focused via set_focus."""

    class Body(Screen):
        def compose(self):
            yield ScrollView(id="chroms")
            yield TextArea("", id="prompt")

    app = ModeApp([("m", "M", Body)], theme=_theme(), start="m")
    app._pixel_size = (640, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 400))
    chroms = screen.query_one("#chroms", ScrollView)
    prompt = screen.query_one("#prompt", TextArea)

    prompt.focus()
    assert prompt.focused
    chroms.focus()
    assert chroms.focused
    assert app._focus is chroms
    assert not prompt.focused
