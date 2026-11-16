"""Footer — key-binding bar derived from focused widget + screen + app."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..binding import Binding
from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..strip import Segment, Strip
from ..widget import Widget
from .chrome import _chrome_band_h

if TYPE_CHECKING:
    from PIL import ImageDraw


def collect_active_bindings(
    app: Any,
    *,
    targets: list[Any] | None = None,
) -> list[tuple[str, str, str, str]]:
    """Return ``(key_display, action, description, raw_key)`` for shown bindings."""
    if targets is None:
        targets = []
        if app is not None:
            focus = getattr(app, "_focus", None)
            if focus is not None:
                targets.append(focus)
            screen = getattr(app, "screen", None)
            if screen is not None:
                targets.append(screen)
            targets.append(app)

    seen_keys: set[str] = set()
    out: list[tuple[str, str, str, str]] = []
    for target in targets:
        if target is None:
            continue
        raw_bindings = getattr(type(target), "BINDINGS", None) or getattr(target, "BINDINGS", None) or []
        for item in raw_bindings:
            if isinstance(item, Binding):
                if not item.show:
                    continue
                raw_key = str(item.key).split(",")[0].strip()
                disp = item.key_display or raw_key
                action = item.action
                desc = item.description or action
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                raw_key = str(item[0]).strip()
                disp = raw_key
                action = str(item[1]).strip()
                desc = str(item[2]).strip() if len(item) >= 3 and item[2] else action
            else:
                continue
            if not raw_key or not action or raw_key.lower() in seen_keys:
                continue
            seen_keys.add(raw_key.lower())
            out.append((disp, action, desc, raw_key))
    return out


class Footer(Widget):
    """Docked bottom key-binding strip (Textual ``Footer`` subset)."""

    RENDER_FONT_SIZE = 13

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        show_command_palette: bool = True,
        compact: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self._children = list(children)
        self.show_command_palette = bool(show_command_palette)
        self.compact = bool(compact)
        band = _chrome_band_h(self.RENDER_FONT_SIZE)
        self.layout_dock = "bottom"
        self.layout_hint_h = band
        self.layout_band_h = band
        self.layout_hint_rows = 1

    @property
    def children(self) -> list[Widget]:
        return self._children

    def bindings_changed(self, screen: Any = None) -> None:
        _ = screen
        self.refresh()

    def _entries(self) -> list[tuple[str, str, str]]:
        """(key_display, label, action)."""
        items = collect_active_bindings(self.app)
        out = [(disp, desc, action) for disp, action, desc, _raw in items]
        if self.show_command_palette and not any(a == "command_palette" for _, _, a in out):
            out.append(("^p", "palette", "command_palette"))
        return out

    def _spans(self) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        col = 0
        for disp, desc, action in self._entries():
            chip = f" {disp} {desc} " if not self.compact else f"{disp}:{desc}"
            w = len(chip)
            spans.append((col, w, action))
            col += w + 1
        return spans

    def render_lines(self) -> list[Strip]:
        t = self.theme
        boost = t.rgba("boost")
        accent = t.rgba("accent")
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        entries = self._entries()
        if not entries:
            return [Strip([Segment(" ", muted, boost)])]
        segs: list[Segment] = []
        for i, (disp, desc, _action) in enumerate(entries):
            if i:
                segs.append(Segment(" ", muted, boost))
            if self.compact:
                segs.append(Segment(disp, accent, boost, bold=True))
                segs.append(Segment(f":{desc}", fg, boost))
            else:
                segs.append(Segment(f" {disp}", accent, boost, bold=True))
                segs.append(Segment(f" {desc} ", fg, boost))
        return [Strip(segs)]

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.disabled or self.rect.w < 2:
            return []
        cw = cell_width(self.RENDER_FONT_SIZE)
        pad_x = 8
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        for start, width, action in self._spans():
            r = Rect(self.rect.x + pad_x + start * cw, self.rect.y, width * cw, self.rect.h)

            def _run(_x: int, _y: int, act: str = action) -> None:
                if self.app is not None and hasattr(self.app, "run_action"):
                    self.app.run_action(act)

            out.append((r, _run))
        return out

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        from ..compositor import paint_widget_strips

        if self.rect.w < 2 or self.rect.h < 2:
            return
        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=t.rgba("boost"),
        )
        ch = cell_height(self.RENDER_FONT_SIZE)
        pad_y = max(0, (self.rect.h - ch) // 2)
        paint_widget_strips(
            draw, self, font_size=self.RENDER_FONT_SIZE,
            fill_bg=False, pad_x=8, pad_y=pad_y,
        )
