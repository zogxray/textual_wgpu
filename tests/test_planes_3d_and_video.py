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
        for _ in range(60):
            f = await asyncio.wait_for(viewer.transport.recv(), timeout=2.0)
            assert f is not None
            viewer.handle_frame(f)
            await asyncio.sleep(0.005)
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


def test_media_hub_fanout_and_two_simultaneous_camera_subscribers(tmp_path: Path):
    import socket
    import time
    from textual_wgpu.remote.media_codec import LocalCameraStreamer, list_linux_cameras
    from textual_wgpu.remote.media_hub import HUB_PKT_STRUCT, _BroadcastHub

    # 1) Unit test _BroadcastHub late-joiner keyframe cache & multi-subscriber fanout
    sock_p = tmp_path / "hub.sock"
    hub = _BroadcastHub(sock_p, idle_timeout=0.5, startup_grace=2.0)
    hub.start_accepting()
    try:
        sps_idr = b"\x00\x00\x00\x01\x67\x42\x00\x1e\x00\x00\x00\x01\x65KF1"
        hub.publish(100, 0x11, sps_idr, is_keyframe=True, codec_flag=0x10)

        c1 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c1.connect(str(sock_p))
        c2.connect(str(sock_p))
        c1.settimeout(1.0)
        c2.settimeout(1.0)
        try:
            for c in (c1, c2):
                hdr = c.recv(HUB_PKT_STRUCT.size)
                pts, fl, plen = HUB_PKT_STRUCT.unpack(hdr)
                assert pts == 100 and (fl & 0x01) == 1
                assert c.recv(plen) == sps_idr

            hub.publish(200, 0x10, b"\x00\x00\x00\x01\x41P2", is_keyframe=False, codec_flag=0x10)
            for c in (c1, c2):
                hdr = c.recv(HUB_PKT_STRUCT.size)
                pts, _fl, plen = HUB_PKT_STRUCT.unpack(hdr)
                assert pts == 200 and c.recv(plen) == b"\x00\x00\x00\x01\x41P2"
        finally:
            c1.close()
            c2.close()
    finally:
        hub.close()

    # 2) If a real V4L2 camera is free on this host, verify TWO LocalCameraStreamers share it simultaneously!
    cams = list_linux_cameras()
    if cams:
        import shutil
        import subprocess

        dev = cams[0][0]
        busy = False
        if shutil.which("fuser"):
            busy = subprocess.run(["fuser", dev], capture_output=True).returncode == 0
        if not busy:
            s1 = LocalCameraStreamer(device=dev)
            s2 = LocalCameraStreamer(device=dev)
            try:
                got1 = []
                got2 = []
                for _ in range(40):
                    got1.extend(s1.pop_aus())
                    got2.extend(s2.pop_aus())
                    if got1 and got2:
                        break
                    time.sleep(0.05)
                assert len(got1) >= 1 and len(got2) >= 1
            finally:
                s1.close()
                s2.close()


