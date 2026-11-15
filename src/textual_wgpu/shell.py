"""Rough app chrome: ModeBar + body + KeyBar + status (CPU paint → GPU blit).

Colors come only from ``Theme`` supplied by the application (or ``Theme.system()``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .app import App
from .blit import FrameBlitter
from .theme import Theme

# Generic mode labels for the chrome demo (apps can replace later).
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


@dataclass(frozen=True)
class Hit:
    x0: int
    y0: int
    x1: int
    y1: int
    action: Callable[[], None]


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

    TITLE = "textual-wgpu shell"
    SIZE = (1100, 700)
    BAR_H = 36
    KEY_H = 32
    STATUS_H = 28

    def __init__(self, theme: Theme | None = None, *, title: str | None = None) -> None:
        super().__init__(theme=theme)
        if title:
            self.TITLE = title
        self.mode_index = 0
        self.quit_modal = False
        self._blitter: FrameBlitter | None = None
        self._pixel_size = self.SIZE
        self._font = _font(15)
        self._font_sm = _font(13)
        self._font_lg = _font(22)
        self._hits: list[Hit] = []

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
        self.bind("q", self.ask_quit)
        self.bind("escape", self.on_escape)
        self.bind("y", self.confirm_quit_yes)
        self.bind("n", self.confirm_quit_no)
        self.bind("enter", self.confirm_quit_yes)
        self.bind("return", self.confirm_quit_yes)
        print("shell · F1–F10 · click ModeBar · Q quit modal", file=sys.stderr, flush=True)

    def on_resize(self, width: int, height: int) -> None:
        self._pixel_size = (width, height)

    def on_pointer(self, x: float, y: float, button: int) -> None:
        if button not in (0, 1):
            return
        ix, iy = int(x), int(y)
        for hit in reversed(self._hits):
            if hit.x0 <= ix < hit.x1 and hit.y0 <= iy < hit.y1:
                hit.action()
                return

    def set_mode(self, index: int) -> None:
        if self.quit_modal:
            return
        if 0 <= index < len(MODE_LABELS):
            self.mode_index = index

    def ask_quit(self) -> None:
        self.quit_modal = True

    def on_escape(self) -> None:
        if self.quit_modal:
            self.quit_modal = False
        else:
            self.ask_quit()

    def confirm_quit_yes(self) -> None:
        if self.quit_modal:
            self.exit()

    def confirm_quit_no(self) -> None:
        if self.quit_modal:
            self.quit_modal = False

    def _add_hit(self, box: tuple[int, int, int, int], action: Callable[[], None]) -> None:
        x0, y0, x1, y1 = box
        self._hits.append(Hit(x0, y0, x1, y1, action))

    def _paint(self) -> Image.Image:
        t = self.theme
        bg = t.rgba("background")
        bar = t.rgba("boost")
        body = t.rgba("panel")
        text = t.rgba("foreground")
        muted = t.rgba("muted")
        accent = t.rgba("accent")
        active_bg = accent
        active_fg = t.rgba("background")
        modal_bg = t.rgba("surface")

        w, h = self._pixel_size
        img = Image.new("RGBA", (w, h), bg)
        draw = ImageDraw.Draw(img)
        self._hits = []

        draw.rectangle([0, 0, w, self.BAR_H], fill=bar)
        x = 10
        for i, (_key, title) in enumerate(MODE_LABELS):
            label = f"F{i + 1} {title}"
            bbox = draw.textbbox((0, 0), label, font=self._font_sm)
            tw = bbox[2] - bbox[0] + 14
            th = bbox[3] - bbox[1] + 8
            y = (self.BAR_H - th) // 2
            box = (x, y, x + tw, y + th)
            if i == self.mode_index:
                draw.rectangle(box, fill=active_bg)
                draw.text((x + 7, y + 3), label, font=self._font_sm, fill=active_fg)
            else:
                draw.text((x + 7, y + 3), label, font=self._font_sm, fill=muted)
            self._add_hit(box, lambda i=i: self.set_mode(i))
            x += tw + 6

        body_top = self.BAR_H + 2
        body_bot = h - self.KEY_H - self.STATUS_H - 2
        draw.rectangle([8, body_top, w - 8, body_bot], fill=body, outline=accent)
        draw.text((28, body_top + 28), self.mode_title, font=self._font_lg, fill=text)
        draw.text(
            (28, body_top + 64),
            "Shell chrome — theme from app / Theme.system()",
            font=self._font,
            fill=muted,
        )
        draw.text(
            (28, body_top + 96),
            f"mode key: {self.mode_key}",
            font=self._font_sm,
            fill=muted,
        )

        key_y = h - self.STATUS_H - self.KEY_H
        draw.rectangle([0, key_y, w, key_y + self.KEY_H], fill=bar)
        draw.text((12, key_y + 8), "F1–F10 Mode", font=self._font_sm, fill=muted)
        q_label = "Q Quit"
        q_bbox = draw.textbbox((0, 0), q_label, font=self._font_sm)
        qx = 140
        qy = key_y + 6
        qw = q_bbox[2] - q_bbox[0] + 16
        qh = q_bbox[3] - q_bbox[1] + 8
        q_box = (qx, qy, qx + qw, qy + qh)
        draw.rectangle(q_box, outline=accent)
        draw.text((qx + 8, qy + 3), q_label, font=self._font_sm, fill=accent)
        self._add_hit(q_box, self.ask_quit)
        draw.text(
            (qx + qw + 16, key_y + 8),
            f"·  {self.mode_title}",
            font=self._font_sm,
            fill=muted,
        )

        st_y = h - self.STATUS_H
        draw.rectangle([0, st_y, w, h], fill=bar)
        draw.text(
            (12, st_y + 6),
            f"textual-wgpu · {w}×{h} · {self.mode_key}",
            font=self._font_sm,
            fill=accent,
        )

        if self.quit_modal:
            draw.rectangle([0, 0, w, h], fill=(0, 0, 0, 160))
            mw, mh = 360, 160
            mx = (w - mw) // 2
            my = (h - mh) // 2
            draw.rectangle(
                [mx, my, mx + mw, my + mh], fill=modal_bg, outline=accent, width=2,
            )
            draw.text((mx + 28, my + 28), "Quit?", font=self._font_lg, fill=text)
            draw.text(
                (mx + 28, my + 64),
                "Y / Enter — yes    N / Esc — no",
                font=self._font_sm,
                fill=muted,
            )
            bw, bh = 100, 34
            yes = (mx + 40, my + mh - 52, mx + 40 + bw, my + mh - 52 + bh)
            no = (mx + mw - 40 - bw, my + mh - 52, mx + mw - 40, my + mh - 52 + bh)
            draw.rectangle(yes, fill=active_bg)
            draw.text((yes[0] + 28, yes[1] + 8), "Yes", font=self._font, fill=active_fg)
            draw.rectangle(no, outline=muted)
            draw.text((no[0] + 34, no[1] + 8), "No", font=self._font, fill=text)
            self._add_hit(yes, self.confirm_quit_yes)
            self._add_hit(no, self.confirm_quit_no)

        return img

    def draw(self) -> None:
        if self._blitter is None or self._context is None:
            return
        try:
            img = self._paint()
            self._blitter.upload(img.tobytes("raw", "RGBA"), img.size)
            view = self._context.get_current_texture().create_view()
            self._blitter.draw(view)
        except Exception as exc:  # noqa: BLE001
            print(f"shell draw error: {exc}", file=sys.stderr, flush=True)
            raise


def main(theme: Theme | None = None) -> None:
    ShellApp(theme=theme or Theme.system()).run()


if __name__ == "__main__":
    main()
