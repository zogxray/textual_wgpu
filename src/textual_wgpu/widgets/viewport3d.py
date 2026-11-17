"""Viewport3D — dual-mode 3D render plane with Client/Server GPU balancing."""

from __future__ import annotations

import math
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw

from ..geometry import Rect
from ..widget import Widget

if TYPE_CHECKING:
    pass


@dataclass
class ClientCapabilities:
    has_gpu: bool = True
    vram_mb: int = 1024
    codecs: tuple[str, ...] = ("h264", "hevc", "av1")


def _build_icosahedron() -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1.0, phi, 0.0), (1.0, phi, 0.0), (-1.0, -phi, 0.0), (1.0, -phi, 0.0),
        (0.0, -1.0, phi), (0.0, 1.0, phi), (0.0, -1.0, -phi), (0.0, 1.0, -phi),
        (phi, 0.0, -1.0), (phi, 0.0, 1.0), (-phi, 0.0, -1.0), (-phi, 0.0, 1.0),
    ]
    norm_v = []
    for x, y, z in verts:
        ln = math.sqrt(x * x + y * y + z * z)
        norm_v.append((x / ln, y / ln, z / ln))
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    return norm_v, faces


_ICO_VERTS, _ICO_FACES = _build_icosahedron()


def render_mesh_rgba(
    width: int,
    height: int,
    *,
    yaw: float,
    pitch: float,
    zoom: float,
    bg: tuple[int, int, int, int] = (18, 22, 30, 255),
    accent: tuple[int, int, int] = (108, 178, 235),
) -> bytes:
    """Fast shaded 3D icosahedron renderer (used locally & as software fallback)."""
    w = max(8, int(width))
    h = max(8, int(height))
    im = Image.new("RGBA", (w, h), bg)
    draw = ImageDraw.Draw(im)

    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    scale = min(w, h) * 0.36 * max(0.3, min(3.0, zoom))
    cx, cy_px = w * 0.5, h * 0.5

    transformed: list[tuple[float, float, float]] = []
    for vx, vy, vz in _ICO_VERTS:
        x1 = vx * cy + vz * sy
        z1 = -vx * sy + vz * cy
        y2 = vy * cp - z1 * sp
        z2 = vy * sp + z1 * cp
        transformed.append((x1, y2, z2))

    # Painter's algorithm sorted by face centroid Z
    tris: list[tuple[float, list[tuple[float, float]], tuple[int, int, int, int]]] = []
    lx, ly, lz = 0.45, -0.55, 0.70
    for i0, i1, i2 in _ICO_FACES:
        p0, p1, p2 = transformed[i0], transformed[i1], transformed[i2]
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        wx, wy, wz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        if nz <= 0:
            continue
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
        ndotl = max(0.15, min(1.0, 0.25 + 0.75 * max(0.0, nx * lx + ny * ly + nz * lz)))
        col = (
            int(accent[0] * ndotl),
            int(accent[1] * ndotl),
            int(accent[2] * ndotl),
            255,
        )
        pts = []
        for px, py, pz in (p0, p1, p2):
            persp = 3.2 / max(0.8, 3.2 - pz)
            pts.append((cx + px * scale * persp, cy_px - py * scale * persp))
        z_avg = (p0[2] + p1[2] + p2[2]) / 3.0
        tris.append((z_avg, pts, col))

    tris.sort(key=lambda item: item[0])
    for _z, pts, col in tris:
        draw.polygon(pts, fill=col, outline=(255, 255, 255, 55))
    return im.tobytes("raw", "RGBA")


