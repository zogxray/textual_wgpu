"""Markdown bake + Help-shaped screen compose without a window."""

from PIL import Image, ImageDraw

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import KeyBar, Markdown, ModeBar, Panel, StatusBar, VerticalScroll
from textual_wgpu.widgets.markdown import bake_markdown


def test_bake_markdown_has_lines():
    md = "# Hello\n\nSome **bold** text.\n\n- a\n- b\n"
    lines = bake_markdown(md, 60, default_fill=(255, 255, 255, 255))
    assert len(lines) >= 3
    plain = "".join(r.text for line in lines for r in line.runs)
    assert "Hello" in plain


def test_bake_markdown_keeps_style_flags():
    md = "# Heading\n\n*italic* and **bold** and ~~maybe~~\n\n---\n"
    lines = bake_markdown(md, 60, default_fill=(240, 230, 216, 255))
    runs = [r for line in lines for r in line.runs if r.text.strip()]
    assert any(r.bold for r in runs)
    # Rich headings are typically bold+underline; italic for *…*
    assert any(r.italic or r.underline or r.dim for r in runs)


def test_bake_wraps_long_lines_to_columns():
    from textual_wgpu.fonts import columns_for_width

    md = "alpha " * 80  # one long paragraph, no newlines
    cols = columns_for_width(320)  # narrow pane (~40 cells)
    lines = bake_markdown(md, cols, default_fill=(255, 255, 255, 255))
    lengths = [sum(len(r.text) for r in line.runs) for line in lines]
    assert len(lines) > 5
    assert max(lengths) <= cols


def test_help_like_screen_compose_and_paint():
    labels = [("home", "Home"), ("help", "Help")]

    class Help(Screen):
        def compose(self):
            yield ModeBar("help", labels, on_select=lambda i: None)
            yield Panel(
                VerticalScroll(Markdown("# Title\n\nBody text here.\n"), id="help-scroll"),
                title="HELP",
            )
            yield KeyBar([("↑↓", "Scroll", None)])
            yield StatusBar("status")

    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72"})
    app = ModeApp(
        [("home", "Home", None), ("help", "Help", Help)],
        theme=theme,
        start="help",
    )
    app._pixel_size = (800, 600)
    screen = app._ensure_screen()
    assert isinstance(screen, Help)
    screen.layout(Rect(0, 0, 800, 600))
    scroll = screen.query_one("#help-scroll", VerticalScroll)
    assert scroll is not None
    img = Image.new("RGBA", (800, 600), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    screen.paint(draw)
    # ModeBar uses boost; top strip should not stay pure black
    assert img.getpixel((10, 10)) != (0, 0, 0, 255)


def test_scrollbar_thumb_when_overflow():
    md = "\n".join(f"Line {i}" for i in range(80))
    theme = Theme.from_mapping({
        "background": "#1E1923",
        "boost": "#25202B",
        "panel": "#2A2430",
        "accent": "#D4AD72",
        "foreground": "#F0E6D8",
    })

    class Body(Screen):
        def compose(self):
            yield VerticalScroll(Markdown(md), id="s")

    app = ModeApp([("help", "Help", Body)], theme=theme, start="help")
    app._pixel_size = (400, 200)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 400, 200))
    scroll = screen.query_one("#s", VerticalScroll)
    assert scroll._max_scroll() > 0
    geo = scroll._bar_geometry()
    assert geo is not None
    _track, thumb = geo
    img = Image.new("RGBA", (400, 200), (0, 0, 0, 255))
    screen.paint(ImageDraw.Draw(img))
    # Thumb uses accent gold
    px = img.getpixel((thumb.x + thumb.w // 2, thumb.y + thumb.h // 2))
    assert px[:3] == (0xD4, 0xAD, 0x72)
    scroll.scroll(20)
    geo2 = scroll._bar_geometry()
    assert geo2 is not None
    assert geo2[1].y > thumb.y
