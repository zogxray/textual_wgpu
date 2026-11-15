"""Minimal App shell: window, clear color, key bindings, draw hook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class App:
    """Tiny Textual-flavoured shell.

    Not API-compatible with Textual. Enough to host a GPU demo and grow
    toward screens / compose later.
    """

    TITLE = "textual-wgpu"
    SIZE = (960, 600)
    CLEAR = (0.05, 0.35, 0.18, 1.0)

    def __init__(self) -> None:
        self._canvas: Any = None
        self._device: Any = None
        self._context: Any = None
        self._format: str | None = None
        self._running = False
        self._bindings: dict[str, Callable[[], None]] = {
            "q": self.exit,
            "escape": self.exit,
        }

    def bind(self, key: str, callback: Callable[[], None]) -> None:
        self._bindings[key.lower()] = callback

    def on_mount(self) -> None:
        """Override for setup after the GPU context exists."""

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

        self.on_mount()
        self._canvas.request_draw(self.draw)
        loop.run()
