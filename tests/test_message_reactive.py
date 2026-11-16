"""Message bus, @on, reactive/watch."""

from __future__ import annotations

from textual_wgpu.message import Message
from textual_wgpu.on import on
from textual_wgpu.reactive import reactive, watch
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Horizontal, Option, OptionList, Panel, Static


class Ping(Message):
    def __init__(self, n: int = 0) -> None:
        super().__init__()
        self.n = n


def test_bubble_and_on_selector():
    seen: list[str] = []

    class Body(Screen):
        @on(Ping, "#src")
        def _got(self, event: Ping) -> None:
            seen.append(f"screen:{event.n}")

        def compose(self):
            yield Panel(Static("x", id="src"), id="box")

    screen = Body()
    screen.build()
    src = screen.query_one("#src")
    box = screen.query_one("#box")
    box.parent = screen
    src.parent = box
    screen.app = type("A", (), {"theme": Theme.system(), "request_draw": lambda: None})()

    src.post_message(Ping(7))
    assert seen == ["screen:7"]


def test_stop_prevents_parent():
    seen: list[str] = []

    class Body(Screen):
        @on(Ping)
        def _screen(self, event: Ping) -> None:
            seen.append("screen")

        def compose(self):
            yield Static("x", id="src")

    class Child(Static):
        @on(Ping)
        def _child(self, event: Ping) -> None:
            seen.append("child")
            event.stop()

    screen = Body()
    # Replace compose child with Child that has handler — rebuild manually.
    screen._composed = [Child(id="src")]
    screen._built = True
    child = screen._composed[0]
    child.parent = screen
    child.post_message(Ping())
    assert seen == ["child"]


def test_option_list_posts_highlighted():
    events: list[OptionList.OptionHighlighted] = []

    class Body(Screen):
        @on(OptionList.OptionHighlighted, "#jobs")
        def _hi(self, event: OptionList.OptionHighlighted) -> None:
            events.append(event)

        def compose(self):
            ol = OptionList(id="jobs")
            yield Panel(ol, id="list-box")

    screen = Body()
    screen.build()
    ol = screen.query_one("#jobs", OptionList)
    box = screen.query_one("#list-box")
    box.parent = screen
    ol.parent = box
    screen.app = type("A", (), {"theme": Theme.system(), "request_draw": lambda: None})()
    ol.set_options([Option("A", id="a"), Option("B", id="b")])
    assert len(events) == 1
    assert events[0].option is not None
    assert events[0].option.id == "a"
    ol.move_highlight(1)
    assert len(events) == 2
    assert events[1].option_index == 1


def test_reactive_watch_method_and_repaint():
    draws = []

    class Body(Screen):
        filter = reactive("all")

        def watch_filter(self, old: str, new: str) -> None:
            self._seen = (old, new)

        def compose(self):
            yield Static("x")

    screen = Body()
    screen.app = type("A", (), {"request_draw": lambda self=None: draws.append(1)})()
    # request_draw may be unbound-style; also accept zero-arg via wrapper
    screen.app.request_draw = lambda: draws.append(1)
    assert screen.filter == "all"
    screen.filter = "done"
    assert screen._seen == ("all", "done")
    assert draws == [1]
    screen.filter = "done"  # no-op
    assert draws == [1]


def test_watch_decorator():
    seen = []

    class Body(Screen):
        n = reactive(0)

        @watch("n")
        def _on_n(self, old, new) -> None:
            seen.append((old, new))

        def compose(self):
            yield Static("x")

    screen = Body()
    screen.app = type("A", (), {})()
    screen.app.request_draw = lambda: None
    screen.n = 3
    assert seen == [(0, 3)]
