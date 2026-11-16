"""CRT post-FX smoke tests."""

from __future__ import annotations

from PIL import Image

from textual_wgpu import fonts
from textual_wgpu.postfx import apply_crt_fx


def test_apply_crt_fx_inplace():
    img = Image.new("RGBA", (64, 48), (0x72, 0x9F, 0xCF, 255))
    apply_crt_fx(img, bloom=0.45, scanlines=0.10)
    assert img.size == (64, 48)
    assert img.mode == "RGBA"
    # Scanlines darken odd rows — not identical to flat fill.
    assert img.getpixel((10, 1))[0] < 0x72 or img.getpixel((10, 0)) != img.getpixel((10, 1))


def test_fonts_configure_width_scale():
    fonts.configure(mono_paths=(), width_scale=1.0)
    base = fonts.cell_width(13)
    fonts.configure(width_scale=1.1)
    wide = fonts.cell_width(13)
    assert wide >= base
    fonts.configure(mono_paths=(), width_scale=1.0)  # reset for other tests
