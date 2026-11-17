"""Zero-JSON binary wire protocol for remote ``textual-wgpu`` surfaces."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

try:
    import zstandard as _zstd  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _zstd = None

PROTOCOL_VERSION = 1

FLAG_ZSTD = 0x01
FLAG_ZLIB = 0x02
FLAG_CHUNK = 0x04
FLAG_CHUNK_END = 0x08

HEADER_STRUCT = struct.Struct("<BBHIII")  # ver_flags, prio, op, surf_id, seq, payload_len (16B)
STYLE_STRUCT = struct.Struct("<HIIBBB")   # style_id, fg, bg, flags, font_id, size (13B)
BLOB_HDR_STRUCT = struct.Struct("<16sHH") # hash16, width, height

OP_RECT = 1
OP_LINE = 2
OP_TEXT = 3
OP_IMG_BLIT = 4
OP_CLIP_PUSH = 5
OP_CLIP_POP = 6
OP_CLEAR_RECT = 7

RECT_FLAG_FILL = 0x01
RECT_FLAG_OUTLINE = 0x02

RECT_STRUCT = struct.Struct("<BBhhhhIIH")     # op, flags, x0, y0, x1, y1, fill, outline, width (20B)
LINE_STRUCT = struct.Struct("<BhhhhIH")       # op, x0, y0, x1, y1, fill, width (15B)
TEXT_STRUCT = struct.Struct("<BhhHH")         # op, x, y, style_id, byte_len (9B)
IMG_BLIT_STRUCT = struct.Struct("<B16shhhhhh")  # op, hash16, dx, dy, sx0, sy0, sx1, sy1 (29B)
CLIP_PUSH_STRUCT = struct.Struct("<Bhhhh")    # op, x0, y0, x1, y1 (9B)
CLIP_POP_STRUCT = struct.Struct("<B")         # op (1B)
CLEAR_RECT_STRUCT = struct.Struct("<Bhhhh")   # op, x0, y0, x1, y1 (9B)

POINTER_DOWN = 1
POINTER_MOVE = 2
POINTER_UP = 3

POINTER_STRUCT = struct.Struct("<BffB")       # kind, x, y, button
WHEEL_STRUCT = struct.Struct("<ffff")         # x, y, dx, dy
RESIZE_STRUCT = struct.Struct("<II")          # w, h
HELLO_STRUCT = struct.Struct("<f16sH")        # width_scale, mono_font_hash, title_len
CAPS_STRUCT = struct.Struct("<BIH")           # has_gpu, vram_mb, codecs_len
PLANE_RECT_STRUCT = struct.Struct("<hhhhBB")  # x0, y0, x1, y1, kind, flags
BUF_WRITE_STRUCT = struct.Struct("<II")       # buf_id, offset
AUDIO_HDR_STRUCT = struct.Struct("<IBB")      # sample_rate, channels, flags
LAYER_HDR_STRUCT = struct.Struct("<HhhhhI")   # id_len, rx0, ry0, rx1, ry1, ops_len
NALU_HDR_STRUCT = struct.Struct("<QB16f")     # pts_us, flags, cam_mat4 (73B)

PLANE_KIND_VIDEO = 1
PLANE_KIND_3D_SERVER = 2
PLANE_KIND_3D_CLIENT = 3

VID_FLAG_KEYFRAME = 0x01
VID_FLAG_RAW_RGBA = 0x02
VID_FLAG_HAS_DIMS = 0x04
VID_CODEC_RGBA = 0x00
VID_CODEC_H264 = 0x10
VID_CODEC_HEVC = 0x20
VID_CODEC_AV1 = 0x30
VID_CODEC_MASK = 0xF0


class Priority(IntEnum):
    P0_CONTROL = 0
    P1_UI = 1
    P2_MEDIA = 2
    P3_BULK = 3


class Op(IntEnum):
    HELLO = 0x01
    CAPS = 0x02
    RESIZE = 0x03
    FS_GET = 0x04
    INPUT_KEY = 0x10
    INPUT_CHAR = 0x11
    INPUT_POINTER = 0x12
    INPUT_WHEEL = 0x13
    CLIP_SET = 0x14
    CLIP_PASTE = 0x15
    OPEN_CLIENT_FILE = 0x16
    CLIENT_FILE_DATA = 0x17
    LIST_APPS = 0x18
    APP_LIST = 0x19
    LAUNCH_APP = 0x1A
    LAUNCH_STATUS = 0x1B
    FS_LIST = 0x1C
    FS_ENTRIES = 0x1D
    PING = 0x1E
    PONG = 0x1F

    STYLE_DEF = 0x20
    LAYER_PATCH = 0x21
    LAYER_DROP = 0x22
    FRAME_COMMIT = 0x23
    FX_SET = 0x24
    TITLE_SET = 0x25

    PUT_BLOB = 0x30
    BLOB_ACK = 0x31
    BUF_WRITE = 0x40
    VID_NALU = 0x41
    PLANE_RECT = 0x42
    AUDIO_PCM = 0x43
    AUDIO_STOP = 0x44


def pack_rgba(color: tuple[int, int, int, int] | tuple[int, int, int] | None) -> int:
    if color is None:
        return 0
    r = int(color[0]) & 0xFF
    g = int(color[1]) & 0xFF
    b = int(color[2]) & 0xFF
    a = int(color[3]) & 0xFF if len(color) > 3 else 255
    return (r << 24) | (g << 16) | (b << 8) | a


def unpack_rgba(val: int) -> tuple[int, int, int, int]:
    return (
        (val >> 24) & 0xFF,
        (val >> 16) & 0xFF,
        (val >> 8) & 0xFF,
        val & 0xFF,
    )


def clamp_i16(v: int | float) -> int:
    iv = int(round(v))
    if iv < -32768:
        return -32768
    if iv > 32767:
        return 32767
    return iv


def compress_payload(raw: bytes, *, allow_compress: bool = True, level: int = 3) -> tuple[bytes, int]:
    """Compress payload with zstd (preferred) or zlib when worthwhile."""
    if not allow_compress or len(raw) < 64:
        return raw, 0
    if _zstd is not None:
        comp = _zstd.ZstdCompressor(level=level).compress(raw)
        if len(comp) < len(raw):
            return comp, FLAG_ZSTD
        return raw, 0
    comp = zlib.compress(raw, level=min(6, max(1, level)))
    if len(comp) < len(raw):
        return comp, FLAG_ZLIB
    return raw, 0


def decompress_payload(payload: bytes, flags: int) -> bytes:
    if flags & FLAG_ZSTD:
        if _zstd is None:
            raise RuntimeError("Received zstd-compressed frame without zstandard installed")
        return _zstd.ZstdDecompressor().decompress(payload)
    if flags & FLAG_ZLIB:
        return zlib.decompress(payload)
    return payload


@dataclass(slots=True)
class Frame:
    op: int
    payload: bytes
    prio: int = Priority.P1_UI
    surf_id: int = 0
    seq: int = 0
    flags: int = 0
    version: int = PROTOCOL_VERSION

    def encode(self, *, compress: bool = True) -> bytes:
        allow = compress and self.prio != Priority.P2_MEDIA and self.op != Op.PUT_BLOB
        body, cflag = compress_payload(self.payload, allow_compress=allow)
        ver_flags = ((self.version & 0x0F) << 4) | ((self.flags | cflag) & 0x0F)
        hdr = HEADER_STRUCT.pack(
            ver_flags,
            int(self.prio) & 0xFF,
            int(self.op) & 0xFFFF,
            int(self.surf_id) & 0xFFFFFFFF,
            int(self.seq) & 0xFFFFFFFF,
            len(body),
        )
        return hdr + body

    @classmethod
    def decode_parts(cls, header_bytes: bytes, body: bytes) -> Frame:
        ver_flags, prio, op, surf_id, seq, _plen = HEADER_STRUCT.unpack(header_bytes)
        version = (ver_flags >> 4) & 0x0F
        flags = ver_flags & 0x0F
        raw = decompress_payload(body, flags) if not (flags & FLAG_CHUNK) else body
        return cls(
            op=op,
            payload=raw,
            prio=prio,
            surf_id=surf_id,
            seq=seq,
            flags=flags & ~(FLAG_ZSTD | FLAG_ZLIB),
            version=version,
        )


def encode_hello(*, width_scale: float = 1.0, mono_font_hash: bytes = b"\x00" * 16, title: str = "") -> bytes:
    tb = title.encode("utf-8")[:65535]
    fh = (mono_font_hash + b"\x00" * 16)[:16]
    return HELLO_STRUCT.pack(float(width_scale), fh, len(tb)) + tb


def decode_hello(data: bytes) -> tuple[float, bytes, str]:
    ws, fh, tlen = HELLO_STRUCT.unpack_from(data, 0)
    off = HELLO_STRUCT.size
    return float(ws), fh, data[off : off + tlen].decode("utf-8", errors="replace")


def encode_style_defs(styles: list[tuple[int, int, int, int, int, int]]) -> bytes:
    buf = bytearray(2 + len(styles) * STYLE_STRUCT.size)
    struct.pack_into("<H", buf, 0, len(styles))
    off = 2
    for sid, fg, bg, flags, font_id, size in styles:
        STYLE_STRUCT.pack_into(buf, off, sid, fg, bg, flags, font_id, size)
        off += STYLE_STRUCT.size
    return bytes(buf)


def decode_style_defs(data: bytes) -> list[tuple[int, int, int, int, int, int]]:
    (count,) = struct.unpack_from("<H", data, 0)
    off = 2
    out: list[tuple[int, int, int, int, int, int]] = []
    for _ in range(count):
        out.append(STYLE_STRUCT.unpack_from(data, off))
        off += STYLE_STRUCT.size
    return out


def encode_layer_patch(layer_id: str, rect: tuple[int, int, int, int], ops: bytes) -> bytes:
    lid = layer_id.encode("utf-8")
    hdr = LAYER_HDR_STRUCT.pack(
        len(lid),
        clamp_i16(rect[0]),
        clamp_i16(rect[1]),
        clamp_i16(rect[2]),
        clamp_i16(rect[3]),
        len(ops),
    )
    return hdr + lid + ops


def decode_layer_patch(data: bytes) -> tuple[str, tuple[int, int, int, int], bytes]:
    id_len, x0, y0, x1, y1, ops_len = LAYER_HDR_STRUCT.unpack_from(data, 0)
    off = LAYER_HDR_STRUCT.size
    lid = data[off : off + id_len].decode("utf-8")
    off += id_len
    return lid, (x0, y0, x1, y1), data[off : off + ops_len]


def encode_frame_commit(layer_ids: list[str], bg_rgba: int) -> bytes:
    parts = [struct.pack("<IH", bg_rgba, len(layer_ids))]
    for lid in layer_ids:
        b = lid.encode("utf-8")
        parts.append(struct.pack("<H", len(b)))
        parts.append(b)
    return b"".join(parts)


def decode_frame_commit(data: bytes) -> tuple[list[str], int]:
    bg_rgba, count = struct.unpack_from("<IH", data, 0)
    off = 6
    ids: list[str] = []
    for _ in range(count):
        (n,) = struct.unpack_from("<H", data, off)
        off += 2
        ids.append(data[off : off + n].decode("utf-8"))
        off += n
    return ids, bg_rgba


def encode_put_blob(hash16: bytes, w: int, h: int, payload: bytes) -> bytes:
    h16 = (hash16 + b"\x00" * 16)[:16]
    return BLOB_HDR_STRUCT.pack(h16, max(0, int(w)), max(0, int(h))) + payload


def decode_put_blob(data: bytes) -> tuple[bytes, int, int, bytes]:
    h16, w, h = BLOB_HDR_STRUCT.unpack_from(data, 0)
    return h16, w, h, data[BLOB_HDR_STRUCT.size :]


def encode_input_pointer(kind: int, x: float, y: float, button: int) -> bytes:
    return POINTER_STRUCT.pack(kind & 0xFF, float(x), float(y), button & 0xFF)


def decode_input_pointer(data: bytes) -> tuple[int, float, float, int]:
    return POINTER_STRUCT.unpack_from(data, 0)


def encode_input_wheel(x: float, y: float, dx: float, dy: float) -> bytes:
    return WHEEL_STRUCT.pack(float(x), float(y), float(dx), float(dy))


def decode_input_wheel(data: bytes) -> tuple[float, float, float, float]:
    return WHEEL_STRUCT.unpack_from(data, 0)


def encode_resize(w: int, h: int) -> bytes:
    return RESIZE_STRUCT.pack(max(1, int(w)), max(1, int(h)))


def decode_resize(data: bytes) -> tuple[int, int]:
    return RESIZE_STRUCT.unpack_from(data, 0)


def encode_fx(bloom: float, scanlines: float) -> bytes:
    return struct.pack("<ff", float(bloom), float(scanlines))


def decode_fx(data: bytes) -> tuple[float, float]:
    return struct.unpack_from("<ff", data, 0)


def encode_vid_nalu(pts_us: int, flags: int, cam_mat4: tuple[float, ...], nalu: bytes) -> bytes:
    mat = tuple(float(v) for v in cam_mat4[:16])
    if len(mat) < 16:
        mat = mat + (0.0,) * (16 - len(mat))
    return NALU_HDR_STRUCT.pack(int(pts_us), flags & 0xFF, *mat) + nalu


def decode_vid_nalu(data: bytes) -> tuple[int, int, tuple[float, ...], bytes]:
    unpacked = NALU_HDR_STRUCT.unpack_from(data, 0)
    return unpacked[0], unpacked[1], unpacked[2:], data[NALU_HDR_STRUCT.size :]


def encode_caps(*, has_gpu: bool, vram_mb: int, codecs: str = "h264,hevc,av1") -> bytes:
    cb = codecs.encode("utf-8")[:65535]
    return CAPS_STRUCT.pack(1 if has_gpu else 0, max(0, int(vram_mb)), len(cb)) + cb


def decode_caps(data: bytes) -> tuple[bool, int, str]:
    hg, vram, clen = CAPS_STRUCT.unpack_from(data, 0)
    off = CAPS_STRUCT.size
    return bool(hg), int(vram), data[off : off + clen].decode("utf-8", errors="replace")


def encode_plane_rect(rect: tuple[int, int, int, int], *, kind: int, flags: int = 0) -> bytes:
    return PLANE_RECT_STRUCT.pack(
        clamp_i16(rect[0]),
        clamp_i16(rect[1]),
        clamp_i16(rect[2]),
        clamp_i16(rect[3]),
        kind & 0xFF,
        flags & 0xFF,
    )


def decode_plane_rect(data: bytes) -> tuple[tuple[int, int, int, int], int, int]:
    x0, y0, x1, y1, kind, flags = PLANE_RECT_STRUCT.unpack_from(data, 0)
    return (x0, y0, x1, y1), kind, flags


def encode_buf_write(buf_id: int, offset: int, payload: bytes) -> bytes:
    return BUF_WRITE_STRUCT.pack(int(buf_id) & 0xFFFFFFFF, int(offset) & 0xFFFFFFFF) + payload


def decode_buf_write(data: bytes) -> tuple[int, int, bytes]:
    buf_id, offset = BUF_WRITE_STRUCT.unpack_from(data, 0)
    return buf_id, offset, data[BUF_WRITE_STRUCT.size :]


def encode_audio_pcm(sample_rate: int, channels: int, pcm: bytes, *, flags: int = 1) -> bytes:
    return AUDIO_HDR_STRUCT.pack(max(8000, int(sample_rate)), max(1, int(channels)) & 0xFF, flags & 0xFF) + pcm


def decode_audio_pcm(data: bytes) -> tuple[int, int, int, bytes]:
    sr, ch, flags = AUDIO_HDR_STRUCT.unpack_from(data, 0)
    return int(sr), int(ch), int(flags), data[AUDIO_HDR_STRUCT.size :]


def encode_client_file(name: str, data: bytes) -> bytes:
    nb = name.encode("utf-8")[:4095]
    return struct.pack("<H", len(nb)) + nb + data


def decode_client_file(data: bytes) -> tuple[str, bytes]:
    (nlen,) = struct.unpack_from("<H", data, 0)
    off = 2
    return data[off : off + nlen].decode("utf-8", errors="replace"), data[off + nlen :]


def _pack_str(s: str, max_bytes: int = 4095) -> bytes:
    b = s.encode("utf-8")[:max_bytes]
    return struct.pack("<H", len(b)) + b


def _unpack_str(data: bytes, off: int) -> tuple[str, int]:
    (slen,) = struct.unpack_from("<H", data, off)
    off += 2
    return data[off : off + slen].decode("utf-8", errors="replace"), off + slen


def encode_app_list(apps: list[tuple[str, str, str, str]]) -> bytes:
    out = bytearray(struct.pack("<H", len(apps)))
    for app_id, name, desc, kind in apps:
        out.extend(_pack_str(app_id, 511))
        out.extend(_pack_str(name, 511))
        out.extend(_pack_str(desc, 1023))
        out.extend(_pack_str(kind, 127))
    return bytes(out)


def decode_app_list(data: bytes) -> list[tuple[str, str, str, str]]:
    if len(data) < 2:
        return []
    (count,) = struct.unpack_from("<H", data, 0)
    off = 2
    out: list[tuple[str, str, str, str]] = []
    for _ in range(count):
        app_id, off = _unpack_str(data, off)
        name, off = _unpack_str(data, off)
        desc, off = _unpack_str(data, off)
        kind, off = _unpack_str(data, off)
        out.append((app_id, name, desc, kind))
    return out


def encode_launch_app(app_id: str, args: str = "") -> bytes:
    return _pack_str(app_id, 511) + _pack_str(args, 2047)


def decode_launch_app(data: bytes) -> tuple[str, str]:
    app_id, off = _unpack_str(data, 0)
    args, _ = _unpack_str(data, off) if off + 2 <= len(data) else ("", off)
    return app_id, args


def encode_launch_status(ok: bool, app_id: str, message: str = "") -> bytes:
    return struct.pack("<B", 1 if ok else 0) + _pack_str(app_id, 511) + _pack_str(message, 1023)


def decode_launch_status(data: bytes) -> tuple[bool, str, str]:
    if not data:
        return False, "", ""
    ok = bool(data[0])
    app_id, off = _unpack_str(data, 1)
    msg, _ = _unpack_str(data, off) if off + 2 <= len(data) else ("", off)
    return ok, app_id, msg


def encode_fs_entries(req_path: str, entries: list[tuple[bool, str, str, int]]) -> bytes:
    out = bytearray(_pack_str(req_path, 1023))
    out.extend(struct.pack("<H", min(65535, len(entries))))
    for is_dir, name, full_path, size in entries[:65535]:
        out.extend(struct.pack("<B", 1 if is_dir else 0))
        out.extend(_pack_str(name, 511))
        out.extend(_pack_str(full_path, 1023))
        out.extend(struct.pack("<Q", max(0, int(size))))
    return bytes(out)


def decode_fs_entries(data: bytes) -> tuple[str, list[tuple[bool, str, str, int]]]:
    if len(data) < 4:
        return "", []
    req_path, off = _unpack_str(data, 0)
    (count,) = struct.unpack_from("<H", data, off)
    off += 2
    out: list[tuple[bool, str, str, int]] = []
    for _ in range(count):
        is_dir = bool(data[off])
        off += 1
        name, off = _unpack_str(data, off)
        full_path, off = _unpack_str(data, off)
        (size,) = struct.unpack_from("<Q", data, off)
        off += 8
        out.append((is_dir, name, full_path, int(size)))
    return req_path, out
