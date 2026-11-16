"""OptionList virtualization + highlight + Strip paint."""

from PIL import Image, ImageDraw

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Option, OptionList, Panel, Static, StatusBar


def _theme():
    return Theme.from_mapping({
        "background": "#1E1923",
        "accent": "#D4AD72",
        "panel": "#2A2430",
        "boost": "#342C3C",
        "muted": "#9A9088",
        "foreground": "#F5F0E8",
        "surface": "#241E2A",
        "primary": "#D4AD72",
    })


def test_option_list_highlight_and_virtual_paint():
    seen: list[str] = []

    class Body(Screen):
        def compose(self):
            from textual_wgpu.widgets import KeyBar, ModeBar

            yield ModeBar("items", [("items", "Items")])
            ol = OptionList(id="list", on_highlight=lambda o: seen.append(o.id if o else ""))
            yield ol
            yield KeyBar([])
            yield StatusBar("s")

    theme = _theme()
    app = ModeApp([("items", "Items", Body)], theme=theme, start="items")
    app._pixel_size = (400, 200)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 400, 200))
    ol = screen.query_one("#list", OptionList)
    ol.set_options([Option(f"row {i}", id=f"r{i}") for i in range(100)])
    assert ol.highlighted_option.id == "r0"
    ol.move_highlight(5)
    assert ol.highlighted == 5
    img = Image.new("RGBA", (400, 200))
    screen.paint(ImageDraw.Draw(img))
    # highlighted row uses accent somewhere in the list area
    assert any(
        img.getpixel((20, y))[:3] == (0xD4, 0xAD, 0x72)
        for y in range(ol.rect.y, min(ol.rect.y1, 200))
    )


def test_option_list_render_lines_strip():
    class A:
        theme = _theme()
        _layout_size = (0, 0)

        def request_draw(self) -> None:
            return None

    app = A()
    ol = OptionList(id="list")
    ol.mount(app)
    ol.layout(Rect(0, 0, 240, 120))
    ol.set_options([
        Option("alpha", id="a"),
        Option("beta", id="b", disabled=True),
        Option("gamma", id="c", runs=[("pre ", (200, 200, 200, 255)), ("post", (100, 180, 100, 255))]),
    ])
    strips = ol.render_lines()
    assert len(strips) == 3
    assert "alpha" in strips[0].plain()
    assert any(seg.bgcolor == app.theme.rgba("accent") for seg in strips[0].segments)
    assert "pre" in strips[2].plain() and "post" in strips[2].plain()
    # Multi-line option expands to row_lines strips each
    ol2 = OptionList(row_lines=2)
    ol2.mount(app)
    ol2.set_options([Option("x", id="x", lines=[[("L1", (255, 255, 255, 255))], [("L2", (200, 200, 200, 255))]])])
    assert len(ol2.render_lines()) == 2


def test_panel_title_strip_paint():
    class A:
        theme = _theme()
        _layout_size = (0, 0)

        def request_draw(self) -> None:
            return None

    app = A()
    panel = Panel(Static("body"), title="JOBS", subtitle="n=3", id="p")
    panel.mount(app)
    panel.layout(Rect(0, 0, 300, 160))
    title = panel._title_strip(app.theme.rgba("muted"))
    assert "JOBS" in title[0].plain()
    img = Image.new("RGBA", (300, 160), (0, 0, 0, 255))
    panel.paint(ImageDraw.Draw(img))
    # Panel fill is not pure black
    assert img.getpixel((20, 20)) != (0, 0, 0, 255)


def test_key_normalize_arrows():
    from textual_wgpu.app import _normalize_key

    assert _normalize_key("ArrowUp") == "up"
    assert _normalize_key("F9") == "f9"
    assert _normalize_key(" ") == "space"
