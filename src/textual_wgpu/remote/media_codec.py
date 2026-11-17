"""Real H.264 / H.265 (HEVC) / AV1 hardware-accelerated video & audio codec helpers."""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import threading
import wave
from collections import deque
from pathlib import Path

from .protocol import VID_CODEC_AV1, VID_CODEC_H264, VID_CODEC_HEVC

_PROBE_CACHE: dict[tuple[str, ...], tuple[str, int] | None] = {}

_CODEC_CANDIDATES: dict[str, list[tuple[str, int, list[str]]]] = {
    "hevc": [
        ("hevc_nvenc", VID_CODEC_HEVC, ["-preset", "p1", "-tune", "ll", "-zerolatency", "1", "-g", "24", "-bf", "0"]),
        ("hevc_vaapi", VID_CODEC_HEVC, ["-g", "24", "-bf", "0"]),
        (
            "libx265",
            VID_CODEC_HEVC,
            ["-preset", "ultrafast", "-tune", "zerolatency", "-x265-params", "log-level=error:repeat-headers=1:keyint=24:bframes=0:frame-threads=1:rc-lookahead=0"],
        ),
    ],
    "h264": [
        ("h264_nvenc", VID_CODEC_H264, ["-preset", "p1", "-tune", "ll", "-zerolatency", "1", "-g", "24", "-bf", "0"]),
        ("h264_vaapi", VID_CODEC_H264, ["-g", "24", "-bf", "0"]),
        ("libx264", VID_CODEC_H264, ["-preset", "ultrafast", "-tune", "zerolatency", "-g", "24", "-bf", "0"]),
    ],
    "av1": [
        ("av1_nvenc", VID_CODEC_AV1, ["-preset", "p1", "-g", "24"]),
        ("libsvtav1", VID_CODEC_AV1, ["-preset", "10", "-g", "24"]),
    ],
}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_video_encoder(preferred: tuple[str, ...] = ("hevc", "h264")) -> tuple[str, int] | None:
    """Return ``(ffmpeg_encoder_name, VID_CODEC_*)`` for the best working encoder."""
    key = tuple(preferred)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    ff = shutil.which("ffmpeg")
    if not ff:
        _PROBE_CACHE[key] = None
        return None
    for family in preferred:
        for enc_name, cflag, _extra in _CODEC_CANDIDATES.get(family, []):
            cmd = [
                ff, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:r=24",
                "-frames:v", "1", "-pix_fmt", "yuv420p",
            ]
            if enc_name.endswith("_vaapi"):
                cmd = [
                    ff, "-y", "-loglevel", "error",
                    "-vaapi_device", "/dev/dri/renderD128",
                    "-f", "lavfi", "-i", "color=c=black:s=256x256:r=24",
                    "-frames:v", "1", "-vf", "format=nv12,hwupload",
                ]
            cmd.extend(["-c:v", enc_name, "-f", "null", "-"])
            try:
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4.0)
                if res.returncode == 0:
                    _PROBE_CACHE[key] = (enc_name, cflag)
                    return _PROBE_CACHE[key]
            except (OSError, subprocess.SubprocessError):
                continue
    _PROBE_CACHE[key] = None
    return None


def _encoder_extra_args(enc_name: str) -> list[str]:
    for cands in _CODEC_CANDIDATES.values():
        for name, _flag, extra in cands:
            if name == enc_name:
                return list(extra)
    return []


def codec_flag_to_fmt(codec_flag: int) -> str:
    if codec_flag == VID_CODEC_HEVC:
        return "hevc"
    if codec_flag == VID_CODEC_AV1:
        return "ivf"
    return "h264"


def _is_annexb_keyframe(au: bytes, codec_flag: int) -> bool:
    i = 0
    n = len(au)
    while i + 4 < n:
        if au[i : i + 4] == b"\x00\x00\x00\x01":
            hdr = au[i + 4]
            if codec_flag == VID_CODEC_HEVC:
                nal_type = (hdr >> 1) & 0x3F
                if nal_type in (19, 20, 21, 32, 33, 34):
                    return True
            else:
                nal_type = hdr & 0x1F
                if nal_type in (5, 7, 8):
                    return True
            i += 4
        else:
            i += 1
    return False


