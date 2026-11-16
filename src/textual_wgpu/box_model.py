"""BoxModel — Textual ``textual.box_model`` parity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from .geometry import Spacing


class BoxModel(NamedTuple):
    width: int = 0
    height: int = 0
    margin: Spacing = Spacing(0, 0, 0, 0) if hasattr(Spacing, "__call__") else None  # type: ignore[assignment]


@dataclass
class _Box:
    width: int = 0
    height: int = 0
    padding: tuple[int, int, int, int] = (0, 0, 0, 0)
    margin: tuple[int, int, int, int] = (0, 0, 0, 0)


def get_box_model(styles: dict, width: int = 0, height: int = 0) -> _Box:
    _ = styles
    return _Box(width=width, height=height)
