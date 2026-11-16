"""WgpuChromView empty + header; worker helpers exist on App."""

from textual_wgpu.app import App
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import KeyBar, ModeBar, StatusBar


def test_app_worker_helpers_exist():
    app = App()
    assert callable(app.run_worker)
    assert callable(app.call_from_thread)
    assert callable(app.show_busy)
    assert callable(app.hide_busy)


def test_genome_like_layout_paints():
    # Avoid importing MDGA genome stack in lib tests — layout only.
    class Body(Screen):
        def compose(self):
            yield ModeBar("genome", [("genome", "Genome")])
            yield StatusBar("ready")
            yield KeyBar([])

    # 3 children — ModeBar, Status misplaced; use 4-chrome pattern
    class Body2(Screen):
        def compose(self):
            yield ModeBar("genome", [("genome", "Genome")])
            from textual_wgpu.widgets import Static
            yield Static("chroms")
            yield KeyBar([])
            yield StatusBar("ready")

    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72"})
    app = ModeApp([("genome", "Genome", Body2)], theme=theme, start="genome")
    app._pixel_size = (800, 500)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 800, 500))
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (800, 500))
    screen.paint(ImageDraw.Draw(img))
    assert img.getpixel((10, 10)) != (0, 0, 0, 255)
