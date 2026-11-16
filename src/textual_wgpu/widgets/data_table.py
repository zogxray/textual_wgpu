"""DataTable — columnar grid (Textual DataTable subset)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

from ..fonts import cell_width
from ..geometry import Rect
from ..message import Message
from ..strip import Segment, Strip
from .scroll_view import ScrollView

if TYPE_CHECKING:
    from PIL import ImageDraw


class DataTable(ScrollView):
    """Simple spreadsheet-like table with cursor navigation."""

    class CellHighlighted(Message):
        def __init__(self, value: Any, coordinate: tuple[int, int]) -> None:
            super().__init__()
            self.value = value
            self.coordinate = coordinate  # (row, column)

    class RowSelected(Message):
        def __init__(self, row_index: int, row_key: Any = None) -> None:
            super().__init__()
            self.row_index = row_index
            self.row_key = row_key

    class CellSelected(Message):
        def __init__(self, value: Any, coordinate: tuple[int, int]) -> None:
            super().__init__()
            self.value = value
            self.coordinate = coordinate

    class RowHighlighted(Message):
        def __init__(self, data_table: DataTable, cursor_row: int, row_key: Any = None) -> None:
            super().__init__()
            self.data_table = data_table
            self.control = data_table
            self.cursor_row = cursor_row
            self.row_key = row_key

    class ColumnHighlighted(Message):
        def __init__(self, data_table: DataTable, cursor_column: int, column_key: Any = None) -> None:
            super().__init__()
            self.data_table = data_table
            self.control = data_table
            self.cursor_column = cursor_column
            self.column_key = column_key

    class ColumnSelected(Message):
        def __init__(self, data_table: DataTable, column_index: int, column_key: Any = None) -> None:
            super().__init__()
            self.data_table = data_table
            self.control = data_table
            self.column_index = column_index
            self.column_key = column_key

    class HeaderSelected(Message):
        def __init__(self, data_table: DataTable, column_key: Any, column_index: int, label: str) -> None:
            super().__init__()
            self.data_table = data_table
            self.control = data_table
            self.column_key = column_key
            self.column_index = column_index
            self.label = label

    class RowLabelSelected(Message):
        def __init__(self, data_table: DataTable, row_index: int, row_key: Any, label: Any) -> None:
            super().__init__()
            self.data_table = data_table
            self.control = data_table
            self.row_index = row_index
            self.row_key = row_key
            self.label = label

    def __init__(self, *, id: str | None = None, zebra_stripes: bool = True) -> None:
        super().__init__(id=id)
        self.zebra_stripes = zebra_stripes
        self._columns: list[str] = []
        self._col_widths: list[int] = []
        self._rows: list[list[Any]] = []
        self._row_keys: list[Any] = []
        self.cursor_row = 0
        self.cursor_column = 0
        self.show_header = True

    def clear(self, columns: bool = False) -> None:
        self._rows.clear()
        self._row_keys.clear()
        if columns:
            self._columns.clear()
            self._col_widths.clear()
        self.cursor_row = 0
        self.cursor_column = 0
        self.refresh()

    def add_column(self, label: str, *, width: int | None = None, key: Any = None) -> Any:
        col_key = key if key is not None else str(label)
        self._columns.append(str(label))
        if not hasattr(self, "_col_keys"):
            self._col_keys: list[Any] = []
        self._col_keys.append(col_key)
        self._col_widths.append(width if width is not None else max(8, len(str(label)) + 2))
        return col_key

    def add_columns(self, *labels: str) -> list[Any]:
        return [self.add_column(label) for label in labels]

    def add_row(self, *cells: Any, key: Any = None, height: int | None = None) -> Any:
        _ = height
        row = list(cells)
        if self._columns:
            if len(row) < len(self._columns):
                row.extend([""] * (len(self._columns) - len(row)))
            else:
                row = row[: len(self._columns)]
            for i, cell in enumerate(row):
                self._col_widths[i] = max(self._col_widths[i], min(40, len(str(cell)) + 2))
        row_key = key if key is not None else len(self._rows)
        self._rows.append(row)
        self._row_keys.append(row_key)
        self.refresh()
        return row_key

    def coordinate_to_cell_key(self, coordinate: tuple[int, int]) -> tuple[Any, Any]:
        r, c = coordinate
        col_keys = getattr(self, "_col_keys", [])
        rk = self._row_keys[r] if 0 <= r < len(self._row_keys) else r
        ck = col_keys[c] if 0 <= c < len(col_keys) else c
        return (rk, ck)

    def update_cell(self, row_key: Any, column_key: Any, value: Any) -> None:
        r = row_key if isinstance(row_key, int) and row_key not in self._row_keys else self._row_keys.index(row_key)
        col_keys = getattr(self, "_col_keys", [])
        c = column_key if isinstance(column_key, int) and column_key not in col_keys else col_keys.index(column_key)
        self._rows[r][c] = value
        self.refresh()

    def remove_row(self, row_key: Any) -> None:
        r = row_key if isinstance(row_key, int) and row_key not in self._row_keys else self._row_keys.index(row_key)
        if 0 <= r < len(self._rows):
            self._rows.pop(r)
            self._row_keys.pop(r)
            self.cursor_row = max(0, min(self.cursor_row, len(self._rows) - 1)) if self._rows else 0
            self.refresh()

    def sort(self, *columns: Any, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> None:
        if not self._rows:
            return
        col_keys = getattr(self, "_col_keys", [])
        col_idxs = [
            c if isinstance(c, int) and c not in col_keys else col_keys.index(c)
            for c in (columns or [0])
        ]
        paired = list(zip(self._rows, self._row_keys))
        if key is not None:
            paired.sort(key=lambda pair: tuple(key(pair[0][i]) for i in col_idxs), reverse=reverse)
        else:
            paired.sort(key=lambda pair: tuple(str(pair[0][i]) for i in col_idxs), reverse=reverse)
        self._rows = [p[0] for p in paired]
        self._row_keys = [p[1] for p in paired]
        self.refresh()

    def add_rows(self, rows: Iterable[Sequence[Any]]) -> None:
        for row in rows:
            self.add_row(*row)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def column_count(self) -> int:
        return len(self._columns)

    @property
    def cursor_coordinate(self) -> tuple[int, int]:
        return (self.cursor_row, self.cursor_column)

    def get_row(self, row_index: int) -> list[Any]:
        return list(self._rows[row_index])

    def get_cell(self, row: int, column: int) -> Any:
        return self._rows[row][column]

    def get_cell_at(self, coordinate: tuple[int, int]) -> Any:
        return self._rows[coordinate[0]][coordinate[1]]

    def get_cell_coordinate(self, row_key: Any, column_key: Any) -> tuple[int, int]:
        return (self.get_row_index(row_key), self.get_column_index(column_key))

    def get_row_index(self, row_key: Any) -> int:
        if row_key in self._row_keys:
            return self._row_keys.index(row_key)
        if isinstance(row_key, int) and 0 <= row_key < len(self._rows):
            return row_key
        raise KeyError(row_key)

    def get_column_index(self, column_key: Any) -> int:
        col_keys = getattr(self, "_col_keys", [])
        if column_key in col_keys:
            return col_keys.index(column_key)
        if isinstance(column_key, int) and 0 <= column_key < len(self._columns):
            return column_key
        raise KeyError(column_key)

    def get_column(self, column_key: Any) -> list[Any]:
        ci = self.get_column_index(column_key)
        return [r[ci] for r in self._rows]

    def get_row_at(self, row_index: int) -> list[Any]:
        return list(self._rows[row_index])

    def get_column_at(self, column_index: int) -> list[Any]:
        return [r[column_index] for r in self._rows]

    def get_row_height(self, row_key: Any) -> int:
        _ = row_key
        return 1

    def is_valid_row_index(self, row_index: int) -> bool:
        return 0 <= row_index < len(self._rows)

    def is_valid_column_index(self, column_index: int) -> bool:
        return 0 <= column_index < len(self._columns)

    def is_valid_coordinate(self, coordinate: tuple[int, int]) -> bool:
        return self.is_valid_row_index(coordinate[0]) and self.is_valid_column_index(coordinate[1])

    def move_cursor(self, *, row: int | None = None, column: int | None = None, animate: bool = False) -> None:
        _ = animate
        if row is not None and self._rows:
            self.cursor_row = max(0, min(len(self._rows) - 1, int(row)))
        if column is not None and self._columns:
            self.cursor_column = max(0, min(len(self._columns) - 1, int(column)))
        self._emit_highlight()
        self.refresh()

    def remove_column(self, column_key: Any) -> None:
        ci = self.get_column_index(column_key)
        self._columns.pop(ci)
        self._col_widths.pop(ci)
        col_keys = getattr(self, "_col_keys", [])
        if ci < len(col_keys):
            col_keys.pop(ci)
        for r in self._rows:
            if ci < len(r):
                r.pop(ci)
        self.cursor_column = max(0, min(self.cursor_column, max(0, len(self._columns) - 1)))
        self.refresh()

    def update_cell_at(self, coordinate: tuple[int, int], value: Any, *, update_width: bool = False) -> None:
        _ = update_width
        self._rows[coordinate[0]][coordinate[1]] = value
        self.refresh()

    def refresh_column(self, column_index: int) -> None:
        _ = column_index
        self.refresh()

    def refresh_row(self, row_index: int) -> None:
        _ = row_index
        self.refresh()

    def refresh_coordinate(self, coordinate: tuple[int, int]) -> None:
        _ = coordinate
        self.refresh()

    def refresh_line(self, y: int) -> None:
        _ = y
        self.refresh()

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        _ = y_start, line_count
        self.refresh()

    def validate_cell_padding(self, padding: int) -> int:
        return max(0, int(padding))

    def validate_cursor_coordinate(self, coord: tuple[int, int]) -> tuple[int, int]:
        r = max(0, min(max(0, len(self._rows) - 1), coord[0]))
        c = max(0, min(max(0, len(self._columns) - 1), coord[1]))
        return (r, c)

    def watch_cell_padding(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_cursor_coordinate(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_cursor_type(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_fixed_columns(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_fixed_rows(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_header_height(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_hover_coordinate(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_show_cursor(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_show_header(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_show_row_labels(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def watch_zebra_stripes(self, _o: Any, _n: Any) -> None:
        self.refresh()

    def get_content_height(self) -> int:
        return len(self._rows) + (1 if self.show_header else 0)

    def _emit_highlight(self) -> None:
        if not self._rows:
            return
        r = max(0, min(self.cursor_row, len(self._rows) - 1))
        c = max(0, min(self.cursor_column, max(0, len(self._columns) - 1)))
        val = self._rows[r][c] if self._columns else None
        rk = self._row_keys[r] if r < len(self._row_keys) else r
        col_keys = getattr(self, "_col_keys", [])
        ck = col_keys[c] if c < len(col_keys) else c
        self.post_message(self.CellHighlighted(val, (r, c)))
        self.post_message(self.RowHighlighted(self, r, rk))
        self.post_message(self.ColumnHighlighted(self, c, ck))

    def action_cursor_up(self) -> None:
        self.on_key("up")

    def action_cursor_down(self) -> None:
        self.on_key("down")

    def action_cursor_left(self) -> None:
        self.on_key("left")

    def action_cursor_right(self) -> None:
        self.on_key("right")

    def action_select_cursor(self) -> None:
        self.on_key("enter")

    def action_scroll_top(self) -> None:
        self.cursor_row = 0
        self.scroll_home()

    def action_scroll_bottom(self) -> None:
        if self._rows:
            self.cursor_row = len(self._rows) - 1
        self.scroll_end()

    def on_key(self, key: str) -> bool:
        if not self._rows:
            return super().on_key(key)
        ncols = max(1, len(self._columns))
        if key == "up":
            self.cursor_row = max(0, self.cursor_row - 1)
            self.ensure_visible_line(self.cursor_row + (1 if self.show_header else 0))
            self._emit_highlight()
            self.refresh()
            return True
        if key == "down":
            self.cursor_row = min(len(self._rows) - 1, self.cursor_row + 1)
            self.ensure_visible_line(self.cursor_row + (1 if self.show_header else 0))
            self._emit_highlight()
            self.refresh()
            return True
        if key == "left":
            self.cursor_column = max(0, self.cursor_column - 1)
            self._emit_highlight()
            self.refresh()
            return True
        if key == "right":
            self.cursor_column = min(ncols - 1, self.cursor_column + 1)
            self._emit_highlight()
            self.refresh()
            return True
        if key in ("enter", "return"):
            r, c = self.cursor_coordinate
            self.post_message(self.CellSelected(self.get_cell(r, c), (r, c)))
            self.post_message(self.RowSelected(r, self._row_keys[r]))
            return True
        return super().on_key(key)

    def _row_strip(self, cells: Sequence[Any], *, fill, bgcolor=None) -> Strip:
        segs: list[Segment] = []
        for c, cell in enumerate(cells):
            w = self._col_widths[c] if c < len(self._col_widths) else 10
            text = str(cell)[:w].ljust(w)
            segs.append(Segment(text, fill, bgcolor))
            segs.append(Segment(" ", fill, bgcolor))
        return Strip(segs) if segs else Strip.from_text(" ", fill)

    def render_lines(self) -> list[Strip]:
        t = self.theme
        out: list[Strip] = []
        muted = t.rgba("muted")
        fg = t.rgba("foreground")
        on_hi = t.rgba("background")
        accent = t.rgba("accent")
        if self.show_header:
            segs: list[Segment] = []
            for i, lab in enumerate(self._columns):
                w = self._col_widths[i] if i < len(self._col_widths) else 10
                segs.append(Segment(str(lab)[:w].ljust(w), muted))
                segs.append(Segment(" ", muted))
            out.append(Strip(segs) if segs else Strip.from_text("(no columns)", muted))
        for r, row in enumerate(self._rows):
            hi = r == self.cursor_row
            if hi:
                out.append(self._row_strip(row, fill=on_hi, bgcolor=accent))
            else:
                out.append(self._row_strip(row, fill=fg))
        return out

    def paint_row_backgrounds(self, draw: ImageDraw.ImageDraw, region: Rect) -> None:
        t = self.theme
        header_offset = 1 if self.show_header else 0
        # Zebra + cursor bands for data rows.
        for r in range(len(self._rows)):
            line = r + header_offset
            y = self.line_y(line, region)
            if y is None:
                continue
            hy1 = min(y + self.LINE_H - 1, region.y1 - 1)
            if hy1 < y:
                continue
            if r == self.cursor_row:
                fill = t.rgba("accent")
            elif self.zebra_stripes and r % 2:
                fill = t.rgba("boost")
            else:
                continue
            draw.rectangle([region.x, y, region.x1 - 1, hy1], fill=fill)

    def hits(self) -> list:
        out = list(super().hits())
        region = self.content_region()
        header_offset = 1 if self.show_header else 0
        cw = cell_width(self.FONT_SIZE)

        def make_pick(row: int) -> Callable[[int, int], None]:
            def _pick(x: int, _y: int, r: int = row) -> None:
                self.cursor_row = r
                col = 0
                cx = region.x + 4
                for i, w in enumerate(self._col_widths):
                    if x < cx + w * cw:
                        col = i
                        break
                    cx += w * cw
                    col = i
                self.cursor_column = col
                self._emit_highlight()
                self.post_message(self.RowSelected(r, self._row_keys[r]))
                self.refresh()

            return _pick

        for r in range(len(self._rows)):
            y = self.line_y(r + header_offset, region)
            if y is None or y >= region.y1:
                continue
            out.append((Rect(region.x, y, region.w, self.LINE_H), make_pick(r)))
        return out
