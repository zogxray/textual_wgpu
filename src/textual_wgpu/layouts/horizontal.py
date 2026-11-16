"""HorizontalLayout wrapper."""

from ..layout import layout_flow


class HorizontalLayout:
    name = "horizontal"

    def arrange(self, parent, children, box):  # noqa: ANN001
        _ = parent
        layout_flow(list(children), box, horizontal=True)
