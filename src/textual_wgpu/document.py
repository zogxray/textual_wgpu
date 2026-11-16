"""Document — text document model (Textual ``textual.document``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Selection:
    start: tuple[int, int] = (0, 0)
    end: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class EditResult:
    end_location: tuple[int, int] = (0, 0)
    replaced_text: str = ""


class Document:
    """Line-based text buffer."""

    def __init__(self, text: str = "", language: str | None = None) -> None:
        self._lines = (text or "").split("\n")
        self.language = language

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def get_line(self, index: int) -> str:
        return self._lines[index]

    def replace_range(self, start: tuple[int, int], end: tuple[int, int], text: str) -> EditResult:
        r0, c0 = start
        r1, c1 = end
        before = self._lines[r0][:c0]
        after = self._lines[r1][c1:]
        inserted = (before + text + after).split("\n")
        self._lines[r0 : r1 + 1] = inserted
        end_row = r0 + len(inserted) - 1
        end_col = len(inserted[-1]) - len(after)
        return EditResult(end_location=(end_row, end_col), replaced_text="")
