"""Smoke: package imports without opening a window."""

from textual_wgpu import App, ShellApp, __version__


def test_version():
    assert __version__ == "0.19.0"


def test_app_bind_roundtrip():
    app = App()
    called = []
    app.bind("q", lambda: called.append("q"))
    assert "q" in app._bindings
    app._bindings["q"]()
    assert called == ["q"]


def test_shell_app_importable():
    assert ShellApp is not None
