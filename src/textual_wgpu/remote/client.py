"""Local GPU viewer & input client for remote ``textual-wgpu`` sessions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from PIL import Image

from .. import fonts as tw_fonts
from ..clipboard import read_clipboard, write_clipboard
from .protocol import (
    POINTER_DOWN,
    POINTER_MOVE,
    POINTER_UP,
    Frame,
    Op,
    Priority,
    decode_app_list,
    decode_audio_pcm,
    decode_launch_status,
    decode_put_blob,
    encode_caps,
    encode_client_file,
    encode_input_pointer,
    encode_input_wheel,
    encode_launch_app,
    encode_resize,
)
from .recorder import RemoteSceneState, apply_remote_frame
from .transport import AsyncStreamTransport


class RemoteViewer:
    """Connects an ``AsyncStreamTransport`` to local GPU composition + OS clipboard."""

    def __init__(
        self,
        transport: AsyncStreamTransport | None = None,
        *,
        connector: Any = None,
        size: tuple[int, int] = (1100, 720),
        cache_dir: Path | None = None,
    ) -> None:
        self.transport: AsyncStreamTransport | None = transport
        self._connector = connector
        self.size = size
        self.state = RemoteSceneState()
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "textual-wgpu" / "remote-blobs")
        self._compositor: Any = None
        self._canvas: Any = None
        self._context: Any = None
        self._frame_img: Image.Image | None = None
        self._running = True
        self._proc: Any = None
        self._audio_proc: Any = None
        self._aloop: asyncio.AbstractEventLoop | None = None
        self.peer_apps: list[tuple[str, str, str, str]] = []
        self.last_launch_status: tuple[bool, str, str] | None = None

    def _blob_path(self, hash16: bytes) -> Path:
        return self.cache_dir / f"{hash16.hex()}.png"

    def handle_frame(self, frame: Frame) -> bool:
        """Apply incoming server frame; returns True if a frame commit completed."""
        if frame.op == Op.PUT_BLOB:
            h16, bw, bh, data = decode_put_blob(frame.payload)
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                ext = "ttf" if (bw == 0 and bh == 0) else "png"
                p = self.cache_dir / f"{h16.hex()}.{ext}"
                if not p.exists():
                    p.write_bytes(data)
            except OSError:
                pass
        elif frame.op == Op.CLIP_SET:
            text = frame.payload.decode("utf-8", errors="replace")
            self.state.clipboard = text
            write_clipboard(text)
            return False
        elif frame.op == Op.AUDIO_PCM:
            sr, ch, _flags, pcm = decode_audio_pcm(frame.payload)
            self._play_local_audio(sr, ch, pcm)
            return False
        elif frame.op == Op.AUDIO_STOP:
            self._stop_local_audio()
            return False
        elif frame.op == Op.OPEN_CLIENT_FILE:
            accept = frame.payload.decode("utf-8", errors="replace")
            self._handle_client_file_request(accept)
            return False
        elif frame.op == Op.APP_LIST:
            self.peer_apps = decode_app_list(frame.payload)
            return False
        elif frame.op == Op.LAUNCH_STATUS:
            self.last_launch_status = decode_launch_status(frame.payload)
            return False
        elif frame.op == Op.HELLO:
            commit = apply_remote_frame(self.state, frame)
            if self.state.width_scale != 1.0:
                tw_fonts.configure(width_scale=self.state.width_scale)
            if self.transport is not None:
                self.transport.send(
                    Frame(
                        op=Op.CAPS,
                        payload=encode_caps(has_gpu=True, vram_mb=1024, codecs="h264,hevc,av1"),
                        prio=Priority.P0_CONTROL,
                    ),
                )
            return commit

        commit = apply_remote_frame(self.state, frame)
        if frame.op == Op.TITLE_SET and self._canvas is not None:
            try:
                self._canvas.set_title(self.state.title)
            except Exception:  # noqa: BLE001
                pass
        if frame.op == Op.FX_SET and self._compositor is not None:
            self._compositor.set_fx(bloom=self.state.fx[0], scanlines=self.state.fx[1])
        return commit

    def render_image(self) -> Image.Image:
        self._frame_img = self.state.render(self.size)
        return self._frame_img

    async def request_apps(self) -> None:
        if self.transport is not None:
            await self.transport.send_now(Frame(op=Op.LIST_APPS, payload=b"", prio=Priority.P0_CONTROL))

    async def launch_app(self, app_id: str, args: str = "") -> None:
        if self.transport is not None:
            await self.transport.send_now(
                Frame(op=Op.LAUNCH_APP, payload=encode_launch_app(app_id, args), prio=Priority.P0_CONTROL),
            )

    def _stop_local_audio(self) -> None:
        if self._audio_proc is not None:
            try:
                self._audio_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._audio_proc = None

    def _handle_client_file_request(self, accept: str) -> None:
        import shutil
        import subprocess
        import threading

        _ = accept

        def _pick() -> None:
            chosen: Path | None = None
            zen = shutil.which("zenity")
            if zen:
                try:
                    res = subprocess.run(
                        [zen, "--file-selection", "--title=Pick media file for remote app"],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        chosen = Path(res.stdout.strip())
                except Exception:  # noqa: BLE001
                    chosen = None
            if chosen is None:
                try:
                    import tkinter as tk
                    from tkinter import filedialog

                    root = tk.Tk()
                    root.withdraw()
                    p = filedialog.askopenfilename(title="Pick media file for remote app")
                    root.destroy()
                    if p:
                        chosen = Path(p)
                except Exception:  # noqa: BLE001
                    chosen = None
            if chosen is not None and chosen.is_file() and self.transport is not None:
                data = chosen.read_bytes()
                frame = Frame(
                    op=Op.CLIENT_FILE_DATA,
                    payload=encode_client_file(chosen.name, data),
                    prio=Priority.P3_BULK,
                )
                if self._aloop is not None and self._aloop.is_running():
                    self._aloop.call_soon_threadsafe(
                        asyncio.ensure_future,
                        self.transport.send_now(frame, compress=False),
                    )
                else:
                    self.transport.send(frame, compress=False)

        threading.Thread(target=_pick, daemon=True).start()

    def _play_local_audio(self, sample_rate: int, channels: int, pcm: bytes) -> None:
        import shutil
        import subprocess
        import wave

        self._stop_local_audio()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self.cache_dir / "remote_audio.wav"
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(max(1, int(channels)))
                wf.setsampwidth(2)
                wf.setframerate(max(8000, int(sample_rate)))
                wf.writeframes(pcm)
            for tool in ("pw-play", "paplay", "aplay"):
                exe = shutil.which(tool)
                if exe:
                    self._audio_proc = subprocess.Popen(
                        [exe, str(wav_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    break
        except Exception:  # noqa: BLE001
            self._audio_proc = None

    async def send_key(self, key: str) -> None:
        if self.transport is None:
            return
        if key == "ctrl+v":
            clip = read_clipboard()
            if clip:
                self.transport.send(
                    Frame(op=Op.CLIP_PASTE, payload=clip.encode("utf-8"), prio=Priority.P0_CONTROL),
                )
        await self.transport.send_now(
            Frame(op=Op.INPUT_KEY, payload=key.encode("utf-8"), prio=Priority.P0_CONTROL),
        )

    async def send_char(self, ch: str) -> None:
        if ch and self.transport is not None:
            await self.transport.send_now(
                Frame(op=Op.INPUT_CHAR, payload=ch.encode("utf-8"), prio=Priority.P0_CONTROL),
            )

    async def send_pointer(self, kind: int, x: float, y: float, button: int = 1) -> None:
        if self.transport is None:
            return
        await self.transport.send_now(
            Frame(
                op=Op.INPUT_POINTER,
                payload=encode_input_pointer(kind, x, y, button),
                prio=Priority.P0_CONTROL,
            ),
        )

    async def send_wheel(self, x: float, y: float, dx: float, dy: float) -> None:
        if self.transport is None:
            return
        await self.transport.send_now(
            Frame(
                op=Op.INPUT_WHEEL,
                payload=encode_input_wheel(x, y, dx, dy),
                prio=Priority.P0_CONTROL,
            ),
        )

    async def send_resize(self, w: int, h: int) -> None:
        self.size = (max(1, int(w)), max(1, int(h)))
        if self.transport is None:
            return
        await self.transport.send_now(
            Frame(op=Op.RESIZE, payload=encode_resize(*self.size), prio=Priority.P0_CONTROL),
        )

    def run_windowed(self) -> None:  # pragma: no cover — interactive GPU loop
        import wgpu
        from rendercanvas.auto import RenderCanvas, loop
        from ..app import _normalize_key
        from ..blit import PlaneCompositor

        aloop = asyncio.new_event_loop()
        asyncio.set_event_loop(aloop)
        self._aloop = aloop

        # Authenticate & complete HELLO handshake in terminal BEFORE opening the GPU window.
        async def _connect_and_hello() -> bool:
            if self._connector is not None and self.transport is None:
                res = await self._connector()
                if isinstance(res, tuple):
                    self._proc, self.transport = res
                else:
                    self.transport = res
            if self.transport is None:
                return False
            while self._running:
                frame = await self.transport.recv()
                if frame is None:
                    return False
                self.handle_frame(frame)
                if frame.op == Op.HELLO:
                    return True
                if frame.op == Op.FRAME_COMMIT:
                    return True
            return False

        ok = False
        try:
            ok = aloop.run_until_complete(_connect_and_hello())
        except Exception as exc:  # noqa: BLE001
            print(f"Remote connect failed: {exc}", flush=True)
            ok = False

        if not ok:
            if self.transport is not None:
                self.transport.close()
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            aloop.close()
            return

        adapter = wgpu.gpu.request_adapter_sync()
        device = adapter.request_device_sync()
        canvas = RenderCanvas(size=self.size, title=self.state.title or "textual-wgpu · remote")
        context = canvas.get_wgpu_context()
        fmt = str(context.get_preferred_format(device.adapter)).replace("-srgb", "") or "bgra8unorm"
        context.configure(device=device, format=fmt)
        comp = PlaneCompositor(device, fmt)
        self._canvas = canvas
        self._context = context
        self._compositor = comp

        def _draw() -> None:
            uploaded_plane = False
            for _sid, pl in self.state.planes.items():
                rect = pl.get("rect")
                rgba = pl.get("rgba")
                psize = pl.get("size")
                if rect and rgba and psize:
                    comp.upload_plane(rgba, psize, rect)
                    uploaded_plane = True
                    break
            if uploaded_plane:
                ui_img = self.state.render_ui(self.size)
                self._frame_img = ui_img
                comp.upload_ui(ui_img.tobytes("raw", "RGBA"), ui_img.size)
            else:
                comp.clear_plane()
                img = self.render_image()
                comp.upload_ui(img.tobytes("raw", "RGBA"), img.size)
            comp.draw(context.get_current_texture().create_view())

        def _submit(coro: Any) -> None:
            if self._running and aloop.is_running():
                aloop.call_soon_threadsafe(asyncio.ensure_future, coro)
            else:
                try:
                    coro.close()
                except Exception:  # noqa: BLE001
                    pass

        @canvas.add_event_handler("close")
        def _on_close(_event: dict) -> None:
            self._running = False
            if self.transport is not None:
                self.transport.close()
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:  # noqa: BLE001
                    pass

        @canvas.add_event_handler("key_down")
        def _on_key(event: dict) -> None:
            key = _normalize_key(str(event.get("key") or ""))
            mods = {str(m).lower() for m in (event.get("modifiers") or ())}
            if "control" in mods or "ctrl" in mods:
                if key in ("s", "c", "v", "a", "p", "enter"):
                    key = f"ctrl+{key}"
            if "shift" in mods and not key.startswith("ctrl+"):
                if key in ("left", "right", "up", "down", "home", "end", "tab"):
                    key = f"shift+{key}"
                elif len(key) == 1 and "a" <= key <= "z":
                    key = f"shift+{key}"
            _submit(self.send_key(key))

        @canvas.add_event_handler("char")
        def _on_char(event: dict) -> None:
            ch = str(event.get("char_str") or event.get("data") or "")
            _submit(self.send_char(ch))

        @canvas.add_event_handler("pointer_down")
        def _on_pdown(event: dict) -> None:
            _submit(self.send_pointer(
                POINTER_DOWN,
                float(event.get("x") or 0),
                float(event.get("y") or 0),
                int(event.get("button") or 1),
            ))

        @canvas.add_event_handler("pointer_move")
        def _on_pmove(event: dict) -> None:
            _submit(self.send_pointer(
                POINTER_MOVE,
                float(event.get("x") or 0),
                float(event.get("y") or 0),
                int(event.get("button") or 0),
            ))

        @canvas.add_event_handler("pointer_up")
        def _on_pup(event: dict) -> None:
            _submit(self.send_pointer(
                POINTER_UP,
                float(event.get("x") or 0),
                float(event.get("y") or 0),
                int(event.get("button") or 1),
            ))

        @canvas.add_event_handler("wheel")
        def _on_wheel(event: dict) -> None:
            _submit(self.send_wheel(
                float(event.get("x") or 0),
                float(event.get("y") or 0),
                float(event.get("dx") or 0),
                float(event.get("dy") or 0),
            ))

        @canvas.add_event_handler("resize")
        def _on_resize(event: dict) -> None:
            w = int(event.get("width") or 0)
            h = int(event.get("height") or 0)
            if w > 0 and h > 0:
                _submit(self.send_resize(w, h))

        async def _rx() -> None:
            if self.transport is None:
                self._running = False
                return
            try:
                pw, ph = canvas.get_physical_size()
                if pw > 0 and ph > 0:
                    await self.send_resize(int(pw), int(ph))
                else:
                    await self.transport.flush()
            except Exception:  # noqa: BLE001
                pass
            while self._running:
                frame = await self.transport.recv()
                if frame is None:
                    self._running = False
                    break
                if self.handle_frame(frame):
                    canvas.request_draw(_draw)
            try:
                canvas.close()
            except Exception:  # noqa: BLE001
                pass

        import threading

        def _bg() -> None:
            asyncio.set_event_loop(aloop)
            try:
                aloop.run_until_complete(_rx())
            finally:
                if self._proc is not None:
                    try:
                        self._proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                aloop.close()

        threading.Thread(target=_bg, daemon=True).start()
        loop.run()
        self._running = False


async def spawn_subprocess_transport(cmd: list[str]) -> tuple[asyncio.subprocess.Process, AsyncStreamTransport]:
    """Spawn a local subprocess and wrap its stdio as ``AsyncStreamTransport``."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert proc.stdout is not None and proc.stdin is not None
    return proc, AsyncStreamTransport(proc.stdout, proc.stdin)


async def spawn_ssh_transport(
    host: str,
    remote_args: list[str],
    *,
    ssh_binary: str = "ssh",
) -> tuple[asyncio.subprocess.Process, AsyncStreamTransport]:
    """Spawn ``ssh -T -o Compression=no <host> <remote_args...>`` and wrap stdio."""
    cmd = [ssh_binary, "-T", "-o", "Compression=no", host, *remote_args]
    return await spawn_subprocess_transport(cmd)


async def spawn_tcp_transport(host: str, port: int) -> AsyncStreamTransport:
    """Connect directly over TCP to a ``serve-tcp`` session."""
    reader, writer = await asyncio.open_connection(host, int(port))
    return AsyncStreamTransport(reader, writer)
