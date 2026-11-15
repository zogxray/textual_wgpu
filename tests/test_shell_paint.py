"""Shell paints ModeBar chrome without opening a window."""

from textual_wgpu.shell import MODE_LABELS, ShellApp


def test_mode_labels_match_mdga_count():
    assert len(MODE_LABELS) == 10
    assert MODE_LABELS[0][0] == "genome"
    assert MODE_LABELS[5][0] == "reference"


def test_shell_paint_rgba():
    app = ShellApp()
    app.set_mode(5)
    img = app._paint()
    assert img.size == app.SIZE
    assert img.mode == "RGBA"
    assert app.mode_key == "reference"
