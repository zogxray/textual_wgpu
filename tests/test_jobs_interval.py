"""set_interval handle + Jobs-shaped empty list paint."""

from PIL import Image, ImageDraw

from textual_wgpu.app import IntervalHandle
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Horizontal, KeyBar, ModeBar, Option, OptionList, Panel, RichText, StatusBar


def test_interval_handle_stop():
    h = IntervalHandle()
    assert h.active
    h.stop()
    assert not h.active


def test_jobs_like_screen_empty_paint():
    class Jobs(Screen):
        def compose(self):
            yield ModeBar("jobs", [("jobs", "Jobs"), ("help", "Help")])
            ol = OptionList(id="jobs", row_lines=2)
            yield Horizontal(
                Panel(ol, title="JOBS", id="list-box"),
                Panel(RichText("detail"), title="DETAIL"),
            )
            yield KeyBar([("P", "Pause", None)])
            yield StatusBar("status")

        def on_screen_mount(self) -> None:
            ol = self.query_one("#jobs", OptionList)
            ol.set_options([
                Option("No tasks", id="__empty__", disabled=True, lines=[
                    [("No tasks", (174, 165, 184, 255))],
                    [("  filter=all", (138, 130, 148, 255))],
                ]),
            ])

    theme = Theme.from_mapping({
        "background": "#1E1923",
        "accent": "#D4AD72",
        "panel": "#2A2430",
        "boost": "#25202B",
        "foreground": "#F0E6D8",
        "muted": "#A89B8C",
    })
    app = ModeApp([("jobs", "Jobs", Jobs)], theme=theme, start="jobs")
    app._pixel_size = (900, 600)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 900, 600))
    ol = screen.query_one("#jobs", OptionList)
    assert ol.row_lines == 2
    assert ol.row_h == OptionList.LINE_H * 2
    img = Image.new("RGBA", (900, 600))
    screen.paint(ImageDraw.Draw(img))
    assert img.getpixel((20, 20)) != (0, 0, 0, 255)
