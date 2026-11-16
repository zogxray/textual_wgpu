"""Driver abstraction — Textual Driver / HeadlessDriver / wgpu backend."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import App


class Driver(ABC):
    """I/O backend for an ``App`` (windowed or headless)."""

    def __init__(self, app: App) -> None:
        self.app = app
        self._started = False

    @abstractmethod
    async def start(self) -> None:
        """Prepare the backend and mark the app running."""

    @abstractmethod
    async def stop(self) -> None:
        """Tear down the backend."""

    def process_key(self, key: str) -> None:
        if hasattr(self.app, "_handle_key"):
            self.app._handle_key(key)
        else:
            self.app.on_key(key)
        self.app.request_draw()

    def process_char(self, ch: str) -> None:
        if ch:
            self.app.on_char(ch)
            self.app.request_draw()

    def process_pointer(self, x: float, y: float, button: int = 1) -> None:
        self.app.on_pointer(x, y, button)
        self.app.request_draw()

    def process_wheel(self, x: float, y: float, dx: float = 0.0, dy: float = 0.0) -> None:
        self.app.on_wheel(x, y, dx, dy)
        self.app.request_draw()


class HeadlessDriver(Driver):
    """No window / GPU — for ``App.run_test`` / Pilot."""

    def __init__(self, app: App, *, size: tuple[int, int] = (1100, 700)) -> None:
        super().__init__(app)
        self.size = size

    async def start(self) -> None:
        app = self.app
        app._running = True
        app._driver = self
        # Bind asyncio loop + message pump (Pilot / workers / timers).
        if hasattr(app, "_attach_loop"):
            app._attach_loop()
        # Synthetic pixel size for ModeApp layout without a canvas.
        if hasattr(app, "_pixel_size"):
            app._pixel_size = self.size  # type: ignore[attr-defined]
        if hasattr(app, "_booting"):
            app._booting = False  # type: ignore[attr-defined]
        # Soft mount: allow screens without GPU.
        import inspect

        try:
            res = app.on_mount()
            if inspect.isawaitable(res):
                await res
        except Exception:  # noqa: BLE001 — headless may lack device
            pass
        if hasattr(app, "after_first_draw"):
            try:
                # Prefer ModeApp path that mounts the start screen.
                if hasattr(app, "_ensure_screen"):
                    app._booting = False  # type: ignore[attr-defined]
                    app._ensure_screen()  # type: ignore[attr-defined]
                else:
                    app.after_first_draw()
            except Exception:  # noqa: BLE001
                if hasattr(app, "_ensure_screen"):
                    try:
                        app._ensure_screen()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
        if hasattr(app, "on_resize"):
            w, h = self.size
            app.on_resize(w, h)
        scr = getattr(app, "screen", None)
        if scr is not None and hasattr(scr, "layout"):
            from .geometry import Rect

            w, h = self.size
            scr.layout(Rect(0, 0, w, h))
            if hasattr(app, "_layout_size"):
                app._layout_size = (w, h)  # type: ignore[attr-defined]
        from .events import Ready
        from .widget import dispatch_event

        dispatch_event(app, Ready(), "on_ready")
        if scr is not None:
            dispatch_event(scr, Ready(), "on_ready")
        self._started = True

    async def stop(self) -> None:
        self.app._running = False
        if getattr(self.app, "_pump_task", None) is not None:
            task = self.app._pump_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._started = False


class WgpuDriver(Driver):
    """rendercanvas + wgpu window (extracted from the historic ``App.run``)."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self._loop: Any = None

    async def start(self) -> None:
        """Sync setup then hand off to rendercanvas ``loop.run()`` (blocks)."""
        # Full window path is sync; ``run_async`` awaits a thread wrapper.
        self._start_sync()

    def _start_sync(self) -> None:
        from .app import _normalize_key

        import wgpu
        from rendercanvas.auto import RenderCanvas, loop

        app = self.app
        adapter = wgpu.gpu.request_adapter_sync()
        app._device = adapter.request_device_sync()
        app._canvas = RenderCanvas(size=app.SIZE, title=app.TITLE)
        app._set_window_visible(False)
        app._context = app._canvas.get_wgpu_context()
        preferred = app._context.get_preferred_format(app._device.adapter)
        app._format = str(preferred).replace("-srgb", "") or "bgra8unorm"
        app._context.configure(device=app._device, format=app._format)
        app._running = True
        app._driver = self
        self._loop = loop

        app.clear(present=True)

        @app._canvas.add_event_handler("key_down")
        def _on_key(event: dict) -> None:
            key = _normalize_key(str(event.get("key") or ""))
            mods = {str(m).lower() for m in (event.get("modifiers") or ())}
            if "control" in mods or "ctrl" in mods:
                if key == "s":
                    key = "ctrl+s"
                elif key == "c":
                    key = "ctrl+shift+c" if "shift" in mods else "ctrl+c"
                elif key == "v":
                    key = "ctrl+v"
                elif key == "a":
                    key = "ctrl+a"
                elif key == "enter":
                    key = "ctrl+enter"
                elif key == "p":
                    key = "ctrl+p"
            if "shift" in mods and not key.startswith("ctrl+"):
                if key in ("left", "right", "up", "down", "home", "end"):
                    key = f"shift+{key}"
                elif len(key) == 1 and "a" <= key <= "z":
                    key = f"shift+{key}"
            if "alt" in mods and key == "enter":
                key = "alt+enter"
            if "meta" in mods and key == "enter":
                key = "meta+enter"
            self.process_key(key)

        @app._canvas.add_event_handler("char")
        def _on_char(event: dict) -> None:
            ch = str(event.get("char_str") or event.get("data") or "")
            self.process_char(ch)

        @app._canvas.add_event_handler("pointer_down")
        def _on_pointer(event: dict) -> None:
            self.process_pointer(
                float(event.get("x") or 0),
                float(event.get("y") or 0),
                int(event.get("button") or 1),
            )

        @app._canvas.add_event_handler("pointer_move")
        def _on_pointer_move(event: dict) -> None:
            x = float(event.get("x") or 0)
            y = float(event.get("y") or 0)
            if app.on_pointer_move(x, y):
                app.request_draw()

        @app._canvas.add_event_handler("pointer_up")
        def _on_pointer_up(event: dict) -> None:
            x = float(event.get("x") or 0)
            y = float(event.get("y") or 0)
            button = int(event.get("button") or 1)
            app.on_pointer_up(x, y, button)
            app.request_draw()

        @app._canvas.add_event_handler("wheel")
        def _on_wheel(event: dict) -> None:
            self.process_wheel(
                float(event.get("x") or 0),
                float(event.get("y") or 0),
                float(event.get("dx") or 0),
                float(event.get("dy") or 0),
            )

        @app._canvas.add_event_handler("resize")
        def _on_resize(event: dict) -> None:
            w = int(event.get("width") or 0)
            h = int(event.get("height") or 0)
            if w > 0 and h > 0:
                app.on_resize(w, h)
                app.clear(present=True)
                app.request_draw()

        app.on_mount()
        try:
            pw, ph = app._canvas.get_physical_size()
            if pw > 0 and ph > 0:
                app.on_resize(int(pw), int(ph))
        except Exception:  # noqa: BLE001
            pass
        app.request_draw()
        try:
            app._canvas.force_draw()
        except Exception:  # noqa: BLE001
            app.clear(present=True)
        try:
            app.after_first_draw()
        except Exception as exc:  # noqa: BLE001
            print(f"after_first_draw error: {exc}", flush=True)
        try:
            app._canvas.force_draw()
        except Exception:  # noqa: BLE001
            app.clear(present=True)
        app._set_window_visible(True)
        try:
            import glfw

            win = getattr(app._canvas, "_window", None)
            if win is not None:
                glfw.set_window_focus_callback(
                    win, lambda _w, focused: app._emit_app_focus(bool(focused)),
                )
        except Exception:  # noqa: BLE001
            pass
        from .events import Ready
        from .widget import dispatch_event

        dispatch_event(app, Ready(), "on_ready")
        if app.screen is not None:
            dispatch_event(app.screen, Ready(), "on_ready")
        self._started = True

    def run_blocking(self) -> None:
        """Start + ``loop.run()`` (main entry for windowed apps)."""
        self._start_sync()
        assert self._loop is not None
        self._loop.run()

    async def stop(self) -> None:
        self.app._running = False
        self._started = False
        try:
            if self.app._canvas is not None:
                self.app._canvas.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._loop is not None:
                self._loop.stop()
        except Exception:  # noqa: BLE001
            pass


async def run_app_async(app: App, *, headless: bool = False, size: tuple[int, int] = (1100, 700)) -> None:
    """Async entry: one asyncio loop for pump/timers/workers; wgpu optional."""
    if hasattr(app, "_attach_loop"):
        app._attach_loop()
    if headless:
        driver: Driver = HeadlessDriver(app, size=size)
        await driver.start()
        # Idle until exit() flips ``_running`` — pump/timers share this loop.
        while app._running:
            await asyncio.sleep(0.05)
            if hasattr(app, "_drain_messages"):
                await app._drain_messages()
        await driver.stop()
        return

    driver = WgpuDriver(app)
    loop = asyncio.get_running_loop()
    # Wgpu/rendercanvas blocks in another thread; asyncio keeps pump alive here.
    exec_task = loop.run_in_executor(None, driver.run_blocking)
    try:
        while app._running and not exec_task.done():
            await asyncio.sleep(0.05)
            if hasattr(app, "_drain_messages"):
                await app._drain_messages()
        if not exec_task.done():
            app.exit()
        await exec_task
    finally:
        await driver.stop()
