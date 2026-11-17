"""Tests for RemoteViewer, persistent UNIX session re-attach, stdio subprocess, and PlaneCompositor."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from textual_wgpu.demo import build_demo_app
from textual_wgpu.remote import AsyncStreamTransport, Frame, Op, Priority, RemoteViewer
from textual_wgpu.remote.cli import serve_unix_session


async def _drain_viewer_commit(viewer: RemoteViewer) -> None:
    while True:
        frame = await asyncio.wait_for(viewer.transport.recv(), timeout=3.0)
        assert frame is not None
        if viewer.handle_frame(frame):
            return


async def test_unix_session_persists_across_disconnect_and_reattach(tmp_path: Path):
    sock = tmp_path / "sess.sock"
    app = build_demo_app()
    server_task = asyncio.create_task(serve_unix_session(app, sock, size=(900, 560)))

    try:
        for _ in range(40):
            if sock.exists():
                break
            await asyncio.sleep(0.02)
        assert sock.exists()

        # Client 1 attaches, switches from F1 Overview → F3 Data & Tree, then drops
        r1, w1 = await asyncio.open_unix_connection(str(sock))
        v1 = RemoteViewer(AsyncStreamTransport(r1, w1), size=(900, 560), cache_dir=tmp_path / "c1")
        await _drain_viewer_commit(v1)
        assert app.mode_key == "overview"

        await v1.send_key("f3")
        for _ in range(5):
            await _drain_viewer_commit(v1)
            if app.mode_key == "data":
                break
        assert app.mode_key == "data"
        v1.transport.close()
        await w1.wait_closed()
        await asyncio.sleep(0.05)

        # Client 2 re-attaches and immediately receives FULL_SYNC in F3 Data & Tree
        r2, w2 = await asyncio.open_unix_connection(str(sock))
        v2 = RemoteViewer(AsyncStreamTransport(r2, w2), size=(900, 560), cache_dir=tmp_path / "c2")
        await _drain_viewer_commit(v2)
        img2 = v2.render_image()
        assert img2.size == (900, 560)
        assert app.mode_key == "data"
        assert len(v2.state.layers) > 0
        v2.transport.close()
        await w2.wait_closed()
    finally:
        app._running = False
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def test_subprocess_serve_stdio(tmp_path: Path):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "textual_wgpu.remote.cli", "serve-stdio",
        "--width", "800", "--height", "500",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stdin is not None
    tx = AsyncStreamTransport(proc.stdout, proc.stdin)
    viewer = RemoteViewer(tx, size=(800, 500), cache_dir=tmp_path / "blobs")
    try:
        await _drain_viewer_commit(viewer)
        assert viewer.state.title == "textual-wgpu · demo"
        img = viewer.render_image()
        assert img.size == (800, 500)

        await viewer.send_key("f2")
        await _drain_viewer_commit(viewer)
        assert len(viewer.state.layers) >= 4
    finally:
        tx.close()
        proc.terminate()
        await proc.wait()


async def test_universal_env_remote_stdio_runs_plain_module(tmp_path: Path):
    import os

    env = dict(os.environ)
    env["TEXTUAL_WGPU_REMOTE"] = "stdio"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "textual_wgpu",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    assert proc.stdout is not None and proc.stdin is not None
    tx = AsyncStreamTransport(proc.stdout, proc.stdin)
    viewer = RemoteViewer(tx, size=(960, 600), cache_dir=tmp_path / "blobs2")
    try:
        await _drain_viewer_commit(viewer)
        assert viewer.state.title == "textual-wgpu · demo"
        assert len(viewer.state.font_blobs) >= 1
    finally:
        tx.close()
        proc.terminate()
        await proc.wait()


async def test_remote_audio_routes_to_client_not_server(monkeypatch, tmp_path: Path):
    from textual_wgpu.remote import RemoteServerDriver
    from tests.test_remote_loopback import _make_pipe_pair

    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)
    app = build_demo_app()
    drv = RemoteServerDriver(app, server_tx, size=(900, 560))
    viewer = RemoteViewer(client_tx, size=(900, 560), cache_dir=tmp_path / "ablob")

    played: list[tuple[int, int, bytes]] = []
    stopped: list[bool] = []
    monkeypatch.setattr(viewer, "_play_local_audio", lambda sr, ch, pcm: played.append((sr, ch, pcm)))
    monkeypatch.setattr(viewer, "_stop_local_audio", lambda: stopped.append(True))

    await drv.start()
    try:
        while True:
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            if viewer.handle_frame(f):
                break

        app.play_audio_pcm(b"\x01\x02\x03\x04" * 20, sample_rate=22050, channels=1)
        await drv.emit_frame()
        for _ in range(10):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if played:
                break
        assert played == [(22050, 1, b"\x01\x02\x03\x04" * 20)]

        app.stop_audio()
        await drv.emit_frame()
        for _ in range(10):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if stopped:
                break
        assert stopped == [True]
    finally:
        await drv.stop()
        viewer.transport.close()


async def test_tcp_session_persists_across_disconnect_and_reattach(tmp_path: Path):
    import socket

    from textual_wgpu.remote.cli import serve_tcp_session
    from textual_wgpu.remote.client import spawn_tcp_transport

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    app = build_demo_app()
    server_task = asyncio.create_task(serve_tcp_session(app, host="127.0.0.1", port=port, size=(900, 560)))
    try:
        await asyncio.sleep(0.05)
        tx1 = await spawn_tcp_transport("127.0.0.1", port)
        v1 = RemoteViewer(tx1, size=(900, 560), cache_dir=tmp_path / "t1")
        await _drain_viewer_commit(v1)
        await v1.send_key("f4")
        for _ in range(5):
            await _drain_viewer_commit(v1)
            if app.mode_key == "editor":
                break
        assert app.mode_key == "editor"
        tx1.close()
        await asyncio.sleep(0.05)

        tx2 = await spawn_tcp_transport("127.0.0.1", port)
        v2 = RemoteViewer(tx2, size=(900, 560), cache_dir=tmp_path / "t2")
        await _drain_viewer_commit(v2)
        assert app.mode_key == "editor"
        assert len(v2.state.layers) > 0

        # Preemptive handoff: v3 connects WHILE v2 is still open -> v3 gets FRAME_COMMIT immediately!
        tx3 = await spawn_tcp_transport("127.0.0.1", port)
        v3 = RemoteViewer(tx3, size=(900, 560), cache_dir=tmp_path / "t3")
        await _drain_viewer_commit(v3)
        assert len(v3.state.layers) > 0
        tx2.close()
        tx3.close()
    finally:
        app._running = False
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


def test_plane_compositor_creation_and_upload():
    import wgpu

    try:
        adapter = wgpu.gpu.request_adapter_sync()
        device = adapter.request_device_sync()
    except Exception:  # noqa: BLE001
        pytest.skip("No wgpu adapter available in this environment")

    from textual_wgpu.blit import PlaneCompositor

    comp = PlaneCompositor(device, "rgba8unorm")
    comp.upload_ui(b"\x10\x20\x30\xff" * (64 * 64), (64, 64))
    comp.upload_plane(b"\xff\x00\x00\xff" * (32 * 32), (32, 32), (8, 8, 40, 40))
    comp.set_fx(bloom=0.35, scanlines=0.1)
    tex = device.create_texture(
        size=(64, 64, 1),
        format="rgba8unorm",
        usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
    )
    comp.draw(tex.create_view())
    comp.clear_plane()
    comp.draw(tex.create_view())
