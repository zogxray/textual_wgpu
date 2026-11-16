"""Core Textual symbols resolve from textual-wgpu."""

from __future__ import annotations

from textual_wgpu import (
    App,
    Binding,
    ComposeResult,
    Content,
    Message,
    ModalScreen,
    Screen,
    ScrollView,
    Selection,
    Size,
    Strip,
    Theme,
    WorkerState,
    __version__,
    on,
)
from textual_wgpu import events
from textual_wgpu.widgets import (
    Button,
    ContentSwitcher,
    DirectoryTree,
    Horizontal,
    Input,
    Label,
    Option,
    OptionList,
    ProgressBar,
    Static,
    TextArea,
    Vertical,
    VerticalGroup,
    VerticalScroll,
)


def test_version():
    assert __version__ == "0.19.0"


def test_core_textual_import_surface():
    assert issubclass(ModalScreen, Screen)
    assert issubclass(VerticalGroup, Vertical)
    b = Binding("a,b", "foo", "Foo", show=False, priority=True)
    assert b.key == "a,b" and b.action == "foo" and b.priority
    assert Size(3, 4).width == 3 and Size(3, 4).height == 4
    assert ComposeResult is not None
    assert WorkerState.SUCCESS
    assert events.Click is not None
    for cls in (
        App, Screen, ModalScreen, Horizontal, Vertical, VerticalGroup,
        VerticalScroll, OptionList, Option, Static, Input, Button, Label,
        TextArea, ProgressBar, DirectoryTree, ContentSwitcher, ScrollView,
        Content, Selection, Strip, Theme, Message,
    ):
        assert cls is not None
    assert callable(on)


def test_binding_and_modal_screen_key():
    from textual_wgpu.geometry import Rect
    from textual_wgpu.mode_app import ModeApp
    from textual_wgpu.theme import Theme as T

    class Confirm(ModalScreen[bool]):
        BINDINGS = [
            Binding("escape", "dismiss(False)", "Cancel", show=False),
            Binding("y", "yes", "Yes", show=False),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.result_seen = None

        def action_yes(self) -> None:
            self.dismiss(True)

        def dismiss(self, result=None) -> None:  # noqa: ANN001
            self.result_seen = result
            super().dismiss(result)

    theme = T.from_mapping({
        "background": "#111", "panel": "#222", "accent": "#D4AD72",
        "foreground": "#fff", "muted": "#999", "boost": "#333",
        "surface": "#222", "primary": "#D4AD72",
    })

    class Host(ModeApp):
        pass

    app = Host([("x", "X", None)], theme=theme, start="x")
    app._pixel_size = (400, 300)
    modal = Confirm()
    app.push_screen(modal)
    modal.mount(app)
    modal.layout(Rect(0, 0, 400, 300))
    assert modal.on_key("escape")
    assert modal.result_seen is False