class Viewport3D(Widget):
    """3D render surface supporting Client GPU, Server GPU, or Hybrid execution."""

    can_focus = True
    ALLOW_SELECT = False

    def __init__(
        self,
        *,
        id: str | None = None,
        scene_vram_bytes: int = 4 * 1024 * 1024,
        allow_client_assets: bool = True,
        prefer_mode: str = "auto",
        renderer: Callable[[int, int, tuple[float, float, float]], bytes] | None = None,
    ) -> None:
        super().__init__(id=id)
        self.scene_vram_bytes = max(0, int(scene_vram_bytes))
        self.allow_client_assets = bool(allow_client_assets)
        self.prefer_mode = prefer_mode  # auto | client | server | hybrid
        self.renderer = renderer
        self.yaw = 0.55
        self.pitch = 0.35
        self.zoom = 1.0
        self.surf_id = 0
        self._lock = threading.Lock()
        self._rgba_buf: bytes = b""
        self._buf_size: tuple[int, int] = (0, 0)
        self._dirty_render = True
        self._frame_seq = 0
        self._worker: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._drag_xy: tuple[int, int] | None = None

    def hits(self):
        if self.rect.w < 4 or self.rect.h < 4:
            return []

        def _down(x: int, y: int) -> None:
            self.on_pointer_down(x, y)

        return [(self.rect, _down)]

    def on_pointer_down(self, x: int, y: int) -> bool:
        if not self.rect.contains(x, y):
            return False
        self.focus()
        self._drag_xy = (int(x), int(y))
        return True

    def on_pointer_move(self, x: int, y: int) -> bool:
        if self._drag_xy is None:
            return False
        lx, ly = self._drag_xy
        dx, dy = int(x) - lx, int(y) - ly
        self._drag_xy = (int(x), int(y))
        if dx != 0 or dy != 0:
            self.orbit(dx * 0.012, dy * 0.012)
            return True
        return False

    def on_pointer_up(self, _x: int, _y: int) -> bool:
        if self._drag_xy is not None:
            self._drag_xy = None
            return True
        return False

    def choose_execution_mode(self, caps: ClientCapabilities) -> str:
        """Balance Server vs Client GPU based on scene VRAM, policy, and client caps."""
        if self.prefer_mode in ("client", "server", "hybrid"):
            if self.prefer_mode == "client" and (not caps.has_gpu or not self.allow_client_assets):
                return "server"
            return self.prefer_mode
        if not self.allow_client_assets or not caps.has_gpu:
            return "server"
        budget = int(caps.vram_mb) * 1024 * 1024 * 0.5
        if self.scene_vram_bytes <= budget:
            return "client"
        return "server"

    def camera_uniform_bytes(self) -> bytes:
        """Pack compact 16-float camera/transform uniform matrix (64 bytes, GPU-ready)."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        mat = (
            cy, sy * sp, sy * cp, 0.0,
            0.0, cp, -sp, 0.0,
            -sy, cy * sp, cy * cp, 0.0,
            0.0, 0.0, self.zoom, 1.0,
        )
        return struct.pack("<16f", *mat)

    def camera_mat4(self) -> tuple[float, ...]:
        return struct.unpack("<16f", self.camera_uniform_bytes())

    def orbit(self, dyaw: float, dpitch: float) -> None:
        self.yaw += float(dyaw)
        self.pitch = max(-1.45, min(1.45, self.pitch + float(dpitch)))
        self._dirty_render = True
        self.refresh()

    def step_zoom(self, delta: float) -> None:
        self.zoom = max(0.3, min(3.0, self.zoom + float(delta)))
        self._dirty_render = True
        self.refresh()

    def render_offscreen(self, width: int | None = None, height: int | None = None) -> tuple[int, int, bytes]:
        """Render current scene offscreen (thread-safe double-buffer)."""
        w = max(8, int(width or self.rect.w or 320))
        h = max(8, int(height or self.rect.h or 240))
        if self.renderer is not None:
            raw = self.renderer(w, h, (self.yaw, self.pitch, self.zoom))
        else:
            t = self.theme
            bg = t.rgba("surface")
            ac = t.rgba("accent")[:3]
            from ..blit import load_tw_native

            nat = load_tw_native()
            if nat is not None:
                raw = nat.render_mesh_rgba(w, h, float(self.yaw), float(self.pitch), float(self.zoom), tuple(bg), tuple(ac))
            else:
                raw = render_mesh_rgba(w, h, yaw=self.yaw, pitch=self.pitch, zoom=self.zoom, bg=bg, accent=ac)
        with self._lock:
            self._rgba_buf = raw
            self._buf_size = (w, h)
            self._dirty_render = False
            self._frame_seq += 1
        return w, h, raw

    def start_background_render(self, fps: float = 30.0) -> None:
        """Run 3D simulation & offscreen rendering in an independent worker thread."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_evt.clear()
        period = 1.0 / max(1.0, float(fps))

        def _loop() -> None:
            while not self._stop_evt.is_set():
                if self.rect.w > 8 and self.rect.h > 8:
                    self.yaw += 0.03
                    self.render_offscreen(self.rect.w, self.rect.h)
                    if self.app is not None:
                        self.app.request_draw()
                time.sleep(period)

        self._worker = threading.Thread(target=_loop, daemon=True)
        self._worker.start()

    def stop_background_render(self) -> None:
        self._stop_evt.set()

    def on_key(self, key: str) -> bool:
        if key == "left":
            self.orbit(-0.15, 0.0)
            return True
        if key == "right":
            self.orbit(0.15, 0.0)
            return True
        if key == "up":
            self.orbit(0.0, -0.12)
            return True
        if key == "down":
            self.orbit(0.0, 0.12)
            return True
        if key in ("plus", "equal"):
            self.step_zoom(0.1)
            return True
        if key == "minus":
            self.step_zoom(-0.1)
            return True
        return False

    def paint(self, draw) -> None:  # noqa: ANN001
        if self.rect.w < 4 or self.rect.h < 4:
            return
        t = self.theme
        # Remote DrawRecorder punches a transparent hole so PlaneCompositor shows the 3D plane.
        if hasattr(draw, "clear_rect"):
            draw.clear_rect(self.rect.x + 1, self.rect.y + 1, self.rect.x1 - 2, self.rect.y1 - 2)
            draw.rectangle(
                [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
                outline=t.rgba("accent" if self.focused else "primary"),
            )
            return
        target_img = getattr(draw, "_image", None)
        with self._lock:
            needs = self._dirty_render or self._buf_size != (self.rect.w, self.rect.h)
            raw = self._rgba_buf
            bw, bh = self._buf_size
        if needs or not raw:
            bw, bh, raw = self.render_offscreen(self.rect.w, self.rect.h)
        if target_img is not None and raw:
            frame_im = Image.frombytes("RGBA", (bw, bh), raw)
            target_img.alpha_composite(frame_im, (self.rect.x, self.rect.y))
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
            outline=t.rgba("accent" if self.focused else "primary"),
        )
