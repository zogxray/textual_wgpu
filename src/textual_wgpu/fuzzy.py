"""Fuzzy matcher — Textual ``textual.fuzzy.Matcher`` subset."""

from __future__ import annotations

from rich.text import Text


class Matcher:
    """Simple fuzzy subsequence scorer & highlighter."""

    def __init__(
        self,
        query: str,
        *,
        match_style: str = "bold reverse",
        case_sensitive: bool = False,
    ) -> None:
        self.query = query or ""
        self.match_style = match_style
        self.case_sensitive = bool(case_sensitive)

    def _match_positions(self, candidate: str) -> tuple[float, list[int]]:
        if not self.query:
            return (1.0, [])
        q = self.query if self.case_sensitive else self.query.casefold()
        c = candidate if self.case_sensitive else candidate.casefold()
        pos: list[int] = []
        qi = 0
        for ci, ch in enumerate(c):
            if qi < len(q) and ch == q[qi]:
                pos.append(ci)
                qi += 1
        if qi < len(q):
            return (0.0, [])
        if q in c:
            score = 1.0 + len(q) / max(1, len(c))
        else:
            gaps = (pos[-1] - pos[0] + 1 - len(pos)) if pos else 0
            score = max(0.1, 1.0 / (1.0 + gaps * 0.25))
        return (score, pos)

    def match(self, candidate: str) -> float:
        score, _ = self._match_positions(candidate)
        return score

    def highlight(self, candidate: str) -> Text:
        score, positions = self._match_positions(candidate)
        text = Text(candidate)
        if score > 0:
            for i in positions:
                text.stylize(self.match_style, i, i + 1)
        return text
