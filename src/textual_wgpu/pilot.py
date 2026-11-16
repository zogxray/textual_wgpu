"""Pilot — Textual-style automated testing helper."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, TypeVar

if TYPE_CHECKING:
    from .app import App

W = TypeVar("W")


class Pilot:
    """Drive an app under ``HeadlessDriver`` (press / click / pause / action)."""

    def __init__(self, app: App) -> None:
        self.app = app

    async def pause(self, delay: float = 0.0) -> None:
        """Drain the message pump, then yield (and optionally sleep)."""
        await self.app.wait_for_idle(delay)

    async def wait_for(
        self,
        selector: str,
        *,
        timeout: float = 5.0,
        expect_type: type[W] | None = None,
    ) -> W:
        """Wait until a widget matching ``selector`` exists on the active screen."""
        return await self.app.wait_for(
            selector, timeout=timeout, expect_type=expect_type,
        )

    async def wait_for_idle(self, delay: float = 0.0) -> None:
        """Settle the message pump (alias of ``pause``)."""
        await self.app.wait_for_idle(delay)

    async def press(self, *keys: str) -> None:
        """Send one or more key names (``\"enter\"``, ``\"ctrl+p\"``, …)."""
        driver = getattr(self.app, "_driver", None)
        for key in keys:
            k = str(key).lower()
            if driver is not None and hasattr(driver, "process_key"):
                driver.process_key(k)
            elif hasattr(self.app, "_handle_key"):
                self.app._handle_key(k)
                self.app.request_draw()
            else:
                self.app.on_key(k)
                self.app.request_draw()
            await self.pause(0)

    async def type_chars(self, text: str) -> None:
        """Feed printable characters via ``on_char``."""
        driver = getattr(self.app, "_driver", None)
        for ch in text:
            if driver is not None and hasattr(driver, "process_char"):
                driver.process_char(ch)
            else:
                self.app.on_char(ch)
                self.app.request_draw()
            await self.pause(0)

    async def click(
        self,
        selector: str | None = None,
        *,
        offset: tuple[int, int] = (0, 0),
        button: int = 1,
        timeout: float = 5.0,
    ) -> None:
        """Click a widget by CSS-ish selector, or ``offset`` canvas coords.

        When ``selector`` is set, waits for the widget (``wait_for``) first.
        """
        x, y = offset
        if selector:
            widget = await self.wait_for(selector, timeout=timeout)
            if widget is not None and hasattr(widget, "rect"):
                r = widget.rect
                x = r.x + r.w // 2 + offset[0]
                y = r.y + r.h // 2 + offset[1]
        driver = getattr(self.app, "_driver", None)
        if driver is not None and hasattr(driver, "process_pointer"):
            driver.process_pointer(float(x), float(y), button)
        else:
            self.app.on_pointer(float(x), float(y), button)
            self.app.request_draw()
        await self.pause(0)

    async def action(self, action: str) -> bool:
        """``app.run_action(action)`` then yield."""
        ok = self.app.run_action(action)
        await self.pause(0)
        return ok

    def query(self, selector: str = "*") -> Any:
        return self.app.query(selector)

    def query_one(self, selector: str, expect_type: type | None = None) -> Any:
        return self.app.query_one(selector, expect_type)

    def _query(self, selector: str) -> Any | None:
        try:
            return self.app.query_one(selector)
        except Exception:  # noqa: BLE001
            return None


@asynccontextmanager
async def run_test(
    app: App,
    *,
    size: tuple[int, int] = (1100, 700),
) -> AsyncIterator[Pilot]:
    """``async with app.run_test() as pilot:`` — headless mount + Pilot."""
    from .driver import HeadlessDriver

    if hasattr(app, "_attach_loop"):
        app._attach_loop()
    driver = HeadlessDriver(app, size=size)
    await driver.start()
    try:
        yield Pilot(app)
    finally:
        app.exit()
        await driver.stop()
