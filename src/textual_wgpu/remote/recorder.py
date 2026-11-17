"""Recording ``ImageDraw`` proxy, retained-layer differ, and binary DrawOps replayer."""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ..fonts import font_from_tag, font_tag
from .protocol import (
    CLEAR_RECT_STRUCT,
    CLIP_POP_STRUCT,
    CLIP_PUSH_STRUCT,
    IMG_BLIT_STRUCT,
    LINE_STRUCT,
    OP_CLEAR_RECT,
    OP_CLIP_POP,
    OP_CLIP_PUSH,
    OP_IMG_BLIT,
    OP_LINE,
    OP_RECT,
    OP_TEXT,
    RECT_FLAG_FILL,
    RECT_FLAG_OUTLINE,
    RECT_STRUCT,
    TEXT_STRUCT,
    clamp_i16,
    pack_rgba,
    unpack_rgba,
)


def _norm_xy2(xy: Any) -> tuple[int, int, int, int]:
    if isinstance(xy, (list, tuple)) and len(xy) == 2 and isinstance(xy[0], (list, tuple)):
        return int(xy[0][0]), int(xy[0][1]), int(xy[1][0]), int(xy[1][1])
    return int(xy[0]), int(xy[1]), int(xy[2]), int(xy[3])


class StyleInterner:
    """Interns ``(fg, bg, flags, font_id, size)`` into compact ``u16`` IDs."""

    def __init__(self) -> None:
        self._map: dict[tuple[int, int, int, int, int], int] = {}
        self._pending: list[tuple[int, int, int, int, int, int]] = []

    def intern(
        self,
        fg: int,
        *,
        bg: int = 0,
        flags: int = 0,
        font_id: int = 0,
        size: int = 13,
    ) -> int:
        key = (fg, bg, flags, int(font_id) & 0xFF, int(size) & 0xFF)
        sid = self._map.get(key)
        if sid is not None:
            return sid
        sid = len(self._map) + 1
        if sid > 0xFFFF:
            # Wrap table if ever exceeded in ultra-long sessions.
            self._map.clear()
            sid = 1
        self._map[key] = sid
        self._pending.append((sid, key[0], key[1], key[2], key[3], key[4]))
        return sid

    def drain_new(self) -> list[tuple[int, int, int, int, int, int]]:
        out = list(self._pending)
        self._pending.clear()
        return out


