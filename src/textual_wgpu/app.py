"""Minimal App shell: window, theme, bindings, pointer, draw hook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .theme import Theme


class App:
    """Tiny Textual-flavoured shell.

    Not API-compatible with Textual. The application owns the Theme;
    this runtime only paints with it (same idea as Textual + terminal/app CSS).
    """

    TITLE = "textual-wgpu"
    SIZE = (960, 600)

    def __init__(self, theme: Theme | None = None) -> None:
        self.theme = theme or Theme.system()
        self._canvas: Any = None
        self._device: Any = None
        self._context: Any = None
        self._format: str | None = None
        self._running = False
        self._bindings: dict[str, Callable[[], None]] = {}

    @property
    def CLEAR(self) -> tuple[float, float, float, float]:
        return self.theme.clear_rgba_f()

    def bind(self, key: str, callback: Callable[[], None]) -> None:
        self._bindings[key.lower()] = callback

    def request_draw(self) -> None:
        if self._canvas is not None:
            self._canvas.request_draw(self.draw)

    def on_mount(self) -> None:
        """Override for setup after the GPU context exists."""

    def on_pointer(self, x: float, y: float, button: int) -> None:
        """Override for mouse / touch. Coordinates are canvas pixels."""

    def on_resize(self, width: int, height: int) -> None:
        """Override when the canvas size changes."""

    def draw(self) -> None:
        """Override to issue GPU work each frame. Default: clear only."""
        self.clear()

    def clear(self, color: tuple[float, float, float, float] | None = None) -> None:
        import wgpu

        assert self._device is not None and self._context is not None
        encoder = self._device.create_command_encoder()
        view = self._context.get_current_texture().create_view()
        encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": view,
                    "clear_value": color or self.CLEAR,
                    "load_op": wgpu.LoadOp.clear,
                    "store_op": wgpu.StoreOp.store,
                }
            ]
        ).end()
        self._device.queue.submit([encoder.finish()])

    def exit(self) -> None:
        self._running = False
        if self._canvas is not None:
            try:
                self._canvas.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            from rendercanvas.auto import loop
            loop.stop()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        import wgpu
        from rendercanvas.auto import RenderCanvas, loop

        adapter = wgpu.gpu.request_adapter_sync()
        self._device = adapter.request_device_sync()
        self._canvas = RenderCanvas(size=self.SIZE, title=self.TITLE)
        self._context = self._canvas.get_wgpu_context()
        self._format = self._context.get_preferred_format(self._device.adapter)
        self._context.configure(device=self._device, format=self._format)
        self._running = True

        @self._canvas.add_event_handler("key_down")
        def _on_key(event: dict) -> None:
            key = str(event.get("key") or "").lower()
            cb = self._bindings.get(key)
            if cb is not None:
                cb()
            self.request_draw()

        @self._canvas.add_event_handler("pointer_down")
        def _on_pointer(event: dict) -> None:
            x = float(event.get("x") or 0)
            y = float(event.get("y") or 0)
            button = int(event.get("button") or 1)
            self.on_pointer(x, y, button)
            self.request_draw()

        @self._canvas.add_event_handler("resize")
        def _on_resize(event: dict) -> None:
            w = int(event.get("width") or 0)
            h = int(event.get("height") or 0)
            if w > 0 and h > 0:
                self.on_resize(w, h)
                self.request_draw()

        self.on_mount()
        try:
            pw, ph = self._canvas.get_physical_size()
            if pw > 0 and ph > 0:
                self.on_resize(int(pw), int(ph))
        except Exception:  # noqa: BLE001
            pass
        self.request_draw()
        try:
            self._canvas.force_draw()
        except Exception:  # noqa: BLE001
            try:
                self.clear()
            except Exception:  # noqa: BLE001
                pass
        loop.run()
