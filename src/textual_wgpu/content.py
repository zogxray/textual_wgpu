"""Content — Rich/markup/Markdown/plain → ``list[Strip]``.

Single text pipeline for Label / Static / Log / Markdown / RichText:
``Content.render(width)`` → strips → cell compositor. Not a terminal;
just shared Rich→Strip conversion.
"""

from __future__ import annotations

from typing import Any

from .strip import Segment, Strip

RGBA = tuple[int, int, int, int]


def _hex_rgb(style_color) -> tuple[int, int, int] | None:
    if style_color is None:
        return None
    try:
        trip = style_color.get_truecolor()
        if trip is None:
            return None
        return int(trip.red), int(trip.green), int(trip.blue)
    except Exception:  # noqa: BLE001
        return None


def _dim(fill: RGBA, factor: float = 0.55) -> RGBA:
    r, g, b, a = fill
    return (int(r * factor), int(g * factor), int(b * factor), a)


def _style_to_segment_attrs(
    style: Any,
    default_fg: RGBA,
) -> tuple[RGBA, RGBA | None, bool, bool, bool, dict[str, Any]]:
    """→ (fg, bg, bold, italic, underline, meta)."""
    if style is None:
        return default_fg, None, False, False, False, {}
    rgb = _hex_rgb(getattr(style, "color", None))
    fg = (*rgb, 255) if rgb else default_fg
    if getattr(style, "dim", False):
        fg = _dim(fg)
    bg_rgb = _hex_rgb(getattr(style, "bgcolor", None))
    bg = (*bg_rgb, 255) if bg_rgb else None
    bold = bool(getattr(style, "bold", False))
    italic = bool(getattr(style, "italic", False))
    underline = bool(getattr(style, "underline", False))
    meta = dict(getattr(style, "meta", None) or {})
    return fg, bg, bold, italic, underline, meta


def rich_line_to_strip(
    line: Any,
    *,
    console: Any,
    default_fg: RGBA,
) -> Strip:
    """One ``rich.text.Text`` line → Strip (merged runs)."""
    plain = getattr(line, "plain", None)
    if plain is None:
        return Strip.from_text(str(line), default_fg)
    if not plain:
        return Strip([Segment("", default_fg)])
    segs: list[Segment] = []
    i = 0
    n = len(plain)
    while i < n:
        style = line.get_style_at_offset(console, i)
        fg, bg, bold, italic, underline, meta = _style_to_segment_attrs(style, default_fg)
        j = i + 1
        while j < n:
            st2 = line.get_style_at_offset(console, j)
            fg2, bg2, b2, i2, u2, m2 = _style_to_segment_attrs(st2, default_fg)
            if (fg2, bg2, b2, i2, u2, m2) != (fg, bg, bold, italic, underline, meta):
                break
            j += 1
        segs.append(Segment(
            plain[i:j], fg, bg,
            bold=bold, italic=italic, underline=underline, meta=meta or None,
        ))
        i = j
    return Strip(segs or [Segment("", default_fg)])


def rich_text_to_strips(
    content: Any,
    *,
    width: int = 80,
    default_fg: RGBA = (240, 230, 216, 255),
) -> list[Strip]:
    """``rich.text.Text`` / plain str → list of Strips (no re-wrap; split on ``\\n``)."""
    from rich.console import Console
    from rich.text import Text

    if content is None:
        return [Strip.from_text("", default_fg)]
    if isinstance(content, str):
        return [Strip.from_text(line, default_fg) for line in content.splitlines() or [""]]
    if not isinstance(content, Text):
        # Renderable → capture as Text via Console.
        try:
            console = Console(
                width=max(8, int(width)),
                height=max(64, int(width) * 4),
                force_terminal=True,
                color_system="truecolor",
                highlight=False,
                legacy_windows=False,
            )
            with console.capture() as cap:
                console.print(content, end="")
            content = Text.from_ansi(cap.get())
        except Exception:  # noqa: BLE001
            return [Strip.from_text(str(content), default_fg)]

    console = Console(
        width=max(8, int(width)),
        height=64,
        force_terminal=True,
        color_system="truecolor",
    )
    return [
        rich_line_to_strip(line, console=console, default_fg=default_fg)
        for line in content.split("\n")
    ]


