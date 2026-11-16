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
_BOLD_SUB_RE = re.compile(r"^\*\*([^*\n:]{3,48})\*\*$")


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
    last_heading_level = 1
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
            last_heading_level = level
            title = m.group(2).strip()
            headings.append(_Heading(
                level=level,
                title=title,
                slug=_slugify(title),
                line_ratio=idx / total,
            ))
            continue
        bm = _BOLD_SUB_RE.match(stripped)
        if bm:
            title = bm.group(1).strip()
            headings.append(_Heading(
                level=min(6, last_heading_level + 1),
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
        toc_title: str = "CONTENTS",
        doc_title: str | None = None,
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
        self.toc_title = toc_title
        self.doc_title = doc_title
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
        created: list[Any] = []
        for h in self._headings:
            while len(stack) > 1 and stack[-1][0] >= h.level:
                stack.pop()
            parent = stack[-1][1]
            node = parent.add(h.title, data=h)
            created.append(node)
            stack.append((h.level, node))
        for node in created:
            if not node.children:
                node.allow_expand = False
        self._toc.root.expand_all()
        from ..events import Load
        from ..widget import dispatch_event

        dispatch_event(self, Load(), "on_load")

    def _scroll_to_heading(self, heading: _Heading) -> None:
        strips = self._document.render_lines()
        if strips:
            # Find the rendered line matching this heading in heading order.
            # Prefer exact trimmed Strip match so body prose mentioning a heading
            # never steals an earlier match from the actual section heading.
            h_idx = self._headings.index(heading) if heading in self._headings else 0
            scan_from = 0
            for i in range(h_idx + 1):
                target = self._headings[i].title.strip().lower()
                found = None
                for li in range(scan_from, len(strips)):
                    if strips[li].plain().strip().lower() == target:
                        found = li
                        break
                if found is None and target:
                    for li in range(scan_from, len(strips)):
                        if target in strips[li].plain().strip().lower():
                            found = li
                            break
                if found is not None:
                    scan_from = found + 1
                    if i == h_idx:
                        self._scroll.scroll_to(y=found)
                        self.refresh()
                        return
        self._scroll.scroll_to_fraction(heading.line_ratio)
        self.refresh()

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
                self._scroll_to_heading(h)
                return True
        return False

    @on(Tree.NodeSelected)
    def _on_toc_selected(self, event: Tree.NodeSelected) -> None:
        data = getattr(event.node, "data", None)
        if isinstance(data, _Heading):
            self._scroll_to_heading(data)

    @on(Tree.NodeHighlighted)
    def _on_toc_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if not self._toc.focused:
            return
        data = getattr(event.node, "data", None)
        if isinstance(data, _Heading):
            self._scroll_to_heading(data)

    def _panel_bounds(self) -> tuple[Rect | None, Rect]:
        if self.show_table_of_contents and self.rect.w >= 320:
            toc_w = min(300, max(200, self.rect.w // 4))
            gap = 6 if self.doc_title is not None else 1
            toc_box = Rect(self.rect.x, self.rect.y, toc_w, self.rect.h)
            doc_box = Rect(
                self.rect.x + toc_w + gap, self.rect.y,
                max(0, self.rect.w - toc_w - gap), self.rect.h,
            )
            return toc_box, doc_box
        return None, self.rect

    def layout_children(self) -> None:
        from ..fonts import cell_height

        toc_box, doc_box = self._panel_bounds()
        top_pad = (cell_height(12) + 8) if self.doc_title is not None else 0
        bot_pad = 4 if self.doc_title is not None else 0
        side_pad = 8 if self.doc_title is not None else 0
        if toc_box is not None:
            self._toc.styles["display"] = "block"
            self._toc.layout(toc_box.inset(side_pad, top_pad, side_pad, bot_pad))
            self._scroll.layout(doc_box.inset(side_pad, top_pad, side_pad, bot_pad))
        else:
            self._toc.styles["display"] = "none"
            self._toc.layout(Rect(0, 0, 0, 0))
            self._scroll.layout(doc_box.inset(side_pad, top_pad, side_pad, bot_pad))

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

    def _paint_box(self, draw: ImageDraw.ImageDraw, box: Rect, title: str, focused: bool) -> None:
        from ..compositor import paint_strips
        from ..fonts import cell_height
        from ..strip import Segment

        if box.w < 4 or box.h < 4:
            return
        t = self.theme
        outline = t.rgba("accent") if focused else t.rgba("primary")
        title_fg = t.rgba("accent") if focused else t.rgba("muted")
        draw.rectangle(
            [box.x, box.y, box.x1 - 1, box.y1 - 1],
            fill=t.rgba("panel"),
            outline=outline,
        )
        if title:
            th = cell_height(12) + 8
            ch = cell_height(12)
            pad_y = max(1, (th - ch) // 2)
            title_rect = Rect(box.x + 6, box.y + 1, max(1, box.w - 12), th - 1)
            paint_strips(
                draw,
                title_rect,
                [Strip([Segment(f" {title}", title_fg)])],
                theme=t,
                font_size=12,
                pad_x=4,
                pad_y=pad_y,
                fill_bg=False,
            )

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        self.layout_children()
        toc_box, doc_box = self._panel_bounds()
        if self.doc_title is not None:
            if toc_box is not None and toc_box.w > 0:
                self._paint_box(draw, toc_box, self.toc_title, self._toc.focused)
            self._paint_box(draw, doc_box, self.doc_title, self._scroll.focused)
        elif toc_box is not None and self._toc.rect.w > 0:
            sx = toc_box.x1
            draw.line(
                [(sx, self.rect.y), (sx, self.rect.y1 - 1)],
                fill=self.theme.rgba("primary"),
                width=1,
            )
        if toc_box is not None and self._toc.rect.w > 0:
            self._toc.paint(draw)
        self._scroll.paint(draw)