class DrawRecorder:
    """Duck-types ``PIL.ImageDraw.ImageDraw`` into compact retained binary layers."""

    def __init__(
        self,
        styles: StyleInterner | None = None,
        known_blobs: set[bytes] | None = None,
        *,
        raw_blob_mode: bool = False,
        measure_fn: Any = None,
    ) -> None:
        self.styles = styles or StyleInterner()
        self.known_blobs: set[bytes] = known_blobs if known_blobs is not None else set()
        self.raw_blob_mode = bool(raw_blob_mode)
        self._measure_fn = measure_fn
        self._prev_hashes: dict[str, tuple[tuple[int, int, int, int], bytes]] = {}
        self._current_id: str | None = None
        self._current_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._buf = bytearray()
        self._frame_layers: list[str] = []
        self._frame_raw_layers: list[bytes] = []
        self._dirty_layers: list[tuple[str, tuple[int, int, int, int], bytes]] = []
        self._pending_blobs: list[tuple[bytes, int, int, bytes]] = []
        self._scratch_img: Any = None
        self._scratch_draw: Any = None

    def begin_frame(self) -> None:
        self._frame_layers = []
        self._frame_raw_layers = []
        self._dirty_layers = []
        self._pending_blobs = []

    def begin_layer(self, layer_id: str, rect: tuple[int, int, int, int]) -> None:
        if self._current_id is not None:
            self.end_layer()
        self._current_id = layer_id
        self._current_rect = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        self._buf = bytearray()

    def end_layer(self) -> None:
        if self._current_id is None:
            return
        lid = self._current_id
        rect = self._current_rect
        raw = bytes(self._buf)
        self._frame_layers.append(lid)
        self._frame_raw_layers.append(raw)
        if not self.raw_blob_mode:
            digest = hashlib.blake2b(raw, digest_size=8).digest()
            prev = self._prev_hashes.get(lid)
            if prev != (rect, digest):
                self._prev_hashes[lid] = (rect, digest)
                self._dirty_layers.append((lid, rect, raw))
        self._current_id = None
        self._buf = bytearray()

    def finish_frame(self) -> tuple[
        list[str],
        list[tuple[str, tuple[int, int, int, int], bytes]],
        list[str],
        list[tuple[int, int, int, int, int, int]],
        list[tuple[bytes, int, int, bytes]],
    ]:
        if self._current_id is not None:
            self.end_layer()
        active = set(self._frame_layers)
        dropped = [lid for lid in list(self._prev_hashes) if lid not in active]
        for lid in dropped:
            self._prev_hashes.pop(lid, None)
        return (
            list(self._frame_layers),
            list(self._dirty_layers),
            dropped,
            self.styles.drain_new(),
            list(self._pending_blobs),
        )

    def reset_retained(self, *, keep_blobs: bool = False) -> None:
        self._prev_hashes.clear()
        self.styles._map.clear()
        self.styles._pending.clear()
        if not keep_blobs:
            self.known_blobs.clear()

    # ── ImageDraw duck-typed methods ───────────────────────────────────

    def rectangle(self, xy: Any, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        x0, y0, x1, y1 = _norm_xy2(xy)
        flags = 0
        if fill is not None:
            flags |= RECT_FLAG_FILL
        if outline is not None:
            flags |= RECT_FLAG_OUTLINE
        if flags == 0:
            return
        self._buf.extend(RECT_STRUCT.pack(
            OP_RECT,
            flags,
            clamp_i16(x0),
            clamp_i16(y0),
            clamp_i16(x1),
            clamp_i16(y1),
            pack_rgba(fill),
            pack_rgba(outline),
            max(1, int(width)) & 0xFFFF,
        ))

    def line(self, xy: Any, fill: Any = None, width: int = 1) -> None:
        if fill is None:
            return
        x0, y0, x1, y1 = _norm_xy2(xy)
        self._buf.extend(LINE_STRUCT.pack(
            OP_LINE,
            clamp_i16(x0),
            clamp_i16(y0),
            clamp_i16(x1),
            clamp_i16(y1),
            pack_rgba(fill),
            max(1, int(width)) & 0xFFFF,
        ))

    def text(self, xy: Any, text: str, font: Any = None, fill: Any = None) -> None:
        if not text:
            return
        x, y = int(xy[0]), int(xy[1])
        kind, size = font_tag(font)
        fg = pack_rgba(fill if fill is not None else (240, 230, 216, 255))
        sid = self.styles.intern(fg, font_id=kind, size=size)
        raw = str(text).encode("utf-8")
        self._buf.extend(TEXT_STRUCT.pack(
            OP_TEXT,
            clamp_i16(x),
            clamp_i16(y),
            sid,
            len(raw) & 0xFFFF,
        ))
        self._buf.extend(raw[:0xFFFF])

    def textbbox(self, xy: Any, text: str, font: Any = None) -> tuple[int, int, int, int]:
        x, y = int(xy[0]), int(xy[1])
        if callable(self._measure_fn):
            kind, size = font_tag(font)
            tw, th = self._measure_fn(kind, size, str(text))
            return (x, y, x + int(tw), y + int(th))
        if self._scratch_draw is None:
            self._scratch_img = Image.new("RGBA", (1, 1))
            self._scratch_draw = ImageDraw.Draw(self._scratch_img)
        return self._scratch_draw.textbbox(xy, text, font=font)

    def push_clip(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._buf.extend(CLIP_PUSH_STRUCT.pack(
            OP_CLIP_PUSH,
            clamp_i16(x0),
            clamp_i16(y0),
            clamp_i16(x1),
            clamp_i16(y1),
        ))

    def pop_clip(self) -> None:
        self._buf.extend(CLIP_POP_STRUCT.pack(OP_CLIP_POP))

    def clear_rect(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Punch a transparent cutout on the UI layer so GPU planes underneath show through."""
        self._buf.extend(CLEAR_RECT_STRUCT.pack(
            OP_CLEAR_RECT,
            clamp_i16(x0),
            clamp_i16(y0),
            clamp_i16(x1),
            clamp_i16(y1),
        ))

    def record_image(
        self,
        path_str: str,
        tw: int,
        th: int,
        dx: int,
        dy: int,
        crop_box: tuple[int, int, int, int],
    ) -> None:
        from ..compositor import _load_scaled_image

        p = Path(path_str)
        try:
            mtime_ns = int(p.stat().st_mtime_ns)
        except OSError:
            return
        key_bytes = f"{p}|{tw}x{th}|{mtime_ns}".encode("utf-8")
        hash16 = hashlib.blake2b(key_bytes, digest_size=16).digest()
        if hash16 not in self.known_blobs:
            rgba = _load_scaled_image(path_str, tw, th)
            if rgba is None:
                return
            if self.raw_blob_mode:
                self._pending_blobs.append((hash16, tw, th, rgba.tobytes("raw", "RGBA")))
            else:
                bio = io.BytesIO()
                rgba.save(bio, format="PNG", compress_level=3)
                self._pending_blobs.append((hash16, tw, th, bio.getvalue()))
            self.known_blobs.add(hash16)
        self._buf.extend(IMG_BLIT_STRUCT.pack(
            OP_IMG_BLIT,
            hash16,
            clamp_i16(dx),
            clamp_i16(dy),
            clamp_i16(crop_box[0]),
            clamp_i16(crop_box[1]),
            clamp_i16(crop_box[2]),
            clamp_i16(crop_box[3]),
        ))


def replay_ops(
    target_img: Image.Image,
    ops: bytes,
    *,
    styles: dict[int, tuple[int, int, int, int, int]],
    blobs: dict[bytes, Image.Image],
) -> None:
    """Replay binary ``DrawOps`` onto ``target_img`` (used by client & loopback tests)."""
    draw = ImageDraw.Draw(target_img)
    clip_stack: list[tuple[int, int, int, int, Image.Image, Image.Image]] = []
    cur_img = target_img
    cur_draw = draw
    off_x = 0
    off_y = 0

    pos = 0
    n = len(ops)
    while pos < n:
        op = ops[pos]
        if op == OP_RECT:
            _, flags, x0, y0, x1, y1, fill_u, outline_u, width = RECT_STRUCT.unpack_from(ops, pos)
            pos += RECT_STRUCT.size
            fill = unpack_rgba(fill_u) if (flags & RECT_FLAG_FILL) else None
            outline = unpack_rgba(outline_u) if (flags & RECT_FLAG_OUTLINE) else None
            cur_draw.rectangle([x0 - off_x, y0 - off_y, x1 - off_x, y1 - off_y], fill=fill, outline=outline, width=width)
        elif op == OP_LINE:
            _, x0, y0, x1, y1, fill_u, width = LINE_STRUCT.unpack_from(ops, pos)
            pos += LINE_STRUCT.size
            cur_draw.line([(x0 - off_x, y0 - off_y), (x1 - off_x, y1 - off_y)], fill=unpack_rgba(fill_u), width=width)
        elif op == OP_TEXT:
            _, x, y, sid, tlen = TEXT_STRUCT.unpack_from(ops, pos)
            pos += TEXT_STRUCT.size
            txt = ops[pos : pos + tlen].decode("utf-8", errors="replace")
            pos += tlen
            fg_u, _bg_u, _fl, font_id, size = styles.get(sid, (0xF0E6D8FF, 0, 0, 0, 13))
            cur_draw.text((x - off_x, y - off_y), txt, font=font_from_tag(font_id, size), fill=unpack_rgba(fg_u))
        elif op == OP_IMG_BLIT:
            _, h16, dx, dy, sx0, sy0, sx1, sy1 = IMG_BLIT_STRUCT.unpack_from(ops, pos)
            pos += IMG_BLIT_STRUCT.size
            src = blobs.get(h16)
            if src is not None:
                piece = src.crop((sx0, sy0, sx1, sy1)) if (sx0, sy0, sx1, sy1) != (0, 0, src.width, src.height) else src
                cur_img.alpha_composite(piece, (dx - off_x, dy - off_y))
        elif op == OP_CLIP_PUSH:
            _, cx0, cy0, cx1, cy1 = CLIP_PUSH_STRUCT.unpack_from(ops, pos)
            pos += CLIP_PUSH_STRUCT.size
            cw = max(1, cx1 - cx0)
            ch = max(1, cy1 - cy0)
            layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            clip_stack.append((cx0, cy0, off_x, off_y, cur_img, cur_draw))
            cur_img = layer
            cur_draw = ImageDraw.Draw(layer)
            off_x = cx0
            off_y = cy0
        elif op == OP_CLIP_POP:
            pos += CLIP_POP_STRUCT.size
            if clip_stack:
                cx0, cy0, prev_ox, prev_oy, prev_img, prev_draw = clip_stack.pop()
                prev_img.alpha_composite(cur_img, (cx0 - prev_ox, cy0 - prev_oy))
                cur_img = prev_img
                cur_draw = prev_draw
                off_x = prev_ox
                off_y = prev_oy
        elif op == OP_CLEAR_RECT:
            _, x0, y0, x1, y1 = CLEAR_RECT_STRUCT.unpack_from(ops, pos)
            pos += CLEAR_RECT_STRUCT.size
            cur_draw.rectangle([x0 - off_x, y0 - off_y, x1 - off_x, y1 - off_y], fill=(0, 0, 0, 0))
        else:
            break


class RemoteSceneState:
    """Retained client-side layer, style, and blob state."""

    def __init__(self) -> None:
        self.styles: dict[int, tuple[int, int, int, int, int]] = {}
        self.blobs: dict[bytes, Image.Image] = {}
        self.font_blobs: dict[bytes, bytes] = {}
        self.layers: dict[str, tuple[tuple[int, int, int, int], bytes]] = {}
        self.order: list[str] = []
        self.bg_rgba: tuple[int, int, int, int] = (20, 20, 24, 255)
        self.title: str = "textual-wgpu"
        self.width_scale: float = 1.0
        self.clipboard: str = ""
        self.fx: tuple[float, float] = (0.0, 0.0)
        self.planes: dict[int, dict[str, Any]] = {}
        self.buffers: dict[tuple[int, int], bytearray] = {}

    def add_styles(self, defs: list[tuple[int, int, int, int, int, int]]) -> None:
        for sid, fg, bg, flags, font_id, size in defs:
            self.styles[sid] = (fg, bg, flags, font_id, size)

    def add_blob(self, hash16: bytes, data: bytes, *, w: int = 1, h: int = 1) -> None:
        if w == 0 and h == 0:
            self.font_blobs[hash16] = data
            return
        with Image.open(io.BytesIO(data)) as im:
            self.blobs[hash16] = im.convert("RGBA")

    def patch_layer(self, layer_id: str, rect: tuple[int, int, int, int], ops: bytes) -> None:
        self.layers[layer_id] = (rect, ops)

    def drop_layer(self, layer_id: str) -> None:
        self.layers.pop(layer_id, None)

    def commit(self, order: list[str], bg_u32: int) -> None:
        self.order = list(order)
        self.bg_rgba = unpack_rgba(bg_u32)

    def render_ui(self, size: tuple[int, int]) -> Image.Image:
        """Render retained UI layers with transparent ``OP_CLEAR_RECT`` cutouts."""
        ui = Image.new("RGBA", size, self.bg_rgba)
        for lid in self.order:
            entry = self.layers.get(lid)
            if entry is None:
                continue
            _rect, ops = entry
            if ops:
                replay_ops(ui, ops, styles=self.styles, blobs=self.blobs)
        return ui

    def render(self, size: tuple[int, int]) -> Image.Image:
        """Composite active Media/3D planes underneath the UI layer."""
        ui = self.render_ui(size)
        if not self.planes:
            return ui
        base = Image.new("RGBA", size, self.bg_rgba)
        for _sid, pl in self.planes.items():
            rect = pl.get("rect")
            rgba = pl.get("rgba")
            psize = pl.get("size")
            if rect and rgba and psize and psize[0] > 0 and psize[1] > 0:
                x0, y0, x1, y1 = rect
                if x1 > x0 and y1 > y0:
                    pim = Image.frombytes("RGBA", psize, rgba)
                    if pim.size != (x1 - x0, y1 - y0):
                        pim = pim.resize((x1 - x0, y1 - y0), Image.Resampling.BILINEAR)
                    base.alpha_composite(pim, (x0, y0))
        base.alpha_composite(ui)
        return base


def apply_remote_frame(state: RemoteSceneState, frame: Any) -> bool:
    """Apply a server→client ``Frame`` onto ``RemoteSceneState``.

    Returns True when a frame commit completed and should be presented.
    """
    from .protocol import (
        PLANE_KIND_3D_CLIENT,
        Op,
        decode_buf_write,
        decode_frame_commit,
        decode_fx,
        decode_hello,
        decode_layer_patch,
        decode_plane_rect,
        decode_put_blob,
        decode_style_defs,
        decode_vid_nalu,
    )

    op = frame.op
    if op == Op.HELLO:
        ws, _fh, title = decode_hello(frame.payload)
        state.width_scale = ws
        state.title = title
        return False
    if op == Op.TITLE_SET:
        state.title = frame.payload.decode("utf-8", errors="replace")
        return False
    if op == Op.FX_SET:
        state.fx = decode_fx(frame.payload)
        return False
    if op == Op.STYLE_DEF:
        state.add_styles(decode_style_defs(frame.payload))
        return False
    if op == Op.PUT_BLOB:
        h16, bw, bh, data = decode_put_blob(frame.payload)
        state.add_blob(h16, data, w=bw, h=bh)
        return False
    if op == Op.PLANE_RECT:
        rect, kind, flags = decode_plane_rect(frame.payload)
        x0, y0, x1, y1 = rect
        if kind == 0 or x1 <= x0 or y1 <= y0:
            state.planes.pop(frame.surf_id, None)
            return False
        entry = state.planes.setdefault(frame.surf_id, {})
        entry["rect"] = rect
        entry["kind"] = kind
        entry["flags"] = flags
        return False
    if op == Op.BUF_WRITE:
        buf_id, offset, data = decode_buf_write(frame.payload)
        key = (frame.surf_id, buf_id)
        buf = state.buffers.setdefault(key, bytearray())
        end = offset + len(data)
        if len(buf) < end:
            buf.extend(b"\x00" * (end - len(buf)))
        buf[offset:end] = data
        # If client-side 3D uniform buffer (16 floats = yaw/pitch/zoom matrix), render locally!
        entry = state.planes.get(frame.surf_id)
        if entry is not None and entry.get("kind") == PLANE_KIND_3D_CLIENT and len(buf) >= 64:
            x0, y0, x1, y1 = entry.get("rect", (0, 0, 0, 0))
            pw, ph = max(8, x1 - x0), max(8, y1 - y0)
            m = struct.unpack_from("<16f", buf, 0)
            import math
            from ..widgets.viewport3d import render_mesh_rgba

            yaw = math.atan2(m[1], m[0]) if (m[0] or m[1]) else 0.55
            pitch = math.atan2(-m[6], m[5]) if (m[5] or m[6]) else 0.35
            zoom = max(0.3, min(3.0, float(m[14] or 1.0)))
            entry["rgba"] = render_mesh_rgba(pw, ph, yaw=yaw, pitch=pitch, zoom=zoom)
            entry["size"] = (pw, ph)
            return True
        return False
    if op == Op.VID_NALU:
        from .protocol import VID_CODEC_MASK

        pts_us, flags, cam_mat4, raw = decode_vid_nalu(frame.payload)
        entry = state.planes.setdefault(frame.surf_id, {})
        entry["pts_us"] = pts_us
        entry["cam_mat4"] = cam_mat4
        entry["last_nalu"] = raw
        entry["nalu_flags"] = flags
        codec_flag = flags & VID_CODEC_MASK
        if codec_flag != 0:
            dec_map = getattr(state, "_decoders", None)
            if dec_map is None:
                dec_map = {}
                state._decoders = dec_map  # type: ignore[attr-defined]
            dec = dec_map.get(frame.surf_id)
            if dec is None or dec.codec_flag != codec_flag:
                if dec is not None:
                    dec.close()
                try:
                    from .media_codec import StreamVideoDecoder

                    dec = StreamVideoDecoder(codec_flag)
                    dec_map[frame.surf_id] = dec
                except Exception:  # noqa: BLE001
                    dec = None
            if dec is not None:
                dec.feed(raw)
                latest = dec.latest_frame()
                if latest is not None:
                    fw, fh, rgba_bytes = latest
                    entry["rgba"] = rgba_bytes
                    entry["size"] = (fw, fh)
            return True
        # Uncompressed RGBA server plane payload (flags & 0x02; 0x04 carries <HH dims)
        if flags & 0x04 and len(raw) >= 4:
            fw, fh = struct.unpack_from("<HH", raw, 0)
            rgba_bytes = raw[4:]
            if fw > 0 and fh > 0 and len(rgba_bytes) == fw * fh * 4:
                entry["rgba"] = rgba_bytes
                entry["size"] = (fw, fh)
        elif flags & 0x02:
            x0, y0, x1, y1 = entry.get("rect", (0, 0, 0, 0))
            pw, ph = max(1, x1 - x0), max(1, y1 - y0)
            if len(raw) == pw * ph * 4:
                entry["rgba"] = raw
                entry["size"] = (pw, ph)
        return True
    if op == Op.LAYER_PATCH:
        lid, rect, ops = decode_layer_patch(frame.payload)
        state.patch_layer(lid, rect, ops)
        return False
    if op == Op.LAYER_DROP:
        state.drop_layer(frame.payload.decode("utf-8", errors="replace"))
        return False
    if op == Op.FRAME_COMMIT:
        order, bg_u32 = decode_frame_commit(frame.payload)
        state.commit(order, bg_u32)
        return True
    if op == Op.CLIP_SET:
        state.clipboard = frame.payload.decode("utf-8", errors="replace")
        return False
    return False
