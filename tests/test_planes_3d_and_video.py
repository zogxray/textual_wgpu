"""Viewport3D + Video planes: Client/Server GPU balancing, parallel render, and remote loopback."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image, ImageDraw

from textual_wgpu.geometry import Rect
from textual_wgpu.mode_app import ModeApp
from textual_wgpu.remote import (
    AsyncStreamTransport,
    Frame,
    Op,
    Priority,
    RemoteServerDriver,
    RemoteViewer,
)
from textual_wgpu.remote.protocol import encode_caps
from textual_wgpu.screen import Screen
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Horizontal, Panel, TextArea, Video, Viewport3D
from textual_wgpu.widgets.viewport3d import ClientCapabilities


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


def test_viewport3d_gpu_balancing_policy():
    small = Viewport3D(scene_vram_bytes=8 * 1024 * 1024)
    huge = Viewport3D(scene_vram_bytes=2 * 1024 * 1024 * 1024)
    private = Viewport3D(scene_vram_bytes=1024, allow_client_assets=False)

    strong_client = ClientCapabilities(has_gpu=True, vram_mb=2048)
    thin_client = ClientCapabilities(has_gpu=False, vram_mb=0)

    assert small.choose_execution_mode(strong_client) == "client"
    assert huge.choose_execution_mode(strong_client) == "server"
    assert private.choose_execution_mode(strong_client) == "server"
    assert small.choose_execution_mode(thin_client) == "server"


def test_viewport3d_and_video_local_paint():
    class Body(Screen):
        DEFAULT_CSS = "#main { height: 1fr; } #vp { width: 1fr; } #vid { width: 1fr; }"

        def compose(self):
            vp = Viewport3D(id="vp")
            vid = Video(id="vid")
            vid.push_rgba_frame(16, 16, b"\x20\xd0\x50\xff" * (16 * 16))
            yield Horizontal(vp, vid, id="main")

    app = ModeApp([("m", "M", Body)], theme=Theme.system(), start="m")
    app._pixel_size = (640, 320)
    screen = app._ensure_screen()
    screen.layout(Rect(0, 0, 640, 320))
    img = Image.new("RGBA", (640, 320))
    screen.paint(ImageDraw.Draw(img))
    # Video green pixels painted on right half; 3D shaded mesh painted on left half
    assert img.getpixel((480, 160))[:3] == (0x20, 0xD0, 0x50)
    vp = screen.query_one("#vp", Viewport3D)
    prev_yaw = vp.yaw
    assert vp.on_key("right") is True
    assert vp.yaw > prev_yaw


async def test_remote_3d_client_mode_vs_server_mode_and_video_passthrough():
    class Body(Screen):
        DEFAULT_CSS = "#main { height: 1fr; } #vp { width: 1fr; } #chat { width: 32; }"

        def compose(self):
            vp = Viewport3D(id="vp", scene_vram_bytes=2 * 1024 * 1024)
            vid = Video(id="vid")
            chat = TextArea("hello", id="chat")
            yield Horizontal(Panel(vp, title="3D"), vid, Panel(chat, title="CHAT"), id="main")

    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)
    app = ModeApp([("m", "M", Body)], theme=Theme.system(), start="m")
    drv = RemoteServerDriver(app, server_tx, size=(800, 400))
    viewer = RemoteViewer(client_tx, size=(800, 400))

    async def _drain_until(op_target: int, *, surf_id: int | None = None) -> list[Frame]:
        seen: list[Frame] = []
        while True:
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            seen.append(f)
            if f.op == op_target and (surf_id is None or f.surf_id == surf_id):
                return seen

    await drv.start()
    try:
        # Initial connect defaults to client-GPU 3D mode (64B uniform BUF_WRITE!)
        init_frames = await _drain_until(Op.FRAME_COMMIT)
        assert any(f.op == Op.PLANE_RECT for f in init_frames)
        assert any(f.op == Op.BUF_WRITE for f in init_frames)
        img_client = viewer.render_image()
        assert img_client.size == (800, 400)
        vp = app.screen.query_one("#vp", Viewport3D)
        cx, cy = (vp.rect.x + vp.rect.x1) // 2, (vp.rect.y + vp.rect.y1) // 2
        # Verify OP_CLEAR_RECT punched through Panel so 3D mesh pixels are visible (not panel bg)
        assert img_client.getpixel((cx, cy)) != app.theme.rgba("panel")

        # Now client reports thin GPU (has_gpu=False) -> server switches 3D to server-rendered VID_NALU!
        await viewer.transport.send_now(
            Frame(op=Op.CAPS, payload=encode_caps(has_gpu=False, vram_mb=0), prio=Priority.P0_CONTROL),
        )
        srv_frames = await _drain_until(Op.VID_NALU)
        assert any(f.op == Op.VID_NALU for f in srv_frames)

        # Queue a compressed Video passthrough NALU packet + verify P2 delivery
        vid = app.screen.query_one("#vid", Video)
        vid.push_packet(12345, b"\x00\x00\x00\x01\x65FAKE_H264_IDR", keyframe=True)
        await drv.emit_frame()
        await _drain_until(Op.VID_NALU, surf_id=vid.surf_id)
        assert viewer.state.planes[vid.surf_id]["last_nalu"] == b"\x00\x00\x00\x01\x65FAKE_H264_IDR"

        # Push an RGBA Video frame smaller than Video.rect (raw fast-path when prefer_hw_video=False)
        drv.prefer_hw_video = False
        vid.push_rgba_frame(64, 32, b"\x20\xd0\x60\xff" * (64 * 32))
        await drv.emit_frame()
        await _drain_until(Op.VID_NALU, surf_id=vid.surf_id)
        img_vid = viewer.render_image()
        vx, vy = (vid.rect.x + vid.rect.x1) // 2, (vid.rect.y + vid.rect.y1) // 2
        assert img_vid.getpixel((vx, vy))[:3] == (0x20, 0xD0, 0x60)

        # Also test real HEVC/H.264 StreamVideoEncoder -> VID_NALU -> StreamVideoDecoder round-trip
        from textual_wgpu.remote.media_codec import StreamVideoDecoder, StreamVideoEncoder, probe_video_encoder

        if probe_video_encoder(("hevc", "h264")) is not None:
            enc = StreamVideoEncoder(256, 128, fps=24, preferred=("hevc", "h264"))
            dec = StreamVideoDecoder(enc.codec_flag)
            try:
                rgba_in = b"\x20\xd0\x60\xff" * (256 * 128)
                for _ in range(12):
                    enc.encode_rgba(rgba_in, (256, 128))
                    await asyncio.sleep(0.03)
                    for au, _kf in enc.drain_aus():
                        dec.feed(au)
                    if dec.latest_frame() is not None:
                        break
                for _ in range(20):
                    if dec.latest_frame() is not None:
                        break
                    for au, _kf in enc.drain_aus():
                        dec.feed(au)
                    await asyncio.sleep(0.03)
                out_frame = dec.latest_frame()
                assert out_frame is not None
                ow, oh, orgba = out_frame
                assert (ow, oh) == (256, 128) and len(orgba) == 256 * 128 * 4
            finally:
                enc.close()
                dec.close()
    finally:
        await drv.stop()
        viewer.transport.close()


async def test_client_file_upload_and_media_screen_file_loading(monkeypatch, tmp_path: Path):
    import io
    from PIL import Image as PilImage

    from textual_wgpu.demo import build_demo_app
    from textual_wgpu.remote.protocol import encode_client_file

    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)
    app = build_demo_app()
    drv = RemoteServerDriver(app, server_tx, size=(960, 600))
    viewer = RemoteViewer(client_tx, size=(960, 600), cache_dir=tmp_path / "mcache")

    opened_prompts: list[str] = []
    monkeypatch.setattr(viewer, "_handle_client_file_request", lambda acc: opened_prompts.append(acc))

    await drv.start()
    try:
        while True:
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            if viewer.handle_frame(f):
                break

        await viewer.send_key("f7")
        for _ in range(10):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if app.mode_key == "media":
                break
        assert app.mode_key == "media"

        from textual_wgpu.remote.directory import DeviceProfile

        # Press O -> unified FilePickerModal opens; when peer == same device, NO duplicate self-entry!
        png_buf = io.BytesIO()
        PilImage.new("RGBA", (64, 48), (12, 180, 240, 255)).save(png_buf, format="PNG")
        peer_file_path = tmp_path / "sample_upload.png"
        peer_file_path.write_bytes(png_buf.getvalue())
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        (tmp_path / "Downloads").mkdir(exist_ok=True)

        await asyncio.sleep(0.05)  # allow CAPS from viewer to arrive
        await viewer.send_key("o")
        for _ in range(20):
            if getattr(app, "_open_file", None) is not None:
                break
            await asyncio.sleep(0.02)
        modal_same = getattr(app, "_open_file", None)
        assert modal_same is not None
        assert not any(e[0] == "peer_dir" for e in modal_same["entries"])
        app._open_file_cancel()

        # Now peer has a distinct device_id ("moto g75 5G") -> appears cleanly with real name!
        drv.peer_profile = DeviceProfile(
            device_id="android-remote-1",
            name="moto g75 5G",
            kind="android",
            cameras=[("front", "Front Camera"), ("back", "Back Camera")],
        )
        await viewer.send_key("o")
        for _ in range(20):
            if getattr(app, "_open_file", None) is not None:
                break
            await asyncio.sleep(0.02)
        modal = getattr(app, "_open_file", None)
        assert modal is not None and modal.get("unified") is True
        entries = modal["entries"]
        peer_idx = next(i for i, e in enumerate(entries) if e[0] == "peer_dir")
        assert "moto g75 5G" in entries[peer_idx][2]
        modal["index"] = peer_idx
        app._open_file_activate()

        # Drain FS_LIST -> viewer responds with FS_ENTRIES -> modal populates peer folders!
        for _ in range(25):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if any(e[0] == "peer_dir" and e[1] != "" for e in (getattr(app, "_open_file", None) or {}).get("entries", [])):
                break
        assert any(e[0] == "peer_dir" for e in app._open_file["entries"])

        # Select the peer file via FS_GET -> CLIENT_FILE_DATA -> MediaScreen loads photo!
        app._open_file["entries"] = [("peer_file", str(peer_file_path), "  sample_upload.png")]
        app._open_file["index"] = 0
        app._open_file_activate()
        for _ in range(25):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if getattr(app.screen, "_custom_photo", None) is not None:
                break
        custom = getattr(app.screen, "_custom_photo", None)
        assert custom is not None and custom.name == "client_sample_upload.png"
        assert opened_prompts == []  # Never triggered OS system picker (zenity/OpenDocument)
    finally:
        await drv.stop()
        viewer.transport.close()


def test_media_screen_loads_real_mp4_and_mp3_files(tmp_path: Path):
    import shutil
    import subprocess
    from textual_wgpu.demo import build_demo_app
    from textual_wgpu.geometry import Rect

    ff = shutil.which("ffmpeg")
    if not ff:
        return
    mp4 = tmp_path / "sample.mp4"
    mp3 = tmp_path / "sample.mp3"
    r1 = subprocess.run(
        [
            ff, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=0.5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(mp4),
        ],
        capture_output=True,
    )
    r2 = subprocess.run(
        [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=523:duration=0.4", str(mp3)],
        capture_output=True,
    )
    if r1.returncode != 0 or r2.returncode != 0:
        return

    app = build_demo_app(start="media")
    app._pixel_size = (960, 600)
    scr = app._ensure_screen()
    scr.layout(Rect(0, 0, 960, 600))

    scr.load_media_file(mp3)
    assert scr._audio_pcm is not None and len(scr._audio_pcm) > 1000
    assert scr._audio_playing is True

    scr.load_media_file(mp4)
    assert scr._file_streamer is not None
    try:
        import time

        aus = []
        for _ in range(30):
            aus.extend(scr._file_streamer.pop_aus())
            if aus:
                break
            time.sleep(0.03)
        assert len(aus) >= 1
    finally:
        scr._file_streamer.close()


async def test_remote_camera_and_mic_control_routing(tmp_path: Path):
    from textual_wgpu.demo import build_demo_app
    from textual_wgpu.remote.protocol import encode_audio_pcm, encode_vid_nalu

    s2c_r, s2c_w, c2s_r, c2s_w = _make_pipe_pair()
    server_tx = AsyncStreamTransport(c2s_r, s2c_w)
    client_tx = AsyncStreamTransport(s2c_r, c2s_w)
    app = build_demo_app(start="media")
    drv = RemoteServerDriver(app, server_tx, size=(960, 600))
    viewer = RemoteViewer(client_tx, size=(960, 600), cache_dir=tmp_path / "cam_cache")

    cam_cmds: list[int] = []
    mic_cmds: list[bool] = []
    viewer._handle_cam_ctl = lambda mode, cam_id="": cam_cmds.append(mode)  # type: ignore[method-assign]
    viewer._handle_mic_ctl = lambda en: mic_cmds.append(en)  # type: ignore[method-assign]

    await drv.start()
    try:
        while True:
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            if viewer.handle_frame(f):
                break

        scr = app.screen
        scr._start_camera_source("peer_back")
        for _ in range(15):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if cam_cmds:
                break
        assert cam_cmds == [2]

        # Simulate peer camera NALU arriving back at server -> server forwards it through Video plane
        await viewer.transport.send_now(
            Frame(
                op=Op.VID_NALU,
                payload=encode_vid_nalu(1234, 0x11, (), b"\x00\x00\x00\x01\x65CAM_IDR"),
                prio=Priority.P2_MEDIA,
                surf_id=1,
            ),
            compress=False,
        )
        got_cam_nalu = False
        for _ in range(30):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            if f.op == Op.VID_NALU:
                from textual_wgpu.remote.protocol import decode_vid_nalu

                _pts, _fl, _mat, raw = decode_vid_nalu(f.payload)
                if raw == b"\x00\x00\x00\x01\x65CAM_IDR":
                    got_cam_nalu = True
                    break
            viewer.handle_frame(f)
        assert got_cam_nalu is True

        scr._start_mic_source("peer")
        for _ in range(15):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            if mic_cmds:
                break
        assert mic_cmds == [True]

        await viewer.transport.send_now(
            Frame(
                op=Op.AUDIO_PCM,
                payload=encode_audio_pcm(16000, 1, b"\x00\x20" * 400),
                prio=Priority.P1_UI,
            ),
            compress=True,
        )
        for _ in range(20):
            if len(scr._mic_buf) >= 800:
                break
            await asyncio.sleep(0.02)
        assert len(scr._mic_buf) >= 800
        scr._stop_camera()
        scr._stop_mic()
    finally:
        await drv.stop()
        viewer.transport.close()


def test_camera_source_discovery_adapts_to_host_os(monkeypatch):
    from textual_wgpu.demo import build_demo_app
    from textual_wgpu.geometry import Rect
    from textual_wgpu.remote.directory import DeviceProfile, local_device_profile
    from textual_wgpu.remote.media_codec import list_linux_cameras

    cams = list_linux_cameras()
    assert isinstance(cams, list)
    for dev, name in cams:
        assert dev.startswith("/dev/video") and name

    app = build_demo_app(start="media")
    app._pixel_size = (960, 600)
    scr = app._ensure_screen()
    scr.layout(Rect(0, 0, 960, 600))

    # Simulate Android host with a distinct Workstation peer -> uses real names, no duplicate self
    monkeypatch.setenv("ANDROID_ROOT", "/system")

    class _FakeDrv:
        def __init__(self, peer):
            self.peer_profile = peer

        def distinct_peer_profile(self, local):
            if self.peer_profile.device_id == local.device_id:
                return None
            return self.peer_profile

        def on_request_peer_camera(self, *_a, **_kw):
            pass

    ws_peer = DeviceProfile(
        device_id="ws-1",
        name="vpavlov-workstation",
        kind="desktop",
        cameras=[("/dev/video0", "HD Webcam")],
    )
    app._driver = _FakeDrv(ws_peer)
    scr.toggle_camera()
    assert app._pick is not None
    keys = [o[0] for o in app._pick["options"]]
    labels = [o[1] for o in app._pick["options"]]
    assert "local:front" in keys and "local:back" in keys and "peer:/dev/video0" in keys
    assert any("vpavlov-workstation" in lbl for lbl in labels)
    app._pick = None

    # Same device_id as peer (e.g. Phone viewing LocalPythonHost) -> omits duplicate peer!
    local_prof = local_device_profile()
    app._driver = _FakeDrv(local_prof)
    scr.toggle_camera()
    assert app._pick is not None
    assert not any(o[0].startswith("peer:") for o in app._pick["options"])
    app._pick = None

    # Verify rotation flag encoding and clean pause cleanup
    vid = scr.query_one("#media-video", Video)
    vid.push_packet(0, b"\x00\x00\x00\x01\x65K", keyframe=True, codec_flag=0x10, rotation=3)
    pkts = vid.drain_packets()
    assert pkts[0].rotation == 3
    scr._cam_mode = "peer"
    scr.on_screen_pause()
    assert scr._cam_mode is None and scr._mic_mode is None