def test_independent_resource_servers_and_dual_app_camera_isolation(tmp_path: Path):
    import socket
    import time
    from textual_wgpu.demo import build_demo_app
    from textual_wgpu.remote.directory import DeviceProfile, discover_all_resources
    from textual_wgpu.remote.media_hub import (
        HUB_PKT_STRUCT,
        RemoteCameraSubscriber,
        RemoteMicSubscriber,
        ResourceServer,
        _BroadcastHub,
        resource_fs_get,
        resource_fs_list,
    )

    # Create two independent camera hubs (e.g. Phone Front & Workstation Webcam) + one mic hub
    ws_cam_sock = tmp_path / "ws_cam.sock"
    ph_front_sock = tmp_path / "ph_front.sock"
    ph_back_sock = tmp_path / "ph_back.sock"
    ph_mic_sock = tmp_path / "ph_mic.sock"
    hub_ws = _BroadcastHub(ws_cam_sock, idle_timeout=5.0, startup_grace=5.0)
    hub_pf = _BroadcastHub(ph_front_sock, idle_timeout=5.0, startup_grace=5.0)
    hub_pb = _BroadcastHub(ph_back_sock, idle_timeout=5.0, startup_grace=5.0)
    hub_pm = _BroadcastHub(ph_mic_sock, idle_timeout=5.0, startup_grace=5.0)
    for h in (hub_ws, hub_pf, hub_pb, hub_pm):
        h.start_accepting()

    ws_files = tmp_path / "ws_files"
    ph_files = tmp_path / "ph_files"
    ws_files.mkdir()
    ph_files.mkdir()
    (ph_files / "phone_clip.bin").write_bytes(b"PHONE-MEDIA-BYTES")

    def _connect_unix(p: Path) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(p))
        return s

    ws_prof = DeviceProfile("ws-dev-1", "Workstation", "desktop", [("/dev/video0", "HD Webcam")])
    ph_prof = DeviceProfile("ph-dev-2", "Moto Phone", "android", [("front", "Front Camera"), ("back", "Back Camera")])

    srv_ws = ResourceServer(
        port=0,
        profile_fn=lambda: ws_prof,
        camera_connector=lambda _cid: _connect_unix(ws_cam_sock),
        mic_connector=lambda: _connect_unix(ph_mic_sock),
        fs_root=ws_files,
    )
    srv_ph = ResourceServer(
        port=0,
        profile_fn=lambda: ph_prof,
        camera_connector=lambda cid: _connect_unix(ph_back_sock if cid == "back" else ph_front_sock),
        mic_connector=lambda: _connect_unix(ph_mic_sock),
        fs_root=ph_files,
    )
    p_ws = srv_ws.start()
    p_ph = srv_ph.start()

    try:
        # 1) Unified cross-device resource discovery lists ALL independent cameras, mics, and files
        all_res = discover_all_resources(ports=(p_ws, p_ph), include_local_fallback=False)
        cams = [r for r in all_res if r.kind == "camera"]
        assert [(r.device_id, r.resource_id) for r in cams] == [
            ("ws-dev-1", "/dev/video0"),
            ("ph-dev-2", "front"),
            ("ph-dev-2", "back"),
        ]
        assert len([r for r in all_res if r.kind == "mic"]) == 2
        assert len([r for r in all_res if r.kind == "files"]) == 2

        # 2) File listing & transfer from Phone ResourceServer directly
        roots = resource_fs_list("127.0.0.1", p_ph, "")
        assert roots and roots[0][1] == "ph_files"
        entries = resource_fs_list("127.0.0.1", p_ph, str(ph_files))
        assert any(e[1] == "phone_clip.bin" for e in entries)
        got = resource_fs_get("127.0.0.1", p_ph, str(ph_files / "phone_clip.bin"))
        assert got == ("phone_clip.bin", b"PHONE-MEDIA-BYTES")

        # 3) Independent dual-device previews: Android app views Phone Front, Workstation app switches
        #    between Phone Front, Phone Back, and Workstation Webcam without affecting Android app!
        ph_front_au = b"\x00\x00\x00\x01\x67\x42\x00\x1e\x00\x00\x00\x01\x65PH_FRONT"
        ph_back_au = b"\x00\x00\x00\x01\x67\x42\x00\x1e\x00\x00\x00\x01\x65PH_BACK"
        ws_au = b"\x00\x00\x00\x01\x67\x42\x00\x1e\x00\x00\x00\x01\x65WS_CAM"
        hub_pf.publish(10, 0x11, ph_front_au, is_keyframe=True, codec_flag=0x10)
        hub_pb.publish(10, 0x11, ph_back_au, is_keyframe=True, codec_flag=0x10)
        hub_ws.publish(10, 0x11, ws_au, is_keyframe=True, codec_flag=0x10)

        android_app = build_demo_app(start="media")
        ws_app = build_demo_app(start="media")
        android_scr = android_app._ensure_screen()
        ws_scr = ws_app._ensure_screen()

        # Both apps receive remote-style NALU streams independently
        class _RemoteTag:
            def on_audio_play(self, *_a, **_kw):
                pass

        android_app._driver = _RemoteTag()
        ws_app._driver = _RemoteTag()

        ph_front_res = next(r for r in cams if r.device_id == "ph-dev-2" and r.resource_id == "front")
        ph_back_res = next(r for r in cams if r.device_id == "ph-dev-2" and r.resource_id == "back")
        ws_cam_res = next(r for r in cams if r.device_id == "ws-dev-1")

        try:
            # Android app selects Phone Front camera
            android_scr.start_camera_resource(ph_front_res)
            # Workstation app ALSO selects Phone Front, then switches to Phone Back, then Workstation cam
            ws_scr.start_camera_resource(ph_front_res)
            ws_scr.start_camera_resource(ph_back_res)
            ws_scr.start_camera_resource(ws_cam_res)

            # Android app MUST still be subscribed to Phone Front and receiving PH_FRONT frames!
            hub_pf.publish(20, 0x11, ph_front_au, is_keyframe=True, codec_flag=0x10)
            hub_ws.publish(20, 0x11, ws_au, is_keyframe=True, codec_flag=0x10)

            got_android = []
            got_ws = []
            for _ in range(30):
                if android_scr._file_streamer is not None:
                    got_android.extend(android_scr._file_streamer.pop_aus())
                if ws_scr._file_streamer is not None:
                    got_ws.extend(ws_scr._file_streamer.pop_aus())
                if got_android and got_ws:
                    break
                time.sleep(0.02)

            assert got_android and all(b"PH_FRONT" in au for au, _kf in got_android)
            assert got_ws and all(b"WS_CAM" in au for au, _kf in got_ws)

            # Stopping Workstation's camera leaves Android's camera preview completely untouched
            ws_scr._stop_camera()
            assert android_scr._cam_mode == "resource"
            hub_pf.publish(30, 0x10, b"\x00\x00\x00\x01\x41PH_NEXT", is_keyframe=False, codec_flag=0x10)
            got_after = []
            for _ in range(25):
                got_after.extend(android_scr._file_streamer.pop_aus())
                if got_after:
                    break
                time.sleep(0.02)
            assert any(b"PH_NEXT" in au for au, _kf in got_after)
        finally:
            android_scr.on_screen_pause()
            ws_scr.on_screen_pause()

        # 4) Verify RemoteMicSubscriber streams independently
        mic_sub = RemoteMicSubscriber("127.0.0.1", p_ph, "default")
        try:
            time.sleep(0.05)
            hub_pm.publish(10, 1, b"\x01\x02" * 160)
            chunks = []
            for _ in range(25):
                chunks.extend(mic_sub.pop_chunks())
                if chunks:
                    break
                time.sleep(0.02)
            assert chunks and chunks[0] == b"\x01\x02" * 160
        finally:
            mic_sub.close()

        # 5) Verify F8 NodesScreen renders System Resources panel (#resources-list)
        nodes_app = build_demo_app(start="nodes")
        nodes_scr = nodes_app._ensure_screen()
        assert nodes_scr.query_one("#resources-list") is not None
    finally:
        srv_ws.close()
        srv_ph.close()
        for h in (hub_ws, hub_pf, hub_pb, hub_pm):
            h.close()


