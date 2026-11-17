"""TextArea — multiline text input (Textual TextArea subset).

Paint path: ``render_lines`` → cell compositor (+ cursor overlay).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..fonts import cell_height, cell_width
from ..geometry import Rect
from ..message import Message
from ..strip import Segment, Strip
from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


class TextArea(Widget):
    """Editable multiline field. Focus via App.set_focus / click.

    Selection: Shift+arrows, mouse drag, Ctrl+A; copy with Ctrl+C.
    ``read_only`` allows select/copy without editing (Chat/Help Select mode).
    """

    class Changed(Message):
        """Posted when text content changes."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class Submitted(Message):
        """Posted on Enter in single-line mode (or explicit submit)."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class SelectionChanged(Message):
        """Posted when selection or cursor changes."""

        def __init__(self, text_area: TextArea, selection: Any) -> None:
            super().__init__()
            self.text_area = text_area
            self.control = text_area
            self.selection = selection

    # Own caret/selection — do not participate in screen-level select.
    ALLOW_SELECT = False
    can_focus = True
    FONT_SIZE = 13
    LINE_H = cell_height(FONT_SIZE)
    RENDER_FONT_SIZE = FONT_SIZE

    def __init__(
        self,
        text: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        placeholder: str = "",
        on_submit: Callable[[], None] | None = None,
        on_change: Callable[[str], None] | None = None,
        single_line: bool = False,
        read_only: bool = False,
        clip_strict: bool = False,
        show_line_numbers: bool = False,
        soft_wrap: bool = True,
        tab_behavior: str = "focus",
        theme: str | None = None,
        language: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self._text = text
        self.placeholder = placeholder
        self.read_only = read_only
        self.show_line_numbers = bool(show_line_numbers)
        self.soft_wrap = bool(soft_wrap)
        self.tab_behavior = str(tab_behavior)
        self.language = language
        _ = theme
        # ClipTextArea mode: Ctrl+C copies selection only (no fall-back to all).
        self.clip_strict = clip_strict
        self.cursor = len(text)
        self.scroll_y = 0
        self.on_submit = on_submit
        self.on_change = on_change
        self.single_line = single_line
        self.layout_hint_h: int | None = 32 if single_line else None
        self.layout_hint_w: int | None = None
        self._sel_anchor: int | None = None  # None = no selection
        self._dragging = False

    @property
    def text(self) -> str:
        return self._text

    def load_text(self, text: str) -> None:
        self._text = text.replace("\n", " ") if self.single_line else text
        self.cursor = len(self._text)
        self._sel_anchor = None
        self._clamp_scroll()
        if self.app is not None:
            self.app.request_draw()

    def select_all(self) -> None:
        self._sel_anchor = 0
        self.cursor = len(self._text)
        if self.app is not None:
            self.app.request_draw()

    def selected_text(self) -> str:
        sel = self._selection_range()
        if sel is None:
            return ""
        a, b = sel
        return self._text[a:b]

    def copy_selection(self) -> bool:
        """Copy selection to clipboard.

        Default: fall back to all text when nothing is selected.
        ``clip_strict`` (ClipTextArea): selection only — return False if empty.
        """
        text = self.selected_text()
        if not text:
            if self.clip_strict:
                return False
            text = self._text
        if not text:
            return False
        app = self.app
        if app is not None and hasattr(app, "copy_to_clipboard"):
            return bool(app.copy_to_clipboard(text))
        from ..clipboard import write_clipboard
        return write_clipboard(text)

    def paste_clipboard(self) -> bool:
        """Insert clipboard text at the cursor (Ctrl+V). True if anything pasted."""
        if not self._can_edit():
            return False
        app = self.app
        clip: str | None = None
        if app is not None and hasattr(app, "read_clipboard"):
            clip = app.read_clipboard()
        else:
            from ..clipboard import read_clipboard
            clip = read_clipboard()
        if clip is None or clip == "":
            return False
        if self.single_line:
            clip = clip.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        from ..events import Paste
        from ..widget import dispatch_event

        ev = Paste(text=clip)
        dispatch_event(self, ev, "on_paste")
        if ev.is_prevented or ev.is_stopped:
            return True
        self.insert(ev.text)
        return True

    def _clear_selection(self) -> None:
        self._sel_anchor = None

    def _selection_range(self) -> tuple[int, int] | None:
        if self._sel_anchor is None:
            return None
        a, b = self._sel_anchor, self.cursor
        if a == b:
            return None
        return (a, b) if a <= b else (b, a)

    def _emit_change(self) -> None:
        if self.on_change is not None:
            self.on_change(self._text)
        msg = self.Changed(self._text)
        msg.input = self  # type: ignore[attr-defined]
        msg.control = self  # type: ignore[attr-defined]
        self.post_message(msg)

    def _emit_submit(self) -> None:
        if self.on_submit is not None:
            self.on_submit()
        msg = self.Submitted(self._text)
        msg.input = self  # type: ignore[attr-defined]
        msg.control = self  # type: ignore[attr-defined]
        self.post_message(msg)

    def _can_edit(self) -> bool:
        return not self.disabled and not self.read_only

    def insert(self, ch: str) -> None:
        if not self._can_edit() or not ch:
            return
        if ch == "\n" or ch == "\r":
            if self.single_line:
                self._emit_submit()
                return
            ch = "\n"
        sel = self._selection_range()
        if sel is not None:
            a, b = sel
            self._text = self._text[:a] + ch + self._text[b:]
            self.cursor = a + len(ch)
            self._sel_anchor = None
        else:
            self._text = self._text[: self.cursor] + ch + self._text[self.cursor :]
            self.cursor += len(ch)
        self._clamp_scroll()
        self._emit_cursor()
        self._emit_change()
        if self.app is not None:
            self.app.request_draw()

    def _lines(self) -> list[str]:
        if self._text == "":
            return [""]
        return self._text.split("\n")

    def _cursor_row_col(self) -> tuple[int, int]:
        before = self._text[: self.cursor]
        if not before:
            return 0, 0
        lines = before.split("\n")
        return len(lines) - 1, len(lines[-1])

    def _index_at(self, row: int, col: int) -> int:
        lines = self._lines()
        row = max(0, min(row, len(lines) - 1))
        col = max(0, min(col, len(lines[row])))
        idx = 0
        for i, line in enumerate(lines):
            if i == row:
                return idx + col
            idx += len(line) + 1  # + newline
        return len(self._text)

    def _index_at_xy(self, x: int, y: int) -> int:
        """Map pointer coords to a text index (cell grid)."""
        cw = cell_width(self.FONT_SIZE)
        ch = self.LINE_H
        x0 = self.rect.x + 6
        y0 = self.rect.y + 4
        row = self.scroll_y + max(0, (y - y0) // ch)
        lines = self._lines()
        row = max(0, min(row, len(lines) - 1))
        col = max(0, min(len(lines[row]), (max(0, x - x0) + cw // 2) // cw))
        return self._index_at(row, col)

    def _visible(self) -> int:
        return max(1, (self.rect.h - 8) // self.LINE_H)

    def _clamp_scroll(self) -> None:
        row, _ = self._cursor_row_col()
        vis = self._visible()
        if row < self.scroll_y:
            self.scroll_y = row
        elif row >= self.scroll_y + vis:
            self.scroll_y = row - vis + 1
        self.scroll_y = max(0, self.scroll_y)

    def scroll(self, delta: int) -> None:
        if delta == 0:
            return
        max_s = max(0, len(self._lines()) - self._visible())
        self.scroll_y = max(0, min(max_s, self.scroll_y + int(delta)))
        self.refresh()

    def _emit_cursor(self) -> None:
        from ..events import CursorPosition
        from ..widget import dispatch_event

        row, col = self._cursor_row_col()
        dispatch_event(self, CursorPosition(x=col, y=row), "on_cursor_position")

    def _move_cursor(self, new_cursor: int, *, extend: bool) -> None:
        prev = self.cursor
        new_cursor = max(0, min(len(self._text), new_cursor))
        if extend:
            if self._sel_anchor is None:
                self._sel_anchor = self.cursor
        else:
            self._clear_selection()
        self.cursor = new_cursor
        self._clamp_scroll()
        if self.cursor != prev:
            self._emit_cursor()
            self.post_message(self.SelectionChanged(self, self._selection_range()))

    def action_copy(self) -> None:
        self.copy_selection()

    def action_cut(self) -> None:
        if not self._can_edit():
            return
        if self.copy_selection():
            sel = self._selection_range()
            if sel is not None:
                a, b = sel
                self._text = self._text[:a] + self._text[b:]
                self.cursor = a
                self._sel_anchor = None
                self._emit_change()

    def action_cursor_left(self) -> None:
        self.on_key("left")

    def action_cursor_right(self) -> None:
        self.on_key("right")

    def action_cursor_up(self) -> None:
        self.on_key("up")

    def action_cursor_down(self) -> None:
        self.on_key("down")

    def action_cursor_line_start(self) -> None:
        self.on_key("home")

    def action_cursor_line_end(self) -> None:
        self.on_key("end")

    def action_delete_left(self) -> None:
        self.on_key("backspace")

    def action_delete_right(self) -> None:
        self.on_key("delete")

    def action_delete_left_all(self) -> None:
        if self._can_edit() and self.cursor > 0:
            self._text = self._text[self.cursor :]
            self.cursor = 0
            self._sel_anchor = None
            self._emit_change()

    def action_delete_right_all(self) -> None:
        if self._can_edit() and self.cursor < len(self._text):
            self._text = self._text[: self.cursor]
            self._sel_anchor = None
            self._emit_change()

    # --- Full TextArea parity helpers ----------------------------------------

    @classmethod
    def code_editor(cls, text: str = "", *, language: str | None = None, **kwargs) -> TextArea:
        return cls(text, language=language, show_line_numbers=True, **kwargs)

    def clear(self) -> None:
        self.load_text("")
        self._emit_change()

    def delete(self, start: int = 0, end: int | None = None) -> None:
        if not self._can_edit():
            return
        hi = len(self._text) if end is None else max(0, min(len(self._text), end))
        lo = max(0, min(hi, start))
        self._text = self._text[:lo] + self._text[hi:]
        self.cursor = lo
        self._sel_anchor = None
        self._emit_change()

    def replace(self, insert_text: str, start: int = 0, end: int | None = None) -> None:
        if not self._can_edit():
            return
        hi = len(self._text) if end is None else max(0, min(len(self._text), end))
        lo = max(0, min(hi, start))
        self._text = self._text[:lo] + insert_text + self._text[hi:]
        self.cursor = lo + len(insert_text)
        self._sel_anchor = None
        self._emit_change()

    def edit(self, edit: Any) -> None:
        _ = edit

    def get_line(self, line_index: int) -> str:
        lines = self._lines()
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def get_text_range(self, start: int, end: int) -> str:
        return self._text[start:end]

    def get_column_width(self, row: int, col: int) -> int:
        _ = row
        return col

    def cell_width_to_column_index(self, cell_x: int, row_index: int = 0) -> int:
        _ = row_index
        return max(0, cell_x)

    def clamp_visitable(self, location: tuple[int, int]) -> tuple[int, int]:
        lines = self._lines()
        r = max(0, min(len(lines) - 1, location[0]))
        c = max(0, min(len(lines[r]), location[1]))
        return (r, c)

    def move_cursor(self, location: tuple[int, int], select: bool = False) -> None:
        r, c = self.clamp_visitable(location)
        self._move_cursor(self._index_at(r, c), extend=select)

    def move_cursor_relative(self, rows: int = 0, columns: int = 0, select: bool = False) -> None:
        r, c = self._cursor_row_col()
        self.move_cursor((r + rows, c + columns), select=select)

    def get_cursor_left_location(self) -> tuple[int, int]:
        r, c = self._cursor_row_col()
        return (r, max(0, c - 1))

    def get_cursor_right_location(self) -> tuple[int, int]:
        r, c = self._cursor_row_col()
        return (r, c + 1)

    def get_cursor_up_location(self) -> tuple[int, int]:
        r, c = self._cursor_row_col()
        return (max(0, r - 1), c)

    def get_cursor_down_location(self) -> tuple[int, int]:
        r, c = self._cursor_row_col()
        return (r + 1, c)

    def get_cursor_line_start_location(self) -> tuple[int, int]:
        r, _ = self._cursor_row_col()
        return (r, 0)

    def get_cursor_line_end_location(self) -> tuple[int, int]:
        r, _ = self._cursor_row_col()
        return (r, len(self._lines()[r]))

    def get_cursor_word_left_location(self) -> tuple[int, int]:
        i = max(0, self.cursor - 1)
        while i > 0 and self._text[i - 1].isspace():
            i -= 1
        while i > 0 and not self._text[i - 1].isspace():
            i -= 1
        saved = self.cursor
        self.cursor = i
        rc = self._cursor_row_col()
        self.cursor = saved
        return rc

    def get_cursor_word_right_location(self) -> tuple[int, int]:
        n = len(self._text)
        i = self.cursor
        while i < n and not self._text[i].isspace():
            i += 1
        while i < n and self._text[i].isspace():
            i += 1
        saved = self.cursor
        self.cursor = i
        rc = self._cursor_row_col()
        self.cursor = saved
        return rc

    def get_target_document_location(self, event: Any) -> tuple[int, int]:
        _ = event
        return self._cursor_row_col()

    def find_matching_bracket(self, *_args: Any) -> tuple[int, int] | None:
        return None

    def record_cursor_width(self) -> None:
        pass

    def scroll_cursor_visible(self, animate: bool = False) -> None:
        _ = animate
        self._clamp_scroll()

    def select_line(self, index: int | None = None) -> None:
        r = self._cursor_row_col()[0] if index is None else index
        lines = self._lines()
        if 0 <= r < len(lines):
            self._sel_anchor = self._index_at(r, 0)
            self.cursor = self._index_at(r, len(lines[r]))

    def undo(self) -> None:
        pass

    def redo(self) -> None:
        pass

    def action_undo(self) -> None:
        self.undo()

    def action_redo(self) -> None:
        self.redo()

    def action_paste(self) -> None:
        self.paste_clipboard()

    def action_select_line(self) -> None:
        self.select_line()

    def action_select_all(self) -> None:
        self.select_all()

    def action_cursor_page_up(self) -> None:
        self.move_cursor_relative(rows=-self._visible())

    def action_cursor_page_down(self) -> None:
        self.move_cursor_relative(rows=self._visible())

    def action_cursor_word_left(self) -> None:
        self.move_cursor(self.get_cursor_word_left_location())

    def action_cursor_word_right(self) -> None:
        self.move_cursor(self.get_cursor_word_right_location())

    def action_delete_word_left(self) -> None:
        r, c = self.get_cursor_word_left_location()
        self.delete(self._index_at(r, c), self.cursor)

    def action_delete_word_right(self) -> None:
        r, c = self.get_cursor_word_right_location()
        self.delete(self.cursor, self._index_at(r, c))

    def action_delete_line(self) -> None:
        r, _ = self._cursor_row_col()
        lines = self._lines()
        if 0 <= r < len(lines):
            start = self._index_at(r, 0)
            end = self._index_at(r, len(lines[r])) + (1 if r + 1 < len(lines) else 0)
            self.delete(start, end)

    def action_delete_to_start_of_line(self) -> None:
        r, _ = self._cursor_row_col()
        self.delete(self._index_at(r, 0), self.cursor)

    def action_delete_to_end_of_line(self) -> None:
        r, _ = self._cursor_row_col()
        self.delete(self.cursor, self._index_at(r, len(self._lines()[r])))

    def action_delete_to_end_of_line_or_delete_line(self) -> None:
        r, c = self._cursor_row_col()
        if c >= len(self._lines()[r]):
            self.action_delete_line()
        else:
            self.action_delete_to_end_of_line()

    def refresh_line(self, y: int) -> None:
        _ = y
        self.refresh()

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        _ = y_start, line_count
        self.refresh()

    def register_language(self, name: str, language: Any, highlight_query: str = "") -> None:
        _ = name, language, highlight_query

    def register_theme(self, theme: Any) -> None:
        _ = theme

    def update_highlight_query(self, name: str, highlight_query: str) -> None:
        _ = name, highlight_query

    def update_suggestion(self) -> None:
        pass

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key == "ctrl+a":
            self.select_all()
            return True
        if key == "ctrl+c":
            self.copy_selection()
            return True
        if key == "ctrl+v":
            return self.paste_clipboard()
        # Navigation / selection (allowed in read_only).
        extend = key.startswith("shift+")
        base = key[6:] if extend else key
        if base == "left":
            self._move_cursor(self.cursor - 1, extend=extend)
            return True
        if base == "right":
            self._move_cursor(self.cursor + 1, extend=extend)
            return True
        if base == "up":
            row, col = self._cursor_row_col()
            if row > 0:
                self._move_cursor(self._index_at(row - 1, col), extend=extend)
            elif extend and self._sel_anchor is None:
                self._sel_anchor = self.cursor
            return True
        if base == "down":
            row, col = self._cursor_row_col()
            self._move_cursor(self._index_at(row + 1, col), extend=extend)
            return True
        if base == "home":
            row, _ = self._cursor_row_col()
            self._move_cursor(self._index_at(row, 0), extend=extend)
            return True
        if base == "end":
            row, _ = self._cursor_row_col()
            lines = self._lines()
            self._move_cursor(self._index_at(row, len(lines[row])), extend=extend)
            return True

        if not self._can_edit():
            return False

        if key in ("enter", "return"):
            if self.single_line:
                self._emit_submit()
                return True
            self.insert("\n")
            return True
        if key == "backspace":
            sel = self._selection_range()
            if sel is not None:
                a, b = sel
                self._text = self._text[:a] + self._text[b:]
                self.cursor = a
                self._sel_anchor = None
                self._clamp_scroll()
                self._emit_change()
            elif self.cursor > 0:
                self._text = self._text[: self.cursor - 1] + self._text[self.cursor :]
                self.cursor -= 1
                self._clamp_scroll()
                self._emit_change()
            return True
        if key == "delete":
            sel = self._selection_range()
            if sel is not None:
                a, b = sel
                self._text = self._text[:a] + self._text[b:]
                self.cursor = a
                self._sel_anchor = None
                self._emit_change()
            elif self.cursor < len(self._text):
                self._text = self._text[: self.cursor] + self._text[self.cursor + 1 :]
                self._emit_change()
            return True
        if key == "space":
            self.insert(" ")
            return True
        return False

    def on_pointer_down(self, x: int, y: int) -> bool:
        if self.disabled or self.rect.w < 2 or self.rect.h < 2:
            return False
        if not self.rect.contains(x, y):
            return False
        self.focus()
        idx = self._index_at_xy(x, y)
        self._sel_anchor = idx
        self.cursor = idx
        self._dragging = True
        self._clamp_scroll()
        return True

    def on_pointer_move(self, x: int, y: int) -> bool:
        if not self._dragging:
            return False
        self.cursor = self._index_at_xy(x, y)
        self._clamp_scroll()
        return True

    def on_pointer_up(self, x: int, y: int) -> bool:
        if not self._dragging:
            return False
        self.cursor = self._index_at_xy(x, y)
        if self._sel_anchor is not None and self._sel_anchor == self.cursor:
            self._sel_anchor = None
        self._dragging = False
        self._clamp_scroll()
        return True

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        if self.rect.w < 2 or self.rect.h < 2 or self.disabled:
            return []

        def _down(x: int, y: int) -> None:
            self.on_pointer_down(x, y)

        return [(self.rect, _down)]

    def render_lines(self) -> list[Strip]:
        t = self.theme
        if not self._text and self.placeholder and not self._focused:
            return [Strip([Segment(self.placeholder[:80], t.rgba("muted"))])]
        fg = t.rgba("muted") if (self.disabled or self.read_only) else t.rgba("foreground")
        if self.read_only and not self.disabled:
            fg = t.rgba("foreground")
        sel = self._selection_range()
        show_sel = sel is not None and self._focused
        accent = t.rgba("accent")
        on_sel = t.rgba("background")
        out: list[Strip] = []
        for i, line in enumerate(self._lines()):
            if show_sel:
                row_start = self._index_at(i, 0)
                row_end = row_start + len(line)
                a = max(sel[0], row_start)
                b = min(sel[1], row_end)
                if a < b:
                    pre = line[: a - row_start]
                    mid = line[a - row_start : b - row_start]
                    post = line[b - row_start :]
                    segs: list[Segment] = []
                    if pre:
                        segs.append(Segment(pre, fg))
                    if mid:
                        segs.append(Segment(mid, on_sel, accent))
                    if post:
                        segs.append(Segment(post, fg))
                    out.append(Strip(segs) if segs else Strip.from_text("", fg))
                    continue
            out.append(Strip.from_text(line if line else " ", fg))
        if self.show_line_numbers:
            gutter_w = max(3, len(str(len(self._lines()))) + 1)
            gutter_fg = t.rgba("muted")
            numbered: list[Strip] = []
            for idx, strip in enumerate(out or [Strip.from_text(" ", fg)], start=1):
                prefix = Segment(f"{str(idx).rjust(gutter_w - 1)} ", gutter_fg)
                numbered.append(Strip([prefix, *strip.segments]))
            return numbered
        return out or [Strip.from_text(" ", fg)]

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        # Collapsed panes (layout_hint_h=0) must not draw — PIL requires y1 >= y0.
        if self.rect.w < 2 or self.rect.h < 2:
            return
        from ..compositor import paint_widget_strips

        t = self.theme
        border = t.rgba("accent") if self._focused else t.rgba("muted")
        bx0, by0 = self.rect.x, self.rect.y
        bx1, by1 = self.rect.x1 - 1, self.rect.y1 - 1
        if bx1 < bx0 or by1 < by0:
            return
        draw.rectangle(
            [bx0, by0, bx1, by1],
            fill=t.rgba("panel"),
            outline=border,
        )
        paint_widget_strips(
            draw, self,
            font_size=self.FONT_SIZE,
            pad_x=6, pad_y=4,
            scroll_y=self.scroll_y,
            fill_bg=False,
        )
        # Cursor caret (geometry overlay — not part of Strip content).
        sel = self._selection_range()
        if self._focused and not self.disabled and sel is None:
            row_c, col_c = self._cursor_row_col()
            if row_c >= self.scroll_y:
                cw = cell_width(self.FONT_SIZE)
                ch = self.LINE_H
                x0 = self.rect.x + 6
                y0 = self.rect.y + 4
                cx = x0 + col_c * cw
                cy = y0 + (row_c - self.scroll_y) * ch
                if cy + 2 < self.rect.y1:
                    draw.line(
                        [(cx, cy), (cx, cy + ch - 2)],
                        fill=t.rgba("accent"),
                        width=1,
                    )
