"""TextArea editing + focus routing helpers."""

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import KeyBar, ModeBar, StatusBar, TextArea


def test_textarea_insert_and_newline():
    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72", "panel": "#2A2430"})

    class Body(Screen):
        def compose(self):
            yield ModeBar("chat", [("chat", "Chat")])
            yield TextArea("", id="prompt", placeholder="type")
            yield KeyBar([])
            yield StatusBar("")

    app = ModeApp([("chat", "Chat", Body)], theme=theme, start="chat")
    app._pixel_size = (600, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 600, 400))
    ta = screen.query_one("#prompt", TextArea)
    ta.focus()
    assert app._focus is ta
    app.on_char("h")
    app.on_char("i")
    app.on_key("enter")
    app.on_char("!")
    assert ta.text == "hi\n!"
    app.on_key("backspace")
    assert ta.text == "hi\n"


def test_textarea_shift_select_and_copy(monkeypatch):
    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72", "panel": "#2A2430"})
    copied: list[str] = []

    class Body(Screen):
        def compose(self):
            yield ModeBar("chat", [("chat", "Chat")])
            yield TextArea("abcdef", id="prompt")
            yield KeyBar([])
            yield StatusBar("")

    app = ModeApp([("chat", "Chat", Body)], theme=theme, start="chat")
    app._pixel_size = (600, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 600, 400))
    ta = screen.query_one("#prompt", TextArea)
    ta.focus()
    monkeypatch.setattr(app, "copy_to_clipboard", lambda t: copied.append(t) or True)
    # Cursor at end; Shift+Home selects the line.
    app.on_key("end")
    app.on_key("shift+home")
    assert ta.selected_text() == "abcdef"
    app.on_key("ctrl+c")
    assert copied == ["abcdef"]


def test_textarea_read_only_blocks_edit():
    ta = TextArea("keep", read_only=True)
    ta.insert("x")
    assert ta.text == "keep"
    ta.cursor = 0
    ta._clear_selection()
    ta.on_key("shift+right")
    assert ta.selected_text() == "k"
