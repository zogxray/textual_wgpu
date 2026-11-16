"""Render helpers — Textual ``textual.render`` parity."""

from __future__ import annotations

from typing import Any


def measure(console: Any, renderable: Any, width: int = 80) -> int:
    _ = console, width
    return len(str(renderable))
