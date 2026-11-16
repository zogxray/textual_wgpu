"""Line filters — Textual ``textual.filter`` parity."""

from __future__ import annotations

from .strip import Strip


class LineFilter:
    def apply(self, strip: Strip, background=None) -> Strip:  # noqa: ANN001
        _ = background
        return strip


class ANSIToTruecolor(LineFilter):
    pass


class Monochrome(LineFilter):
    pass


class DimFilter(LineFilter):
    pass
