"""Native inline RGBA image compositing in Strip compositor & VerticalScroll."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from rich.text import Text

from textual_wgpu.compositor import make_image_text
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import RichText, VerticalScroll


def test_native_inline_image_renders_and_scrolls_smoothly(tmp_path: Path):
    img_path = tmp_path / "swatch.png"
    src = Image.new("RGBA", (160, 100), (230, 30, 30, 255))
    for x in range(80, 160):
        for y in range(100):
            src.putpixel((x, y), (30, 180, 70, 255))
    src.save(img_path)

    doc = Text("Header line\n")
    placeholder = make_image_text(img_path, max_width_px=160, max_height_px=100, row_height_px=18, indent_px=12)
    assert placeholder is not None
    doc.append_text(placeholder)
    doc.append("Footer line\n")

    class Body(Screen):
        def compose(self):
            yield VerticalScroll(RichText(doc, id="rt", can_focus=False), id="sc")

    app = ModeApp([("m", "M", Body)], theme=Theme.system(), start="m")
    app._pixel_size = (400, 120)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 400, 120))
    sc = screen.query_one("#sc", VerticalScroll)

    frame0 = Image.new("RGBA", (400, 120), (0, 0, 0, 255))
    screen.paint(ImageDraw.Draw(frame0))
    # Verify native red & green pixels painted (not half-blocks)
    reds0 = [
        (x, y)
        for y in range(sc.rect.y, sc.rect.y1)
        for x in range(sc.rect.x, sc.rect.x1 - 10)
        if frame0.getpixel((x, y))[:3] == (230, 30, 30)
    ]
    greens0 = [
        (x, y)
        for y in range(sc.rect.y, sc.rect.y1)
        for x in range(sc.rect.x, sc.rect.x1 - 10)
        if frame0.getpixel((x, y))[:3] == (30, 180, 70)
    ]
    assert len(reds0) > 1000
    assert len(greens0) > 1000
    top_y0 = min(y for _, y in reds0)

    # Smooth sub-row pixel scroll (e.g. 9px) shifts the image upward cleanly
    sc.scroll_pixels(9.0)
    frame1 = Image.new("RGBA", (400, 120), (0, 0, 0, 255))
    screen.paint(ImageDraw.Draw(frame1))
    reds1 = [
        (x, y)
        for y in range(sc.rect.y, sc.rect.y1)
        for x in range(sc.rect.x, sc.rect.x1 - 10)
        if frame1.getpixel((x, y))[:3] == (230, 30, 30)
    ]
    top_y1 = min(y for _, y in reds1)
    assert top_y1 == top_y0 - 9
