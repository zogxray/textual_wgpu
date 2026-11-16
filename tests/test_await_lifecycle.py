"""AwaitMount, wait_for / query async, refresh batching."""

from __future__ import annotations

import asyncio

import pytest

from textual_wgpu import AwaitMount, ModeApp, __version__
from textual_wgpu.await_mount import AwaitComplete
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Static, Vertical


def _theme() -> Theme:
    return Theme.from_mapping({
        "background": "#1E1923",
        "foreground": "#F5F0E8",
        "accent": "#D4AD72",
        "muted": "#9A9088",
        "panel": "#2A2430",
        "boost": "#342C3C",
        "surface": "#241E2A",
        "primary": "#D4AD72",
    })


def test_version_bump():
    assert __version__.startswith("0.")


@pytest.mark.asyncio
async def test_await_mount_after_mount_child():
    class Body(Screen):
        def compose(self):
            yield Vertical(id="col")

    screen = Body()
    app = ModeApp([("x", "X", lambda: screen)], theme=_theme(), start="x")
    app._factories["x"] = lambda: screen
    app._screens.clear()

    async with app.run_test(size=(400, 300)) as pilot:
        col = app.query_one("#col", Vertical)
        child = Static("hi", id="hi")
        handle = col.mount_child(child)
        assert isinstance(handle, AwaitMount)
        await handle
        assert child.is_mounted
        found = await pilot.wait_for("#hi", expect_type=Static)
        assert found is child


@pytest.mark.asyncio
async def test_mount_widgets_and_wait_for():
    class Body(Screen):
        def compose(self):
            yield Vertical(id="col")

    screen = Body()
    app = ModeApp([("x", "X", lambda: screen)], theme=_theme(), start="x")
    app._factories["x"] = lambda: screen
    app._screens.clear()

    async with app.run_test(size=(400, 300)) as pilot:
        col = app.query_one("#col", Vertical)
        a = Static("a", id="a")
        b = Static("b", id="b")
        await col.mount_widgets(a, b)
        assert [c.id for c in col.children] == ["a", "b"]
        assert await app.wait_for("#b") is b
        assert pilot.query_one("#a") is a


@pytest.mark.asyncio
async def test_wait_for_timeout():
    class Body(Screen):
        def compose(self):
            yield Static("only", id="only")

    app = ModeApp([("x", "X", Body)], theme=_theme(), start="x")
    async with app.run_test(size=(200, 100)):
        with pytest.raises(TimeoutError):
            await app.wait_for("#missing", timeout=0.05)


@pytest.mark.asyncio
async def test_batch_update_coalesces_draws():
    class Body(Screen):
        def compose(self):
            yield Static("x", id="x")

    draws: list[int] = []

    app = ModeApp([("x", "X", Body)], theme=_theme(), start="x")

    async with app.run_test(size=(200, 100)):
        # Fake canvas that counts request_draw.
        class Canvas:
            def request_draw(self, _cb) -> None:
                draws.append(1)

        app._canvas = Canvas()
        w = app.query_one("#x")
        with app.batch_update():
            w.refresh()
            w.refresh(layout=True)
            w.refresh()
            assert app._batch_count == 1
            assert app._batch_refresh_pending
            assert draws == []  # suppressed inside batch
        assert app._batch_count == 0
        assert draws == [1]  # single flush


@pytest.mark.asyncio
async def test_wait_for_refresh():
    class Body(Screen):
        def compose(self):
            yield Static("x", id="x")

    app = ModeApp([("x", "X", Body)], theme=_theme(), start="x")
    async with app.run_test(size=(200, 100)):
        w = app.query_one("#x")
        await w.wait_for_refresh()


@pytest.mark.asyncio
async def test_await_complete_nothing():
    await AwaitComplete.nothing()
    done = asyncio.Event()

    async def mark() -> None:
        done.set()

    await AwaitComplete(mark())
    assert done.is_set()


@pytest.mark.asyncio
async def test_late_mount_visible_to_wait_for():
    """Mount after start — wait_for sees the new node."""

    class Body(Screen):
        def compose(self):
            yield Vertical(id="col")

    screen = Body()
    app = ModeApp([("x", "X", lambda: screen)], theme=_theme(), start="x")
    app._factories["x"] = lambda: screen
    app._screens.clear()

    async with app.run_test(size=(400, 300)) as pilot:
        col = app.query_one("#col", Vertical)

        async def add_later() -> None:
            await asyncio.sleep(0.02)
            await col.mount_widgets(Static("late", id="late"))

        task = asyncio.create_task(add_later())
        late = await pilot.wait_for("#late", timeout=1.0)
        assert late.id == "late"
        await task
