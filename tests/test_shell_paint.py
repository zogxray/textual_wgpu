"""Shell paints ModeBar chrome without opening a window."""

from textual_wgpu.shell import MODE_LABELS, ShellApp


def test_mode_labels_defined():
    assert len(MODE_LABELS) == 6
    assert MODE_LABELS[0][0] == "overview"
    assert MODE_LABELS[-1][0] == "help"


def test_shell_paint_rgba():
    app = ShellApp()
    app.set_mode(4)
    img = app._paint()
    assert img.size == app.SIZE
    assert img.mode == "RGBA"
    assert app.mode_key == "themes"
    assert len(app._hits) >= len(MODE_LABELS)  # mode tabs (+ quit)


def test_quit_modal_hits():
    app = ShellApp()
    app.ask_quit()
    assert app.quit_modal
    app._paint()
    # Mode switching blocked while modal open.
    app.set_mode(3)
    assert app.mode_index == 0
    app.confirm_quit_no()
    assert not app.quit_modal
    app.set_mode(3)
    assert app.mode_index == 3


def test_pointer_hits_mode_tab():
    app = ShellApp()
    app._paint()
    # Click roughly first tab center from recorded hit.
    hit = app._hits[0]
    cx = (hit.x0 + hit.x1) // 2
    cy = (hit.y0 + hit.y1) // 2
    app.on_pointer(cx, cy, 1)
    # First tab is already mode 0; click a later tab.
    hit2 = app._hits[2]
    app.on_pointer((hit2.x0 + hit2.x1) // 2, (hit2.y0 + hit2.y1) // 2, 1)
    assert app.mode_index == 2
