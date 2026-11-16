"""HelpPanel — key-bindings help panel (Textual HelpPanel subset)."""

from __future__ import annotations

from ..fonts import cell_height
from ..strip import Segment, Strip
from ..widget import Widget
from .footer import collect_active_bindings


class HelpPanel(Widget):
    """Panel displaying active app, screen, and focused-widget bindings."""

    ALLOW_SELECT = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.markup = markup
        self._children = list(children)
        self.layout_dock = "right"
        self.layout_hint_cells = 36

    @property
    def children(self) -> list[Widget]:
        return self._children

    def update_help(self, _event: Any = None) -> None:
        self.refresh()

    def render_lines(self) -> list[Strip]:
        t = self.theme
        accent = t.rgba("accent")
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        items = collect_active_bindings(self.app)
        strips: list[Strip] = [Strip([Segment(" Key Bindings", accent, bold=True)])]
        if not items:
            strips.append(Strip([Segment(" (no bindings)", muted)]))
            return strips
        key_w = max((len(disp) for disp, _, _, _ in items), default=4)
        for disp, action, desc, _raw in items:
            strips.append(Strip([
                Segment(f" {disp.ljust(key_w)} ", accent, bold=True),
                Segment(desc or action, fg),
            ]))
        return strips

    def get_content_height(self) -> int | None:
        return max(cell_height(self.RENDER_FONT_SIZE) + 8, len(self.render_lines()) * cell_height(self.RENDER_FONT_SIZE))
