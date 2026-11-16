"""OptionList — virtualized selectable list (Textual OptionList subset).

Paint path: ``render_lines`` → cell compositor (+ highlight band / scrollbar).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..fonts import cell_height
from ..geometry import Rect
from ..message import Message
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


@dataclass
class Option:
    """One row. Prefer ``lines`` (multi-line runs); else ``runs`` / ``label``."""

    label: str
    id: str | None = None
    runs: list[tuple[str, tuple[int, int, int, int]]] = field(default_factory=list)
    lines: list[list[tuple[str, tuple[int, int, int, int]]]] = field(default_factory=list)
    data: Any = None
    disabled: bool = False

    @property
    def prompt(self) -> str:
        return self.label


class OptionList(Widget):
    """Keyboard + click list. Only visible rows are painted."""

    class OptionMessage(Message):
        def __init__(
            self,
            option: Option | None,
            option_index: int,
            *,
            option_list: OptionList | None = None,
        ) -> None:
            super().__init__()
            self.option = option
            self.option_index = option_index
            self.option_list = option_list
            self.control = option_list

    class OptionHighlighted(OptionMessage):
        """Posted when the highlighted row changes."""

    class OptionSelected(OptionMessage):
        """Posted when a row is activated (Enter / click)."""

    FONT_SIZE = 13
    LINE_H = cell_height(FONT_SIZE)
    RENDER_FONT_SIZE = FONT_SIZE
    DOUBLE_CLICK_S = 0.4
    can_focus = True

    def __init__(
        self,
        *content: Option | str | None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        on_highlight: Callable[[Option | None], None] | None = None,
        on_activate: Callable[[Option | None], None] | None = None,
        on_row_click: Callable[[Option, int, int, bool], None] | None = None,
        click_activates: bool = True,
        row_lines: int = 1,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self._options: list[Option] = []
        self.highlighted: int | None = None
        self.scroll_y = 0
        self.on_highlight = on_highlight
        self.on_activate = on_activate
        self.on_row_click = on_row_click
        self.click_activates = click_activates
        self.row_lines = max(1, row_lines)
        self.layout_hint_h: int | None = None
        self._last_click: tuple[float, int] | None = None
        if content:
            self.add_options(content)

    @property
    def row_h(self) -> int:
        return self.LINE_H * self.row_lines

    @property
    def options(self) -> Sequence[Option]:
        return self._options

    @property
    def highlighted_option(self) -> Option | None:
        if not self._options or self.highlighted is None:
            return None
        i = max(0, min(self.highlighted, len(self._options) - 1))
        return self._options[i]

    @property
    def option_count(self) -> int:
        return len(self._options)

    def get_option_at_index(self, index: int) -> Option:
        return self._options[index]

    def get_option_index(self, option_id: str) -> int:
        for i, o in enumerate(self._options):
            if o.id == option_id:
                return i
        raise KeyError(f"No option with id {option_id!r}")

    def get_option(self, option_id: str) -> Option:
        return self._options[self.get_option_index(option_id)]

    def disable_option_at_index(self, index: int) -> OptionList:
        if 0 <= index < len(self._options):
            self._options[index].disabled = True
            self.refresh()
        return self

    def enable_option_at_index(self, index: int) -> OptionList:
        if 0 <= index < len(self._options):
            self._options[index].disabled = False
            self.refresh()
        return self

    def disable_option(self, option_id: str) -> OptionList:
        return self.disable_option_at_index(self.get_option_index(option_id))

    def enable_option(self, option_id: str) -> OptionList:
        return self.enable_option_at_index(self.get_option_index(option_id))

    def action_cursor_up(self) -> None:
        self.move_highlight(-1)

    def action_cursor_down(self) -> None:
        self.move_highlight(1)

    def action_first(self) -> None:
        idx = self._first_enabled(0, 1)
        if idx is not None:
            self.highlighted = idx
            self._ensure_visible()
            self._emit_highlight()
            self.refresh()

    def action_last(self) -> None:
        idx = self._first_enabled(len(self._options) - 1, -1)
        if idx is not None:
            self.highlighted = idx
            self._ensure_visible()
            self._emit_highlight()
            self.refresh()

    def action_select(self) -> None:
        self.activate_highlighted()

    def add_option(self, item: Option | str | None = None) -> OptionList:
        if item is None:
            self._options.append(Option("─" * 16, disabled=True))
        elif isinstance(item, Option):
            self._options.append(item)
        else:
            self._options.append(Option(str(item)))
        if self.highlighted is None and not self._options[-1].disabled:
            self.highlighted = len(self._options) - 1
        self.refresh()
        return self

    def add_options(self, items: Sequence[Option | str | None]) -> OptionList:
        for item in items:
            self.add_option(item)
        return self

    def remove_option_at_index(self, index: int) -> OptionList:
        if 0 <= index < len(self._options):
            self._options.pop(index)
            self.highlighted = (
                max(0, min(self.highlighted or 0, len(self._options) - 1))
                if self._options
                else None
            )
            self.refresh()
        return self

    def replace_option_prompt_at_index(self, index: int, prompt: Any) -> OptionList:
        if 0 <= index < len(self._options):
            old = self._options[index]
            label = getattr(prompt, "plain", None)
            label_s = str(label) if label is not None else str(prompt)
            self._options[index] = Option(
                label=label_s,
                id=old.id,
                runs=old.runs,
                lines=old.lines,
                data=old.data,
                disabled=old.disabled,
            )
            self.refresh()
        return self

    def remove_option(self, option_id: str) -> OptionList:
        return self.remove_option_at_index(self.get_option_index(option_id))

    def replace_option_prompt(self, option_id: str, prompt: Any) -> OptionList:
        return self.replace_option_prompt_at_index(self.get_option_index(option_id), prompt)

    def scroll_to_highlight(self) -> None:
        self._ensure_visible()

    def validate_highlighted(self, highlighted: int | None) -> int | None:
        if highlighted is None or not self._options:
            return None
        return max(0, min(len(self._options) - 1, int(highlighted)))

    def watch_highlighted(self, _old: int | None, _new: int | None) -> None:
        self.refresh()

    def on_show(self, _event: Any = None) -> None:
        self.refresh()

    def refresh_line(self, y: int) -> None:
        _ = y
        self.refresh()

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        _ = y_start, line_count
        self.refresh()

    def clear_options(self) -> None:
        self._options = []
        self.highlighted = None
        self.scroll_y = 0
        self.refresh()

    def set_options(self, options: Sequence[Option], *, keep_id: str | None = None) -> None:
        self._options = list(options)
        idx = 0
        if keep_id is not None:
            for i, opt in enumerate(self._options):
                if opt.id == keep_id:
                    idx = i
                    break
        self.highlighted = idx if self._options else None
        self._ensure_visible()
        self._emit_highlight()

    def _visible(self) -> int:
        return max(1, self.rect.h // self.row_h)

    def _max_scroll(self) -> int:
        return max(0, len(self._options) - self._visible())

    def _ensure_visible(self) -> None:
        if self.highlighted is None:
            self.scroll_y = 0
            return
        vis = self._visible()
        if self.highlighted < self.scroll_y:
            self.scroll_y = self.highlighted
        elif self.highlighted >= self.scroll_y + vis:
            self.scroll_y = self.highlighted - vis + 1
        self.scroll_y = max(0, min(self._max_scroll(), self.scroll_y))

    def _emit_highlight(self) -> None:
        opt = self.highlighted_option
        if self.on_highlight is not None:
            self.on_highlight(opt)
        idx = -1 if self.highlighted is None else self.highlighted
        self.post_message(self.OptionHighlighted(opt, idx, option_list=self))

    def activate_highlighted(self) -> None:
        opt = self.highlighted_option
        if opt is None or opt.disabled or self.highlighted is None:
            return
        if self.on_activate is not None:
            self.on_activate(opt)
        self.post_message(self.OptionSelected(opt, self.highlighted, option_list=self))

    def _first_enabled(self, start: int, step: int) -> int | None:
        n = len(self._options)
        if not n:
            return None
        i = start
        for _ in range(n):
            if 0 <= i < n and not self._options[i].disabled:
                return i
            i += step
        return None

    def move_highlight(self, delta: int) -> None:
        if not self._options:
            return
        step = 1 if delta >= 0 else -1
        # Skip disabled rows (group headers).
        target = 0 if self.highlighted is None else self.highlighted
        for _ in range(abs(delta)):
            nxt = self._first_enabled(target + step, step)
            if nxt is None or nxt == target:
                break
            target = nxt
        if target != self.highlighted:
            self.highlighted = target
            self._ensure_visible()
            self._emit_highlight()

    def on_key(self, key: str) -> bool:
        if key == "up":
            self.move_highlight(-1)
            return True
        if key == "down":
            self.move_highlight(1)
            return True
        if key == "pageup":
            self.move_highlight(-self._visible())
            return True
        if key == "pagedown":
            self.move_highlight(self._visible())
            return True
        if key == "home":
            idx = self._first_enabled(0, 1)
            if idx is not None:
                self.highlighted = idx
                self._ensure_visible()
                self._emit_highlight()
            return True
        if key == "end":
            idx = self._first_enabled(len(self._options) - 1, -1)
            if idx is not None:
                self.highlighted = idx
                self._ensure_visible()
                self._emit_highlight()
            return True
        if key in ("enter", "return"):
            opt = self.highlighted_option
            if opt is None or opt.disabled:
                return False
            # Always post OptionSelected (callbacks optional); consume Enter.
            self.activate_highlighted()
            return True
        return False

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        if not self._options or self.rect.h <= 0:
            return out
        vis = self._visible()
        start = self.scroll_y
        end = min(len(self._options), start + vis + 1)
        rh = self.row_h
        for i in range(start, end):
            row = i - start
            y = self.rect.y + row * rh
            if y >= self.rect.y1:
                break
            box = Rect(self.rect.x, y, self.rect.w, rh)

            def _pick(_x: int, _y: int, idx: int = i) -> None:
                if self._options[idx].disabled:
                    nxt = self._first_enabled(idx, 1)
                    if nxt is None:
                        nxt = self._first_enabled(idx, -1)
                    if nxt is None:
                        return
                    idx = nxt
                now = time.monotonic()
                double = bool(
                    self._last_click
                    and self._last_click[1] == idx
                    and (now - self._last_click[0]) <= self.DOUBLE_CLICK_S
                )
                self._last_click = (now, idx)
                self.highlighted = idx
                self._ensure_visible()
                self._emit_highlight()
                opt = self._options[idx]
                if self.on_row_click is not None:
                    self.on_row_click(opt, _x, _y, double)
                if self.click_activates:
                    self.activate_highlighted()
                if self.app is not None:
                    self.app.request_draw()

            out.append((box, _pick))
        return out

    def _option_lines(
        self, opt: Option, *, fg, muted,
    ) -> list[list[tuple[str, tuple[int, int, int, int]]]]:
        lines = opt.lines
        if not lines:
            if opt.runs:
                lines = [opt.runs]
            else:
                lines = [[(opt.label, muted if opt.disabled else fg)]]
        return lines

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self._options:
            return [Strip([Segment("(empty)", t.rgba("muted"))])]
        fg = t.rgba("foreground")
        muted = t.rgba("muted")
        accent = t.rgba("accent")
        on_hi = t.rgba("background")
        out: list[Strip] = []
        for i, opt in enumerate(self._options):
            hi = i == self.highlighted and not opt.disabled
            lines = self._option_lines(opt, fg=fg, muted=muted)
            for li in range(self.row_lines):
                runs = lines[li] if li < len(lines) else [(" ", fg)]
                segs: list[Segment] = []
                for text, fill in runs:
                    text = (text or "").replace("\n", " ")
                    if not text:
                        continue
                    if hi:
                        segs.append(Segment(text, on_hi, accent))
                    elif opt.disabled:
                        segs.append(Segment(text, muted))
                    else:
                        segs.append(Segment(text, fill))
                if not segs:
                    if hi:
                        segs = [Segment(" ", on_hi, accent)]
                    else:
                        segs = [Segment(" ", muted if opt.disabled else fg)]
                out.append(Strip(segs))
        return out

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_strips

        t = self.theme
        x0, y0 = self.rect.x, self.rect.y
        x1, y1 = self.rect.x1 - 1, self.rect.y1 - 1
        draw.rectangle([x0, y0, x1, y1], fill=t.rgba("panel"))
        strips = self.render_lines()
        if not self._options:
            paint_strips(
                draw, self.rect, strips,
                theme=t, font_size=self.FONT_SIZE,
                pad_x=8, pad_y=8, fill_bg=False,
            )
            return

        # Full-width highlight band (Strip bgcolor only covers glyphs).
        rh = self.row_h
        vis = self._visible()
        start = self.scroll_y
        end = min(len(self._options), start + vis + 1)
        accent = t.rgba("accent")
        for i in range(start, end):
            if i != self.highlighted or self._options[i].disabled:
                continue
            y = self.rect.y + (i - start) * rh
            hy1 = min(y + rh - 1, self.rect.y1 - 1)
            if hy1 >= y:
                draw.rectangle(
                    [self.rect.x, y, self.rect.x1 - 1, hy1],
                    fill=accent,
                )
            break

        paint_strips(
            draw,
            self.rect,
            strips,
            theme=t,
            font_size=self.FONT_SIZE,
            pad_x=6,
            pad_y=2,
            scroll_y=self.scroll_y * self.row_lines,
            fill_bg=False,
        )

        max_s = self._max_scroll()
        if max_s > 0 and self.rect.h > 8 and self.rect.w > 8:
            muted = t.rgba("muted")
            track_h = max(1, self.rect.h - 4)
            thumb_h = min(
                track_h,
                max(8, int(track_h * (vis / max(1, len(self._options))))),
            )
            travel = max(0, track_h - thumb_h)
            ty = self.rect.y + 2 + (
                int(travel * (self.scroll_y / max_s)) if travel else 0
            )
            bx = self.rect.x1 - 6
            track_y1 = self.rect.y1 - 2
            if track_y1 >= self.rect.y + 2 and bx + 4 >= bx:
                draw.rectangle(
                    [bx, self.rect.y + 2, bx + 4, track_y1],
                    fill=t.rgba("boost"),
                )
                ty1 = min(ty + thumb_h, track_y1)
                if ty1 >= ty:
                    draw.rectangle([bx, ty, bx + 4, ty1], fill=muted)
