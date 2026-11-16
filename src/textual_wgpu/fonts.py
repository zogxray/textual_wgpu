"""Shared TrueType helpers — proportional chrome, monospace body (Textual-like cells)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import ImageFont

if TYPE_CHECKING:
    from PIL import ImageDraw

_CACHE: dict[tuple, ImageFont.ImageFont] = {}

# Optional CRT / app overrides (see ``configure``).
_MONO_PREFER: tuple[str, ...] = ()
_WIDTH_SCALE: float = 1.0

# Braille Patterns — missing from DejaVuSansMono / LiberationMono (PIL → tofu boxes).
_BRAILLE_LO = 0x2800
_BRAILLE_HI = 0x28FF


def configure(
    *,
    mono_paths: tuple[str, ...] | list[str] | None = None,
    width_scale: float | None = None,
) -> None:
    """Prefer custom mono faces (e.g. IBM VGA) and/or wider cells (CRT fontWidth)."""
    global _MONO_PREFER, _WIDTH_SCALE
    if mono_paths is not None:
        _MONO_PREFER = tuple(mono_paths)
    if width_scale is not None:
        _WIDTH_SCALE = max(0.5, float(width_scale))
    _CACHE.clear()


def _load(names: tuple[str, ...], size: int, key: tuple) -> ImageFont.ImageFont:
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    for name in names:
        try:
            f = ImageFont.truetype(name, size)
            _CACHE[key] = f
            return f
        except OSError:
            continue
    f = ImageFont.load_default()
    _CACHE[key] = f
    return f


def font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        ),
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    )
    return _load(names, size, ("sans-bold" if bold else "sans", size))


def mono(size: int, *, bold: bool = False, italic: bool = False) -> ImageFont.ImageFont:
    """UI monospace — optional CRT prefer list, then DejaVu."""
    prefer = _MONO_PREFER
    if italic and bold:
        names = prefer + (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-BoldItalic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
        )
        key = ("mono-bold-italic", size, prefer)
    elif italic:
        names = prefer + (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        )
        key = ("mono-italic", size, prefer)
    elif bold:
        names = prefer + (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
        key = ("mono-bold", size, prefer)
    else:
        names = prefer + (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
        key = ("mono", size, prefer)
    return _load(names, size, key)


def mono_braille(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Fallback mono with full Braille block (world maps)."""
    names = (
        (
            "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf"
            if bold
            else "/usr/share/fonts/truetype/freefont/FreeMono.ttf"
        ),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    )
    return _load(names, size, ("mono-braille-bold" if bold else "mono-braille", size))


def cell_width(size: int = 14) -> int:
    """Pixel width of one monospace cell — Rich columns map 1:1 to these."""
    f = mono(size)
    try:
        w = max(1, int(round(f.getlength("M"))))
    except Exception:  # noqa: BLE001
        w = max(1, size * 3 // 5)
    if _WIDTH_SCALE != 1.0:
        w = max(1, int(round(w * _WIDTH_SCALE)))
    return w


def cell_height(size: int = 14) -> int:
    """Pixel height of one monospace row (Textual terminal cell analogue)."""
    # At least font ascent+descent so glyphs are not clipped in a 1-row band.
    try:
        ascent, descent = mono(size).getmetrics()
        metric = int(ascent) + int(descent)
    except Exception:  # noqa: BLE001
        metric = size + 2
    return max(size + 2, int(round(size * 1.2)), metric)


def cells_to_px(n: int, size: int = 13) -> int:
    """Convert Textual CSS character columns to pixels."""
    return max(1, int(n) * cell_width(size))


def rows_to_px(n: int, size: int = 13) -> int:
    """Convert Textual CSS character rows to pixels."""
    return max(1, int(n) * cell_height(size))


def columns_for_width(pixel_width: int, size: int = 14) -> int:
    return max(40, int(pixel_width) // cell_width(size))


def _is_braille(ch: str) -> bool:
    if len(ch) != 1:
        return False
    o = ord(ch)
    return _BRAILLE_LO <= o <= _BRAILLE_HI


def draw_mono(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    size: int,
    fill: tuple[int, int, int, int] | tuple[int, int, int],
    bold: bool = False,
) -> float:
    """Draw monospace text; Braille via FreeMono. Advance uses primary cell width."""
    if not text:
        return 0.0
    primary = mono(size, bold=bold)
    x, y = float(xy[0]), float(xy[1])
    x0 = x
    # Fast path: no Braille → one draw call.
    if not any(_is_braille(ch) for ch in text):
        draw.text((x, y), text, font=primary, fill=fill)
        try:
            return float(primary.getlength(text))
        except Exception:  # noqa: BLE001
            return float(len(text) * cell_width(size))

    fb = mono_braille(size, bold=bold)
    cell = float(cell_width(size))
    i = 0
    n = len(text)
    while i < n:
        braille = _is_braille(text[i])
        j = i + 1
        while j < n and _is_braille(text[j]) == braille:
            j += 1
        chunk = text[i:j]
        if braille:
            # Draw per cell so FreeMono width quirks don't drift columns.
            for ch in chunk:
                draw.text((x, y), ch, font=fb, fill=fill)
                x += cell
        else:
            draw.text((x, y), chunk, font=primary, fill=fill)
            try:
                x += float(primary.getlength(chunk))
            except Exception:  # noqa: BLE001
                x += cell * len(chunk)
        i = j
    return x - x0
