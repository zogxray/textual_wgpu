"""CSS auto-layout — dock + fr/percent/cells/auto content-size (+ simple grid).

Replaces ModeBar-position conventions: parents lay out children from resolved
styles (``layout_hint_*``, ``layout_*_fr``, ``styles.height_frac``, content size).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .fonts import cell_height, cell_width, cells_to_px, rows_to_px
from .geometry import Rect

if TYPE_CHECKING:
    from .widget import Widget

Axis = Literal["horizontal", "vertical"]


@dataclass
class _Slot:
    widget: Widget
    kind: Literal["fixed", "fr", "percent", "auto", "flex"]
    value: float  # px for fixed; weight for fr/flex; 0-1 for percent; 0 for auto
    min_px: int = 0
    max_px: int | None = None


def _is_displayed(c: Widget) -> bool:
    return (c.styles or {}).get("display") != "none"


def _dock_of(c: Widget) -> str | None:
    dock = getattr(c, "layout_dock", None)
    if dock:
        return str(dock)
    d = (c.styles or {}).get("dock")
    return str(d) if d else None


def _apply_offset(c: Widget, rect: Rect) -> Rect:
    off = (c.styles or {}).get("offset")
    if isinstance(off, tuple) and len(off) == 2:
        return Rect(rect.x + int(off[0]), rect.y + int(off[1]), rect.w, rect.h)
    return rect


def _padding(c: Widget) -> tuple[int, int, int, int]:
    """Return (top, right, bottom, left) px from CSS ``padding`` if present."""
    raw = (c.styles or {}).get("padding")
    if not raw:
        return (0, 0, 0, 0)
    if isinstance(raw, (tuple, list)) and len(raw) == 4:
        return tuple(int(x) for x in raw)  # type: ignore[return-value]
    if isinstance(raw, str):
        from .css import parse_size

        parts = raw.replace(",", " ").split()
        vals: list[int] = []
        for p in parts:
            sz = parse_size(p)
            if sz and sz.kind == "cells":
                vals.append(cells_to_px(int(sz.value)))
            else:
                try:
                    vals.append(int(float(p)))
                except ValueError:
                    vals.append(0)
        if len(vals) == 1:
            v = vals[0]
            return (v, v, v, v)
        if len(vals) == 2:
            return (vals[0], vals[1], vals[0], vals[1])
        if len(vals) == 3:
            return (vals[0], vals[1], vals[2], vals[1])
        if len(vals) >= 4:
            return (vals[0], vals[1], vals[2], vals[3])
    return (0, 0, 0, 0)


def content_width_px(widget: Widget) -> int:
    """Intrinsic width in px (``get_content_width`` or Strip/render heuristics)."""
    fn = getattr(widget, "get_content_width", None)
    if callable(fn):
        try:
            w = fn()
            if isinstance(w, int) and w >= 0:
                return w
        except Exception:  # noqa: BLE001
            pass
    cells = getattr(widget, "layout_hint_cells", None)
    if isinstance(cells, int):
        return cells_to_px(cells)
    w = getattr(widget, "layout_hint_w", None)
    if isinstance(w, int):
        return w
    # Strip path: longest render line.
    if hasattr(widget, "render_lines"):
        try:
            lines = widget.render_lines()
            if lines:
                n = max((len(s) for s in lines), default=0)
                return cells_to_px(max(1, n))
        except Exception:  # noqa: BLE001
            pass
    return cell_width(13) * 8


def content_height_px(widget: Widget) -> int:
    """Intrinsic height in px."""
    fn = getattr(widget, "get_content_height", None)
    if callable(fn):
        try:
            h = fn()
            if isinstance(h, int) and h >= 0:
                return h
        except Exception:  # noqa: BLE001
            pass
    h = getattr(widget, "layout_hint_h", None)
    if isinstance(h, int):
        return h
    rows = getattr(widget, "layout_hint_rows", None)
    if isinstance(rows, int):
        return rows_to_px(rows)
    if hasattr(widget, "render_lines"):
        try:
            lines = widget.render_lines()
            if lines:
                return rows_to_px(max(1, len(lines)))
        except Exception:  # noqa: BLE001
            pass
    return cell_height(13) + 4


def resolve_main_size(widget: Widget, *, axis: Axis, parent_main: int) -> _Slot:
    """Classify widget size along the main axis of a flow layout."""
    styles = widget.styles or {}
    if axis == "horizontal":
        # Fixed px / cells
        w = getattr(widget, "layout_hint_w", None)
        if isinstance(w, int):
            return _Slot(widget, "fixed", float(w), min_px=_min_width_px(widget))
        cells = getattr(widget, "layout_hint_cells", None)
        if isinstance(cells, int):
            return _Slot(
                widget, "fixed", float(cells_to_px(cells)), min_px=_min_width_px(widget),
            )
        fr = getattr(widget, "layout_width_fr", None)
        if isinstance(fr, (int, float)) and float(fr) > 0:
            return _Slot(widget, "fr", float(fr), min_px=_min_width_px(widget))
        # styles may stash fr
        sfr = styles.get("width_fr")
        if isinstance(sfr, (int, float)) and float(sfr) > 0:
            return _Slot(widget, "fr", float(sfr), min_px=_min_width_px(widget))
        pct = getattr(widget, "layout_frac", None)
        if isinstance(pct, (int, float)) and float(pct) > 0:
            return _Slot(widget, "percent", float(pct), min_px=_min_width_px(widget))
        if styles.get("width") == "auto" or styles.get("width_auto"):
            return _Slot(
                widget, "auto", float(content_width_px(widget)),
                min_px=_min_width_px(widget),
            )
        return _Slot(widget, "flex", 1.0, min_px=_min_width_px(widget))

    # vertical
    h = getattr(widget, "layout_hint_h", None)
    if isinstance(h, int):
        return _Slot(widget, "fixed", float(h), min_px=0, max_px=_max_height_px(widget))
    rows = getattr(widget, "layout_hint_rows", None)
    if isinstance(rows, int):
        # Chrome (ModeBar/KeyBar/StatusBar): CSS height:1 → padded band, not bare cell.
        band = getattr(widget, "layout_band_h", None)
        px = (
            int(rows) * int(band)
            if isinstance(band, int) and band > 0
            else rows_to_px(rows)
        )
        return _Slot(
            widget, "fixed", float(px),
            min_px=0, max_px=_max_height_px(widget),
        )
    fr = getattr(widget, "layout_height_fr", None)
    if isinstance(fr, (int, float)) and float(fr) > 0:
        return _Slot(widget, "fr", float(fr), max_px=_max_height_px(widget))
    sfr = styles.get("height_fr")
    if isinstance(sfr, (int, float)) and float(sfr) > 0:
        return _Slot(widget, "fr", float(sfr), max_px=_max_height_px(widget))
    pct = styles.get("height_frac")
    if isinstance(pct, (int, float)) and float(pct) > 0:
        return _Slot(widget, "percent", float(pct), max_px=_max_height_px(widget))
    if styles.get("height") == "auto" or styles.get("height_auto"):
        return _Slot(
            widget, "auto", float(content_height_px(widget)),
            max_px=_max_height_px(widget),
        )
    return _Slot(widget, "flex", 1.0, max_px=_max_height_px(widget))


def _min_width_px(widget: Widget) -> int:
    cells = getattr(widget, "layout_min_cells", None)
    if isinstance(cells, int) and cells > 0:
        return cells_to_px(cells)
    return 0


def _max_height_px(widget: Widget) -> int | None:
    rows = (widget.styles or {}).get("max_height_rows")
    if isinstance(rows, int) and rows > 0:
        return rows_to_px(rows)
    return None


def _distribute(slots: list[_Slot], budget: int) -> list[int]:
    """Assign px sizes for slots given total budget along main axis."""
    n = len(slots)
    if n == 0:
        return []
    sizes = [0] * n
    remaining = max(0, budget)

    # 1) fixed + auto (content)
    for i, s in enumerate(slots):
        if s.kind in ("fixed", "auto"):
            px = int(s.value)
            if s.max_px is not None:
                px = min(px, s.max_px)
            px = max(px, s.min_px)
            sizes[i] = px
            remaining -= px

    remaining = max(0, remaining)

    # 2) percent of original budget (Textual-ish: % of parent)
    pct_indices = [i for i, s in enumerate(slots) if s.kind == "percent"]
    pct_used = 0
    for i in pct_indices:
        px = int(budget * slots[i].value)
        px = max(px, slots[i].min_px)
        if slots[i].max_px is not None:
            px = min(px, slots[i].max_px)
        sizes[i] = px
        pct_used += px
    # If percents overshoot, scale down.
    if pct_used > remaining and pct_used > 0:
        scale = remaining / pct_used
        for i in pct_indices:
            sizes[i] = int(sizes[i] * scale)
        pct_used = sum(sizes[i] for i in pct_indices)
    remaining = max(0, remaining - pct_used)

    # 3) fr + flex share the rest
    fr_indices = [i for i, s in enumerate(slots) if s.kind in ("fr", "flex")]
    weight_sum = sum(slots[i].value for i in fr_indices) or float(len(fr_indices))
    allocated = 0
    for j, i in enumerate(fr_indices):
        if j == len(fr_indices) - 1:
            px = remaining - allocated
        else:
            px = int(remaining * (slots[i].value / weight_sum))
            allocated += px
        px = max(px, slots[i].min_px)
        if slots[i].max_px is not None:
            px = min(px, slots[i].max_px)
        sizes[i] = px
    return sizes


def layout_flow(children: list[Widget], box: Rect, *, horizontal: bool) -> None:
    """Lay out non-docked children in a row or column."""
    shown = [c for c in children if _is_displayed(c)]
    if not shown:
        return
    axis: Axis = "horizontal" if horizontal else "vertical"
    parent_main = box.w if horizontal else box.h
    slots = [resolve_main_size(c, axis=axis, parent_main=parent_main) for c in shown]
    sizes = _distribute(slots, parent_main)
    if horizontal:
        x = box.x
        for c, w in zip(shown, sizes):
            c.layout(_apply_offset(c, Rect(x, box.y, max(0, w), box.h)))
            x += w
    else:
        y = box.y
        for c, h in zip(shown, sizes):
            c.layout(_apply_offset(c, Rect(box.x, y, box.w, max(0, h))))
            y += h


def layout_grid(children: list[Widget], box: Rect, *, columns: int) -> None:
    """Simple equal-cell grid (Textual ``layout: grid`` lite)."""
    shown = [c for c in children if _is_displayed(c)]
    if not shown or columns < 1:
        return
    cols = max(1, columns)
    rows = (len(shown) + cols - 1) // cols
    cell_w = max(1, box.w // cols)
    cell_h = max(1, box.h // max(1, rows))
    for i, c in enumerate(shown):
        r, col = divmod(i, cols)
        # Honour fixed/fr within cell? Fill cell for now.
        c.layout(_apply_offset(c, Rect(
            box.x + col * cell_w,
            box.y + r * cell_h,
            cell_w if col < cols - 1 else box.w - col * cell_w,
            cell_h if r < rows - 1 else box.h - r * cell_h,
        )))


def _align_of(parent: Widget) -> tuple[str | None, str | None]:
    styles = parent.styles or {}
    ah = styles.get("content_align_h")
    av = styles.get("content_align_v")
    raw = styles.get("content-align") or styles.get("content_align")
    if isinstance(raw, str) and raw.strip():
        parts = raw.lower().split()
        for p in parts:
            if p in ("left", "center", "right") and ah is None:
                ah = p
            elif p in ("top", "middle", "bottom") and av is None:
                av = p
    return (str(ah).lower() if ah else None, str(av).lower() if av else None)


def _layout_aligned(children: list[Widget], box: Rect, ah: str | None, av: str | None) -> None:
    shown = [c for c in children if _is_displayed(c)]
    if not shown:
        return
    # Compute natural/requested sizes per child, then place within box.
    cw_list: list[int] = []
    ch_list: list[int] = []
    for c in shown:
        sw = resolve_main_size(c, axis="horizontal", parent_main=box.w)
        sh = resolve_main_size(c, axis="vertical", parent_main=box.h)
        if sw.kind in ("fixed", "auto"):
            cw = int(sw.value)
        elif sw.kind == "percent":
            cw = int(box.w * sw.value)
        elif ah in ("center", "right", "left"):
            cw = min(box.w, content_width_px(c))
        else:
            cw = box.w
        if sh.kind in ("fixed", "auto"):
            ch = int(sh.value)
        elif sh.kind == "percent":
            ch = int(box.h * sh.value)
        elif av in ("middle", "bottom", "top"):
            ch = min(box.h, content_height_px(c))
        else:
            ch = box.h
        cw_list.append(max(1, min(box.w, cw)))
        ch_list.append(max(1, min(box.h, ch)))

    total_h = sum(ch_list)
    if av == "middle":
        cur_y = box.y + max(0, (box.h - total_h) // 2)
    elif av == "bottom":
        cur_y = box.y + max(0, box.h - total_h)
    else:
        cur_y = box.y

    for c, cw, ch in zip(shown, cw_list, ch_list):
        if ah == "center":
            cx = box.x + max(0, (box.w - cw) // 2)
        elif ah == "right":
            cx = box.x + max(0, box.w - cw)
        else:
            cx = box.x
        c.layout(_apply_offset(c, Rect(cx, cur_y, cw, ch)))
        cur_y += ch


def layout_children(parent: Widget, children: list[Widget] | None = None) -> None:
    """Dock → padding → grid or flow. Entry point for Screen / Vertical / Horizontal."""
    kids = list(children if children is not None else (parent.children or []))
    shown = [c for c in kids if _is_displayed(c)]
    if not shown:
        return

    pt, pr, pb, pl = _padding(parent)
    x = parent.rect.x + pl
    y = parent.rect.y + pt
    w = max(0, parent.rect.w - pl - pr)
    h = max(0, parent.rect.h - pt - pb)

    docked = [c for c in shown if _dock_of(c)]
    flowing = [c for c in shown if not _dock_of(c)]

    for c in docked:
        dock = _dock_of(c)
        slot_w = resolve_main_size(c, axis="horizontal", parent_main=w)
        slot_h = resolve_main_size(c, axis="vertical", parent_main=h)
        cw = int(slot_w.value) if slot_w.kind in ("fixed", "auto") else min(120, w)
        if slot_w.kind == "fr":
            cw = min(120, w)  # docked fr → sensible default
        if slot_w.kind == "percent":
            cw = int(w * slot_w.value)
        ch = int(slot_h.value) if slot_h.kind in ("fixed", "auto") else min(40, h)
        if slot_h.kind == "fr":
            ch = min(40, h)
        if slot_h.kind == "percent":
            ch = int(h * slot_h.value)
        cw = max(cw, slot_w.min_px)
        if dock == "top":
            c.layout(_apply_offset(c, Rect(x, y, w, ch)))
            y += ch
            h = max(0, h - ch)
        elif dock == "bottom":
            c.layout(_apply_offset(c, Rect(x, y + h - ch, w, ch)))
            h = max(0, h - ch)
        elif dock == "left":
            c.layout(_apply_offset(c, Rect(x, y, cw, h)))
            x += cw
            w = max(0, w - cw)
        elif dock == "right":
            c.layout(_apply_offset(c, Rect(x + w - cw, y, cw, h)))
            w = max(0, w - cw)

    flow_box = Rect(x, y, w, h)
    styles = parent.styles or {}
    layout_mode = str(styles.get("layout") or "").lower()
    type_name = type(parent).__name__
    if layout_mode == "grid" or type_name == "Grid":
        cols = styles.get("grid_columns") or styles.get("grid_size_columns") or getattr(
            parent, "_grid_columns", 2,
        )
        try:
            cols = int(cols)
        except (TypeError, ValueError):
            cols = 2
        gs = styles.get("grid_size")
        if isinstance(gs, int):
            cols = gs
        elif isinstance(gs, str) and gs.strip():
            parts = gs.replace(",", " ").split()
            if parts:
                try:
                    cols = int(parts[0])
                except ValueError:
                    pass
        layout_grid(flowing, flow_box, columns=max(1, cols))
        return

    # Align containers (Center / Middle / CenterMiddle / Right / CSS content-align).
    ah, av = _align_of(parent)
    if ah or av or type_name in ("Center", "Middle", "CenterMiddle", "Right"):
        if type_name == "Center":
            ah = ah or "center"
        elif type_name == "Right":
            ah = ah or "right"
        elif type_name == "Middle":
            av = av or "middle"
        elif type_name == "CenterMiddle":
            ah = ah or "center"
            av = av or "middle"
        _layout_aligned(flowing, flow_box, ah, av)
        return

    # Infer orientation from parent type name / styles.
    if layout_mode in ("horizontal", "row") or type_name in ("Horizontal", "HorizontalGroup"):
        horizontal = True
    elif layout_mode in ("vertical", "column") or type_name in ("Vertical", "Screen"):
        horizontal = False
    else:
        horizontal = False
    layout_flow(flowing, flow_box, horizontal=horizontal)
