"""Command palette — Textual ``action_command_palette`` subset."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .actions import list_actions
from .geometry import Rect
from .screen import Screen
from .widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


class CommandPaletteScreen(Screen):
    """Fuzzy-filterable list of ``action_*`` commands for app + underlay screen."""

    BINDINGS = [
        ("escape", "dismiss"),
        ("enter", "run_selected"),
        ("up", "cursor_up"),
        ("down", "cursor_down"),
    ]

    DEFAULT_CSS = """
    CommandPaletteScreen { layer: above; }
    """

    def __init__(self, commands: list[tuple[str, str]] | None = None) -> None:
        super().__init__()
        self._all: list[tuple[str, str]] = list(commands or [])
        self._query = ""
        self._index = 0
        self._filtered: list[tuple[str, str]] = []
        self._refilter()

    def compose(self) -> Iterator[Widget]:
        yield from ()

    def _refilter(self) -> None:
        q = self._query.lower().strip()
        if not q:
            self._filtered = list(self._all)
        else:
            self._filtered = [
                (name, doc)
                for name, doc in self._all
                if q in name.lower() or q in doc.lower()
            ]
        self._index = max(0, min(self._index, max(0, len(self._filtered) - 1)))

    def action_dismiss(self) -> None:
        if self.app is not None:
            self.app.pop_screen()

    def action_cursor_up(self) -> None:
        if self._filtered:
            self._index = max(0, self._index - 1)

    def action_cursor_down(self) -> None:
        if self._filtered:
            self._index = min(len(self._filtered) - 1, self._index + 1)

    def action_run_selected(self) -> None:
        if not self._filtered or self.app is None:
            self.action_dismiss()
            return
        name, _doc = self._filtered[self._index]
        app = self.app
        app.pop_screen()
        app.run_action(name)

    def on_char(self, ch: str) -> bool:
        if ch and ch >= " ":
            self._query += ch
            self._refilter()
            return True
        return False

    def on_key(self, key: str) -> bool:
        if key == "backspace":
            self._query = self._query[:-1]
            self._refilter()
            return True
        # BINDINGS → action_* via Screen / App.run_action path.
        return super().on_key(key)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        from .fonts import font

        t = self.theme
        w, h = self.rect.w, self.rect.h
        # Dim underlay.
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1, self.rect.y1],
            fill=(0, 0, 0, 160),
        )
        mw = min(560, max(320, w - 80))
        mh = min(420, max(200, h - 80))
        mx = self.rect.x + (w - mw) // 2
        my = self.rect.y + max(40, (h - mh) // 3)
        draw.rectangle(
            [mx, my, mx + mw, my + mh],
            fill=t.rgba("surface"), outline=t.rgba("accent"), width=2,
        )
        draw.text(
            (mx + 16, my + 12),
            "Command palette",
            font=font(15),
            fill=t.rgba("foreground"),
        )
        # Query field.
        qbox = Rect(mx + 16, my + 40, mw - 32, 28)
        draw.rectangle(
            [qbox.x, qbox.y, qbox.x1, qbox.y1],
            fill=t.rgba("panel"), outline=t.rgba("accent"),
        )
        draw.text(
            (qbox.x + 8, qbox.y + 5),
            (self._query + "▌")[:64],
            font=font(13),
            fill=t.rgba("foreground"),
        )
        row_h = 22
        list_top = my + 78
        visible = max(1, (mh - 100) // row_h)
        start = max(0, min(self._index - visible // 2, max(0, len(self._filtered) - visible)))
        if not self._filtered:
            draw.text(
                (mx + 16, list_top),
                "(no matching commands)",
                font=font(13),
                fill=t.rgba("muted"),
            )
        else:
            for i in range(start, min(len(self._filtered), start + visible)):
                name, doc = self._filtered[i]
                y = list_top + (i - start) * row_h
                row = Rect(mx + 12, y, mw - 24, row_h - 2)
                if i == self._index:
                    draw.rectangle(
                        [row.x, row.y, row.x1, row.y1],
                        fill=t.rgba("accent"),
                    )
                    fill = t.rgba("background")
                else:
                    fill = t.rgba("foreground")
                label = f"{name}  —  {doc}" if doc and doc != name else name
                draw.text((row.x + 8, row.y + 2), label[:70], font=font(13), fill=fill)
        draw.text(
            (mx + 16, my + mh - 24),
            "Type to filter · ↑↓ · Enter run · Esc close",
            font=font(11),
            fill=t.rgba("muted"),
        )

    def hits(self):
        # Palette is keyboard-first; absorb clicks on the card area.
        return [(self.rect, lambda _x, _y: None)]


def collect_commands(app: Any) -> list[tuple[str, str]]:
    """Merge ``action_*`` from active screen + app (dedup by name)."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    targets: list[Any] = []
    screen = getattr(app, "screen", None)
    # Prefer underlay (mode) screen for commands when palette itself is top.
    stack = getattr(app, "_screen_stack", None) or []
    if stack and getattr(stack[-1].screen, "__class__", None) is CommandPaletteScreen:
        if len(stack) >= 2:
            targets.append(stack[-2].screen)
        elif hasattr(app, "_ensure_screen"):
            try:
                targets.append(app._ensure_screen())
            except Exception:  # noqa: BLE001
                pass
        else:
            targets.append(screen)
    else:
        targets.append(screen)
    targets.append(app)
    for target in targets:
        if target is None:
            continue
        for name, doc in list_actions(target):
            if name in seen:
                continue
            # Skip palette-internal / stack plumbing noise in the list? Keep dismiss off.
            if name in ("dismiss", "run_selected", "cursor_up", "cursor_down"):
                continue
            seen.add(name)
            out.append((name, doc))
    out.sort(key=lambda t: t[0])
    return out