def _is_vcl_nal(nal: bytes, codec_flag: int) -> bool:
    if len(nal) < 5:
        return False
    hdr = nal[4]
    if codec_flag == VID_CODEC_HEVC:
        return ((hdr >> 1) & 0x3F) < 32
    return (hdr & 0x1F) in (1, 2, 3, 4, 5)


class AnnexBChunker:
    """Splits streaming Annex-B NALUs into per-frame Access Units (headers attached to VCL)."""

    def __init__(self, codec_flag: int) -> None:
        self.codec_flag = codec_flag
        self._buf = bytearray()
        self._pending_headers = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[bytes, bool]]:
        self._buf.extend(chunk)
        aus: list[tuple[bytes, bool]] = []
        sc = b"\x00\x00\x00\x01"
        while True:
            pos0 = self._buf.find(sc)
            if pos0 < 0:
                break
            if pos0 > 0:
                del self._buf[:pos0]
            pos1 = self._buf.find(sc, 4)
            if pos1 < 0:
                break
            nal = bytes(self._buf[:pos1])
            del self._buf[:pos1]
            if _is_vcl_nal(nal, self.codec_flag):
                if self._pending_headers:
                    au = bytes(self._pending_headers) + nal
                    self._pending_headers.clear()
                else:
                    au = nal
                aus.append((au, _is_annexb_keyframe(au, self.codec_flag)))
            else:
                self._pending_headers.extend(nal)
        return aus

    def flush(self) -> list[tuple[bytes, bool]]:
        if not self._buf:
            return []
        nal = bytes(self._buf)
        self._buf.clear()
        if _is_vcl_nal(nal, self.codec_flag):
            au = bytes(self._pending_headers) + nal if self._pending_headers else nal
            self._pending_headers.clear()
            return [(au, _is_annexb_keyframe(au, self.codec_flag))]
        return []


class StreamVideoEncoder:
    """Persistent low-latency ``ffmpeg`` encoder: RGBA frames → Annex-B NALUs."""

    def __init__(
        self,
        width: int,
        height: int,
        *,
        fps: int = 24,
        preferred: tuple[str, ...] = ("hevc", "h264"),
        on_au: Any = None,
    ) -> None:
        self.width = max(16, int(width) & ~1)
        self.height = max(16, int(height) & ~1)
        self.fps = max(1, int(fps))
        self._on_au = on_au
        found = probe_video_encoder(preferred)
        if found is None:
            raise RuntimeError("No working H.264/HEVC encoder found")
        self.encoder_name, self.codec_flag = found
        min_w, min_h = (160, 128) if self.encoder_name.endswith(("_vaapi", "_nvenc")) else (16, 16)
        self.width = max(min_w, int(width) & ~1)
        self.height = max(min_h, int(height) & ~1)
        self.fps = max(1, int(fps))
        self._on_au = on_au
        self._fmt = codec_flag_to_fmt(self.codec_flag)
        self._aus: deque[tuple[bytes, bool]] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False

        ff = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-fflags", "nobuffer", "-flush_packets", "1",
            "-f", "rawvideo", "-pix_fmt", "rgba",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "pipe:0",
        ]
        if self.encoder_name.endswith("_vaapi"):
            cmd = [
                ff, "-y", "-loglevel", "error",
                "-vaapi_device", "/dev/dri/renderD128",
                "-fflags", "nobuffer", "-flush_packets", "1",
                "-f", "rawvideo", "-pix_fmt", "rgba",
                "-s", f"{self.width}x{self.height}",
                "-r", str(self.fps),
                "-i", "pipe:0",
                "-vf", "format=nv12,hwupload",
            ]
        else:
            cmd.extend(["-pix_fmt", "yuv420p"])
        cmd.extend(["-c:v", self.encoder_name])
        cmd.extend(_encoder_extra_args(self.encoder_name))
        cmd.extend(["-f", self._fmt, "pipe:1"])

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._chunker = AnnexBChunker(self.codec_flag)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        out = self._proc.stdout
        if out is None:
            return
        fd = out.fileno()
        try:
            while not self._closed:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                aus = self._chunker.feed(chunk)
                if aus:
                    with self._lock:
                        self._aus.extend(aus)
                    if callable(self._on_au):
                        self._on_au()
            for au in self._chunker.flush():
                with self._lock:
                    self._aus.append(au)
                if callable(self._on_au):
                    self._on_au()
        except Exception:  # noqa: BLE001
            pass

    def encode_rgba(self, rgba: bytes, src_size: tuple[int, int]) -> None:
        if self._closed or self._proc.stdin is None or self._proc.poll() is not None:
            return
        sw, sh = src_size
        if (sw, sh) != (self.width, self.height):
            from PIL import Image

            im = Image.frombytes("RGBA", (sw, sh), rgba).resize(
                (self.width, self.height), Image.Resampling.BILINEAR,
            )
            rgba = im.tobytes("raw", "RGBA")
        try:
            self._proc.stdin.write(rgba)
            self._proc.stdin.flush()
        except OSError:
            self._closed = True

    def drain_aus(self) -> list[tuple[bytes, bool]]:
        with self._lock:
            out = list(self._aus)
            self._aus.clear()
            return out

    def close(self) -> None:
        self._closed = True
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass


