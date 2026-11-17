"""Shared local Camera & Microphone resource hub (encode once, fan out to N subscribers)."""

from __future__ import annotations

import argparse
import fcntl
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .media_codec import (
    AnnexBChunker,
    _encoder_extra_args,
    codec_flag_to_fmt,
    probe_video_encoder,
)
from .protocol import VID_CODEC_H264, VID_CODEC_HEVC, VID_FLAG_KEYFRAME

HUB_PKT_STRUCT = struct.Struct("<QBI")  # pts_us (u64), flags (u8), len (u32)


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="textual-wgpu-media-hub")
    p.add_argument("--video", default="", help="V4L2 video device (e.g. /dev/video0)")
    p.add_argument("--mic", action="store_true", help="Run shared microphone hub")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--rate", type=int, default=16000)
    args = p.parse_args(argv)
    if args.video:
        return run_video_hub(args.video, width=args.width, height=args.height, fps=args.fps)
    if args.mic:
        return run_mic_hub(sample_rate=args.rate)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
