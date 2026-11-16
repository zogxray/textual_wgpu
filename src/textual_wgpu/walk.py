"""Tree walk helpers — Textual ``textual.walk`` parity."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from typing import Any


def walk_depth_first(root: Any, *, with_root: bool = True) -> Iterator[Any]:
    if with_root:
        yield root
    for child in getattr(root, "children", []) or []:
        yield from walk_depth_first(child, with_root=True)


def walk_breadth_first(root: Any, *, with_root: bool = True) -> Iterator[Any]:
    q: deque[Any] = deque()
    if with_root:
        q.append(root)
    else:
        q.extend(getattr(root, "children", []) or [])
    while q:
        node = q.popleft()
        yield node
        q.extend(getattr(node, "children", []) or [])
