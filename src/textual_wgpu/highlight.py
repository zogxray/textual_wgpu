"""Syntax highlight helper — Textual ``textual.highlight`` parity."""

from __future__ import annotations

from rich.syntax import Syntax


def guess_language(code: str, path: str | None = None) -> str:
    if path and path.endswith(".py"):
        return "python"
    if path and path.endswith(".json"):
        return "json"
    return "text" if code else "text"


def Highlight(code: str, *, language: str | None = None, path: str | None = None) -> Syntax:
    lang = language or guess_language(code, path)
    return Syntax(code, lang)
