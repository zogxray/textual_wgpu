"""Layout containers — Container / Vertical / Horizontal / Grid / Center / Middle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw


class Container(Widget):
    """Generic container (vertical flow by default, Textual ``Container``)."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.name = name
        self._children = list(children)
        self.styles.setdefault("layout", "vertical")

    @property
    def children(self) -> list[Widget]:
        return self._children

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        super().paint(draw)


class Vertical(Container):
    """Stack children top → bottom via CSS auto-layout (dock / fr / auto)."""


class VerticalGroup(Vertical):
    """Vertical stack without default scrolling (Textual ``VerticalGroup``)."""


class Horizontal(Container):
    """Stack children left → right via CSS auto-layout (dock / fr / percent)."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self.styles["layout"] = "horizontal"


class Grid(Container):
    """Simple CSS grid container (``layout: grid`` + ``grid-size``)."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        columns: int = 2,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self._grid_columns = max(1, int(columns))
        self.styles["layout"] = "grid"
        self.styles["grid_columns"] = self._grid_columns


class HorizontalGroup(Horizontal):
    """Horizontal stack without default scrolling (Textual ``HorizontalGroup``)."""


class Right(Container):
    """Align children to the right edge of available space."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self.markup = markup
        self.styles["content_align_h"] = "right"


class ItemGrid(Grid):
    """Auto-wrapping grid of items (Textual ``ItemGrid`` subset)."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        min_column_width: int = 20,
        max_column_width: int | None = None,
        gutter: tuple[int, int] = (0, 0),
        regular: bool = False,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled, columns=2)
        self.min_column_width = max(1, int(min_column_width))
        self.max_column_width = max_column_width
        self.gutter = gutter
        self.regular = bool(regular)

    def layout_children(self) -> None:
        if self.rect.w > 0:
            from ..fonts import cell_width

            col_px = max(40, self.min_column_width * cell_width(13))
            cols = max(1, self.rect.w // col_px)
            self._grid_columns = cols
            self.styles["grid_columns"] = cols
        super().layout_children()


class Center(Container):
    """Center children horizontally within available space."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self.markup = markup
        self.styles["content_align_h"] = "center"


class Middle(Container):
    """Center children vertically within available space."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self.markup = markup
        self.styles["content_align_v"] = "middle"


class CenterMiddle(Container):
    """Center children both horizontally and vertically."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(*children, name=name, id=id, classes=classes, disabled=disabled)
        self.markup = markup
        self.styles["content_align_h"] = "center"
        self.styles["content_align_v"] = "middle"
