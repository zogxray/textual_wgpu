"""Headless remote server driver emitting retained binary layer diffs."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import fonts as tw_fonts
from ..driver import Driver
from ..geometry import Rect
from ..widget import Widget
from .protocol import (
    PLANE_KIND_3D_CLIENT,
    PLANE_KIND_3D_SERVER,
    PLANE_KIND_VIDEO,
    POINTER_DOWN,
    POINTER_MOVE,
    POINTER_UP,
    Frame,
    Op,
    Priority,
    decode_audio_pcm,
    decode_caps,
    decode_client_file,
    decode_fs_entries,
    decode_input_pointer,
    decode_input_wheel,
    decode_launch_app,
    decode_resize,
    decode_vid_nalu,
    encode_app_list,
    encode_audio_pcm,
    encode_buf_write,
    encode_frame_commit,
    encode_hello,
    encode_launch_status,
    encode_layer_patch,
    encode_plane_rect,
    encode_put_blob,
    encode_style_defs,
    encode_vid_nalu,
    pack_rgba,
)
from .recorder import DrawRecorder
from .transport import AsyncStreamTransport

if TYPE_CHECKING:
    from ..app import App


def _is_layout_wrapper(widget: Any) -> bool:
    """True for pure layout groups whose children should be tracked as individual layers."""
    from ..widgets.containers import (
        Center,
        CenterMiddle,
        Container,
        Grid,
        Horizontal,
        HorizontalGroup,
        ItemGrid,
        Middle,
        Right,
        Vertical,
        VerticalGroup,
    )

    if not isinstance(
        widget,
        (
            Container,
            Horizontal,
            HorizontalGroup,
            Vertical,
            VerticalGroup,
            Grid,
            ItemGrid,
            Center,
            Middle,
            CenterMiddle,
            Right,
        ),
    ):
        return False
    # If the wrapper itself has custom paint or explicit border/bg, keep it as its own layer.
    styles = getattr(widget, "styles", None) or {}
    if styles.get("border") or styles.get("background"):
        return False
    return type(widget).paint is Widget.paint or type(widget).paint.__qualname__.startswith("Container.")


def rlog(tag: str, msg: str) -> None:
    """Write lightweight debug/performance log to ~/.cache/textual-wgpu/remote.log and stderr."""
    line = f"[{time.strftime('%H:%M:%S')}] [{tag}] {msg}"
    if os.environ.get("TEXTUAL_WGPU_DEBUG", "").strip().lower() in ("1", "true", "yes"):
        print(line, file=sys.stderr, flush=True)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        lp = Path.home() / ".cache" / "textual-wgpu" / "remote.log"
        lp.parent.mkdir(parents=True, exist_ok=True)
        if lp.exists() and lp.stat().st_size > 512 * 1024:
            lp.write_text("", encoding="utf-8")
        with lp.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class RemoteServerDriver(Driver):
    """Run an ``App`` / ``ModeApp`` headlessly and stream retained layers over a binary pipe."""

    def __init__(
        self,
        app: App,
        transport: AsyncStreamTransport,
        *,
        size: tuple[int, int] = (1100, 720),
        detach_on_eof: bool = False,
    ) -> None:
        super().__init__(app)
        self.transport = transport
        self.size = size
        self.detach_on_eof = bool(detach_on_eof)
        self.recorder = DrawRecorder()
        self.remote_clipboard: str = ""
        self._draw_pending = False
        self._rx_task: asyncio.Task | None = None
        self._draw_task: asyncio.Task | None = None
        self._draw_event = asyncio.Event()
        self._last_title: str | None = None
        from ..widgets.viewport3d import ClientCapabilities

        self.client_caps = ClientCapabilities()
        self._next_surf_id = 1
        self._surf_ids: dict[int, int] = {}
        self._last_plane_rect: dict[int, tuple[tuple[int, int, int, int], int]] = {}
        self._vid_encoders: dict[int, Any] = {}
        self._client_file_cb: Any = None
        self._fs_list_cb: Any = None
        self._fs_req_t0: float = 0.0
        self._peer_cam_cb: Any = None
        self._peer_mic_cb: Any = None
        self._frame_counter: int = 0
        self._last_order: list[str] | None = None
        self._last_bg: Any = None
        self.prefer_hw_video = True
        self.apps_path: Any = None

    async def start(self) -> None:
        app = self.app
        app._running = True
        app._driver = self
        if hasattr(app, "_attach_loop"):
            app._attach_loop()
        if hasattr(app, "_pixel_size"):
            app._pixel_size = self.size  # type: ignore[attr-defined]
        if hasattr(app, "_booting"):
            app._booting = False  # type: ignore[attr-defined]

        res = app.on_mount()
        if inspect.isawaitable(res):
            await res
        if hasattr(app, "_ensure_screen"):
            app._booting = False  # type: ignore[attr-defined]
            app._ensure_screen()  # type: ignore[attr-defined]
        elif hasattr(app, "after_first_draw"):
            app.after_first_draw()
        if hasattr(app, "on_resize"):
            app.on_resize(*self.size)

        from ..events import Ready
        from ..widget import dispatch_event

        dispatch_event(app, Ready(), "on_ready")
        if app.screen is not None:
            dispatch_event(app.screen, Ready(), "on_ready")

        self._started = True
        await self._send_hello()
        await self.emit_frame()

        self._rx_task = asyncio.create_task(self._rx_loop())
        self._draw_task = asyncio.create_task(self._draw_loop())

    async def stop(self) -> None:
        self.app._running = False
        self._started = False
        for task in (self._rx_task, self._draw_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for enc in self._vid_encoders.values():
            try:
                enc.close()
            except Exception:  # noqa: BLE001
                pass
        self._vid_encoders.clear()
        self.transport.close()

    def on_request_draw(self) -> None:
        self._draw_pending = True
        self._draw_event.set()

    def _wake_from_thread(self) -> None:
        loop = getattr(self.app, "_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self.on_request_draw)

    def on_clipboard_set(self, text: str) -> bool:
        self.remote_clipboard = text
        self.transport.send(
            Frame(
                op=Op.CLIP_SET,
                payload=text.encode("utf-8"),
                prio=Priority.P0_CONTROL,
            ),
        )
        self._draw_event.set()
        return True

    def on_audio_play(self, pcm: bytes, *, sample_rate: int = 22050, channels: int = 1) -> None:
        self.transport.send(
            Frame(
                op=Op.AUDIO_PCM,
                payload=encode_audio_pcm(sample_rate, channels, pcm, flags=1),
                prio=Priority.P1_UI,
            ),
            compress=True,
        )
        self._draw_event.set()

    def on_audio_stop(self) -> None:
        self.transport.send(
            Frame(op=Op.AUDIO_STOP, payload=b"", prio=Priority.P0_CONTROL),
            compress=False,
        )
        self._draw_event.set()

    def on_request_client_file(self, callback: Any, *, accept: str = "*/*") -> None:
        self._client_file_cb = callback
        self.transport.send(
            Frame(
                op=Op.OPEN_CLIENT_FILE,
                payload=accept.encode("utf-8"),
                prio=Priority.P0_CONTROL,
            ),
        )
        self._draw_event.set()

    def on_request_peer_fs_list(self, path: str, callback: Any) -> None:
        self._fs_list_cb = callback
        self._fs_req_t0 = time.monotonic()
        rlog("FS", f"FS_LIST -> peer path={path!r}")
        self.transport.send(
            Frame(
                op=Op.FS_LIST,
                payload=path.encode("utf-8"),
                prio=Priority.P0_CONTROL,
            ),
        )
        self._draw_event.set()

    def on_request_peer_fs_get(self, path: str, callback: Any) -> None:
        self._client_file_cb = callback
        self._fs_req_t0 = time.monotonic()
        rlog("FS", f"FS_GET -> peer path={path!r}")
        self.transport.send(
            Frame(
                op=Op.FS_GET,
                payload=path.encode("utf-8"),
                prio=Priority.P0_CONTROL,
            ),
        )
        self._draw_event.set()

    def on_request_peer_camera(self, mode: int, callback: Any = None) -> None:
        self._peer_cam_cb = callback if mode else None
        rlog("CAM", f"CAM_CTL -> peer mode={mode}")
        self.transport.send(
            Frame(op=Op.CAM_CTL, payload=bytes([int(mode) & 0xFF]), prio=Priority.P0_CONTROL),
            compress=False,
        )
        self._draw_event.set()

    def on_request_peer_mic(self, enabled: bool, callback: Any = None) -> None:
        self._peer_mic_cb = callback if enabled else None
        rlog("MIC", f"MIC_CTL -> peer enabled={enabled}")
        self.transport.send(
            Frame(op=Op.MIC_CTL, payload=b"\x01" if enabled else b"\x00", prio=Priority.P0_CONTROL),
            compress=False,
        )
        self._draw_event.set()

    async def _send_hello(self) -> None:
        import hashlib
        from pathlib import Path

        title = str(getattr(self.app, "TITLE", "textual-wgpu") or "textual-wgpu")
        self._last_title = title
        font_paths = tw_fonts.active_font_paths()
        mono_hash = b"\x00" * 16
        font_blobs: list[tuple[bytes, bytes]] = []
        for kind in (tw_fonts.FONT_MONO, tw_fonts.FONT_SANS):
            p_str = font_paths.get(kind)
            if not p_str:
                continue
            try:
                raw = Path(p_str).read_bytes()
            except OSError:
                continue
            h16 = hashlib.blake2b(raw, digest_size=16).digest()
            if kind == tw_fonts.FONT_MONO:
                mono_hash = h16
            font_blobs.append((h16, raw))

        hello = encode_hello(
            width_scale=float(getattr(tw_fonts, "_WIDTH_SCALE", 1.0)),
            mono_font_hash=mono_hash,
            title=title,
        )
        await self.transport.send_now(
            Frame(op=Op.HELLO, payload=hello, prio=Priority.P0_CONTROL),
            compress=False,
        )
        for h16, raw in font_blobs:
            if h16 not in self.recorder.known_blobs:
                self.recorder.known_blobs.add(h16)
                # w=0, h=0 marks font/asset blob (non-PNG)
                await self.transport.send_now(
                    Frame(op=Op.PUT_BLOB, payload=encode_put_blob(h16, 0, 0, raw), prio=Priority.P1_UI),
                    compress=True,
                )

    def _paint_widget_layers(self, node: Any, prefix: str) -> None:
        if (getattr(node, "styles", None) or {}).get("display") == "none":
            return
        if getattr(node, "layout_hint_h", None) == 0:
            return
        if _is_layout_wrapper(node):
            for idx, child in enumerate(getattr(node, "children", ()) or ()):
                cid = getattr(child, "id", None) or f"{prefix}.{idx}"
                self._paint_widget_layers(child, str(cid))
            return
        r = getattr(node, "rect", None)
        rect_tuple = (r.x, r.y, r.x1, r.y1) if r is not None else (0, 0, 0, 0)
        lid = f"{prefix}:{getattr(node, 'id', None) or id(node)}"
        self.recorder.begin_layer(lid, rect_tuple)
        node.paint(self.recorder)
        self.recorder.end_layer()

    def record_frame(self) -> dict[str, Any]:
        """Layout (if needed) and record dirty layers for the current frame."""
        app = self.app
        w, h = getattr(app, "_pixel_size", None) or self.size
        if w <= 0 or h <= 0:
            w, h = self.size
        root = app._ensure_screen() if hasattr(app, "_ensure_screen") else app.screen
        if root is None:
            return {"order": [], "dirty": [], "dropped": [], "styles": [], "blobs": [], "bg": (20, 20, 24, 255)}

        if getattr(app, "_layout_size", (0, 0)) != (w, h):
            root.layout(Rect(0, 0, w, h))
            for entry in getattr(app, "_screen_stack", ()) or ():
                try:
                    entry.screen.layout(Rect(0, 0, w, h))
                except Exception:  # noqa: BLE001
                    pass
            if hasattr(app, "_layout_size"):
                app._layout_size = (w, h)  # type: ignore[attr-defined]
        else:
            for entry in getattr(app, "_screen_stack", ()) or ():
                if entry.screen.rect.w != w or entry.screen.rect.h != h:
                    try:
                        entry.screen.layout(Rect(0, 0, w, h))
                    except Exception:  # noqa: BLE001
                        pass

        self.recorder.begin_frame()
        if callable(getattr(root, "apply_styles", None)):
            root.apply_styles()
        for idx, child in enumerate(getattr(root, "children", ()) or ()):
            cid = getattr(child, "id", None) or f"c{idx}"
            self._paint_widget_layers(child, str(cid))

        if hasattr(root, "hits"):
            try:
                app._hits = list(root.hits())  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

        for sidx, entry in enumerate(getattr(app, "_screen_stack", ()) or ()):
            scr = entry.screen
            if scr.rect.w <= 0 or scr.rect.h <= 0:
                scr.layout(Rect(0, 0, w, h))
            self.recorder.begin_layer(f"stack:{sidx}", (0, 0, w, h))
            scr.paint(self.recorder)
            self.recorder.end_layer()
            if hasattr(scr, "hits"):
                try:
                    app._hits = list(scr.hits())  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    app._hits = []  # type: ignore[attr-defined]

        toast = getattr(app, "_toast", None)
        if toast is not None and getattr(toast, "visible", False):
            toast.app = app
            self.recorder.begin_layer("overlay:toast", (0, 0, w, h))
            toast.paint_at(self.recorder, Rect(0, 0, w, h))
            self.recorder.end_layer()

        tooltip = getattr(app, "_tooltip", None)
        if tooltip is not None and getattr(tooltip, "visible", False):
            tooltip.app = app
            self.recorder.begin_layer("overlay:tooltip", (0, 0, w, h))
            tooltip.paint(self.recorder)
            self.recorder.end_layer()

        # ModeApp modal overlays
        modal_checks = (
            ("quit_modal", "_paint_quit_modal"),
            ("_confirm", "_paint_confirm"),
            ("_form", "_paint_form"),
            ("_open_file", "_paint_open_file"),
            ("_overlay", "_paint_overlay"),
            ("_prompt", "_paint_prompt"),
            ("_text", "_paint_text"),
            ("_pick", "_paint_pick"),
            ("_busy", "_paint_busy"),
        )
        for attr, painter in modal_checks:
            if getattr(app, attr, None):
                fn = getattr(app, painter, None)
                if callable(fn):
                    self.recorder.begin_layer(f"modal:{attr}", (0, 0, w, h))
                    fn(self.recorder, w, h)
                    self.recorder.end_layer()
                if attr == "_busy":
                    app._hits = []  # type: ignore[attr-defined]

        order, dirty, dropped, new_styles, new_blobs = self.recorder.finish_frame()
        bg = app.theme.rgba("background")
        return {
            "order": order,
            "dirty": dirty,
            "dropped": dropped,
            "styles": new_styles,
            "blobs": new_blobs,
            "bg": bg,
        }

    async def emit_frame(self) -> int:
        """Record current frame and send all pending deltas; return bytes queued."""
        t0 = time.monotonic()
        snap = self.record_frame()
        bytes_queued = 0

        title = str(getattr(self.app, "TITLE", "textual-wgpu") or "textual-wgpu")
        ui_changed = False
        if title != self._last_title:
            self._last_title = title
            f = Frame(op=Op.TITLE_SET, payload=title.encode("utf-8"), prio=Priority.P1_UI)
            self.transport.send(f)
            bytes_queued += len(f.payload)
            ui_changed = True

        if snap["styles"]:
            payload = encode_style_defs(snap["styles"])
            self.transport.send(Frame(op=Op.STYLE_DEF, payload=payload, prio=Priority.P1_UI))
            bytes_queued += len(payload)
            ui_changed = True

        for h16, bw, bh, blob in snap["blobs"]:
            payload = encode_put_blob(h16, bw, bh, blob)
            self.transport.send(
                Frame(op=Op.PUT_BLOB, payload=payload, prio=Priority.P1_UI),
                compress=False,
            )
            bytes_queued += len(payload)
            ui_changed = True

        for lid in snap["dropped"]:
            payload = lid.encode("utf-8")
            self.transport.send(Frame(op=Op.LAYER_DROP, payload=payload, prio=Priority.P1_UI))
            bytes_queued += len(payload)
            ui_changed = True

        for lid, rect, ops in snap["dirty"]:
            payload = encode_layer_patch(lid, rect, ops)
            self.transport.send(Frame(op=Op.LAYER_PATCH, payload=payload, prio=Priority.P1_UI))
            bytes_queued += len(payload)
            ui_changed = True

        plane_bytes = self._emit_planes()
        bytes_queued += plane_bytes

        if (
            ui_changed
            or snap["order"] != self._last_order
            or snap["bg"] != self._last_bg
            or plane_bytes == 0
        ):
            self._last_order = list(snap["order"])
            self._last_bg = snap["bg"]
            commit = encode_frame_commit(snap["order"], pack_rgba(snap["bg"]))
            self.transport.send(Frame(op=Op.FRAME_COMMIT, payload=commit, prio=Priority.P1_UI))
            bytes_queued += len(commit)

        await self.transport.flush()
        self._draw_pending = False
        self._frame_counter += 1
        dt_ms = (time.monotonic() - t0) * 1000.0
        if dt_ms >= 25.0 or self._frame_counter <= 3:
            rlog("PERF", f"emit_frame #{self._frame_counter}: dirty={len(snap['dirty'])} bytes={bytes_queued} dt={dt_ms:.1f}ms")
        return bytes_queued

    def _emit_planes(self) -> int:
        import time
        from ..widgets.video import Video
        from ..widgets.viewport3d import Viewport3D

        sent = 0
        root = self.app._ensure_screen() if hasattr(self.app, "_ensure_screen") else self.app.screen
        if root is None:
            return 0
        active_sids: set[int] = set()
        for w in root._walk(root):
            if (getattr(w, "styles", None) or {}).get("display") == "none":
                continue
            if not isinstance(w, (Viewport3D, Video)):
                continue
            r = w.rect
            if r.w < 4 or r.h < 4:
                continue
            sid = self._surf_ids.get(id(w))
            if sid is None:
                sid = self._next_surf_id
                self._next_surf_id += 1
                self._surf_ids[id(w)] = sid
                w.surf_id = sid
            active_sids.add(sid)
            rect_t = (r.x, r.y, r.x1, r.y1)
            if isinstance(w, Viewport3D):
                mode = w.choose_execution_mode(self.client_caps)
                kind = PLANE_KIND_3D_CLIENT if mode == "client" else PLANE_KIND_3D_SERVER
                if self._last_plane_rect.get(sid) != (rect_t, kind):
                    self._last_plane_rect[sid] = (rect_t, kind)
                    p = encode_plane_rect(rect_t, kind=kind)
                    self.transport.send(Frame(op=Op.PLANE_RECT, payload=p, prio=Priority.P1_UI, surf_id=sid))
                    sent += len(p)
                if mode == "client":
                    ub = encode_buf_write(0, 0, w.camera_uniform_bytes())
                    self.transport.send(Frame(op=Op.BUF_WRITE, payload=ub, prio=Priority.P1_UI, surf_id=sid))
                    sent += len(ub)
                else:
                    bw, bh, rgba = w.render_offscreen(r.w, r.h)
                    import struct as _st

                    vp = encode_vid_nalu(
                        int(time.monotonic() * 1_000_000),
                        0x07,
                        w.camera_mat4(),
                        _st.pack("<HH", bw, bh) + rgba,
                    )
                    self.transport.send(
                        Frame(op=Op.VID_NALU, payload=vp, prio=Priority.P2_MEDIA, surf_id=sid),
                        compress=False,
                        keyframe=True,
                    )
                    sent += len(vp)
            elif isinstance(w, Video):
                if self._last_plane_rect.get(sid) != (rect_t, PLANE_KIND_VIDEO):
                    self._last_plane_rect[sid] = (rect_t, PLANE_KIND_VIDEO)
                    p = encode_plane_rect(rect_t, kind=PLANE_KIND_VIDEO)
                    self.transport.send(Frame(op=Op.PLANE_RECT, payload=p, prio=Priority.P1_UI, surf_id=sid))
                    sent += len(p)
                for pkt in w.drain_packets():
                    cflag = getattr(pkt, "codec_flag", 0x10) & 0xF0
                    fl = (0x01 if pkt.keyframe else 0x00) | cflag
                    vp = encode_vid_nalu(pkt.pts_us, fl, (), pkt.data)
                    self.transport.send(
                        Frame(op=Op.VID_NALU, payload=vp, prio=Priority.P2_MEDIA, surf_id=sid),
                        compress=False,
                        keyframe=pkt.keyframe,
                    )
                    sent += len(vp)
                if w._rgba_buf and w._buf_size[0] > 0:
                    last_seq = getattr(self, "_last_vid_seq", {}).get(sid, -1)
                    cur_seq = getattr(w, "_frame_seq", 0)
                    vw, vh = w._buf_size
                    encoded_sent = False
                    codecs = self.client_caps.codecs or ()
                    want = tuple(c for c in ("hevc", "h264") if c in codecs)
                    if self.prefer_hw_video and want:
                        from .media_codec import StreamVideoEncoder, probe_video_encoder

                        if probe_video_encoder(want) is not None:
                            enc = self._vid_encoders.get(sid)
                            target_w, target_h = max(16, vw & ~1), max(16, vh & ~1)
                            if enc is None or getattr(enc, "_req_size", (enc.width, enc.height)) != (target_w, target_h):
                                if enc is not None:
                                    enc.close()
                                enc = StreamVideoEncoder(
                                    target_w,
                                    target_h,
                                    fps=24,
                                    preferred=want,
                                    on_au=self._wake_from_thread,
                                )
                                enc._req_size = (target_w, target_h)  # type: ignore[attr-defined]
                                self._vid_encoders[sid] = enc
                            if cur_seq != last_seq:
                                if not hasattr(self, "_last_vid_seq"):
                                    self._last_vid_seq: dict[int, int] = {}
                                self._last_vid_seq[sid] = cur_seq
                                enc.encode_rgba(w._rgba_buf, (vw, vh))
                            for au, is_kf in enc.drain_aus():
                                fl = (0x01 if is_kf else 0x00) | (enc.codec_flag & 0xF0)
                                vp = encode_vid_nalu(int(time.monotonic() * 1_000_000), fl, (), au)
                                self.transport.send(
                                    Frame(op=Op.VID_NALU, payload=vp, prio=Priority.P2_MEDIA, surf_id=sid),
                                    compress=False,
                                    keyframe=is_kf,
                                )
                                sent += len(vp)
                            encoded_sent = True
                    if not encoded_sent and cur_seq != last_seq:
                        if not hasattr(self, "_last_vid_seq"):
                            self._last_vid_seq = {}
                        self._last_vid_seq[sid] = cur_seq
                        import struct as _st

                        vp = encode_vid_nalu(
                            int(time.monotonic() * 1_000_000),
                            0x07,
                            (),
                            _st.pack("<HH", vw, vh) + w._rgba_buf,
                        )
                        self.transport.send(
                            Frame(op=Op.VID_NALU, payload=vp, prio=Priority.P2_MEDIA, surf_id=sid),
                            compress=False,
                            keyframe=False,
                        )
                        sent += len(vp)

        # Drop any planes belonging to screens/widgets no longer visible
        for gone_sid in [s for s in self._last_plane_rect if s not in active_sids]:
            self._last_plane_rect.pop(gone_sid, None)
            p = encode_plane_rect((0, 0, 0, 0), kind=0)
            self.transport.send(Frame(op=Op.PLANE_RECT, payload=p, prio=Priority.P1_UI, surf_id=gone_sid))
            sent += len(p)
        return sent

    async def _draw_loop(self) -> None:
        while self.app._running:
            await self._draw_event.wait()
            self._draw_event.clear()
            if self._draw_pending:
                await self.emit_frame()
            elif self.transport.mux.pending_count() > 0:
                await self.transport.flush()

    async def _rx_loop(self) -> None:
        while self.app._running:
            try:
                frame = await self.transport.recv()
            except Exception as exc:  # noqa: BLE001
                rlog("ERR", f"rx recv error: {exc}")
                self.transport.close()
                if not self.detach_on_eof:
                    self.app._running = False
                self._draw_event.set()
                break
            if frame is None:
                rlog("NET", "peer EOF")
                self.transport.close()
                if not self.detach_on_eof:
                    self.app._running = False
                self._draw_event.set()
                break
            try:
                await self._handle_frame(frame)
            except Exception as exc:  # noqa: BLE001
                rlog("ERR", f"handle_frame op={frame.op:#x} error: {exc}")
            if self._draw_pending:
                await self.emit_frame()

    async def _handle_frame(self, frame: Frame) -> None:
        op = frame.op
        if op == Op.INPUT_KEY:
            self.process_key(frame.payload.decode("utf-8", errors="replace"))
        elif op == Op.INPUT_CHAR:
            self.process_char(frame.payload.decode("utf-8", errors="replace"))
        elif op == Op.INPUT_POINTER:
            kind, x, y, btn = decode_input_pointer(frame.payload)
            if kind == POINTER_DOWN:
                self.process_pointer(x, y, btn)
            elif kind == POINTER_MOVE:
                if self.app.on_pointer_move(x, y):
                    self.app.request_draw()
            elif kind == POINTER_UP:
                self.app.on_pointer_up(x, y, btn)
                self.app.request_draw()
        elif op == Op.INPUT_WHEEL:
            x, y, dx, dy = decode_input_wheel(frame.payload)
            self.process_wheel(x, y, dx, dy)
        elif op == Op.RESIZE:
            w, h = decode_resize(frame.payload)
            self.size = (w, h)
            if hasattr(self.app, "_pixel_size"):
                self.app._pixel_size = (w, h)  # type: ignore[attr-defined]
            self.app.on_resize(w, h)
            self.app.request_draw()
        elif op == Op.CLIP_PASTE:
            self.remote_clipboard = frame.payload.decode("utf-8", errors="replace")
        elif op == Op.CLIENT_FILE_DATA:
            name, data = decode_client_file(frame.payload)
            dt_ms = (time.monotonic() - self._fs_req_t0) * 1000.0 if self._fs_req_t0 else 0.0
            rlog("FS", f"CLIENT_FILE_DATA <- peer name={name!r} bytes={len(data)} dt={dt_ms:.1f}ms")
            cb = self._client_file_cb
            self._client_file_cb = None
            if callable(cb):
                try:
                    cb(name, data)
                except Exception as exc:  # noqa: BLE001
                    rlog("ERR", f"client_file callback error: {exc}")
                self.app.request_draw()
        elif op == Op.FS_ENTRIES:
            req_path, entries = decode_fs_entries(frame.payload)
            dt_ms = (time.monotonic() - self._fs_req_t0) * 1000.0 if self._fs_req_t0 else 0.0
            rlog("FS", f"FS_ENTRIES <- peer path={req_path!r} items={len(entries)} dt={dt_ms:.1f}ms")
            cb = self._fs_list_cb
            self._fs_list_cb = None
            if callable(cb):
                try:
                    cb(req_path, entries)
                except Exception as exc:  # noqa: BLE001
                    rlog("ERR", f"fs_entries callback error: {exc}")
                self.app.request_draw()
        elif op == Op.VID_NALU:
            pts_us, flags, _mat, raw = decode_vid_nalu(frame.payload)
            if callable(self._peer_cam_cb):
                self._peer_cam_cb(pts_us, flags, raw)
        elif op == Op.AUDIO_PCM:
            sr, ch, _fl, pcm = decode_audio_pcm(frame.payload)
            if callable(self._peer_mic_cb):
                self._peer_mic_cb(sr, ch, pcm)
        elif op == Op.CAPS:
            from ..widgets.viewport3d import ClientCapabilities
            from ..widgets.video import Video
            from ..widgets.viewport3d import Viewport3D

            hg, vram, codecs = decode_caps(frame.payload)
            self.client_caps = ClientCapabilities(
                has_gpu=hg,
                vram_mb=vram,
                codecs=tuple(c.strip() for c in codecs.split(",") if c.strip()),
            )
            self._last_plane_rect.clear()
            root = self.app._ensure_screen() if hasattr(self.app, "_ensure_screen") else self.app.screen
            if root is not None and any(isinstance(w, (Viewport3D, Video)) for w in root._walk(root)):
                self.app.request_draw()
        elif op == Op.PING:
            await self.transport.send_now(
                Frame(op=Op.PONG, payload=frame.payload, prio=Priority.P0_CONTROL),
                compress=False,
            )
        elif op == Op.LIST_APPS:
            from .directory import discover_local_apps

            apps = [a.as_wire_tuple() for a in discover_local_apps(apps_path=self.apps_path)]
            await self.transport.send_now(
                Frame(op=Op.APP_LIST, payload=encode_app_list(apps), prio=Priority.P0_CONTROL),
            )
        elif op == Op.LAUNCH_APP:
            app_id, args = decode_launch_app(frame.payload)
            await self._launch_app(app_id, args)

    async def _swap_app(self, new_app: Any) -> None:
        old_app = self.app
        try:
            old_app._running = False
        except Exception:  # noqa: BLE001
            pass
        for enc in self._vid_encoders.values():
            try:
                enc.close()
            except Exception:  # noqa: BLE001
                pass
        self._vid_encoders.clear()
        self._last_plane_rect.clear()
        self._surf_ids.clear()
        if hasattr(self, "_last_vid_seq"):
            self._last_vid_seq.clear()
        self.recorder.reset_retained(keep_blobs=True)

        self.app = new_app
        new_app._driver = self
        new_app._loop = asyncio.get_running_loop()
        new_app._running = True
        if hasattr(new_app, "_pixel_size"):
            new_app._pixel_size = self.size
        if hasattr(new_app, "_layout_size"):
            new_app._layout_size = (0, 0)
        if hasattr(new_app, "on_mount"):
            res = new_app.on_mount()
            if inspect.isawaitable(res):
                await res
        if hasattr(new_app, "_ensure_screen"):
            new_app._booting = False
            new_app._ensure_screen()
        elif hasattr(new_app, "after_first_draw"):
            new_app.after_first_draw()
        if hasattr(new_app, "on_resize"):
            new_app.on_resize(*self.size)

        from ..events import Ready
        from ..widget import dispatch_event

        dispatch_event(new_app, Ready(), "on_ready")
        if new_app.screen is not None:
            dispatch_event(new_app.screen, Ready(), "on_ready")
        await self._send_hello()
        await self.emit_frame()

    async def _launch_app(self, app_id: str, args: str) -> None:
        from .directory import instantiate_python_app, resolve_app_entry

        entry = resolve_app_entry(app_id, apps_path=self.apps_path)
        if entry is None:
            await self.transport.send_now(
                Frame(
                    op=Op.LAUNCH_STATUS,
                    payload=encode_launch_status(False, app_id, f"Unknown app: {app_id}"),
                    prio=Priority.P0_CONTROL,
                ),
            )
            return
        if entry.kind == "mode":
            if hasattr(self.app, "switch_mode") and self.app.switch_mode(entry.target):
                self.app.request_draw()
                await self.transport.send_now(
                    Frame(
                        op=Op.LAUNCH_STATUS,
                        payload=encode_launch_status(True, app_id, f"Switched to {entry.name}"),
                        prio=Priority.P0_CONTROL,
                    ),
                )
                return
            # Otherwise fall through to launching demo app directly in that mode
            try:
                from ..demo import build_demo_app

                new_app = build_demo_app(start=entry.target)
                await self._swap_app(new_app)
                await self.transport.send_now(
                    Frame(
                        op=Op.LAUNCH_STATUS,
                        payload=encode_launch_status(True, app_id, f"Launched {entry.name}"),
                        prio=Priority.P0_CONTROL,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                await self.transport.send_now(
                    Frame(
                        op=Op.LAUNCH_STATUS,
                        payload=encode_launch_status(False, app_id, str(exc)),
                        prio=Priority.P0_CONTROL,
                    ),
                )
            return

        if entry.kind == "python":
            try:
                new_app = instantiate_python_app(entry.target, args)
                await self._swap_app(new_app)
                await self.transport.send_now(
                    Frame(
                        op=Op.LAUNCH_STATUS,
                        payload=encode_launch_status(True, app_id, f"Launched {entry.name}"),
                        prio=Priority.P0_CONTROL,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                await self.transport.send_now(
                    Frame(
                        op=Op.LAUNCH_STATUS,
                        payload=encode_launch_status(False, app_id, f"Launch failed: {exc}"),
                        prio=Priority.P0_CONTROL,
                    ),
                )
            return

        await self.transport.send_now(
            Frame(
                op=Op.LAUNCH_STATUS,
                payload=encode_launch_status(False, app_id, f"Unsupported app kind: {entry.kind}"),
                prio=Priority.P0_CONTROL,
            ),
        )
