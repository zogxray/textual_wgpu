"""Simple layout boxes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offset:
    """Cell offset into widget content (``x`` = column, ``y`` = line)."""

    x: int
    y: int

    @property
    def transpose(self) -> tuple[int, int]:
        """``(line, column)`` — Textual ``Offset.transpose``."""
        return (self.y, self.x)


@dataclass(frozen=True)
class Size:
    """Width × height (Textual ``geometry.Size``)."""

    width: int
    height: int

    def __iter__(self):
        yield self.width
        yield self.height


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x1 and self.y <= py < self.y1

    def overlaps(self, other: Rect) -> bool:
        return not (
            self.x1 <= other.x
            or other.x1 <= self.x
            or self.y1 <= other.y
            or other.y1 <= self.y
        )

    def inset(self, l: int = 0, t: int = 0, r: int = 0, b: int = 0) -> Rect:
        return Rect(self.x + l, self.y + t, max(0, self.w - l - r), max(0, self.h - t - b))


@dataclass(frozen=True)
class Region:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


@dataclass(frozen=True)
class Spacing:
    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0

    @classmethod
    def all(cls, value: int) -> Spacing:
        return cls(value, value, value, value)


Shape = Size


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
