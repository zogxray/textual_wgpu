"""ModeApp modal push/pop stack (Textual push_screen analogue)."""

from __future__ import annotations

from textual_wgpu.mode_app import ModeApp
from textual_wgpu.theme import Theme


def _app() -> ModeApp:
    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72"})
    return ModeApp([("x", "X", None)], theme=theme, start="x")


def test_nested_prompt_over_pick_restores():
    app = _app()
    picked: list[object] = []
    prompted: list[object] = []

    app.ask_pick("Pick", [("a", "A"), ("b", "B")], picked.append)
    assert app._pick is not None
    assert app._modal_stack == []

    app.ask_prompt("Name", "", prompted.append)
    assert app._prompt is not None
    assert app._pick is None
    assert len(app._modal_stack) == 1
    assert "_pick" in app._modal_stack[0]

    app._prompt_finish("hello")
    assert prompted == ["hello"]
    assert app._prompt is None
    assert app._pick is not None
    assert app._modal_stack == []

    app._pick_accept("a")
    assert picked == ["a"]
    assert app._pick is None


def test_busy_blocks_begin_modal():
    app = _app()
    app.show_busy("Working")
    app.ask_prompt("Nope", "", lambda _v: None)
    assert app._prompt is None
    assert app._modal_stack == []
    app.hide_busy()


def test_escape_pops_stack():
    app = _app()
    app.ask_confirm("Sure?", callback=lambda _ok: None)
    app.ask_prompt("Nested", "x", lambda _v: None)
    assert app._prompt is not None
    assert len(app._modal_stack) == 1
    app.on_escape()
    assert app._prompt is None
    assert app._confirm is not None
    assert app._modal_stack == []
