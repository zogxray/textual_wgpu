"""End-to-end loopback tests for RemoteServerDriver + DrawRecorder + RemoteSceneState."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image, ImageDraw
from rich.text import Text

from textual_wgpu.compositor import make_image_text
from textual_wgpu.demo import build_demo_app
from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.remote import (
    AsyncStreamTransport,
    Frame,
    Op,
    Priority,
    RemoteSceneState,
    RemoteServerDriver,
    apply_remote_frame,
)
from textual_wgpu.remote.protocol import encode_input_wheel, encode_resize
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Horizontal, Option, OptionList, Panel, RichText, TextArea, VerticalScroll


def _make_pipe_pair() -> tuple[asyncio.StreamReader, asyncio.StreamWriter, asyncio.StreamReader, asyncio.StreamWriter]:
    """Create two cross-wired in-memory asyncio stream pairs."""
    loop = asyncio.get_running_loop()

    class _MemReader(asyncio.StreamReader):
        pass

    s2c_reader = _MemReader(loop=loop)
    c2s_reader = _MemReader(loop=loop)

    class _PipeTransport(asyncio.Transport):
        def __init__(self, target: asyncio.StreamReader) -> None:
            super().__init__()
            self._target = target
            self._closed = False

        def write(self, data: bytes | bytearray | memoryview) -> None:
            if not self._closed:
                self._target.feed_data(bytes(data))

        def is_closing(self) -> bool:
            return self._closed

        def close(self) -> None:
            if not self._closed:
                self._closed = True
                self._target.feed_eof()

    s2c_writer = asyncio.StreamWriter(
        _PipeTransport(s2c_reader), asyncio.StreamReaderProtocol(s2c_reader, loop=loop), s2c_reader, loop,
    )
    c2s_writer = asyncio.StreamWriter(
        _PipeTransport(c2s_reader), asyncio.StreamReaderProtocol(c2s_reader, loop=loop), c2s_reader, loop,
    )
    return s2c_reader, s2c_writer, c2s_reader, c2s_writer


async def _drain_until_commit(transport: AsyncStreamTransport, state: RemoteSceneState) -> int:
    total_wire = 0
    while True:
        frame = await asyncio.wait_for(transport.recv(), timeout=2.0)
        assert frame is not None
        total_wire += len(frame.payload)
        if apply_remote_frame(state, frame):
            return total_wire


async def test_remote_loopback_demo_dirty_layers_and_clipboard():
    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)

    app = build_demo_app()
    drv = RemoteServerDriver(app, server_tx, size=(1000, 640))
    state = RemoteSceneState()

    await drv.start()
    try:
        # Drain initial HELLO + first full frame commit
        init_bytes = await _drain_until_commit(client_tx, state)
        assert state.title == "textual-wgpu · demo"
        assert init_bytes > 0
        img0 = state.render((1000, 640))
        assert img0.getpixel((30, 30)) != (0, 0, 0, 0)

        # Switch to F4 Editor & Modals, Tab into TextArea, and type:
        # Verify dirty layer diff only updates edited panels (tiny payload)
        await client_tx.send_now(Frame(op=Op.INPUT_KEY, payload=b"f4", prio=Priority.P0_CONTROL))
        await _drain_until_commit(client_tx, state)
        await client_tx.send_now(Frame(op=Op.INPUT_KEY, payload=b"tab", prio=Priority.P0_CONTROL))
        await _drain_until_commit(client_tx, state)

        # Measure incremental typing diff (should be small, < 2KB uncompressed ops)
        await client_tx.send_now(Frame(op=Op.INPUT_CHAR, payload=b"Z", prio=Priority.P0_CONTROL))
        type_bytes = await _drain_until_commit(client_tx, state)
        assert type_bytes < 2500

        # Paste from client → Ctrl+V → Select All → Ctrl+C back to client clipboard
        await client_tx.send_now(Frame(op=Op.CLIP_PASTE, payload=b"REMOTE_CLIP", prio=Priority.P0_CONTROL))
        await client_tx.send_now(Frame(op=Op.INPUT_KEY, payload=b"ctrl+v", prio=Priority.P0_CONTROL))
        await _drain_until_commit(client_tx, state)
        assert "REMOTE_CLIP" in app.screen.query_one("#demo-editor", TextArea).text

        await client_tx.send_now(Frame(op=Op.INPUT_KEY, payload=b"ctrl+a", prio=Priority.P0_CONTROL))
        await _drain_until_commit(client_tx, state)
        await client_tx.send_now(Frame(op=Op.INPUT_KEY, payload=b"ctrl+c", prio=Priority.P0_CONTROL))
        # Drain until CLIP_SET arrives on client
        for _ in range(10):
            f = await asyncio.wait_for(client_tx.recv(), timeout=2.0)
            assert f is not None
            apply_remote_frame(state, f)
            if state.clipboard:
                break
        assert "REMOTE_CLIP" in state.clipboard
    finally:
        await drv.stop()
        client_tx.close()


async def test_remote_loopback_preserves_inline_images_and_pixel_parity(tmp_path: Path):
    img_path = tmp_path / "swatch.png"
    src = Image.new("RGBA", (120, 80), (210, 40, 40, 255))
    for x in range(60, 120):
        for y in range(80):
            src.putpixel((x, y), (40, 90, 220, 255))
    src.save(img_path)

    doc = Text("Title\n")
    doc.append_text(make_image_text(img_path, max_width_px=120, max_height_px=80, row_height_px=18, indent_px=12))

    class Body(Screen):
        DEFAULT_CSS = "#main { height: 1fr; } #left { width: 24; } #right { width: 1fr; }"

        def compose(self):
            ol = OptionList(Option("A", id="a"), Option("B", id="b"), id="ol")
            rt = RichText(doc, id="rt", can_focus=False)
            sc = VerticalScroll(rt, id="sc")
            yield Horizontal(Panel(ol, title="LIST", id="left"), Panel(sc, title="IMG", id="right"), id="main")

    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)

    app = ModeApp([("m", "M", Body)], theme=Theme.system(), start="m")
    drv = RemoteServerDriver(app, server_tx, size=(600, 300))
    state = RemoteSceneState()

    await drv.start()
    try:
        await _drain_until_commit(client_tx, state)
        assert len(state.blobs) == 1

        # Wheel scroll over the image panel (sub-row smooth scroll via CLIP_PUSH/CLIP_POP)
        sc = app.screen.query_one("#sc", VerticalScroll)
        await client_tx.send_now(
            Frame(op=Op.INPUT_WHEEL, payload=encode_input_wheel(sc.rect.x + 20, sc.rect.y + 20, 0.0, 20.0), prio=Priority.P0_CONTROL),
        )
        await _drain_until_commit(client_tx, state)
        # Image blob was cached once and NOT re-uploaded on scroll!
        assert len(state.blobs) == 1

        # Compare replayed image against direct local ImageDraw paint
        remote_img = state.render((600, 300))
        local_img = Image.new("RGBA", (600, 300), app.theme.rgba("background"))
        app.screen.paint(ImageDraw.Draw(local_img))
        assert remote_img.size == local_img.size
        # Check red/blue inline image pixels exist identically in both
        reds_remote = sum(
            1 for y in range(300) for x in range(600)
            if remote_img.getpixel((x, y))[:3] == (210, 40, 40)
        )
        reds_local = sum(
            1 for y in range(300) for x in range(600)
            if local_img.getpixel((x, y))[:3] == (210, 40, 40)
        )
        assert reds_remote > 500
        assert reds_remote == reds_local

        # Resize triggers re-layout and commit cleanly
        await client_tx.send_now(Frame(op=Op.RESIZE, payload=encode_resize(720, 360), prio=Priority.P0_CONTROL))
        await _drain_until_commit(client_tx, state)
        assert app._pixel_size == (720, 360)
    finally:
        await drv.stop()
        client_tx.close()
