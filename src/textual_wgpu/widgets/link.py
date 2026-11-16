"""Link — clickable underlined hyperlink (Textual Link subset)."""

from __future__ import annotations

from collections.abc import Callable

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..reactive import reactive
from ..strip import Segment, Strip
from ..widget import Widget


class Link(Widget):
    """Focusable clickable link that invokes ``app.open_url``."""

    can_focus = True
    ALLOW_SELECT = False
    RENDER_FONT_SIZE = 13

    text = reactive("", repaint=True)
    url = reactive(None, repaint=True)

    def __init__(
        self,
        text: str,
        *,
        url: str | None = None,
        tooltip: str | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self.text = str(text)
        self.url = url if url is not None else str(text)
        self.tooltip = tooltip
        self.layout_hint_rows = 1

    def on_click(self, event: Any = None) -> None:
        if event is not None and hasattr(event, "stop"):
            event.stop()
        self.open()

    def update(self, text: str | None = None, url: str | None = None) -> None:
        if text is not None:
            self.text = str(text)
        if url is not None:
            self.url = url
        self.refresh(layout=True)

    def watch_text(self, _old: str, _new: str) -> None:
        self.refresh(layout=True)

    def open(self) -> bool:
        if self.disabled or not self.url:
            return False
        app = self.app
        if app is not None and hasattr(app, "open_url"):
            return bool(app.open_url(str(self.url)))
        return False

    def action_open_link(self) -> None:
        self.open()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key in ("enter", "return", "space"):
            self.open()
            return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2:
            return []

        def _click(_x: int, _y: int) -> None:
            self.focus()
            self.open()

        return [(self.rect, _click)]

    def render_lines(self) -> list[Strip]:
        t = self.theme
        hovered = getattr(self.app, "_hover", None) is self
        focused = self.focused
        if self.disabled:
            fg = t.rgba("muted")
            bg = None
        elif focused:
            fg = t.rgba("background")
            bg = t.rgba("accent")
        elif hovered:
            fg = t.rgba("foreground")
            bg = None
        else:
            fg = t.rgba("accent")
            bg = None
        return [Strip([Segment(self.text, fg, bg, underline=True)])]

    def get_content_width(self) -> int | None:
        return cell_width(self.RENDER_FONT_SIZE) * max(1, len(self.text))

    def get_content_height(self) -> int | None:
        return cell_height(self.RENDER_FONT_SIZE) + 4