class StreamVideoDecoder:
    """Hardware-accelerated ``ffmpeg`` decoder: Annex-B NALUs → RGBA frames via PAM pipe."""

    def __init__(self, codec_flag: int) -> None:
        self.codec_flag = codec_flag
        self._fmt = codec_flag_to_fmt(codec_flag)
        self._lock = threading.Lock()
        self._latest: tuple[int, int, bytes] | None = None
        self._closed = False

        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError("ffmpeg not found")
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-flags", "low_delay", "-flush_packets", "1",
            "-probesize", "32", "-analyzeduration", "0", "-threads", "1",
            "-f", self._fmt, "-i", "pipe:0",
            "-f", "image2pipe", "-vcodec", "pam", "-pix_fmt", "rgba",
            "pipe:1",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        out = self._proc.stdout
        if out is None:
            return
        fd = out.fileno()
        buf = bytearray()
        try:
            while not self._closed:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    end_hdr = buf.find(b"ENDHDR\n")
                    if end_hdr < 0:
                        break
                    hdr_txt = buf[:end_hdr].decode("ascii", errors="ignore")
                    w = h = 0
                    for ln in hdr_txt.splitlines():
                        if ln.startswith("WIDTH "):
                            w = int(ln.split()[1])
                        elif ln.startswith("HEIGHT "):
                            h = int(ln.split()[1])
                    body_start = end_hdr + 7
                    need = w * h * 4
                    if w <= 0 or h <= 0:
                        del buf[:body_start]
                        continue
                    if len(buf) < body_start + need:
                        break
                    rgba = bytes(buf[body_start : body_start + need])
                    del buf[: body_start + need]
                    with self._lock:
                        self._latest = (w, h, rgba)
        except Exception:  # noqa: BLE001
            pass

    def feed(self, au: bytes) -> None:
        if self._closed or self._proc.stdin is None or self._proc.poll() is not None:
            return
        try:
            self._proc.stdin.write(au)
            self._proc.stdin.flush()
        except OSError:
            self._closed = True

    def latest_frame(self) -> tuple[int, int, bytes] | None:
        with self._lock:
            return self._latest

    def close(self) -> None:
        self._closed = True
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def probe_media_file(path: Path | str) -> dict:
    """Return basic stream metadata via ``ffprobe`` (width, height, codec_name, fps)."""
    ffp = shutil.which("ffprobe")
    if not ffp:
        return {}
    try:
        res = subprocess.run(
            [
                ffp, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res.returncode != 0:
            return {}
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return {}
        st = streams[0]
        fps_s = str(st.get("r_frame_rate") or "24/1")
        if "/" in fps_s:
            num, _, den = fps_s.partition("/")
            fps = float(num) / max(1.0, float(den))
        else:
            fps = float(fps_s)
        return {
            "codec": str(st.get("codec_name") or "").lower(),
            "width": int(st.get("width") or 0),
            "height": int(st.get("height") or 0),
            "fps": max(1.0, min(60.0, fps)),
        }
    except Exception:  # noqa: BLE001
        return {}


def compute_pcm_envelope(pcm: bytes, *, buckets: int = 48) -> list[float]:
    n_samples = len(pcm) // 2
    if n_samples < 1:
        return [0.1] * buckets
    step = max(1, n_samples // buckets)
    out: list[float] = []
    for b in range(buckets):
        start = min(n_samples - 1, b * step)
        end = min(n_samples, start + step)
        acc = 0.0
        cnt = 0
        # Subsample every 4th sample for fast RMS
        for idx in range(start, end, max(1, (end - start) // 32)):
            (val,) = struct.unpack_from("<h", pcm, idx * 2)
            acc += (val / 32768.0) ** 2
            cnt += 1
        rms = math.sqrt(acc / max(1, cnt))
        out.append(round(min(1.0, 0.08 + rms * 2.2), 3))
    return out


def _android_media_helper() -> Any | None:
    try:
        from com.textualwgpu.app import LocalMediaHelper  # type: ignore[import-not-found]

        return LocalMediaHelper
    except Exception:  # noqa: BLE001
        return None


def decode_audio_file_pcm(
    path: Path | str,
    *,
    sample_rate: int = 22050,
    channels: int = 1,
) -> tuple[bytes, int, int, list[float], float]:
    """Decode any audio/video file's audio track to PCM16 + RMS waveform + duration."""
    p = Path(path)
    if p.suffix.lower() == ".wav":
        with wave.open(str(p), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            if sw == 2:
                dur = n_frames / max(1, sr)
                return raw, sr, ch, compute_pcm_envelope(raw), dur

    ff = shutil.which("ffmpeg")
    if not ff:
        helper = _android_media_helper()
        if helper is not None:
            blob = bytes(helper.decodeAudioPcm(str(p)) or b"")
            if len(blob) > 8:
                sr, ch = struct.unpack_from("<II", blob, 0)
                pcm = blob[8:]
                dur = (len(pcm) // (2 * max(1, ch))) / max(1, sr)
                return pcm, int(sr), int(ch), compute_pcm_envelope(pcm), dur
        raise RuntimeError("ffmpeg or Android MediaCodec required to decode audio")
    res = subprocess.run(
        [
            ff, "-y", "-loglevel", "error",
            "-i", str(p),
            "-vn",
            "-f", "s16le",
            "-ac", str(max(1, int(channels))),
            "-ar", str(max(8000, int(sample_rate))),
            "pipe:1",
        ],
        capture_output=True,
        timeout=30.0,
    )
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError(f"Audio decode failed: {p.name}")
    pcm = res.stdout
    dur = (len(pcm) // (2 * max(1, int(channels)))) / max(1, int(sample_rate))
    return pcm, int(sample_rate), int(channels), compute_pcm_envelope(pcm), dur


class FileVideoStreamer:
    """Reads a video file into Annex-B NALUs (zero-encode passthrough or transcode) + local PAM preview."""

    def __init__(
        self,
        path: Path | str,
        *,
        preferred: tuple[str, ...] = ("hevc", "h264"),
        max_w: int = 1280,
        max_h: int = 720,
    ) -> None:
        self.path = Path(path)
        meta = probe_media_file(self.path)
        self.fps = float(meta.get("fps") or 24.0)
        src_codec = str(meta.get("codec") or "")
        src_w = int(meta.get("width") or 0)
        src_h = int(meta.get("height") or 0)
        self._aus: deque[tuple[bytes, bool]] = deque(maxlen=256)
        self._rgba_frames: deque[tuple[int, int, bytes]] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False
        self._procs: list[subprocess.Popen] = []
        self._android_sess: Any = None

        ff = shutil.which("ffmpeg")
        if not ff:
            helper = _android_media_helper()
            if helper is None:
                raise RuntimeError("ffmpeg or Android MediaExtractor required for video file playback")
            sess = helper.openVideo(str(self.path))
            self._android_sess = sess
            self.codec_flag = int(sess.codecFlag())
            threading.Thread(target=self._read_android_loop, args=(sess,), daemon=True).start()
            return

        # 1) Annex-B NALU stream for remote hardware decode (passthrough if already matching codec!)
        passthrough = (
            src_codec in preferred
            and src_codec in ("h264", "hevc")
            and src_w <= 1920
            and src_h <= 1080
        )
        if passthrough:
            self.codec_flag = VID_CODEC_HEVC if src_codec == "hevc" else VID_CODEC_H264
            bsf = "hevc_mp4toannexb" if src_codec == "hevc" else "h264_mp4toannexb"
            cmd_nalu = [
                ff, "-y", "-loglevel", "error", "-stream_loop", "-1", "-re",
                "-i", str(self.path),
                "-an", "-c:v", "copy", "-bsf:v", bsf,
                "-f", src_codec, "pipe:1",
            ]
        else:
            found = probe_video_encoder(preferred)
            if found is None:
                raise RuntimeError("No working H.264/HEVC encoder")
            enc_name, self.codec_flag = found
            fmt = codec_flag_to_fmt(self.codec_flag)
            vf = f"scale='min({max_w},iw)':'min({max_h},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
            cmd_nalu = [ff, "-y", "-loglevel", "error", "-stream_loop", "-1", "-re", "-i", str(self.path), "-an"]
            if enc_name.endswith("_vaapi"):
                cmd_nalu = [
                    ff, "-y", "-loglevel", "error",
                    "-vaapi_device", "/dev/dri/renderD128",
                    "-stream_loop", "-1", "-re", "-i", str(self.path), "-an",
                    "-vf", f"{vf},format=nv12,hwupload",
                ]
            else:
                cmd_nalu.extend(["-vf", vf, "-pix_fmt", "yuv420p"])
            cmd_nalu.extend(["-c:v", enc_name, *_encoder_extra_args(enc_name), "-f", fmt, "pipe:1"])

        p_nalu = subprocess.Popen(cmd_nalu, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._procs.append(p_nalu)
        self._chunker = AnnexBChunker(self.codec_flag)
        threading.Thread(target=self._read_nalu_loop, args=(p_nalu,), daemon=True).start()

        # 2) Local PAM RGBA stream (lightweight preview for local WgpuDriver)
        cmd_pam = [
            ff, "-y", "-loglevel", "error", "-stream_loop", "-1", "-re",
            "-i", str(self.path), "-an",
            "-vf", "scale='min(640,iw)':'min(400,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-f", "image2pipe", "-vcodec", "pam", "-pix_fmt", "rgba", "pipe:1",
        ]
        p_pam = subprocess.Popen(cmd_pam, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._procs.append(p_pam)
        threading.Thread(target=self._read_pam_loop, args=(p_pam,), daemon=True).start()

    def _read_android_loop(self, sess: Any) -> None:
        try:
            while not self._closed:
                pkt = sess.nextFrame()
                if pkt is None:
                    continue
                raw = bytes(pkt)
                if len(raw) <= 9:
                    continue
                is_kf = bool(raw[0])
                au = raw[9:]
                with self._lock:
                    self._aus.append((au, is_kf))
        except Exception:  # noqa: BLE001
            pass

    def _read_nalu_loop(self, proc: subprocess.Popen) -> None:
        out = proc.stdout
        if out is None:
            return
        fd = out.fileno()
        try:
            while not self._closed:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                aus = self._chunker.feed(chunk)
                if aus:
                    with self._lock:
                        self._aus.extend(aus)
            for au in self._chunker.flush():
                with self._lock:
                    self._aus.append(au)
        except Exception:  # noqa: BLE001
            pass

    def _read_pam_loop(self, proc: subprocess.Popen) -> None:
        out = proc.stdout
        if out is None:
            return
        fd = out.fileno()
        buf = bytearray()
        try:
            while not self._closed:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    end_hdr = buf.find(b"ENDHDR\n")
                    if end_hdr < 0:
                        break
                    hdr_txt = buf[:end_hdr].decode("ascii", errors="ignore")
                    w = h = 0
                    for ln in hdr_txt.splitlines():
                        if ln.startswith("WIDTH "):
                            w = int(ln.split()[1])
                        elif ln.startswith("HEIGHT "):
                            h = int(ln.split()[1])
                    body_start = end_hdr + 7
                    need = w * h * 4
                    if w <= 0 or h <= 0:
                        del buf[:body_start]
                        continue
                    if len(buf) < body_start + need:
                        break
                    rgba = bytes(buf[body_start : body_start + need])
                    del buf[: body_start + need]
                    with self._lock:
                        self._rgba_frames.append((w, h, rgba))
        except Exception:  # noqa: BLE001
            pass

    def pop_aus(self) -> list[tuple[bytes, bool]]:
        with self._lock:
            out = list(self._aus)
            self._aus.clear()
            return out

    def pop_rgba(self) -> tuple[int, int, bytes] | None:
        with self._lock:
            if not self._rgba_frames:
                return None
            # Keep latest frame to stay real-time
            while len(self._rgba_frames) > 1:
                self._rgba_frames.popleft()
            return self._rgba_frames.popleft()

    def close(self) -> None:
        self._closed = True
        if self._android_sess is not None:
            try:
                self._android_sess.close()
            except Exception:  # noqa: BLE001
                pass
            self._android_sess = None
        for p in self._procs:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._procs.clear()


def pcm_rms_level(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n < 1:
        return 0.0
    step = max(1, n // 32)
    acc = 0.0
    cnt = 0
    for i in range(0, n, step):
        (v,) = struct.unpack_from("<h", pcm, i * 2)
        acc += (v / 32768.0) ** 2
        cnt += 1
    return min(1.0, math.sqrt(acc / max(1, cnt)) * 3.0)


def list_linux_cameras() -> list[tuple[str, str]]:
    """Return ``[('/dev/video0', 'Camera Name'), ...]`` for V4L2 capture nodes."""
    v4l_dir = Path("/sys/class/video4linux")
    out: list[tuple[str, str]] = []
    if v4l_dir.is_dir():
        for node in sorted(v4l_dir.iterdir(), key=lambda p: p.name):
            if not node.name.startswith("video"):
                continue
            dev_path = Path("/dev") / node.name
            if not dev_path.exists():
                continue
            idx_file = node / "index"
            try:
                idx = idx_file.read_text(encoding="utf-8").strip() if idx_file.is_file() else "0"
            except OSError:
                idx = "0"
            if idx != "0":
                continue
            try:
                name = (node / "name").read_text(encoding="utf-8").strip() or node.name
            except OSError:
                name = node.name
            out.append((str(dev_path), name))
    if not out and Path("/dev/video0").exists():
        out.append(("/dev/video0", "Camera"))
    return out


class LocalCameraStreamer:
    """Subscribes to the shared local camera resource hub (Linux media_hub or Android Camera2 hub)."""

    def __init__(
        self,
        *,
        width: int = 640,
        height: int = 360,
        fps: int = 24,
        facing: int = 1,
        device: str | None = None,
        emit_rgba: bool = False,
        on_frame: Any = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.facing = int(facing)
        self.rotation = 0
        self._emit_rgba = bool(emit_rgba)
        self._on_frame = on_frame
        self._aus: deque[tuple[bytes, bool]] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False
        self._sock: Any = None
        self._decoder: StreamVideoDecoder | None = None
        self._android_sub_id: int | None = None

        helper = _android_media_helper()
        if helper is not None and not shutil.which("ffmpeg"):
            from com.textualwgpu.app import LocalPythonHost  # type: ignore[import-not-found]

            ctx = LocalPythonHost.appContext
            if ctx is None:
                raise RuntimeError("Android context not initialized")
            sub_id = int(helper.subscribeCamera(ctx, self.facing))
            if sub_id <= 0:
                raise RuntimeError("Android camera permission or hardware unavailable")
            self._android_sub_id = sub_id
            self.codec_flag = VID_CODEC_H264
            threading.Thread(target=self._read_android_loop, args=(helper, sub_id), daemon=True).start()
            return

        dev = device or (list_linux_cameras()[0][0] if list_linux_cameras() else "/dev/video0")
        self.device = dev
        if not Path(dev).exists():
            raise RuntimeError(f"No {dev} camera device found")

        from .media_hub import connect_camera_hub

        self._sock = connect_camera_hub(dev, width=self.width, height=self.height, fps=self.fps)
        self.codec_flag = VID_CODEC_H264
        threading.Thread(target=self._read_hub_loop, daemon=True).start()

    def _read_android_loop(self, helper: Any, sub_id: int) -> None:
        try:
            while not self._closed:
                pkt = helper.takeCameraFrame(sub_id)
                if pkt is None:
                    break
                raw = bytes(pkt)
                if len(raw) <= 1:
                    continue
                hdr = raw[0]
                is_kf = bool(hdr & 0x01)
                self.rotation = (hdr >> 1) & 0x03
                au = raw[1:]
                with self._lock:
                    self._aus.append((au, is_kf))
                if callable(self._on_frame):
                    self._on_frame()
        except Exception:  # noqa: BLE001
            pass

    def _read_hub_loop(self) -> None:
        from .media_hub import HUB_PKT_STRUCT

        sock = self._sock
        if sock is None:
            return
        hdr_sz = HUB_PKT_STRUCT.size

        def _recv_exact(n: int) -> bytes | None:
            buf = bytearray()
            while len(buf) < n and not self._closed:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf.extend(chunk)
            return bytes(buf) if len(buf) == n else None

        try:
            while not self._closed:
                hdr = _recv_exact(hdr_sz)
                if hdr is None:
                    break
                _pts, flags, plen = HUB_PKT_STRUCT.unpack(hdr)
                au = _recv_exact(plen) if plen > 0 else b""
                if au is None:
                    break
                is_kf = bool(flags & 0x01)
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
        if self._android_sub_id is not None:
            helper = _android_media_helper()
            if helper is not None:
                try:
                    helper.unsubscribeCamera(self._android_sub_id)
                except Exception:  # noqa: BLE001
                    pass
            self._android_sub_id = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._decoder is not None:
            try:
                self._decoder.close()
            except Exception:  # noqa: BLE001
                pass
            self._decoder = None


class LocalMicCapture:
    """Subscribes to the shared local microphone hub (Linux media_hub or Android AudioRecord hub)."""

    def __init__(self, *, sample_rate: int = 16000, channels: int = 1, chunk_ms: int = 80) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.chunk_bytes = max(640, (self.sample_rate * self.channels * 2 * int(chunk_ms)) // 1000)
        self._chunks: deque[bytes] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False
        self._sock: Any = None
        self._android_sub_id: int | None = None

        helper = _android_media_helper()
        if helper is not None and not shutil.which("pw-record") and not shutil.which("arecord"):
            from com.textualwgpu.app import LocalPythonHost  # type: ignore[import-not-found]

            ctx = LocalPythonHost.appContext
            if ctx is not None:
                sub_id = int(helper.subscribeMic(ctx, self.sample_rate))
                if sub_id > 0:
                    self._android_sub_id = sub_id
                    threading.Thread(target=self._read_android_loop, args=(helper, sub_id), daemon=True).start()
                    return
            raise RuntimeError("Android microphone permission or hardware unavailable")

        from .media_hub import connect_mic_hub

        self._sock = connect_mic_hub(sample_rate=self.sample_rate)
        threading.Thread(target=self._read_hub_loop, daemon=True).start()

    def _read_hub_loop(self) -> None:
        from .media_hub import HUB_PKT_STRUCT

        sock = self._sock
        if sock is None:
            return
        hdr_sz = HUB_PKT_STRUCT.size

        def _recv_exact(n: int) -> bytes | None:
            buf = bytearray()
            while len(buf) < n and not self._closed:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf.extend(chunk)
            return bytes(buf) if len(buf) == n else None

        try:
            while not self._closed:
                hdr = _recv_exact(hdr_sz)
                if hdr is None:
                    break
                _pts, _fl, plen = HUB_PKT_STRUCT.unpack(hdr)
                pcm = _recv_exact(plen) if plen > 0 else b""
                if pcm is None:
                    break
                if pcm:
                    with self._lock:
                        self._chunks.append(pcm)
        except OSError:
            pass

    def _read_android_loop(self, helper: Any, sub_id: int) -> None:
        try:
            while not self._closed:
                pcm = helper.takeMicChunk(sub_id)
                if pcm is None:
                    break
                b = bytes(pcm)
                if b:
                    with self._lock:
                        self._chunks.append(b)
        except Exception:  # noqa: BLE001
            pass

    def pop_chunks(self) -> list[bytes]:
        with self._lock:
            out = list(self._chunks)
            self._chunks.clear()
            return out

    def close(self) -> None:
        self._closed = True
        if self._android_sub_id is not None:
            helper = _android_media_helper()
            if helper is not None:
                try:
                    helper.unsubscribeMic(self._android_sub_id)
                except Exception:  # noqa: BLE001
                    pass
            self._android_sub_id = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
