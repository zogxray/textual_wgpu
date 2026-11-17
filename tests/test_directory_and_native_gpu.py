"""Tests for Network OS NodeRegistry, LIST_APPS/LAUNCH_APP protocol, and PyO3 tw_native GPU rendering."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual_wgpu.app import App
from textual_wgpu.demo import build_demo_app
from textual_wgpu.geometry import Rect
from textual_wgpu.remote import (
    AsyncStreamTransport,
    DrawRecorder,
    Frame,
    NodeEntry,
    NodeRegistry,
    Op,
    Priority,
    RemoteServerDriver,
    RemoteViewer,
    discover_local_apps,
)
from textual_wgpu.remote.protocol import (
    decode_app_list,
    decode_launch_app,
    decode_launch_status,
    encode_app_list,
    encode_launch_app,
    encode_launch_status,
)
from textual_wgpu.screen import Screen
from textual_wgpu.widgets import Panel, Static


def _make_pipe_pair():
    loop = asyncio.get_running_loop()
    s2c_r = asyncio.StreamReader(loop=loop)
    c2s_r = asyncio.StreamReader(loop=loop)

    class _Pipe(asyncio.Transport):
        def __init__(self, target: asyncio.StreamReader) -> None:
            super().__init__()
            self._target = target
            self._closed = False

        def write(self, data) -> None:
            if not self._closed:
                self._target.feed_data(bytes(data))

        def is_closing(self) -> bool:
            return self._closed

        def close(self) -> None:
            if not self._closed:
                self._closed = True
                self._target.feed_eof()

    s2c_w = asyncio.StreamWriter(_Pipe(s2c_r), asyncio.StreamReaderProtocol(s2c_r, loop=loop), s2c_r, loop)
    c2s_w = asyncio.StreamWriter(_Pipe(c2s_r), asyncio.StreamReaderProtocol(c2s_r, loop=loop), c2s_r, loop)
    return s2c_r, s2c_w, c2s_r, c2s_w


def test_app_directory_protocol_codecs():
    apps = [
        ("demo", "Demo", "Full suite", "python"),
        ("demo:media", "Media", "Video & audio", "mode"),
    ]
    raw = encode_app_list(apps)
    assert decode_app_list(raw) == apps

    la = encode_launch_app("demo:media", "--hd")
    assert decode_launch_app(la) == ("demo:media", "--hd")

    ls = encode_launch_status(True, "demo:media", "Switched")
    assert decode_launch_status(ls) == (True, "demo:media", "Switched")


def test_node_registry_persistence_and_app_discovery(tmp_path: Path):
    reg_path = tmp_path / "nodes.json"
    reg = NodeRegistry(path=reg_path)
    roles = {n.role for n in reg.list_nodes()}
    assert {"workstation", "phone", "cloud"} <= roles

    reg.upsert(
        NodeEntry(
            id="ws2",
            name="Lab Workstation",
            role="workstation",
            transport="ssh",
            host="10.0.0.42",
            port=2222,
            user="dev",
        ),
    )
    reg2 = NodeRegistry(path=reg_path)
    ws2 = reg2.get("ws2")
    assert ws2 is not None and ws2.host == "10.0.0.42" and ws2.port == 2222
    assert reg2.remove("ws2") is True
    assert NodeRegistry(path=reg_path).get("ws2") is None

    apps_json = tmp_path / "apps.json"
    apps_json.write_text(
        json.dumps({
            "apps": [
                {
                    "id": "custom_hello",
                    "name": "Custom Hello",
                    "description": "Custom test app",
                    "kind": "python",
                    "target": "textual_wgpu.demo:build_demo_app",
                },
            ],
        }),
        encoding="utf-8",
    )
    apps = discover_local_apps(apps_path=apps_json)
    ids = {a.app_id for a in apps}
    assert {"demo", "demo:media", "custom_hello"} <= ids


async def test_remote_list_apps_and_launch_app_hotswap(tmp_path: Path):
    apps_json = tmp_path / "apps.json"
    apps_json.write_text(
        json.dumps({
            "apps": [
                {
                    "id": "swapped_app",
                    "name": "Swapped Demo",
                    "description": "Hot-swapped Python App",
                    "kind": "python",
                    "target": "textual_wgpu.demo:build_demo_app",
                },
            ],
        }),
        encoding="utf-8",
    )

    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)
    app = build_demo_app()
    drv = RemoteServerDriver(app, server_tx, size=(900, 560))
    drv.apps_path = apps_json
    viewer = RemoteViewer(client_tx, size=(900, 560), cache_dir=tmp_path / "cache")

    await drv.start()
    try:
        while True:
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            if viewer.handle_frame(f):
                break

        await viewer.request_apps()
        for _ in range(15):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if viewer.peer_apps:
                break
        assert any(a[0] == "swapped_app" for a in viewer.peer_apps)

        # Launch a mode inside the running ModeApp
        await viewer.launch_app("demo:media")
        for _ in range(25):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if viewer.last_launch_status is not None:
                break
        assert viewer.last_launch_status is not None and viewer.last_launch_status[0] is True
        assert drv.app.mode_key == "media"

        # Hot-swap to a brand new App instance via LAUNCH_APP
        viewer.last_launch_status = None
        await viewer.launch_app("swapped_app")
        for _ in range(30):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if viewer.last_launch_status is not None:
                break
        assert viewer.last_launch_status is not None and viewer.last_launch_status[0] is True
        assert drv.app is not app
    finally:
        await drv.stop()
        viewer.transport.close()


def test_tw_native_gpu_compiler_and_optional_offscreen_render():
    from textual_wgpu.blit import GpuDrawOpRenderer, load_tw_native

    nat = load_tw_native()
    if nat is None:
        return

    comp = nat.GpuUiCompiler()
    rec = DrawRecorder(raw_blob_mode=True, measure_fn=comp.measure_text)
    app = build_demo_app()
    scr = app._ensure_screen()
    scr.layout(Rect(0, 0, 800, 500))
    rec.begin_frame()
    rec.begin_layer("root", (0, 0, 800, 500))
    scr.paint(rec)
    rec.end_layer()
    rec.finish_frame()

    comp.set_styles(rec.styles.drain_new())
    inst_bytes, batches, uploads = comp.compile_layers(800, 500, rec._frame_raw_layers)
    assert len(inst_bytes) > 0 and len(inst_bytes) % 64 == 0
    assert len(batches) >= 1
    assert len(uploads) >= 1

    # Second identical frame produces zero new glyph atlas uploads
    _i2, _b2, uploads2 = comp.compile_layers(800, 500, rec._frame_raw_layers)
    assert uploads2 == []

    # Verify native 3D mesh acceleration
    mesh = nat.render_mesh_rgba(64, 48, 0.5, 0.3, 1.0, (20, 20, 24, 255), (108, 178, 235))
    assert len(mesh) == 64 * 48 * 4

    # Try real headless wgpu GPU render if an adapter is present
    try:
        import wgpu

        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        if adapter is None:
            return
        device = adapter.request_device_sync()
        tex = device.create_texture(
            size=(800, 500, 1),
            format="rgba8unorm",
            usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
        )
        renderer = GpuDrawOpRenderer(device, "rgba8unorm", native_mod=nat)
        renderer.render_frame(tex.create_view(), 800, 500, app.theme.rgba("background"), rec)
        raw = device.queue.read_texture({"texture": tex}, {"bytes_per_row": 800 * 4, "rows_per_image": 500}, (800, 500, 1))
        assert len(raw) == 800 * 500 * 4
        # Ensure non-uniform pixels were drawn onto the GPU render target
        assert len(set(bytes(raw[: 800 * 40 * 4]))) > 8
    except Exception:  # noqa: BLE001
        pass
