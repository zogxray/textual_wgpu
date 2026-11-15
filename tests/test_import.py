"""Smoke: package imports without opening a window."""

from textual_wgpu import App, __version__


def test_version():
    assert __version__ == "0.1.0"


def test_app_bindings_default():
    app = App()
    assert "q" in app._bindings
    assert "escape" in app._bindings
