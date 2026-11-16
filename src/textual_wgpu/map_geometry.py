"""Map geometry — Textual ``textual.map_geometry`` parity."""

from __future__ import annotations

from .geometry import Region


class MapGeometry:
    def __init__(self, region: Region | None = None) -> None:
        self.region = region or Region()
