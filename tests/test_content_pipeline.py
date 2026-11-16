"""Content → Strip → compositor pipeline."""

from __future__ import annotations

from PIL import Image, ImageDraw
from rich.text import Text

from textual_wgpu.content import Content, markdown_to_strips, markup_to_strips
from textual_wgpu.geometry import Rect
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Label, Log, Markdown, RichText, Static, StatusBar


def _theme() -> Theme:
    return Theme.from_mapping({
        "background": "#1E1923",
        "foreground": "#F5F0E8",
        "accent": "#D4AD72",
        "muted": "#9A9088",
        "panel": "#2A2430",
        "boost": "#342C3C",
        "primary": "#D4AD72",
    })


def _app():
    class A:
        theme = _theme()
        _layout_size = (0, 0)

        def request_draw(self) -> None:
            return None

    return A()


def test_markup_to_strips_styles():
    strips = markup_to_strips("[bold]Hi[/] there", width=40, default_fg=(255, 255, 255, 255))
    plain = "".join(s.plain() for s in strips)
    assert "Hi" in plain and "there" in plain
    assert any(seg.bold for s in strips for seg in s.segments)


def test_content_markdown_render():
    c = Content.from_markdown("# Title\n\n**bold** and *italic*\n")
    strips = c.render(60, default_fg=(255, 255, 255, 255))
    plain = "\n".join(s.plain() for s in strips)
    assert "Title" in plain
    assert any(seg.bold for s in strips for seg in s.segments)


def test_markdown_widget_render_lines():
    app = _app()
    md = Markdown("# Hello\n\nBody.\n")
    md.mount(app)
    md.layout(Rect(0, 0, 400, 300))
    lines = md.render_lines()
    assert md.line_count == len(lines)
    assert "Hello" in "".join(s.plain() for s in lines)


def test_static_and_label_markup():
    app = _app()
    s = Static("plain\nline")
    s.mount(app)
    s.layout(Rect(0, 0, 200, 40))
    assert [x.plain() for x in s.render_lines()] == ["plain", "line"]

    lab = Label("[bold]Go[/]", markup=True)
    lab.mount(app)
    lab.layout(Rect(0, 0, 100, 24))
    assert "Go" in lab.render_lines()[0].plain()
    assert any(seg.bold for seg in lab.render_lines()[0].segments)


def test_richtext_via_content():
    app = _app()
    rt = RichText(Text.from_markup("[bold]X[/] y"))
    rt.mount(app)
    rt.layout(Rect(0, 0, 200, 40))
    strips = rt.render_lines()
    assert "X" in strips[0].plain()
    img = Image.new("RGBA", (220, 60), (0, 0, 0, 255))
    rt.paint(ImageDraw.Draw(img))  # compositor path


def test_log_write_and_scroll():
    app = _app()
    log = Log(max_lines=100)
    log.mount(app)
    log.layout(Rect(0, 0, 300, 100))
    log.write("one")
    log.write("[bold]two[/]", markup=True)
    assert len(log.render_lines()) >= 2
    assert "two" in "".join(s.plain() for s in log.render_lines())
    log.clear()
    assert log.render_lines() == []


def test_statusbar_strip_paint():
    app = _app()
    bar = StatusBar("ready")
    bar.mount(app)
    bar.layout(Rect(0, 0, 400, 28))
    assert "ready" in bar.render_lines()[0].plain()
    img = Image.new("RGBA", (400, 40), (0, 0, 0, 255))
    bar.paint(ImageDraw.Draw(img))


def test_markdown_to_strips_wraps():
    strips = markdown_to_strips("word " * 80, 40, default_fg=(255, 255, 255, 255))
    assert len(strips) > 5
