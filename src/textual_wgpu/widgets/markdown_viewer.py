"""MarkdownViewer — Markdown body + Table of Contents navigator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..geometry import Rect
from ..message import Message
from ..on import on
from ..reactive import reactive
from ..strip import Strip
from ..widget import Widget
from .markdown import Markdown
from .scroll import VerticalScroll
from .tree import Tree

if TYPE_CHECKING:
    from PIL import ImageDraw

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class _Heading:
    level: int
    title: str
    slug: str
    line_ratio: float


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", title.lower()).strip()
    return re.sub(r"[\s-]+", "-", s)


def _extract_headings(md: str) -> list[_Heading]:
    lines = (md or "").splitlines()
    total = max(1, len(lines))
    headings: list[_Heading] = []
    in_fence = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            headings.append(_Heading(
                level=level,
                title=title,
                slug=_slugify(title),
                line_ratio=idx / total,
            ))
    return headings


class MarkdownViewer(Widget):
    """Markdown viewer with optional Table-of-Contents Tree sidecar."""

    class NavigatorUpdated(Message):
        """Posted when the viewer updates its document / TOC."""

    show_table_of_contents = reactive(True, repaint=True, layout=True)
    ALLOW_SELECT = False

    def __init__(
        self,
        markdown: str | None = None,
        *,
        show_table_of_contents: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        parser_factory: Any = None,
        open_links: bool = True,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.name = name
        self.parser_factory = parser_factory
        self.open_links = bool(open_links)
        self._markdown_text = markdown or ""
        self._document = Markdown(self._markdown_text, id="md-doc")
        self._scroll = VerticalScroll(self._document, id="md-scroll")
        self._toc = Tree("Contents", id="md-toc")
        self._headings: list[_Heading] = []
        self._history: list[str] = []
        self._history_index: int = -1
        self._children: list[Widget] = [self._toc, self._scroll]
        self.show_table_of_contents = bool(show_table_of_contents)
        self._rebuild_toc()

    def watch_show_table_of_contents(self, _old: bool, _new: bool) -> None:
        self.refresh(layout=True)

    def go(self, location: str) -> bool:
        if location.startswith("#"):
            return self.goto_anchor(location[1:])
        from pathlib import Path

        p = Path(location)
        if p.is_file():
            self._history = self._history[: self._history_index + 1] + [str(p)]
            self._history_index = len(self._history) - 1
            self.update(p.read_text(encoding="utf-8"))
            return True
        return False

    def back(self) -> bool:
        if self._history_index > 0:
            self._history_index -= 1
            return self.go(self._history[self._history_index])
        return False

    def forward(self) -> bool:
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            return self.go(self._history[self._history_index])
        return False

    @property
    def children(self) -> list[Widget]:
        return self._children

    @property
    def document(self) -> Markdown:
        return self._document

    @property
    def table_of_contents(self) -> Tree:
        return self._toc

    def _rebuild_toc(self) -> None:
        self._headings = _extract_headings(self._markdown_text)
        self._toc.clear()
        stack: list[tuple[int, Any]] = [(0, self._toc.root)]
        for h in self._headings:
            while len(stack) > 1 and stack[-1][0] >= h.level:
                stack.pop()
            parent = stack[-1][1]
            node = parent.add_leaf(h.title, data=h)
            stack.append((h.level, node))
        self._toc.root.expand()
        from ..events import Load
        from ..widget import dispatch_event

        dispatch_event(self, Load(), "on_load")

    def update(self, markdown: str) -> None:
        self._markdown_text = markdown or ""
        self._document.update(self._markdown_text)
        self._rebuild_toc()
        self.post_message(self.NavigatorUpdated())
        self.refresh(layout=True)

    def goto_anchor(self, slug: str) -> bool:
        target = _slugify(slug)
        for h in self._headings:
            if h.slug == target:
                self._scroll.scroll_to_fraction(h.line_ratio)
                self.refresh()
                return True
        return False

    @on(Tree.NodeSelected)
    def _on_toc_selected(self, event: Tree.NodeSelected) -> None:
        data = getattr(event.node, "data", None)
        if isinstance(data, _Heading):
            self._scroll.scroll_to_fraction(data.line_ratio)
            self.refresh()

    def layout_children(self) -> None:
        if self.show_table_of_contents and self.rect.w >= 320:
            toc_w = min(260, max(160, self.rect.w // 4))
            self._toc.styles["display"] = "block"
            self._toc.layout(Rect(self.rect.x, self.rect.y, toc_w, self.rect.h))
            self._scroll.layout(Rect(
                self.rect.x + toc_w, self.rect.y,
                max(0, self.rect.w - toc_w), self.rect.h,
            ))
        else:
            self._toc.styles["display"] = "none"
            self._toc.layout(Rect(0, 0, 0, 0))
            self._scroll.layout(self.rect)

    def on_key(self, key: str) -> bool:
        if self.show_table_of_contents and self._toc.focused:
            if self._toc.on_key(key):
                return True
        return self._scroll.on_key(key)

    def hits(self):
        out = []
        if self.show_table_of_contents:
            out.extend(self._toc.hits())
        out.extend(self._scroll.hits())
        return out

    def render_lines(self) -> list[Strip]:
        return []

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        self.layout_children()
        if self.show_table_of_contents and self._toc.rect.w > 0:
            self._toc.paint(draw)
        self._scroll.paint(draw)