def markup_to_strips(
    markup: str,
    *,
    width: int = 80,
    default_fg: RGBA = (240, 230, 216, 255),
) -> list[Strip]:
    from rich.text import Text

    return rich_text_to_strips(
        Text.from_markup(markup or ""),
        width=width,
        default_fg=default_fg,
    )


def markdown_to_strips(
    md: str,
    columns: int,
    *,
    default_fg: RGBA = (240, 230, 216, 255),
) -> list[Strip]:
    """Parse Markdown via Rich at ``columns`` cells → Strip rows."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.padding import Padding
    from rich.text import Text

    columns = max(8, int(columns))
    console = Console(
        width=columns,
        height=max(256, columns * 8),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    with console.capture() as cap:
        console.print(
            Padding(Markdown(md or "", justify="left", code_theme="monokai"), (1, 2, 1, 2)),
            end="",
        )
    rich_text = Text.from_ansi(cap.get())
    return rich_text_to_strips(rich_text, width=columns, default_fg=default_fg)


class Content:
    """Renderable text source. ``render(width)`` → ``list[Strip]`` (cached)."""

    def __init__(
        self,
        source: Any = "",
        *,
        kind: str = "auto",
    ) -> None:
        """
        ``kind``: ``auto`` | ``plain`` | ``markup`` | ``markdown`` | ``rich``.
        ``auto``: str→plain, Text→rich, else try renderable.
        """
        self._source = source
        self._kind = kind
        self._cache_key: tuple[Any, ...] | None = None
        self._cache: list[Strip] | None = None

    @classmethod
    def from_text(cls, text: str) -> Content:
        return cls(text or "", kind="plain")

    @classmethod
    def from_markup(cls, markup: str) -> Content:
        return cls(markup or "", kind="markup")

    @classmethod
    def from_markdown(cls, md: str) -> Content:
        return cls(md or "", kind="markdown")

    @classmethod
    def from_rich(cls, text: Any) -> Content:
        return cls(text, kind="rich")

    def update(self, source: Any, *, kind: str | None = None) -> None:
        self._source = source
        if kind is not None:
            self._kind = kind
        self.invalidate()

    def invalidate(self) -> None:
        self._cache = None
        self._cache_key = None

    @property
    def source(self) -> Any:
        return self._source

    def render(
        self,
        width: int = 80,
        *,
        default_fg: RGBA = (240, 230, 216, 255),
    ) -> list[Strip]:
        width = max(8, int(width))
        key = (id(self._source), self._kind, width, default_fg, str(self._source)[:64])
        if self._cache is not None and self._cache_key == key:
            return self._cache
        kind = self._kind
        src = self._source
        if kind == "auto":
            if src is None or isinstance(src, str):
                kind = "plain"
            else:
                from rich.text import Text

                kind = "rich" if isinstance(src, Text) else "rich"
        if kind == "plain":
            strips = rich_text_to_strips(str(src or ""), width=width, default_fg=default_fg)
        elif kind == "markup":
            strips = markup_to_strips(str(src or ""), width=width, default_fg=default_fg)
        elif kind == "markdown":
            strips = markdown_to_strips(str(src or ""), width, default_fg=default_fg)
        else:
            strips = rich_text_to_strips(src, width=width, default_fg=default_fg)
        self._cache = strips
        self._cache_key = key
        return strips

    def plain(self, width: int = 80, *, default_fg: RGBA = (240, 230, 216, 255)) -> str:
        return "\n".join(s.plain() for s in self.render(width, default_fg=default_fg))
