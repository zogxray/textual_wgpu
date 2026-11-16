"""Runtime App/Screen — stack, actions, palette, Pilot, drivers."""

from __future__ import annotations

import asyncio

import pytest

from textual_wgpu.actions import list_actions, parse_action, run_action_on
from textual_wgpu.app import App
from textual_wgpu.command_palette import CommandPaletteScreen, collect_commands
from textual_wgpu.driver import HeadlessDriver
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme


class _ProbeScreen(Screen):
    BINDINGS = [("p", "ping"), ("x", "custom")]

    def __init__(self) -> None:
        super().__init__()
        self.pings: list[str] = []

    def action_ping(self) -> None:
        self.pings.append("ping")

    def action_custom(self) -> None:
        self.pings.append("custom")


def _mode_app() -> ModeApp:
    theme = Theme.from_mapping({"background": "#1E1923", "accent": "#D4AD72"})
    return ModeApp(
        [("x", "X", _ProbeScreen), ("y", "Y", None)],
        theme=theme,
        start="x",
    )


def test_parse_action():
    assert parse_action("quit") == ("quit", ())
    assert parse_action("action_quit") == ("quit", ())
    assert parse_action("notify('hi', 1)") == ("notify", ("hi", 1))


def test_run_action_on_screen():
    scr = _ProbeScreen()
    assert run_action_on([scr], "ping")
    assert scr.pings == ["ping"]
    assert list_actions(scr)[0][0] == "custom" or any(n == "ping" for n, _ in list_actions(scr))


def test_push_pop_screen_stack():
    app = _mode_app()
    base = app._ensure_screen()
    assert app.screen is base
    assert app.screen_stack == []

    results: list[object] = []
    overlay = _ProbeScreen()
    app.push_screen(overlay, callback=results.append)
    assert app.screen is overlay
    assert app.screen_stack == [overlay]

    popped = app.pop_screen("done")
    assert popped is overlay
    assert results == ["done"]
    assert app.screen is base
    assert app.screen_stack == []


def test_switch_screen():
    app = _mode_app()
    app._ensure_screen()
    a, b = _ProbeScreen(), _ProbeScreen()
    app.push_screen(a)
    app.switch_screen(b)
    assert app.screen is b
    assert app.screen_stack == [b]


def test_run_action_app_and_screen():
    app = _mode_app()
    scr = app._ensure_screen()
    assert isinstance(scr, _ProbeScreen)
    assert app.run_action("ping")
    assert scr.pings == ["ping"]
    assert app.run_action("quit")  # ModeApp.action_quit → ask_quit
    assert app.quit_modal is True


def test_command_palette_collect_and_open():
    app = _mode_app()
    app._ensure_screen()
    cmds = collect_commands(app)
    names = {n for n, _ in cmds}
    assert "quit" in names
    assert "ping" in names or "custom" in names

    app.action_command_palette()
    assert len(app.screen_stack) == 1
    assert isinstance(app.screen, CommandPaletteScreen)

    # Nested open blocked.
    app.action_command_palette()
    assert len(app.screen_stack) == 1

    app.on_key("escape")
    assert app.screen_stack == []


def test_bindings_resolve_action():
    app = _mode_app()
    scr = app._ensure_screen()
    assert isinstance(scr, _ProbeScreen)
    scr.on_key("p")
    assert scr.pings == ["ping"]


@pytest.mark.asyncio
async def test_pilot_press_and_action():
    app = _mode_app()
    async with app.run_test(size=(800, 600)) as pilot:
        assert app._running
        assert app.screen is not None
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, CommandPaletteScreen)
        await pilot.press("escape")
        assert app.screen_stack == []
        ok = await pilot.action("ping")
        assert ok
        scr = app._base_screen()
        assert isinstance(scr, _ProbeScreen)
        assert "ping" in scr.pings


@pytest.mark.asyncio
async def test_headless_driver_start_stop():
    app = App(theme=Theme.from_mapping({"background": "#000000"}))
    driver = HeadlessDriver(app, size=(640, 480))
    await driver.start()
    assert app._running
    assert app._driver is driver
    await driver.stop()
    assert not app._running


@pytest.mark.asyncio
async def test_run_async_headless_exits():
    app = App(theme=Theme.from_mapping({"background": "#000000"}))

    async def _stop_soon() -> None:
        await asyncio.sleep(0.05)
        app.exit()

    task = asyncio.create_task(_stop_soon())
    await app.run_async(headless=True, size=(320, 240))
    await task
    assert not app._running
