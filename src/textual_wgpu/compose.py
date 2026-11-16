"""Compose helper — Textual ``textual.compose``."""

from __future__ import annotations

from typing import Any


def compose(node: Any) -> list[Any]:
    fn = getattr(node, "_collect_compose", None)
    if callable(fn):
        return fn()
    return list(node.compose())
