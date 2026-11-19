"""Shared local Camera & Microphone resource hub (encode once, fan out to N subscribers)."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .media_codec import (
    AnnexBChunker,
    StreamVideoDecoder,
    _encoder_extra_args,
    codec_flag_to_fmt,
    probe_video_encoder,
)
from .protocol import VID_CODEC_H264, VID_CODEC_HEVC, VID_FLAG_KEYFRAME

HUB_PKT_STRUCT = struct.Struct("<QBI")  # pts_us (u64), flags (u8), len (u32)
RESOURCE_SERVICE_PORT = 7423
PEER_RESOURCE_PORTS = tuple(range(7424, 7456))
ALL_RESOURCE_PORTS = (RESOURCE_SERVICE_PORT, *PEER_RESOURCE_PORTS)


def hub_cache_dir() -> Path:
    d = Path.home() / ".cache" / "textual-wgpu"
    d.mkdir(parents=True, exist_ok=True)
    return d


def camera_socket_path(device: str = "/dev/video0") -> Path:
    name = Path(device).name or "video0"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return hub_cache_dir() / f"cam_{safe}.sock"


def mic_socket_path() -> Path:
    return hub_cache_dir() / "mic_default.sock"


def _extract_annexb_headers(au: bytes, codec_flag: int) -> bytes:
    """Return any SPS/PPS/VPS NALUs present at the start of ``au``."""
    sc = b"\x00\x00\x00\x01"
    hdrs = bytearray()
    pos = 0
    n = len(au)
    while pos + 5 <= n:
        idx = au.find(sc, pos)
        if idx < 0:
            break
        nxt = au.find(sc, idx + 4)
        nal = au[idx : nxt if nxt >= 0 else n]
        if len(nal) >= 5:
            b4 = nal[4]
            if codec_flag == VID_CODEC_HEVC:
                if ((b4 >> 1) & 0x3F) >= 32:
                    hdrs.extend(nal)
            else:
                if (b4 & 0x1F) in (7, 8):
                    hdrs.extend(nal)
        if nxt < 0:
            break
        pos = nxt
    return bytes(hdrs)


class _BroadcastHub:
    """UNIX domain socket broadcast hub with late-joiner keyframe cache and auto-idle exit."""

    def __init__(self, sock_path: Path, *, idle_timeout: float = 2.0, startup_grace: float = 5.0) -> None:
        self.sock_path = sock_path
        self.idle_timeout = float(idle_timeout)
        self.startup_grace = float(startup_grace)
        self._lock = threading.Lock()
        self._subs: dict[int, queue.Queue[bytes | None]] = {}
        self._next_id = 1
        self._ever_connected = False
        self._last_sub_at = time.monotonic()
        self._started_at = time.monotonic()
        self._closed = False
        self._cached_headers = b""
        self._last_kf: bytes | None = None
        self._srv: socket.socket | None = None

    def start_accepting(self) -> None:
        if self.sock_path.exists():
            try:
                self.sock_path.unlink()
            except OSError:
                pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        srv.listen(16)
        srv.settimeout(0.25)
        self._srv = srv
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._closed:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            q: queue.Queue[bytes | None] = queue.Queue(maxsize=48)
            with self._lock:
                sid = self._next_id
                self._next_id += 1
                self._subs[sid] = q
                self._ever_connected = True
                self._last_sub_at = time.monotonic()
                kf = self._last_kf
            if kf is not None:
                try:
                    q.put_nowait(kf)
                except queue.Full:
                    pass
            threading.Thread(target=self._writer_loop, args=(sid, conn, q), daemon=True).start()

    def _writer_loop(self, sid: int, conn: socket.socket, q: queue.Queue[bytes | None]) -> None:
        try:
            while not self._closed:
                pkt = q.get()
                if pkt is None:
                    break
                conn.sendall(pkt)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self._lock:
                self._subs.pop(sid, None)
                self._last_sub_at = time.monotonic()

    def publish(self, pts_us: int, flags: int, payload: bytes, *, is_keyframe: bool = False, codec_flag: int = 0) -> None:
        if is_keyframe and codec_flag != 0:
            hdrs = _extract_annexb_headers(payload, codec_flag)
            if hdrs:
                self._cached_headers = hdrs
            elif self._cached_headers:
                payload = self._cached_headers + payload
        pkt = HUB_PKT_STRUCT.pack(int(pts_us), int(flags) & 0xFF, len(payload)) + payload
        with self._lock:
            if is_keyframe:
                self._last_kf = pkt
            for q in self._subs.values():
                if q.full():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    q.put_nowait(pkt)
                except queue.Full:
                    pass

    def should_exit_idle(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._subs:
                return False
            if not self._ever_connected:
                return (now - self._started_at) >= self.startup_grace
            return (now - self._last_sub_at) >= self.idle_timeout

    def close(self) -> None:
        self._closed = True
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        with self._lock:
            for q in self._subs.values():
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
            self._subs.clear()
        try:
            if self.sock_path.exists():
                self.sock_path.unlink()
        except OSError:
            pass


def run_video_hub(device: str = "/dev/video0", *, width: int = 640, height: int = 360, fps: int = 24) -> int:
    ff = shutil.which("ffmpeg")
    if not ff or not Path(device).exists():
        return 1
    found = probe_video_encoder(("h264", "hevc"))
    if found is None:
        return 1
    enc_name, codec_flag = found
    fmt = codec_flag_to_fmt(codec_flag)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    cmd = [ff, "-y", "-loglevel", "error"]
    if enc_name.endswith("_vaapi"):
        cmd.extend(["-vaapi_device", "/dev/dri/renderD128"])
    cmd.extend(["-f", "v4l2", "-framerate", str(fps), "-i", device])
    if enc_name.endswith("_vaapi"):
        cmd.extend(["-vf", f"{vf},format=nv12,hwupload"])
    else:
        cmd.extend(["-vf", vf, "-pix_fmt", "yuv420p"])
    cmd.extend(["-c:v", enc_name, *_encoder_extra_args(enc_name), "-f", fmt, "pipe:1"])

    hub = _BroadcastHub(camera_socket_path(device))
    hub.start_accepting()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunker = AnnexBChunker(codec_flag)
    t0 = time.monotonic()

    def _read() -> None:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        try:
            while not hub._closed:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                for au, is_kf in chunker.feed(chunk):
                    pts_us = int((time.monotonic() - t0) * 1_000_000)
                    fl = (VID_FLAG_KEYFRAME if is_kf else 0) | (codec_flag & 0xF0)
                    hub.publish(pts_us, fl, au, is_keyframe=is_kf, codec_flag=codec_flag)
        except OSError:
            pass

    rt = threading.Thread(target=_read, daemon=True)
    rt.start()
    try:
        while proc.poll() is None and not hub.should_exit_idle():
            time.sleep(0.1)
    finally:
        hub.close()
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    return 0


def run_mic_hub(*, sample_rate: int = 16000, channels: int = 1, chunk_ms: int = 80) -> int:
    chunk_bytes = max(640, (sample_rate * channels * 2 * chunk_ms) // 1000)
    cmd = None
    if shutil.which("pw-record"):
        cmd = ["pw-record", "--format=s16", f"--rate={sample_rate}", f"--channels={channels}", "-"]
    elif shutil.which("parecord"):
        cmd = ["parecord", "--raw", "--format=s16le", f"--rate={sample_rate}", f"--channels={channels}"]
    elif shutil.which("arecord"):
        cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", str(channels), "-"]
    elif shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "pulse", "-i", "default",
            "-f", "s16le", "-ac", str(channels), "-ar", str(sample_rate), "pipe:1",
        ]
    if cmd is None:
        return 1

    hub = _BroadcastHub(mic_socket_path())
    hub.start_accepting()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    t0 = time.monotonic()

    def _read() -> None:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        buf = bytearray()
        try:
            while not hub._closed:
                chunk = os.read(fd, chunk_bytes)
                if not chunk:
                    break
                buf.extend(chunk)
                while len(buf) >= chunk_bytes:
                    piece = bytes(buf[:chunk_bytes])
                    del buf[:chunk_bytes]
                    pts_us = int((time.monotonic() - t0) * 1_000_000)
                    hub.publish(pts_us, 1, piece)
        except OSError:
            pass

    threading.Thread(target=_read, daemon=True).start()
    try:
        while proc.poll() is None and not hub.should_exit_idle():
            time.sleep(0.1)
    finally:
        hub.close()
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    return 0


def _try_connect(sock_path: Path, timeout: float = 0.2) -> socket.socket | None:
    if not sock_path.exists():
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.settimeout(None)
        return s
    except OSError:
        try:
            s.close()
        except OSError:
            pass
        return None


def connect_camera_hub(device: str = "/dev/video0", *, width: int = 640, height: int = 360, fps: int = 24) -> socket.socket:
    """Connect to the shared camera hub for ``device``, spawning it cleanly if not running."""
    sock_path = camera_socket_path(device)
    s = _try_connect(sock_path)
    if s is not None:
        return s
    lock_path = sock_path.with_suffix(".lock")
    with lock_path.open("w", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        s = _try_connect(sock_path)
        if s is not None:
            return s
        subprocess.Popen(
            [
                sys.executable, "-m", "textual_wgpu.remote.media_hub",
                "--video", device, "--width", str(width), "--height", str(height), "--fps", str(fps),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            s = _try_connect(sock_path)
            if s is not None:
                return s
            time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for camera hub on {device}")


def connect_mic_hub(*, sample_rate: int = 16000) -> socket.socket:
    """Connect to the shared microphone hub, spawning it cleanly if not running."""
    sock_path = mic_socket_path()
    s = _try_connect(sock_path)
    if s is not None:
        return s
    lock_path = sock_path.with_suffix(".lock")
    with lock_path.open("w", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        s = _try_connect(sock_path)
        if s is not None:
            return s
        subprocess.Popen(
            [sys.executable, "-m", "textual_wgpu.remote.media_hub", "--mic", "--rate", str(sample_rate)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            s = _try_connect(sock_path)
            if s is not None:
                return s
            time.sleep(0.05)
    raise RuntimeError("Timed out waiting for microphone hub")


def _read_line(sock: socket.socket, *, max_bytes: int = 65536) -> str | None:
    buf = bytearray()
    while len(buf) < max_bytes:
        chunk = sock.recv(1)
        if not chunk:
            return buf.decode("utf-8", errors="replace") if buf else None
        if chunk == b"\n":
            return buf.decode("utf-8", errors="replace")
        buf.extend(chunk)
    return buf.decode("utf-8", errors="replace")


def _recv_exact(sock: socket.socket, n: int, *, stop_flag: Callable[[], bool] | None = None) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        if stop_flag is not None and stop_flag():
            return None
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            continue
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _list_local_fs(req_path: str) -> list[dict[str, Any]]:
    if not req_path:
        roots: list[dict[str, Any]] = []
        dl = Path.home() / "Downloads"
        if dl.is_dir():
            roots.append({"d": True, "n": "Downloads", "p": str(dl), "s": 0})
        home = Path.home()
        roots.append({"d": True, "n": "Home", "p": str(home), "s": 0})
        return roots
    p = Path(req_path).expanduser()
    if not p.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        kids = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except OSError:
        kids = []
    for k in kids:
        if k.name.startswith("."):
            continue
        try:
            is_d = k.is_dir()
            sz = 0 if is_d else k.stat().st_size
        except OSError:
            continue
        out.append({"d": is_d, "n": k.name, "p": str(k), "s": int(sz)})
    return out


class _LocalSpeakerProcess:
    """Writes raw PCM16 chunks to the Linux audio sink (pw-play / paplay / aplay)."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        sr = max(8000, int(sample_rate))
        ch = max(1, min(2, int(channels)))
        cmd = None
        if shutil.which("pw-play"):
            cmd = ["pw-play", "--format=s16", f"--rate={sr}", f"--channels={ch}", "-"]
        elif shutil.which("paplay"):
            cmd = ["paplay", "--raw", "--format=s16le", f"--rate={sr}", f"--channels={ch}"]
        elif shutil.which("aplay"):
            cmd = ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sr), "-c", str(ch), "-"]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if cmd else None

    def write(self, pcm: bytes) -> None:
        if self._proc is not None and self._proc.stdin is not None and pcm:
            try:
                self._proc.stdin.write(pcm)
                self._proc.stdin.flush()
            except OSError:
                pass

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.3)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None


