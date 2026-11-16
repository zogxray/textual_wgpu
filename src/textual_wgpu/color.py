"""Color — Textual ``textual.color`` subset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ColorParseError(ValueError):
    """Raised when a color string cannot be parsed."""


@dataclass(frozen=True)
class Color:
    r: int = 0
    g: int = 0
    b: int = 0
    a: float = 1.0

    @classmethod
    def parse(cls, value: str | Color) -> Color:
        if isinstance(value, Color):
            return value
        s = str(value or "").strip()
        if s.startswith("#"):
            h = s[1:]
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            if len(h) == 6:
                return cls(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
            if len(h) == 8:
                return cls(
                    int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16),
                    int(h[6:8], 16) / 255.0,
                )
        named = {
            "black": (0, 0, 0, 1.0),
            "white": (255, 255, 255, 1.0),
            "transparent": (0, 0, 0, 0.0),
            "red": (255, 0, 0, 1.0),
            "green": (0, 128, 0, 1.0),
            "blue": (0, 0, 255, 1.0),
        }.get(s.lower())
        if named is not None:
            return cls(*named)
        raise ColorParseError(f"Cannot parse color: {value!r}")

    @property
    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def hex6(self) -> str:
        return self.hex

    def blend(self, destination: Color, factor: float, alpha: float | None = None) -> Color:
        t = max(0.0, min(1.0, float(factor)))
        return Color(
            int(self.r + (destination.r - self.r) * t),
            int(self.g + (destination.g - self.g) * t),
            int(self.b + (destination.b - self.b) * t),
            self.a if alpha is None else alpha,
        )

    def with_alpha(self, alpha: float) -> Color:
        return Color(self.r, self.g, self.b, max(0.0, min(1.0, float(alpha))))


BLACK = Color(0, 0, 0, 1.0)
WHITE = Color(255, 255, 255, 1.0)
TRANSPARENT = Color(0, 0, 0, 0.0)


class Gradient:
    def __init__(self, *stops: tuple[float, Color | str], quality: int = 10) -> None:
        self.stops = [(float(p), Color.parse(c)) for p, c in stops]
        self.quality = quality

    def get_color(self, position: float) -> Color:
        if not self.stops:
            return BLACK
        return self.stops[0][1]
