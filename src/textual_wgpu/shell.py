"""Rough MDGA chrome: ModeBar + body + KeyBar + status (CPU paint → GPU blit)."""

from __future__ import annotations

import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .app import App
from .blit import FrameBlitter

# Same order as MDGA MODE_LABELS — labels only, no DNA logic.
MODE_LABELS = (
    ("genome", "Genome"),
    ("data", "Data"),
    ("tree", "Tree"),
    ("pgs", "PGS"),
    ("gwas", "GWAS"),
    ("reference", "Reference"),
    ("chat", "Chat"),
    ("jobs", "Jobs"),
    ("samples", "Samples"),
    ("help", "Help"),
)

# Brand-ish dark + gold (rough, not pixel-perfect Textual theme).
BG = (18, 16, 22, 255)
BAR = (32, 28, 38, 255)
BODY = (24, 22, 30, 255)
TEXT = (251, 246, 240, 255)
MUTED = (140, 130, 150, 255)
ACCENT = (212, 173, 114, 255)
ACTIVE_BG = (212, 173, 114, 255)
ACTIVE_FG = (18, 16, 22, 255)


def _font(size: int) -> ImageFont.ImageFont:
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class ShellApp(App):
    """Shared shell sketch. Body is a placeholder per mode."""

    TITLE = "MDGA · textual-wgpu shell"
    SIZE = (1100, 700)
    BAR_H = 36
    KEY_H = 32
    STATUS_H = 28

    def __init__(self) -> None:
        super().__init__()
        self.mode_index = 0
        self._blitter: FrameBlitter | None = None
        self._dirty = True
        self._pixel_size = self.SIZE
        self._font = _font(15)
        self._font_sm = _font(13)
        self._font_lg = _font(22)

    @property
    def mode_key(self) -> str:
        return MODE_LABELS[self.mode_index][0]

    @property
    def mode_title(self) -> str:
        return MODE_LABELS[self.mode_index][1]

    def on_mount(self) -> None:
        assert self._device is not None and self._format is not None
        self._blitter = FrameBlitter(self._device, self._format)
        for i in range(10):
            self.bind(f"f{i + 1}", lambda i=i: self.set_mode(i))
        self.bind("q", self.exit)
        self.bind("escape", self.exit)
        print("shell · F1–F10 modes · Q quit", file=sys.stderr, flush=True)
        if self._canvas is not None:
            @self._canvas.add_event_handler("resize")
            def _on_resize(event: dict) -> None:
                w = int(event.get("width") or self._pixel_size[0])
                h = int(event.get("height") or self._pixel_size[1])
                if w > 0 and h > 0:
                    self._pixel_size = (w, h)
                    self._dirty = True
                    self._canvas.request_draw(self.draw)

            @self._canvas.add_event_handler("key_down")
            def _redraw(_event: dict) -> None:
                # Binding already ran; force a frame after mode switch.
                self._dirty = True
                self._canvas.request_draw(self.draw)

    def set_mode(self, index: int) -> None:
        if 0 <= index < len(MODE_LABELS):
            self.mode_index = index
            self._dirty = True

    def _paint(self) -> Image.Image:
        w, h = self._pixel_size
        img = Image.new("RGBA", (w, h), BG)
        draw = ImageDraw.Draw(img)

        # ModeBar
        draw.rectangle([0, 0, w, self.BAR_H], fill=BAR)
        x = 10
        for i, (_key, title) in enumerate(MODE_LABELS):
            label = f"F{i + 1} {title}"
            bbox = draw.textbbox((0, 0), label, font=self._font_sm)
            tw = bbox[2] - bbox[0] + 14
            th = bbox[3] - bbox[1] + 8
            y = (self.BAR_H - th) // 2
            if i == self.mode_index:
                draw.rectangle([x, y, x + tw, y + th], fill=ACTIVE_BG)
                draw.text((x + 7, y + 3), label, font=self._font_sm, fill=ACTIVE_FG)
            else:
                draw.text((x + 7, y + 3), label, font=self._font_sm, fill=MUTED)
            x += tw + 6

        # Body
        body_top = self.BAR_H + 2
        body_bot = h - self.KEY_H - self.STATUS_H - 2
        draw.rectangle([8, body_top, w - 8, body_bot], fill=BODY, outline=ACCENT)
        title = self.mode_title
        note = "Rough shell — body placeholder (no screen copy yet)"
        draw.text((28, body_top + 28), title, font=self._font_lg, fill=TEXT)
        draw.text((28, body_top + 64), note, font=self._font, fill=MUTED)
        draw.text(
            (28, body_top + 96),
            f"mode key: {self.mode_key}",
            font=self._font_sm,
            fill=MUTED,
        )

        # KeyBar
        key_y = h - self.STATUS_H - self.KEY_H
        draw.rectangle([0, key_y, w, key_y + self.KEY_H], fill=BAR)
        keys = f"F1–F10 Mode   Q Quit   ·  {self.mode_title}"
        draw.text((12, key_y + 8), keys, font=self._font_sm, fill=MUTED)

        # Status
        st_y = h - self.STATUS_H
        draw.rectangle([0, st_y, w, h], fill=BAR)
        draw.text(
            (12, st_y + 6),
            f"textual-wgpu shell · {w}×{h} · {self.mode_key}",
            font=self._font_sm,
            fill=ACCENT,
        )
        return img

    def draw(self) -> None:
        if self._blitter is None or self._context is None:
            return
        try:
            # Always paint for POC (cheap at this resolution).
            img = self._paint()
            self._blitter.upload(img.tobytes("raw", "RGBA"), img.size)
            self._dirty = False
            view = self._context.get_current_texture().create_view()
            self._blitter.draw(view)
        except Exception as exc:  # noqa: BLE001
            print(f"shell draw error: {exc}", file=sys.stderr, flush=True)
            raise


def main() -> None:
    ShellApp().run()


if __name__ == "__main__":
    main()
