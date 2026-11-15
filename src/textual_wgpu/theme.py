"""App/system theme tokens — the library does not own brand colors.

Like Textual: the application (or the host environment) supplies style;
textual-wgpu only paints with whatever Theme it is given.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = (value or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (0, 0, 0, alpha)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _prefer_dark() -> bool:
    """Best-effort host preference (terminal / desktop). Default: dark."""
    # Explicit override for demos / CI.
    forced = (os.environ.get("TEXTUAL_WGPU_THEME") or "").strip().lower()
    if forced in ("dark", "light"):
        return forced == "dark"
    # Common terminal hint: COLORFGBG=fg;bg (0–7 classic, 15 light bg, …)
    cfg = os.environ.get("COLORFGBG") or ""
    if ";" in cfg:
        try:
            bg = int(cfg.split(";")[-1])
            # Light backgrounds are typically high ANSI indexes.
            if bg in (7, 15):
                return False
            if bg in (0, 8):
                return True
        except ValueError:
            pass
    gtk = (os.environ.get("GTK_THEME") or "").lower()
    if "dark" in gtk:
        return True
    if "light" in gtk:
        return False
    return True


@dataclass(frozen=True)
class Theme:
    """Semantic colors the shell / widgets read. All fields are #RRGGBB."""

    background: str
    surface: str
    panel: str
    boost: str
    foreground: str
    muted: str
    accent: str
    primary: str

    def rgba(self, key: str, alpha: int = 255) -> tuple[int, int, int, int]:
        return _hex_to_rgba(getattr(self, key), alpha)

    def clear_rgba_f(self) -> tuple[float, float, float, float]:
        r, g, b, a = self.rgba("background")
        return (r / 255, g / 255, b / 255, a / 255)

    @classmethod
    def system(cls) -> Theme:
        """Neutral palette derived from host light/dark preference — not an app brand."""
        if _prefer_dark():
            return cls(
                background="#1A1A1A",
                surface="#242424",
                panel="#2E2E2E",
                boost="#3A3A3A",
                foreground="#E8E8E8",
                muted="#9A9A9A",
                accent="#6CB2EB",
                primary="#5A5A5A",
            )
        return cls(
            background="#F5F5F5",
            surface="#FFFFFF",
            panel="#EBEBEB",
            boost="#E0E0E0",
            foreground="#1A1A1A",
            muted="#6A6A6A",
            accent="#0B57D0",
            primary="#C8C8C8",
        )

    @classmethod
    def from_mapping(cls, data: dict[str, str]) -> Theme:
        """Build from an application-provided map (e.g. MDGA theme tokens)."""
        base = cls.system()
        return cls(
            background=data.get("background") or data.get("bg") or base.background,
            surface=data.get("surface") or base.surface,
            panel=data.get("panel") or base.panel,
            boost=data.get("boost") or base.boost,
            foreground=data.get("foreground") or data.get("ink") or base.foreground,
            muted=data.get("muted") or base.muted,
            accent=data.get("accent") or data.get("hi") or base.accent,
            primary=data.get("primary") or base.primary,
        )
