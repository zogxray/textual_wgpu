"""CPU CRT post-effects (tests / offline). Live UI uses GPU FX in ``blit.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def apply_crt_fx(
    img: Image.Image,
    *,
    bloom: float = 0.45,
    scanlines: float = 0.10,
) -> None:
    """Mutate ``img`` (RGBA). Prefer ``FrameBlitter.set_fx`` for realtime."""
    if img.mode != "RGBA":
        return
    bloom = max(0.0, min(1.0, float(bloom)))
    scanlines = max(0.0, min(1.0, float(scanlines)))
    if bloom > 0.01:
        _bloom(img, bloom)
    if scanlines > 0.01:
        _scanlines(img, scanlines)


def _bloom(img: Image.Image, amount: float) -> None:
    from PIL import Image as PILImage
    from PIL import ImageChops, ImageFilter

    w, h = img.size
    if w < 8 or h < 8:
        return
    sw, sh = max(1, w // 8), max(1, h // 8)
    small = img.resize((sw, sh), PILImage.BILINEAR)
    glow = small.filter(ImageFilter.GaussianBlur(radius=1.5))
    glow = glow.resize((w, h), PILImage.BILINEAR)
    screened = ImageChops.screen(img, glow)
    img.paste(PILImage.blend(img, screened, amount))


def _scanlines(img: Image.Image, amount: float) -> None:
    # Darken odd rows via raw buffer (fast enough for tests / stills).
    w, h = img.size
    factor = max(0.0, min(1.0, 1.0 - amount * 0.45))
    scale = int(factor * 256)
    buf = bytearray(img.tobytes())
    row = w * 4
    for y in range(1, h, 2):
        i0 = y * row
        i1 = i0 + row
        for i in range(i0, i1, 4):
            buf[i] = (buf[i] * scale) >> 8
            buf[i + 1] = (buf[i + 1] * scale) >> 8
            buf[i + 2] = (buf[i + 2] * scale) >> 8
    img.frombytes(bytes(buf))
