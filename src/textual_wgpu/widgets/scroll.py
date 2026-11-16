"""ScrollableContainer / VerticalScroll / HorizontalScroll with scrollbar gutter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..fonts import cell_height, cell_width, columns_for_width
from ..geometry import Rect
from ..widget import Widget
from .containers import Vertical
from .scrollbar import Scrollbar
from .static import Static

if TYPE_CHECKING:
    from PIL import ImageDraw

Hit = tuple[Rect, Callable[[int, int], None]]


class ScrollableContainer(Widget):
    """Generic focusable scroll container (vertical & horizontal scroll axes)."""

    can_focus = True
    LINE_H = 18
    BAR_W = Scrollbar.BAR_W

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
        allow_vertical: bool = True,
        allow_horizontal: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        if can_focus is not None:
            self.can_focus = bool(can_focus)
        self.can_focus_children = can_focus_children
        self.can_maximize = can_maximize
        self._allow_vertical = bool(allow_vertical)
        self._allow_horizontal = bool(allow_horizontal)

        self._raw_children: list[Widget] = list(children)
        if len(self._raw_children) == 1:
            self.child: Widget = self._raw_children[0]
        elif len(self._raw_children) > 1:
            self.child = Vertical(*self._raw_children)
        else:
            self.child = Static("")

        self.scroll_y = 0
        self.scroll_py: float = 0.0
        self.scroll_x = 0
        self._scrollbar = Scrollbar(vertical=True, on_scroll=self._on_bar_scroll)
        self._hbar = Scrollbar(vertical=False, on_scroll=self._on_hbar_scroll)

    @property
    def children(self) -> list[Widget]:
        return [self.child]

    def _content_box(self) -> Rect:
        rw = max(0, self.rect.w - (self.BAR_W + 2 if self._allow_vertical else 0))
        rh = max(0, self.rect.h - (self.BAR_W + 2 if self._allow_horizontal else 0))
        return Rect(self.rect.x, self.rect.y, rw, rh)

    def layout_children(self) -> None:
        content = self._content_box()
        if (
            self.scroll_y
            and not hasattr(self.child, "paint_lines")
            and type(self.child).render_lines is Widget.render_lines
        ):
            shifted = Rect(
                content.x - self.scroll_x * cell_width(13),
                content.y - self.scroll_y * self.LINE_H,
                content.w,
                max(content.h, self._line_count() * self.LINE_H),
            )
            self.child.layout(shifted)
        else:
            self.child.layout(content)
        ensure = getattr(self.child, "ensure_baked", None)
        if callable(ensure) and content.w > 0:
            ensure(max(40, content.w - 8))
        self._layout_scrollbar()

    def _layout_scrollbar(self) -> None:
        if self._allow_vertical:
            gh = max(0, self.rect.h - (self.BAR_W if self._allow_horizontal else 0))
            gutter = Rect(self.rect.x1 - self.BAR_W, self.rect.y, self.BAR_W, gh)
            self._scrollbar.app = self.app
            self._scrollbar.layout(gutter)
        if self._allow_horizontal:
            gw = max(0, self.rect.w - (self.BAR_W if self._allow_vertical else 0))
            hgutter = Rect(self.rect.x, self.rect.y1 - self.BAR_W, gw, self.BAR_W)
            self._hbar.app = self.app
            self._hbar.layout(hgutter)
        self._sync_scrollbar()

    def _sync_scrollbar(self) -> None:
        if self._allow_vertical:
            rh = self._row_h
            max_px = self._max_scroll() * rh
            total = max(1, self._line_count())
            visible = self._visible_lines()
            if max_px <= 0:
                self._scrollbar.set_metrics(position=0.0, window=1.0)
            else:
                self._scrollbar.set_metrics(
                    position=self.scroll_py / max_px,
                    window=min(1.0, visible / total),
                )
        if self._allow_horizontal:
            max_x = self._max_scroll_x()
            total_c = max(1, self._col_count())
            vis_c = self._visible_cols()
            if max_x <= 0:
                self._hbar.set_metrics(position=0.0, window=1.0)
            else:
                self._hbar.set_metrics(
                    position=self.scroll_x / max_x,
                    window=min(1.0, vis_c / total_c),
                )

    def _on_bar_scroll(self, frac: float) -> None:
        self.scroll_to_fraction(frac)

    def _on_hbar_scroll(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        self.scroll_x = int(round(self._max_scroll_x() * frac))
        self._sync_scrollbar()

    def _line_count(self) -> int:
        ensure = getattr(self.child, "ensure_baked", None)
        if callable(ensure) and self.child.rect.w > 0:
            ensure(max(40, self.child.rect.w - 8))
        lc = getattr(self.child, "line_count", None)
        if isinstance(lc, int) and lc > 0:
            return lc
        if hasattr(self.child, "render_lines") and type(self.child).render_lines is not Widget.render_lines:
            try:
                return len(self.child.render_lines())
            except Exception:  # noqa: BLE001
                pass
        if self._raw_children:
            total_px = sum(
                (c.get_content_height() or self.LINE_H) for c in self._raw_children
            )
            return max(1, (total_px + self.LINE_H - 1) // self.LINE_H)
        return 0

    def _col_count(self) -> int:
        if hasattr(self.child, "render_lines"):
            try:
                lines = self.child.render_lines()
                return max((len(s) for s in lines), default=0)
            except Exception:  # noqa: BLE001
                pass
        return 0

    @property
    def _row_h(self) -> int:
        fs = getattr(self.child, "RENDER_FONT_SIZE", getattr(self.child, "FONT_SIZE", None))
        if isinstance(fs, int) and fs > 0:
            return max(1, cell_height(fs))
        return max(1, int(self.LINE_H))

    def _visible_lines(self) -> int:
        return max(1, self._content_box().h // self._row_h)

    def _visible_cols(self) -> int:
        fs = getattr(self.child, "RENDER_FONT_SIZE", 13)
        return max(1, columns_for_width(max(40, self._content_box().w - 8), fs))

    def _max_scroll(self) -> int:
        return max(0, self._line_count() - self._visible_lines())

    def _max_scroll_x(self) -> int:
        return max(0, self._col_count() - self._visible_cols())

    def scroll(self, delta: int) -> None:
        rh = self._row_h
        self.scroll_y = max(0, min(self._max_scroll(), self.scroll_y + delta))
        self.scroll_py = float(self.scroll_y * rh)
        self._sync_scrollbar()
        if len(self._raw_children) > 1 and self.rect.w > 0:
            self.layout_children()
        self.refresh()

    def scroll_pixels(self, delta_px: float) -> None:
        rh = self._row_h
        max_px = float(self._max_scroll() * rh)
        self.scroll_py = max(0.0, min(max_px, self.scroll_py + float(delta_px)))
        self.scroll_y = int(round(self.scroll_py / rh)) if rh else 0
        self._sync_scrollbar()
        if len(self._raw_children) > 1 and self.rect.w > 0:
            self.layout_children()
        self.refresh()

    def scroll_to(self, x: int | None = None, y: int | None = None, **_kw) -> None:
        rh = self._row_h
        if y is not None:
            self.scroll_y = max(0, min(self._max_scroll(), int(y)))
            self.scroll_py = float(self.scroll_y * rh)
        if x is not None:
            self.scroll_x = max(0, min(self._max_scroll_x(), int(x)))
        self._sync_scrollbar()

    def scroll_up(self) -> None:
        self.scroll(-1)

    def scroll_down(self) -> None:
        self.scroll(1)

    def scroll_left(self) -> None:
        self.scroll_x = max(0, self.scroll_x - 4)
        self._sync_scrollbar()

    def scroll_right(self) -> None:
        self.scroll_x = min(self._max_scroll_x(), self.scroll_x + 4)
        self._sync_scrollbar()

    def scroll_home(self, *, animate: bool = True) -> None:
        _ = animate
        self.scroll_y = 0
        self.scroll_py = 0.0
        self.scroll_x = 0
        self._sync_scrollbar()

    def scroll_end(self, *, animate: bool = True) -> None:
        _ = animate
        self.scroll_y = self._max_scroll()
        self.scroll_py = float(self.scroll_y * self._row_h)
        self._sync_scrollbar()

    def scroll_relative(
        self,
        x: int = 0,
        y: int = 0,
        *,
        animate: bool = True,
    ) -> None:
        _ = animate
        if x:
            self.scroll_x = max(0, min(self._max_scroll_x(), self.scroll_x + int(x)))
        if y:
            self.scroll_y = max(0, min(self._max_scroll(), self.scroll_y + int(y)))
            self.scroll_py = float(self.scroll_y * self._row_h)
        self._sync_scrollbar()
        if len(self._raw_children) > 1 and self.rect.w > 0:
            self.layout_children()
        self.refresh()

    def scroll_page_up(self) -> None:
        self.scroll(-self._visible_lines())

    def scroll_page_down(self) -> None:
        self.scroll(self._visible_lines())

    def action_scroll_up(self) -> None:
        self.scroll_up()

    def action_scroll_down(self) -> None:
        self.scroll_down()

    def action_scroll_left(self) -> None:
        self.scroll_left()

    def action_scroll_right(self) -> None:
        self.scroll_right()

    def action_scroll_home(self) -> None:
        self.scroll_home()

    def action_scroll_end(self) -> None:
        self.scroll_end()

    def action_page_up(self) -> None:
        self.scroll_page_up()

    def action_page_down(self) -> None:
        self.scroll_page_down()

    def scroll_to_fraction(self, frac: float) -> None:
        rh = self._row_h
        frac = max(0.0, min(1.0, frac))
        max_px = float(self._max_scroll() * rh)
        self.scroll_py = max_px * frac
        self.scroll_y = int(round(self.scroll_py / rh)) if rh else 0
        self._sync_scrollbar()

    def on_key(self, key: str) -> bool:
        if self._allow_vertical:
            rh = self._row_h
            if key == "up":
                self.scroll_pixels(-rh)
                return True
            if key == "down":
                self.scroll_pixels(rh)
                return True
            if key == "pageup":
                self.scroll(-self._visible_lines())
                return True
            if key == "pagedown":
                self.scroll(self._visible_lines())
                return True
        if self._allow_horizontal:
            if key == "left":
                self.scroll_left()
                return True
            if key == "right":
                self.scroll_right()
                return True
        if key == "home":
            self.scroll_home()
            return True
        if key == "end":
            if self._allow_vertical:
                self.scroll_end()
            elif self._allow_horizontal:
                self.scroll_x = self._max_scroll_x()
                self._sync_scrollbar()
            return True
        return False

    def _bar_geometry(self) -> tuple[Rect, Rect] | None:
        self._layout_scrollbar()
        if self._allow_vertical:
            return self._scrollbar._geometry()
        return self._hbar._geometry()

    def on_pointer_move(self, x: int, y: int) -> bool:
        v = self._scrollbar.on_pointer_move(x, y) if self._allow_vertical else False
        h = self._hbar.on_pointer_move(x, y) if self._allow_horizontal else False
        return v or h

    def on_pointer_up(self, x: int, y: int) -> bool:
        v = self._scrollbar.on_pointer_up(x, y) if self._allow_vertical else False
        h = self._hbar.on_pointer_up(x, y) if self._allow_horizontal else False
        return v or h

    def hits(self) -> list[Hit]:
        out: list[Hit] = list(super().hits())  # type: ignore[arg-type]
        self._layout_scrollbar()
        if self._allow_vertical:
            out.extend(self._scrollbar.hits())
        if self._allow_horizontal:
            out.extend(self._hbar.hits())
        return out

    def paint_backdrop(self, draw: ImageDraw.ImageDraw) -> None:
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            fill=self.theme.rgba("panel"),
        )

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        if self.rect.w < 2 or self.rect.h < 2:
            return
        child = self.child
        content_w = max(0, self.child.rect.w - 8)
        self.paint_backdrop(draw)
        paint_lines = getattr(child, "paint_lines", None)
        ensure = getattr(child, "ensure_baked", None)
        if callable(ensure):
            ensure(max(40, content_w))
        if callable(paint_lines) and self.scroll_x == 0:
            paint_lines(
                draw,
                x=self.child.rect.x + 4,
                y=self.child.rect.y + 2,
                width=content_w,
                height=self.child.rect.h - 4,
                scroll=self.scroll_y,
                scroll_px=int(round(self.scroll_py)),
                line_h=self.LINE_H,
            )
        elif hasattr(child, "render_lines"):
            from ..compositor import paint_strips
            from ..selection import apply_selection_bg, selection_style_rgba

            strips = list(child.render_lines())
            if self.scroll_x > 0:
                strips = [s.crop(self.scroll_x) for s in strips]
            sel = getattr(child, "text_selection", None)
            if sel is not None:
                strips = apply_selection_bg(strips, sel, selection_style_rgba(child))
            paint_strips(
                draw,
                self.child.rect,
                strips,
                theme=self.theme,
                styles=getattr(child, "styles", None) or {},
                font_size=getattr(child, "RENDER_FONT_SIZE", 13),
                pad_x=4,
                pad_y=2,
                scroll_y=self.scroll_y,
                scroll_px=int(round(self.scroll_py)),
                fill_bg=False,
            )
        else:
            child.paint(draw)
        self._layout_scrollbar()
        if self._allow_vertical:
            self._scrollbar.paint(draw)
        if self._allow_horizontal:
            self._hbar.paint(draw)


class VerticalScroll(ScrollableContainer):
    """Scrolls vertically. Right gutter via composed ``Scrollbar``."""

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
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            can_focus=can_focus,
            can_focus_children=can_focus_children,
            can_maximize=can_maximize,
            allow_vertical=True,
            allow_horizontal=False,
        )


class HorizontalScroll(ScrollableContainer):
    """Scrolls horizontally. Bottom gutter via horizontal ``Scrollbar``."""

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
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            can_focus=can_focus,
            can_focus_children=can_focus_children,
            can_maximize=can_maximize,
            allow_vertical=False,
            allow_horizontal=True,
        )
