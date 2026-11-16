"""Tab expansion helpers (Textual ``textual.expand_tabs``)."""

from __future__ import annotations


def expand_tabs_inline(text: str, tab_size: int = 4) -> str:
    return (text or "").expandtabs(tab_size)


def get_tab_widths(text: str, tab_size: int = 4) -> list[int]:
    return [tab_size if ch == "\t" else 1 for ch in (text or "")]
