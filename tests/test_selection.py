"""Screen-level Selection / copy UX (Textual subset)."""

from __future__ import annotations

from textual_wgpu.geometry import Offset, Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.selection import (
    SELECT_ALL,
    Selection,
    apply_selection_bg,
    strips_plain,
    word_span_at,
)
from textual_wgpu.strip import Segment, Strip
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Markdown, RichText, TextArea, VerticalScroll


def test_selection_extract_and_span():
    sel = Selection.from_offsets(Offset(2, 0), Offset(3, 1))
    text = "abcdef\nghijkl"
    assert sel.extract(text) == "cdef\nghi"
    assert sel.get_span(0) == (2, -1)
    assert sel.get_span(1) == (0, 3)
    assert sel.get_span(2) is None
    assert SELECT_ALL.extract("a\nb") == "a\nb"


def test_word_span_and_apply_bg():
    assert word_span_at("hello world", 1) == (0, 5)
    strips = [Strip([Segment("hello world", (255, 255, 255, 255))])]
    sel = Selection(Offset(0, 0), Offset(5, 0))
    painted = apply_selection_bg(strips, sel, (100, 100, 200, 110))
    assert painted[0].segments[0].bgcolor == (100, 100, 200, 110)
    assert painted[0].plain() == "hello world"


def test_markdown_screen_selection_copy():
    class Help(Screen):
        def compose(self):
            yield VerticalScroll(
                Markdown("# Title\n\nBody alpha beta gamma.\n", id="help-body"),
                id="help-scroll",
            )

    theme = Theme.from_mapping({
        "background": "#1E1923", "panel": "#2A2430", "accent": "#D4AD72",
        "foreground": "#F0E6D8", "muted": "#999", "boost": "#333",
        "surface": "#222", "primary": "#D4AD72",
    })
    app = ModeApp(
        [("help", "Help", Help)],
        theme=theme,
        start="help",
    )
    app._pixel_size = (640, 480)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 480))
    md = screen.query_one("#help-body", Markdown)
    md.ensure_baked(400)
    plain = strips_plain(md.render_lines())
    assert "alpha" in plain
    # Select a mid span on line containing alpha
    lines = plain.splitlines()
    y = next(i for i, ln in enumerate(lines) if "alpha" in ln)
    x0 = lines[y].index("alpha")
    x1 = x0 + len("alpha")
    screen.selections = {md: Selection(Offset(x0, y), Offset(x1, y))}
    got = screen.get_selected_text()
    assert got == "alpha"
    assert app.copy_to_clipboard(got) or app.clipboard == "alpha"
    assert app.clipboard == "alpha"


def test_mode_app_drag_select_and_ctrl_c():
    class Help(Screen):
        def compose(self):
            yield VerticalScroll(Markdown("one two three four five", id="body"))

    theme = Theme.from_mapping({
        "background": "#1E1923", "panel": "#2A2430", "accent": "#D4AD72",
        "foreground": "#F0E6D8", "muted": "#999", "boost": "#333",
        "surface": "#222", "primary": "#D4AD72",
    })
    app = ModeApp([("help", "Help", Help)], theme=theme, start="help")
    app._pixel_size = (640, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 400))
    md = screen.query_one("#body", Markdown)
    md.ensure_baked(max(40, md.rect.w))
    plain = strips_plain(md.render_lines())
    lines = plain.splitlines()
    y_line = next(i for i, ln in enumerate(lines) if "one" in ln)
    # Pointer on the content line (Markdown may pad a blank line above).
    x0 = md.rect.x + 4 + 8  # past pad into text
    y0 = md.rect.y + 2 + y_line * md.LINE_H + 2
    x1 = x0 + 12 * 8  # ~12 cells
    app.on_pointer(x0, y0, 1)
    assert app._selecting
    app.on_pointer_move(x1, y0)
    app.on_pointer_up(x1, y0, 1)
    assert screen.selections
    text = screen.get_selected_text()
    assert text and text.strip()
    assert any(ch.isalpha() for ch in text)
    # Ctrl+C copies screen selection
    app._clipboard = ""
    app.on_key("ctrl+c")
    assert app.clipboard == text


def test_textarea_allow_select_false_and_richtext_true():
    assert TextArea.ALLOW_SELECT is False
    assert RichText.ALLOW_SELECT is True
    assert Markdown.ALLOW_SELECT is True


def test_double_click_selects_word():
    import time

    class Help(Screen):
        def compose(self):
            yield VerticalScroll(Markdown("alpha beta gamma", id="body"))

    theme = Theme.from_mapping({
        "background": "#1E1923", "panel": "#2A2430", "accent": "#D4AD72",
        "foreground": "#F0E6D8", "muted": "#999", "boost": "#333",
        "surface": "#222", "primary": "#D4AD72",
    })
    app = ModeApp([("help", "Help", Help)], theme=theme, start="help")
    app._pixel_size = (640, 400)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 400))
    md = screen.query_one("#body", Markdown)
    md.ensure_baked(max(40, md.rect.w))
    plain = strips_plain(md.render_lines())
    lines = plain.splitlines()
    y_line = next(i for i, ln in enumerate(lines) if "alpha" in ln)
    col = lines[y_line].index("alpha") + 1
    x = md.rect.x + 4 + col * 8
    y = md.rect.y + 2 + y_line * md.LINE_H + 2
    app.on_pointer(x, y, 1)
    app.on_pointer_up(x, y, 1)
    app._select_click_t = time.monotonic()
    app._select_click_n = 1
    app._select_click_xy = (x, y)
    app.on_pointer(x, y, 1)
    app.on_pointer_up(x, y, 1)
    sel = (screen.get_selected_text() or "").strip()
    assert sel == "alpha"
