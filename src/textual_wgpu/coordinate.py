"""Coordinate — row/column tuple for DataTable (Textual ``textual.coordinate``)."""

from __future__ import annotations

from typing import NamedTuple


class Coordinate(NamedTuple):
    row: int = 0
    column: int = 0

    @property
    def left(self) -> Coordinate:
        return Coordinate(self.row, self.column - 1)

    @property
    def right(self) -> Coordinate:
        return Coordinate(self.row, self.column + 1)

    @property
    def up(self) -> Coordinate:
        return Coordinate(self.row - 1, self.column)

    @property
    def down(self) -> Coordinate:
        return Coordinate(self.row + 1, self.column)