def _probe_local_gpu_label() -> str:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "Local GPU (Vulkan/wgpu)"
    try:
        if shutil.which("vulkaninfo"):
            out = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, timeout=0.8)
            if out.returncode == 0:
                for ln in out.stdout.decode("utf-8", errors="replace").splitlines():
                    if "deviceName" in ln and "=" in ln:
                        name = ln.split("=", 1)[1].strip()
                        if name and "llvmpipe" not in name.lower():
                            return f"GPU · {name}"
    except Exception:  # noqa: BLE001
        pass
    return "Local GPU (Vulkan/wgpu)"


_CACHED_GPU_LABEL: str | None = None


def local_gpu_label() -> str:
    global _CACHED_GPU_LABEL
    if _CACHED_GPU_LABEL is None:
        _CACHED_GPU_LABEL = _probe_local_gpu_label()
    return _CACHED_GPU_LABEL


class ResourceServer:
    """Independent per-device TCP Resource Service (cameras, mic, speaker, clipboard, files, AI, sessions)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = RESOURCE_SERVICE_PORT,
        *,
        profile_fn: Callable[[], Any] | None = None,
        camera_connector: Callable[[str], socket.socket] | None = None,
        mic_connector: Callable[[], socket.socket] | None = None,
        speaker_sink_factory: Callable[[int, int], Any] | None = None,
        ai_providers_fn: Callable[[], list[Any]] | None = None,
        ai_infer_fn: Callable[..., str] | None = None,
        session_broker: Any | None = None,
        fs_root: Path | None = None,
    ) -> None:
        self.host = host
        self._req_port = int(port)
        self._profile_fn = profile_fn
        self._camera_connector = camera_connector or connect_camera_hub
        self._mic_connector = mic_connector or connect_mic_hub
        self._speaker_sink_factory = speaker_sink_factory or (lambda sr, ch: _LocalSpeakerProcess(sr, ch))
        self._ai_providers_fn = ai_providers_fn
        self._ai_infer_fn = ai_infer_fn
        self._session_broker = session_broker
        self._fs_root = fs_root
        self._clip_lock = threading.Lock()
        self._clip_buf = ""
        self._srv: socket.socket | None = None
        self._closed = False
        self._port = 0

    def _get_broker(self) -> Any:
        if self._session_broker is None:
            from .session_broker import SessionBroker

            self._session_broker = SessionBroker()
        return self._session_broker

    def _get_ai_providers(self) -> list[Any]:
        if self._ai_providers_fn is not None:
            return list(self._ai_providers_fn())
        from .ai_resource import discover_local_ai_providers

        return discover_local_ai_providers()

    def _clip_read(self) -> str:
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-ob"]):
                if shutil.which(cmd[0]):
                    try:
                        res = subprocess.run(cmd, capture_output=True, timeout=0.6)
                        if res.returncode == 0:
                            txt = res.stdout.decode("utf-8", errors="replace")
                            with self._clip_lock:
                                self._clip_buf = txt
                            return txt
                    except Exception:  # noqa: BLE001
                        pass
        with self._clip_lock:
            return self._clip_buf

    def _clip_write(self, text: str) -> None:
        with self._clip_lock:
            self._clip_buf = text
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=0.6)
                        return
                    except Exception:  # noqa: BLE001
                        pass

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> int:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self._req_port))
        srv.listen(32)
        srv.settimeout(0.25)
        self._srv = srv
        self._port = int(srv.getsockname()[1])
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self._port

    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._closed:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()

    def _build_list_payload(self) -> dict[str, Any]:
        if self._profile_fn is not None:
            prof = self._profile_fn()
        else:
            from .directory import local_device_profile

            prof = local_device_profile()
        resources: list[dict[str, str]] = [
            {"kind": "camera", "id": cid, "label": label}
            for cid, label in (getattr(prof, "cameras", ()) or ())
        ]
        if getattr(prof, "has_mic", True):
            resources.append({"kind": "mic", "id": "default", "label": "Microphone"})
        resources.append({"kind": "speaker", "id": "default", "label": "Speaker"})
        resources.append({"kind": "clipboard", "id": "default", "label": "Clipboard"})
        resources.append({"kind": "files", "id": "fs", "label": "Files & Media"})
        resources.append({"kind": "gpu", "id": "vulkan", "label": local_gpu_label()})
        for p in self._get_ai_providers():
            resources.append({"kind": "ai", "id": p.resource_id, "label": p.label})
        return {
            "ok": True,
            "device": {
                "device_id": prof.device_id,
                "name": prof.name,
                "kind": prof.kind,
                "icon": prof.icon,
            },
            "resources": resources,
        }

    def _handle_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10.0)
            line = _read_line(conn)
            if not line:
                return
            req = json.loads(line)
            if not isinstance(req, dict):
                return
            op = str(req.get("op") or "")
            if op == "list":
                conn.sendall((json.dumps(self._build_list_payload()) + "\n").encode("utf-8"))
                return
            if op == "fs_list":
                rpath = str(req.get("path") or "")
                if self._fs_root is not None and not rpath:
                    entries = [{"d": True, "n": self._fs_root.name or "Root", "p": str(self._fs_root), "s": 0}]
                else:
                    entries = _list_local_fs(rpath)
                conn.sendall((json.dumps({"ok": True, "path": rpath, "entries": entries}) + "\n").encode("utf-8"))
                return
            if op == "fs_get":
                rpath = str(req.get("path") or "")
                p = Path(rpath).expanduser()
                if not rpath or not p.is_file():
                    conn.sendall(b'{"ok":false,"error":"not found"}\n')
                    return
                data = p.read_bytes()
                hdr = json.dumps({"ok": True, "name": p.name, "size": len(data)}) + "\n"
                conn.sendall(hdr.encode("utf-8"))
                conn.sendall(data)
                return
            if op == "clip_get":
                conn.sendall((json.dumps({"ok": True, "text": self._clip_read()}) + "\n").encode("utf-8"))
                return
            if op == "clip_set":
                self._clip_write(str(req.get("text") or ""))
                conn.sendall(b'{"ok":true}\n')
                return
            if op == "play":
                sr = int(req.get("sr") or 16000)
                ch = int(req.get("ch") or 1)
                sink = self._speaker_sink_factory(sr, ch)
                conn.settimeout(None)
                conn.sendall(b'{"ok":true}\n')
                hdr_sz = HUB_PKT_STRUCT.size
                try:
                    while not self._closed:
                        hdr = _recv_exact(conn, hdr_sz)
                        if hdr is None:
                            break
                        _pts, _fl, plen = HUB_PKT_STRUCT.unpack(hdr)
                        if plen > 4 * 1024 * 1024:
                            break
                        pcm = _recv_exact(conn, plen) if plen > 0 else b""
                        if pcm is None:
                            break
                        if pcm:
                            sink.write(pcm)
                finally:
                    try:
                        sink.close()
                    except Exception:  # noqa: BLE001
                        pass
                return
            if op == "ai_infer":
                from .ai_resource import run_ai_inference

                model_id = str(req.get("model") or "builtin:analyzer")
                prompt = str(req.get("prompt") or "")
                context = str(req.get("context") or "")
                image_b64 = str(req.get("image_b64") or "")
                providers = self._get_ai_providers()
                chosen = next((p for p in providers if p.resource_id == model_id), None)
                if chosen is None and providers:
                    chosen = providers[0]
                conn.settimeout(60.0)

                def _emit_tok(tok: str) -> None:
                    conn.sendall((json.dumps({"token": tok}) + "\n").encode("utf-8"))

                if self._ai_infer_fn is not None:
                    full = self._ai_infer_fn(chosen, prompt, context=context, image_b64=image_b64, on_token=_emit_tok)
                else:
                    full = run_ai_inference(chosen, prompt, context=context, image_b64=image_b64, on_token=_emit_tok)
                conn.sendall((json.dumps({"done": True, "text": full}) + "\n").encode("utf-8"))
                return
            if op == "sessions":
                sessions = [s.to_dict() for s in self._get_broker().list_sessions()]
                conn.sendall((json.dumps({"ok": True, "sessions": sessions}) + "\n").encode("utf-8"))
                return
            if op == "spawn_session":
                app_id = str(req.get("app_id") or "demo")
                args = str(req.get("args") or "")
                w = int(req.get("width") or 1100)
                h = int(req.get("height") or 720)
                info = self._get_broker().spawn_session(app_id, args, width=w, height=h)
                conn.sendall((json.dumps({"ok": True, "session": info.to_dict()}) + "\n").encode("utf-8"))
                return
            if op == "stop_session":
                sid = str(req.get("session_id") or "")
                ok = self._get_broker().stop_session(sid)
                conn.sendall((json.dumps({"ok": ok}) + "\n").encode("utf-8"))
                return
            if op == "tunnel_tcp":
                tport = int(req.get("port") or 0)
                if tport <= 1024 or tport > 65535:
                    conn.sendall(b'{"ok":false,"error":"invalid port"}\n')
                    return
                upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                upstream.settimeout(4.0)
                upstream.connect(("127.0.0.1", tport))
                upstream.settimeout(None)
                conn.settimeout(None)
                conn.sendall(b'{"ok":true}\n')
                self._bridge_bidi(conn, upstream)
                return
            if op == "sub":
                kind = str(req.get("kind") or "")
                rid = str(req.get("id") or "")
                if kind == "camera":
                    hub_sock = self._camera_connector(rid or "/dev/video0")
                elif kind == "mic":
                    hub_sock = self._mic_connector()
                else:
                    conn.sendall(b'{"ok":false,"error":"unknown resource kind"}\n')
                    return
                conn.settimeout(None)
                conn.sendall(b'{"ok":true}\n')
                self._pump_stream(hub_sock, conn)
                return
            conn.sendall(b'{"ok":false,"error":"unknown op"}\n')
        except Exception as exc:  # noqa: BLE001
            try:
                conn.sendall((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode("utf-8"))
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _bridge_bidi(self, a: socket.socket, b: socket.socket) -> None:
        def _pipe(src: socket.socket, dst: socket.socket) -> None:
            try:
                while not self._closed:
                    buf = src.recv(65536)
                    if not buf:
                        break
                    dst.sendall(buf)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t1 = threading.Thread(target=_pipe, args=(a, b), daemon=True)
        t2 = threading.Thread(target=_pipe, args=(b, a), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        try:
            b.close()
        except OSError:
            pass

    def _pump_stream(self, hub_sock: socket.socket, client_sock: socket.socket) -> None:
        stop_ev = threading.Event()

        def _watch_client_eof() -> None:
            try:
                while not stop_ev.is_set() and not self._closed:
                    b = client_sock.recv(1)
                    if not b:
                        break
            except OSError:
                pass
            finally:
                stop_ev.set()
                try:
                    hub_sock.close()
                except OSError:
                    pass

        threading.Thread(target=_watch_client_eof, daemon=True).start()
        try:
            while not stop_ev.is_set() and not self._closed:
                chunk = hub_sock.recv(65536)
                if not chunk:
                    break
                client_sock.sendall(chunk)
        except OSError:
            pass
        finally:
            stop_ev.set()
            try:
                hub_sock.close()
            except OSError:
                pass

    def close(self) -> None:
        self._closed = True
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None


def _tcp_listening(host: str, port: int, timeout: float = 0.1) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def ensure_resource_service(host: str = "127.0.0.1", port: int = RESOURCE_SERVICE_PORT) -> bool:
    """Ensure the local device Resource Service is listening on ``host:port``."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if _tcp_listening(host, port):
        return True
    lock_path = hub_cache_dir() / "resource_service.lock"
    try:
        with lock_path.open("w", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            if _tcp_listening(host, port):
                return True
            subprocess.Popen(
                [sys.executable, "-m", "textual_wgpu.remote.media_hub", "--serve-resources", "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if _tcp_listening(host, port):
                    return True
                time.sleep(0.05)
    except OSError:
        return False
    return False


def query_resource_server(host: str, port: int, *, timeout: float = 0.2) -> dict[str, Any] | None:
    """Query a Resource Service endpoint for its device metadata & independent resources."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall(b'{"op":"list"}\n')
        line = _read_line(s)
        if not line:
            return None
        data = json.loads(line)
        return data if isinstance(data, dict) and data.get("ok") else None
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_fs_list(
    host: str,
    port: int,
    req_path: str = "",
    *,
    timeout: float = 3.0,
) -> list[tuple[bool, str, str, int]]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall((json.dumps({"op": "fs_list", "path": req_path}) + "\n").encode("utf-8"))
        line = _read_line(s)
        if not line:
            return []
        data = json.loads(line)
        out: list[tuple[bool, str, str, int]] = []
        for it in data.get("entries") or ():
            out.append((bool(it.get("d")), str(it.get("n") or ""), str(it.get("p") or ""), int(it.get("s") or 0)))
        return out
    except (OSError, json.JSONDecodeError):
        return []
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_fs_get(
    host: str,
    port: int,
    req_path: str,
    *,
    timeout: float = 30.0,
) -> tuple[str, bytes] | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall((json.dumps({"op": "fs_get", "path": req_path}) + "\n").encode("utf-8"))
        line = _read_line(s)
        if not line:
            return None
        meta = json.loads(line)
        if not meta.get("ok"):
            return None
        name = str(meta.get("name") or Path(req_path).name or "media.bin")
        size = int(meta.get("size") or 0)
        if size <= 0:
            return (name, b"")
        raw = _recv_exact(s, size)
        if raw is None:
            return None
        return (name, raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


class RemoteCameraSubscriber:
    """Independent TCP subscriber to any device's camera resource (local or remote over SSH)."""

    def __init__(
        self,
        host: str,
        port: int,
        resource_id: str,
        *,
        emit_rgba: bool = False,
        on_frame: Any = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.resource_id = resource_id
        self.rotation = 0
        self.codec_flag = VID_CODEC_H264
        self._emit_rgba = bool(emit_rgba)
        self._on_frame = on_frame
        self._aus: deque[tuple[bytes, bool]] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False
        self._decoder: StreamVideoDecoder | None = None

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4.0)
        sock.connect((self.host, self.port))
        sock.sendall((json.dumps({"op": "sub", "kind": "camera", "id": self.resource_id}) + "\n").encode("utf-8"))
        line = _read_line(sock)
        if not line:
            sock.close()
            raise RuntimeError("Empty response from camera resource service")
        meta = json.loads(line)
        if not meta.get("ok"):
            sock.close()
            raise RuntimeError(str(meta.get("error") or "Camera resource unavailable"))
        sock.settimeout(0.5)
        self._sock = sock
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        hdr_sz = HUB_PKT_STRUCT.size
        sock = self._sock
        try:
            while not self._closed:
                hdr = _recv_exact(sock, hdr_sz, stop_flag=lambda: self._closed)
                if hdr is None:
                    break
                _pts, flags, plen = HUB_PKT_STRUCT.unpack(hdr)
                au = _recv_exact(sock, plen, stop_flag=lambda: self._closed) if plen > 0 else b""
                if au is None:
                    break
                is_kf = bool(flags & 0x01)
                self.rotation = (flags >> 2) & 0x03
                cflag = flags & 0xF0
                if cflag:
                    self.codec_flag = cflag
                if self._emit_rgba:
                    if self._decoder is None and cflag:
                        self._decoder = StreamVideoDecoder(cflag)
                    if self._decoder is not None:
                        self._decoder.feed(au)
                else:
                    with self._lock:
                        self._aus.append((au, is_kf))
                if callable(self._on_frame):
                    self._on_frame()
        except OSError:
            pass

    def pop_aus(self) -> list[tuple[bytes, bool]]:
        with self._lock:
            out = list(self._aus)
            self._aus.clear()
            return out

    def pop_rgba(self) -> tuple[int, int, bytes] | None:
        if self._decoder is not None:
            return self._decoder.latest_frame()
        return None

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass
        if self._decoder is not None:
            try:
                self._decoder.close()
            except Exception:  # noqa: BLE001
                pass
            self._decoder = None


class RemoteMicSubscriber:
    """Independent TCP subscriber to any device's microphone resource."""

    def __init__(
        self,
        host: str,
        port: int,
        resource_id: str = "default",
        *,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._chunks: deque[bytes] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4.0)
        sock.connect((host, int(port)))
        sock.sendall((json.dumps({"op": "sub", "kind": "mic", "id": resource_id}) + "\n").encode("utf-8"))
        line = _read_line(sock)
        if not line:
            sock.close()
            raise RuntimeError("Empty response from mic resource service")
        meta = json.loads(line)
        if not meta.get("ok"):
            sock.close()
            raise RuntimeError(str(meta.get("error") or "Mic resource unavailable"))
        sock.settimeout(0.5)
        self._sock = sock
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        hdr_sz = HUB_PKT_STRUCT.size
        sock = self._sock
        try:
            while not self._closed:
                hdr = _recv_exact(sock, hdr_sz, stop_flag=lambda: self._closed)
                if hdr is None:
                    break
                _pts, _fl, plen = HUB_PKT_STRUCT.unpack(hdr)
                pcm = _recv_exact(sock, plen, stop_flag=lambda: self._closed) if plen > 0 else b""
                if pcm is None:
                    break
                if pcm:
                    with self._lock:
                        self._chunks.append(pcm)
        except OSError:
            pass

    def pop_chunks(self) -> list[bytes]:
        with self._lock:
            out = list(self._chunks)
            self._chunks.clear()
            return out

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass


class RemoteSpeakerSink:
    """Streams PCM16 audio out of any device's Speaker resource (local or remote over SSH)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4.0)
        sock.connect((host, int(port)))
        sock.sendall(
            (json.dumps({"op": "play", "kind": "speaker", "sr": self.sample_rate, "ch": self.channels}) + "\n").encode("utf-8"),
        )
        line = _read_line(sock)
        if not line:
            sock.close()
            raise RuntimeError("Empty response from speaker resource service")
        meta = json.loads(line)
        if not meta.get("ok"):
            sock.close()
            raise RuntimeError(str(meta.get("error") or "Speaker resource unavailable"))
        sock.settimeout(None)
        self._sock = sock
        self._t0 = time.monotonic()

    def write_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        pts_us = int((time.monotonic() - self._t0) * 1_000_000)
        pkt = HUB_PKT_STRUCT.pack(pts_us, 1, len(pcm)) + pcm
        self._sock.sendall(pkt)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def resource_clip_get(host: str, port: int, *, timeout: float = 2.0) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall(b'{"op":"clip_get"}\n')
        line = _read_line(s)
        if not line:
            return ""
        data = json.loads(line)
        return str(data.get("text") or "") if data.get("ok") else ""
    except (OSError, json.JSONDecodeError):
        return ""
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_clip_set(host: str, port: int, text: str, *, timeout: float = 2.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall((json.dumps({"op": "clip_set", "text": text}) + "\n").encode("utf-8"))
        line = _read_line(s)
        if not line:
            return False
        data = json.loads(line)
        return bool(data.get("ok"))
    except (OSError, json.JSONDecodeError):
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_ai_infer(
    host: str,
    port: int,
    model_id: str,
    prompt: str,
    *,
    context: str = "",
    image_b64: str = "",
    on_token: Callable[[str], None] | None = None,
    timeout: float = 60.0,
) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        req = {
            "op": "ai_infer",
            "model": model_id,
            "prompt": prompt,
            "context": context,
            "image_b64": image_b64,
        }
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        chunks: list[str] = []
        while True:
            line = _read_line(s, max_bytes=2 * 1024 * 1024)
            if not line:
                break
            obj = json.loads(line)
            if obj.get("ok") is False and "error" in obj:
                raise RuntimeError(str(obj["error"]))
            tok = obj.get("token")
            if isinstance(tok, str) and tok:
                chunks.append(tok)
                if callable(on_token):
                    on_token(tok)
            if obj.get("done"):
                full = obj.get("text")
                return str(full) if full is not None else "".join(chunks)
        return "".join(chunks)
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_list_sessions(host: str, port: int, *, timeout: float = 2.0) -> list[dict[str, Any]]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall(b'{"op":"sessions"}\n')
        line = _read_line(s)
        if not line:
            return []
        data = json.loads(line)
        return list(data.get("sessions") or ()) if data.get("ok") else []
    except (OSError, json.JSONDecodeError):
        return []
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_spawn_session(
    host: str,
    port: int,
    app_id: str = "demo",
    args: str = "",
    *,
    timeout: float = 6.0,
) -> dict[str, Any] | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall((json.dumps({"op": "spawn_session", "app_id": app_id, "args": args}) + "\n").encode("utf-8"))
        line = _read_line(s)
        if not line:
            return None
        data = json.loads(line)
        return data.get("session") if data.get("ok") else None
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def resource_stop_session(host: str, port: int, session_id: str, *, timeout: float = 2.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall((json.dumps({"op": "stop_session", "session_id": session_id}) + "\n").encode("utf-8"))
        line = _read_line(s)
        if not line:
            return False
        data = json.loads(line)
        return bool(data.get("ok"))
    except (OSError, json.JSONDecodeError):
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="textual-wgpu-media-hub")
    p.add_argument("--video", default="", help="V4L2 video device (e.g. /dev/video0)")
    p.add_argument("--mic", action="store_true", help="Run shared microphone hub")
    p.add_argument("--serve-resources", action="store_true", help="Run independent TCP Resource Service")
    p.add_argument("--port", type=int, default=RESOURCE_SERVICE_PORT)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--rate", type=int, default=16000)
    args = p.parse_args(argv)
    if args.serve_resources:
        srv = ResourceServer(port=args.port)
        srv.start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            srv.close()
        return 0
    if args.video:
        return run_video_hub(args.video, width=args.width, height=args.height, fps=args.fps)
    if args.mic:
        return run_mic_hub(sample_rate=args.rate)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
