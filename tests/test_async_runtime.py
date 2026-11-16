"""Async runtime — message pump, Worker/@work, await push_screen, timers."""

from __future__ import annotations

import asyncio

import pytest

from textual_wgpu.app import App
from textual_wgpu.message import Message
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.on import on
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Static
from textual_wgpu.worker import WorkerState, work


def _theme() -> Theme:
    return Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72"})


class _Ping(Message):
    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value


class _ProbeScreen(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.pings: list[str] = []

    def compose(self):
        yield Static("x", id="body")

    @on(_Ping)
    def _on_ping(self, event: _Ping) -> None:
        self.pings.append(event.value)


class _ResultScreen(Screen):
    BINDINGS = [("enter", "ok")]

    def compose(self):
        yield Static("pick", id="body")

    def action_ok(self) -> None:
        self.dismiss("chosen")


@pytest.mark.asyncio
async def test_message_pump_via_pilot():
    app = ModeApp([("x", "X", _ProbeScreen)], theme=_theme(), start="x")
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, _ProbeScreen)
        body = screen.query_one("#body")
        body.post_message(_Ping("hello"))
        await pilot.pause(0)
        assert screen.pings == ["hello"]


@pytest.mark.asyncio
async def test_push_screen_wait_dismiss():
    app = ModeApp([("x", "X", _ProbeScreen)], theme=_theme(), start="x")
    async with app.run_test() as pilot:
        result_task = asyncio.create_task(
            app.push_screen_wait(_ResultScreen()),
        )
        await pilot.pause(0)
        assert len(app.screen_stack) == 1
        await pilot.press("enter")
        result = await asyncio.wait_for(result_task, timeout=1.0)
        assert result == "chosen"
        assert app.screen_stack == []


@pytest.mark.asyncio
async def test_async_worker_and_work_decorator():
    app = App(theme=_theme())
    seen: list[str] = []

    class Host(App):
        def __init__(self) -> None:
            super().__init__(theme=_theme())

        @work(exclusive=True, group="load")
        async def load(self) -> str:
            await asyncio.sleep(0.01)
            return "done"

    host = Host()
    async with host.run_test() as pilot:
        w = host.load()
        assert w.state in (WorkerState.PENDING, WorkerState.RUNNING)
        result = await w.wait()
        assert result == "done"
        assert w.state == WorkerState.SUCCESS
        await pilot.pause(0)


@pytest.mark.asyncio
async def test_exclusive_async_worker_cancels_previous():
    app = App(theme=_theme())
    async with app.run_test():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def first() -> None:
            started.set()
            try:
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def second() -> str:
            await asyncio.sleep(0.01)
            return "ok"

        w1 = app.spawn_worker(first, name="a", group="g", exclusive=True)
        await started.wait()
        w2 = app.spawn_worker(second, name="b", group="g", exclusive=True)
        result = await w2.wait()
        assert result == "ok"
        # Give cancel a beat.
        await asyncio.sleep(0.05)
        assert w1.is_cancelled or w1.state == WorkerState.CANCELLED


@pytest.mark.asyncio
async def test_timer_on_asyncio_loop():
    app = App(theme=_theme())
    fired: list[int] = []
    async with app.run_test():
        app.set_timer(0.05, lambda: fired.append(1))
        await asyncio.sleep(0.12)
        assert fired == [1]


@pytest.mark.asyncio
async def test_thread_worker_still_works_via_run_worker():
    app = App(theme=_theme())
    done = asyncio.Event()

    def job() -> None:
        app.call_from_thread(done.set)

    async with app.run_test():
        app.run_worker(job, name="t", exclusive=True, group="g")
        await asyncio.wait_for(done.wait(), timeout=1.0)
