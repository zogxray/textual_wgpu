"""DockLayout wrapper."""

from ..layout import layout_children


class DockLayout:
    name = "dock"

    def arrange(self, parent, children=None):  # noqa: ANN001
        layout_children(parent, list(children) if children is not None else None)
