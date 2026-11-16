"""Canvas — pixel/braille drawing surface (Textual ``textual.canvas``)."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .strip import Segment, Strip
from .widget import Widget


class Canvas(Widget):
    """Simple RGBA pixel-backed drawing widget."""

    def __init__(self, width: int = 40, height: int = 20, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.canvas_width = max(1, int(width))
        self.canvas_height = max(1, int(height))
        self._image = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        self._draw = ImageDraw.Draw(self._image)

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> None:
        if 0 <= x < self.canvas_width and 0 <= y < self.canvas_height:
            self._image.putpixel((x, y), color)
            self.refresh()

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> None:
        self._draw.line([(x0, y0), (x1, y1)], fill=color)
        self.refresh()

    def clear(self) -> None:
        self._image = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        self._draw = ImageDraw.Draw(self._image)
        self.refresh()

    def render_lines(self) -> list[Strip]:
        return [Strip([Segment(" " * min(40, self.canvas_width))])]
