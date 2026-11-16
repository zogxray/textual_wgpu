"""Welcome — built-in welcome card (Textual Welcome subset)."""

from __future__ import annotations

from ..geometry import Rect
from ..widget import Widget
from .button import Button
from .markdown import Markdown
from .static import Static

_WELCOME_MD = """\
# Welcome to textual-wgpu!

Textual-inspired GPU UI runtime on **wgpu**.

- Press **Ctrl+P** for the command palette
- Press **Tab** to move focus across controls
"""


class Welcome(Static):
    """Welcome card with Markdown body + dismiss OK button."""

    def __init__(
        self,
        content: str = "",
        *,
        expand: bool = False,
        shrink: bool = False,
        markup: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(content or "", id=id, markup=markup)
        self.name = name
        self.expand = expand
        self.shrink = shrink
        self.disabled = disabled
        if classes:
            self.classes.update(c.strip().lstrip(".") for c in classes.split() if c.strip())
        md = str(content) if content else _WELCOME_MD
        self._body = Markdown(md, id="text")
        self._close = Button("OK", id="close", variant="success", on_press=self._on_close)
        self._close.layout_dock = "bottom"
        self._children: list[Widget] = [self._body, self._close]

    def _on_close(self) -> None:
        self.remove()

    @property
    def children(self) -> list[Widget]:
        return self._children

    def layout_children(self) -> None:
        super().layout_children()

    def paint(self, draw) -> None:  # noqa: ANN001
        if self.rect.w < 2 or self.rect.h < 2:
            return
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=self.theme.rgba("surface"),
        )
        for child in self._children:
            if (child.styles or {}).get("display") != "none":
                child.paint(draw)

    def hits(self):
        out = []
        for child in self._children:
            out.extend(child.hits())
        return out
