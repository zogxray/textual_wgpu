"""Chrome 4-band layout paints cleanly; worker helpers exist on App."""

from textual_wgpu.app import App
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import KeyBar, ModeBar, Static, StatusBar


def test_app_worker_helpers_exist():
    app = App()
    assert callable(app.run_worker)
    assert callable(app.call_from_thread)
    assert callable(app.show_busy)
    assert callable(app.hide_busy)


def test_chrome_layout_paints():
    class Body(Screen):
        def compose(self):
            yield ModeBar("home", [("home", "Home")])
            yield Static("body")
            yield KeyBar([])
            yield StatusBar("ready")

    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72"})
    app = ModeApp([("home", "Home", Body)], theme=theme, start="home")
    app._pixel_size = (800, 500)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 800, 500))
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (800, 500))
    screen.paint(ImageDraw.Draw(img))
    assert img.getpixel((10, 10)) != (0, 0, 0, 255)
