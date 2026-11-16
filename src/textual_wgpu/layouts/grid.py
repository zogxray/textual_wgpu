"""GridLayout wrapper."""

from ..layout import layout_grid


class GridLayout:
    name = "grid"

    def arrange(self, parent, children, box, columns: int = 2):  # noqa: ANN001
        _ = parent
        layout_grid(list(children), box, columns=columns)
