"""KeyPanel — focused widget key-bindings view (Textual KeyPanel subset)."""

from __future__ import annotations

from ..fonts import cell_height
from ..strip import Segment, Strip
from ..widget import Widget
from .footer import collect_active_bindings


class KeyPanel(Widget):
    """Displays bindings for the currently focused widget (or active screen)."""

    ALLOW_SELECT = True
    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        can_focus: bool | None = None,
        can_focus_children: bool | None = None,
        can_maximize: bool | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self._children = list(children)
        if can_focus is not None:
            self.can_focus = bool(can_focus)
        self.can_focus_children = can_focus_children
        self.can_maximize = can_maximize
        self.layout_hint_rows = 6

    @property
    def children(self) -> list[Widget]:
        return self._children

    def render_lines(self) -> list[Strip]:
        t = self.theme
        accent = t.rgba("accent")
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        focus = getattr(self.app, "_focus", None) if self.app else None
        screen = getattr(self.app, "screen", None) if self.app else None
        targets = [focus] if focus is not None else ([screen] if screen is not None else [])
        items = collect_active_bindings(self.app, targets=targets)
        if not items and screen is not None and focus is not None:
            items = collect_active_bindings(self.app, targets=[screen])
        title = type(focus).__name__ if focus is not None else "Keys"
        strips: list[Strip] = [Strip([Segment(f" {title}", accent, bold=True)])]
        if not items:
            strips.append(Strip([Segment(" (none)", muted)]))
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
