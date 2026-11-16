"""Screen BINDINGS: action returning False does not consume the key."""

from __future__ import annotations

from textual_wgpu.screen import Screen


class _EscScreen(Screen):
    BINDINGS = [("escape", "try_clear")]

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.clear_ok = False

    def try_clear(self) -> bool:
        self.calls += 1
        return self.clear_ok


def test_binding_false_falls_through():
    s = _EscScreen()
    s.clear_ok = False
    assert s.on_key("escape") is False
    assert s.calls == 1


def test_binding_true_or_none_consumes():
    s = _EscScreen()
    s.clear_ok = True
    assert s.on_key("escape") is True
    assert s.calls == 1
