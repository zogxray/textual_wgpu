"""DOM walk + CSS-ish query (Textual ``query`` / ``query_one`` subset)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from .widget import Widget

T = TypeVar("T", bound="Widget")

_SEL_RE = re.compile(
    r"^(?:(?P<type>[A-Za-z_][A-Za-z0-9_]*)?)?(?:#(?P<id>[A-Za-z0-9_-]+))?$"
)


class DOMQuery(Generic[T]):
    """Result of ``Widget.query`` — iterable list-like (Textual DOMQuery lite)."""

    def __init__(self, nodes: Sequence[T]) -> None:
        self._nodes = list(nodes)

    def __iter__(self) -> Iterator[T]:
        return iter(self._nodes)

    def __len__(self) -> int:
        return len(self._nodes)

    def __bool__(self) -> bool:
        return bool(self._nodes)

    def __getitem__(self, index: int) -> T:
        return self._nodes[index]

    def first(self, expect_type: type | None = None) -> T:
        items = self._nodes
        if expect_type is not None:
            items = [n for n in items if isinstance(n, expect_type)]
        if not items:
            raise KeyError("DOMQuery is empty")
        return items[0]

    def last(self, expect_type: type | None = None) -> T:
        items = self._nodes
        if expect_type is not None:
            items = [n for n in items if isinstance(n, expect_type)]
        if not items:
            raise KeyError("DOMQuery is empty")
        return items[-1]

    def only_one(self, expect_type: type | None = None) -> T:
        items = self._nodes
        if expect_type is not None:
            items = [n for n in items if isinstance(n, expect_type)]
        if len(items) != 1:
            raise KeyError(f"Expected 1 node, got {len(items)}")
        return items[0]

    def results(self, filter_type: type | None = None) -> list[T]:
        if filter_type is None:
            return list(self._nodes)
        return [n for n in self._nodes if isinstance(n, filter_type)]

    def filter(self, selector: str) -> DOMQuery[T]:
        return DOMQuery([n for n in self._nodes if matches_selector(n, selector)])

    def exclude(self, selector: str) -> DOMQuery[T]:
        return DOMQuery([n for n in self._nodes if not matches_selector(n, selector)])


def walk_descendants(root: Widget, *, include_self: bool = False) -> Iterator[Widget]:
    """Depth-first walk of the widget tree."""
    if include_self:
        yield root
    for child in getattr(root, "children", []) or []:
        yield from walk_descendants(child, include_self=True)


def parse_selector(selector: str) -> tuple[str | None, str | None]:
    """Parse a simple ``Type``, ``#id``, or ``Type#id`` selector."""
    s = (selector or "").strip()
    if not s or s == "*":
        return None, None
    m = _SEL_RE.match(s)
    if not m:
        raise ValueError(f"Unsupported selector: {selector!r}")
    return m.group("type"), m.group("id")


def matches_selector(node: Widget, selector: str | type) -> bool:
    """Match one node against a selector (type, CSS string, comma lists, descendants)."""
    if isinstance(selector, type):
        return isinstance(node, selector)
    if "," in (selector or ""):
        return any(matches_selector(node, part) for part in selector.split(",") if part.strip())
    parts = [p for p in (selector or "").strip().split() if p]
    if not parts:
        return True
    if len(parts) == 1:
        return _match_simple(node, parts[0])
    if not _match_simple(node, parts[-1]):
        return False
    anc = getattr(node, "parent", None)
    for part in reversed(parts[:-1]):
        found = False
        while anc is not None:
            if _match_simple(anc, part):
                found = True
                anc = getattr(anc, "parent", None)
                break
            anc = getattr(anc, "parent", None)
        if not found:
            return False
    return True


def _match_simple(node: Widget, selector: str) -> bool:
    if selector == "*":
        return True
    type_name, wid = parse_selector(selector)
    if wid is not None and getattr(node, "id", None) != wid:
        return False
    if type_name is not None and type(node).__name__ != type_name:
        return False
    return True


def query(root: Widget, selector: str | type = "*") -> DOMQuery[Any]:
    """Find nodes under ``root`` (including root) matching ``selector``."""
    nodes = [
        n for n in walk_descendants(root, include_self=True)
        if matches_selector(n, selector)
    ]
    # Textual ``query`` excludes the queried node for descendant searches;
    # keep root if it matches (handy for Screen.query("#main")).
    return DOMQuery(nodes)


def query_one(
    root: Widget,
    selector: str | type,
    expect_type: type[T] | None = None,
) -> T:
    """First match or KeyError / TypeError (Textual ``query_one``)."""
    if isinstance(selector, type) and expect_type is None:
        expect_type = selector  # type: ignore[assignment]
    matches = query(root, selector)
    if not matches:
        raise KeyError(selector)
    node = matches.first()
    if expect_type is not None and not isinstance(node, expect_type):
        raise TypeError(
            f"{selector} is {type(node).__name__}, not {expect_type.__name__}"
        )
    return node  # type: ignore[return-value]
