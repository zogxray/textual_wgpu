"""Video — media playback plane supporting compressed NALU passthrough or RGBA frames."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PIL import Image

from ..widget import Widget


@dataclass(slots=True)
class VideoPacket:
    pts_us: int
    data: bytes
    keyframe: bool = False
    codec_flag: int = 0x10


class Video(Widget):
    """Hardware-accelerated / remote-passthrough video viewport widget."""

    can_focus = True
    ALLOW_SELECT = False

    def __init__(self, *, id: str | None = None, codec: str = "h264") -> None:
        super().__init__(id=id)
        self.codec = codec
        self.playing = False
        self.surf_id = 0
        self._packets: deque[VideoPacket] = deque(maxlen=64)
        self._rgba_buf: bytes = b""
        self._buf_size: tuple[int, int] = (0, 0)
        self._frame_seq: int = 0

    def play(self) -> None:
        self.playing = True
        self.refresh()

    def pause(self) -> None:
        self.playing = False
        self.refresh()

    def push_packet(
        self,
        pts_us: int,
        data: bytes,
        *,
        keyframe: bool = False,
        codec_flag: int = 0x10,
    ) -> None:
        """Queue a compressed bitstream packet (Stream-to-Render passthrough)."""
        self._packets.append(
            VideoPacket(
                pts_us=int(pts_us),
                data=bytes(data),
                keyframe=keyframe,
                codec_flag=int(codec_flag),
            ),
        )
        self.refresh()

    def drain_packets(self) -> list[VideoPacket]:
        out = list(self._packets)
        self._packets.clear()
        return out

    def push_rgba_frame(self, width: int, height: int, rgba: bytes) -> None:
        self._buf_size = (max(1, int(width)), max(1, int(height)))
        self._rgba_buf = bytes(rgba)
        self._frame_seq += 1
        self.refresh()

    def on_key(self, key: str) -> bool:
        if key == "space":
            if self.playing:
                self.pause()
            else:
                self.play()
            return True
        return False

    def paint(self, draw) -> None:  # noqa: ANN001
        if self.rect.w < 4 or self.rect.h < 4:
            return
        t = self.theme
        if hasattr(draw, "clear_rect"):
            draw.clear_rect(self.rect.x + 1, self.rect.y + 1, self.rect.x1 - 2, self.rect.y1 - 2)
            draw.rectangle(
                [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
                outline=t.rgba("accent" if self.focused else "primary"),
            )
            return
        target_img = getattr(draw, "_image", None)
        if target_img is not None and self._rgba_buf and self._buf_size[0] > 0:
            im = Image.frombytes("RGBA", self._buf_size, self._rgba_buf)
            if im.size != (self.rect.w, self.rect.h):
                im = im.resize((self.rect.w, self.rect.h), Image.Resampling.BILINEAR)
            target_img.alpha_composite(im, (self.rect.x, self.rect.y))
        else:
            draw.rectangle(
                [self.rect.x, self.rect.y, self.rect.x1 - 1, self.rect.y1 - 1],
                fill=t.rgba("surface"),
                outline=t.rgba("accent" if self.focused else "primary"),
            )