def test_meta_os_speaker_clipboard_ai_sessions_and_composer(tmp_path: Path):
    import base64
    import io
    import socket
    import threading
    import time
    from PIL import Image as PilImage

    from textual_wgpu.demo import build_demo_app
    from textual_wgpu.remote.ai_resource import AiProviderInfo, synthesize_speech_pcm
    from textual_wgpu.remote.directory import DeviceProfile, discover_all_resources
    from textual_wgpu.remote.media_hub import (
        RemoteSpeakerSink,
        ResourceServer,
        _BroadcastHub,
        resource_ai_infer,
        resource_clip_get,
        resource_clip_set,
        resource_list_sessions,
        resource_spawn_session,
        resource_stop_session,
    )
    from textual_wgpu.remote.session_broker import SessionBroker, start_session_tcp_relay
    from textual_wgpu.widgets import Checkbox, Input, Video

    # Phase 1: Speaker sink + Clipboard across two devices
    ws_pcm_log: list[bytes] = []

    class _FakeSink:
        def write(self, data: bytes) -> None:
            ws_pcm_log.append(data)

        def close(self) -> None:
            pass

    # Phase 3: SessionBroker spawning an echo TCP server on its assigned port
    def _spawn_echo(target: str, args: str, port: int):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(4)
        srv.settimeout(0.2)
        stop_flag = [False]

        def _loop():
            while not stop_flag[0]:
                try:
                    c, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                data = c.recv(1024)
                c.sendall(b"ECHO:" + data)
                c.close()

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

        class _H:
            pid = 4242

            def is_alive(self) -> bool:
                return not stop_flag[0]

            def close(self) -> None:
                stop_flag[0] = True
                srv.close()

        return _H()

    broker = SessionBroker(spawner=_spawn_echo)

    cam_sock = tmp_path / "cam.sock"
    hub_cam = _BroadcastHub(cam_sock, idle_timeout=5.0, startup_grace=5.0)
    hub_cam.start_accepting()
    hub_cam.publish(10, 0x11, b"\x00\x00\x00\x01\x65CAM1", is_keyframe=True, codec_flag=0x10)

    def _conn_cam(_cid: str) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(cam_sock))
        return s

    ai_prov = AiProviderInfo("ollama:demo-vision", "Local Vision LLM", "builtin", "local://test", "demo-vision")
    srv_ws = ResourceServer(
        port=0,
        profile_fn=lambda: DeviceProfile("ws-1", "Workstation", "desktop", [("/dev/video0", "Webcam")]),
        camera_connector=_conn_cam,
        speaker_sink_factory=lambda _sr, _ch: _FakeSink(),
        ai_providers_fn=lambda: [ai_prov],
        session_broker=broker,
    )
    srv_ph = ResourceServer(
        port=0,
        profile_fn=lambda: DeviceProfile("ph-2", "Phone", "android", [("front", "Front Cam")]),
        camera_connector=_conn_cam,
        ai_providers_fn=lambda: [],
    )
    p_ws = srv_ws.start()
    p_ph = srv_ph.start()

    try:
        # 1) Discovery includes speaker, clipboard, ai, gpu, camera, mic, files
        res_all = discover_all_resources(ports=(p_ws, p_ph), include_local_fallback=False)
        assert any(r.kind == "speaker" and r.device_id == "ws-1" for r in res_all)
        assert any(r.kind == "clipboard" and r.device_id == "ph-2" for r in res_all)
        assert any(r.kind == "ai" and r.resource_id == "ollama:demo-vision" for r in res_all)
        assert any(r.kind == "gpu" and r.device_id == "ws-1" for r in res_all)

        # 2) Speaker stream & Clipboard roundtrip
        sink = RemoteSpeakerSink("127.0.0.1", p_ws, sample_rate=16000, channels=1)
        sink.write_pcm(b"\x10\x20" * 160)
        sink.close()
        for _ in range(20):
            if ws_pcm_log:
                break
            time.sleep(0.02)
        assert ws_pcm_log and ws_pcm_log[0] == b"\x10\x20" * 160

        assert resource_clip_set("127.0.0.1", p_ph, "Hello from Workstation")
        assert resource_clip_get("127.0.0.1", p_ph) == "Hello from Workstation"

        # 3) AI inference with attached PNG snapshot + TTS synth
        im = PilImage.new("RGBA", (32, 32), (240, 180, 60, 255))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        ans = resource_ai_infer("127.0.0.1", p_ws, "ollama:demo-vision", "Inspect scene", context="clip=test", image_b64=img_b64)
        assert "32x32" in ans and "Inspect scene" in ans
        tts_pcm = synthesize_speech_pcm("Ready", sample_rate=16000)
        assert len(tts_pcm) >= 640

        # 4) SessionBroker spawn + list + tunnel_tcp relay + stop
        sinfo = resource_spawn_session("127.0.0.1", p_ws, "demo:media")
        assert sinfo is not None and sinfo["port"] >= 7430
        listed = resource_list_sessions("127.0.0.1", p_ws)
        assert len(listed) == 1 and listed[0]["session_id"] == sinfo["session_id"]

        relay_port, close_relay = start_session_tcp_relay("127.0.0.1", p_ws, int(sinfo["port"]))
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(2.0)
            c.connect(("127.0.0.1", relay_port))
            c.sendall(b"PING")
            assert c.recv(64) == b"ECHO:PING"
            c.close()
        finally:
            close_relay()
        assert resource_stop_session("127.0.0.1", p_ws, sinfo["session_id"])
        assert resource_list_sessions("127.0.0.1", p_ws) == []

        # 5) F7 Media dual-camera slots + F9 Composer Eyes -> Knowledge -> Hands
        media_app = build_demo_app(start="media")
        mscr = media_app._ensure_screen()

        class _RemoteTag:
            def on_audio_play(self, *_a, **_kw):
                pass

        media_app._driver = _RemoteTag()
        ph_cam = next(r for r in res_all if r.kind == "camera" and r.device_id == "ph-2")
        ws_cam = next(r for r in res_all if r.kind == "camera" and r.device_id == "ws-1")
        mscr.start_camera_resource(ph_cam)
        mscr._cam2_resources = {f"res:{ws_cam.uid}": ws_cam}
        mscr._start_camera2_source(f"res:{ws_cam.uid}")
        try:
            assert mscr._cam_mode == "resource" and mscr._cam2_mode == "resource"
            assert mscr.query_one("#media-video-2", Video).styles["display"] == "block"
        finally:
            mscr.on_screen_pause()

        # F9 Composer: Clipboard Eyes (Phone) -> AI Brain (Workstation) -> Speaker + Clipboard + UI Hands!
        comp_app = build_demo_app(start="composer")
        cscr = comp_app._ensure_screen()
        ws_spk = next(r for r in res_all if r.kind == "speaker" and r.device_id == "ws-1")
        ph_clp = next(r for r in res_all if r.kind == "clipboard" and r.device_id == "ph-2")
        ws_ai = next(r for r in res_all if r.kind == "ai" and r.device_id == "ws-1")
        cscr._eyes = [ph_clp]
        cscr._brains = [ws_ai]
        cscr._speakers = [ws_spk]
        cscr._clipboards = [ph_clp]
        cscr._spk_idx = 0
        cscr._clip_idx = 0
        assert cscr.query_one("#comp-eye-preview") is not None
        cscr._on_eye_file_bytes("photo.png", buf.getvalue())
        assert cscr._last_image_b64 != ""
        ws_pcm_log.clear()
        cscr.query_one("#chk-hand-speak", Checkbox).value = True
        cscr.query_one("#chk-hand-clip", Checkbox).value = True
        cscr.query_one("#chk-hand-ui", Checkbox).value = True
        cscr.query_one("#comp-prompt", Input).value = "Switch to media after reading clipboard"
        cscr.run_pipeline()
        for _ in range(25):
            if ws_pcm_log:
                break
            time.sleep(0.02)
        assert ws_pcm_log  # Spoken onto Workstation speaker!
        assert "Switch to media" in resource_clip_get("127.0.0.1", p_ph)  # Written to Phone clipboard!
        assert comp_app.mode_key == "media"  # UI Hands switched mode!

        # F8 NodesScreen routes speaker, clipboard, and gpu resources cleanly
        from textual_wgpu.widgets import OptionList

        comp_app.switch_mode("nodes")
        nscr = comp_app.screen
        nscr._resources = [ws_spk, ph_clp]
        nscr.query_one("#resources-list", OptionList).highlighted = 0
        ws_pcm_log.clear()
        nscr.use_selected_resource()
        assert comp_app.mode_key == "media"
        for _ in range(25):
            if ws_pcm_log:
                break
            time.sleep(0.02)
        assert ws_pcm_log

        comp_app.switch_mode("nodes")
        nscr._resources = [ws_spk, ph_clp]
        nscr.query_one("#resources-list", OptionList).highlighted = 1
        nscr.use_selected_resource()
        assert comp_app.mode_key == "composer"

        # Ubuntu top-bar status panel snapshot & icon helper
        from textual_wgpu.remote.tray import collect_status_snapshot, ensure_tray_icon

        icon_p = ensure_tray_icon()
        assert icon_p.is_file() and "<svg" in icon_p.read_text(encoding="utf-8")
        snap = collect_status_snapshot(ports=(p_ws, p_ph))
        assert len(snap.devices) == 2
        assert "peer" in snap.label_text() and "cam" in snap.label_text()
        assert snap.fingerprint() == collect_status_snapshot(ports=(p_ws, p_ph)).fingerprint()
    finally:
        broker.close_all()
        srv_ws.close()
        srv_ph.close()
        hub_cam.close()
