"""App/system theme tokens — Textual Theme parity + system palette."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = (value or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (0, 0, 0, alpha)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _prefer_dark() -> bool:
    """Best-effort host preference (terminal / desktop). Default: dark."""
    forced = (os.environ.get("TEXTUAL_WGPU_THEME") or "").strip().lower()
    if forced in ("dark", "light"):
        return forced == "dark"
    cfg = os.environ.get("COLORFGBG") or ""
    if ";" in cfg:
        try:
            bg = int(cfg.split(";")[-1])
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


def _blend_hex(a: str, b: str, t: float = 0.5) -> str:
    ra, ga, ba, _ = _hex_to_rgba(a)
    rb, gb, bb, _ = _hex_to_rgba(b)
    return "#{:02X}{:02X}{:02X}".format(
        int(ra + (rb - ra) * t),
        int(ga + (gb - ga) * t),
        int(ba + (bb - ba) * t),
    )


@dataclass(frozen=True)
class Theme:
    """Semantic colors compatible with Textual ``Theme(...)`` + wgpu tokens."""

    name: str = "textual-wgpu"
    primary: str = "#5A5A5A"
    secondary: str | None = None
    warning: str | None = None
    error: str | None = None
    success: str | None = None
    accent: str = "#6CB2EB"
    foreground: str = "#E8E8E8"
    background: str = "#1A1A1A"
    surface: str = "#242424"
    panel: str = "#2E2E2E"
    boost: str = "#3A3A3A"
    muted: str = ""
    dark: bool = True
    luminosity_spread: float = 0.15
    text_alpha: float = 0.95
    variables: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.muted:
            object.__setattr__(
                self, "muted", _blend_hex(self.foreground, self.background, 0.45),
            )
        if self.secondary is None:
            object.__setattr__(self, "secondary", self.primary)
        if self.warning is None:
            object.__setattr__(self, "warning", "#D4AD72")
        if self.error is None:
            object.__setattr__(self, "error", "#B04A4A")
        if self.success is None:
            object.__setattr__(self, "success", "#4E9A66")

    def rgba(self, key: str, alpha: int = 255) -> tuple[int, int, int, int]:
        val = getattr(self, key, None)
        if isinstance(val, str):
            return _hex_to_rgba(val, alpha)
        if key in self.variables:
            return _hex_to_rgba(self.variables[key], alpha)
        return _hex_to_rgba(self.foreground, alpha)

    def clear_rgba_f(self) -> tuple[float, float, float, float]:
        r, g, b, a = self.rgba("background")
        return (r / 255, g / 255, b / 255, a / 255)

    @classmethod
    def system(cls) -> Theme:
        """Neutral palette derived from host light/dark preference."""
        if _prefer_dark():
            return cls(
                name="system-dark",
                background="#1A1A1A",
                surface="#242424",
                panel="#2E2E2E",
                boost="#3A3A3A",
                foreground="#E8E8E8",
                muted="#9A9A9A",
                accent="#6CB2EB",
                primary="#5A5A5A",
                dark=True,
            )
        return cls(
            name="system-light",
            background="#F5F5F5",
            surface="#FFFFFF",
            panel="#EBEBEB",
            boost="#E0E0E0",
            foreground="#1A1A1A",
            muted="#6A6A6A",
            accent="#0B57D0",
            primary="#C8C8C8",
            dark=False,
        )

    @classmethod
    def from_mapping(cls, data: dict[str, str], *, name: str = "custom") -> Theme:
        """Build from an application-provided map (e.g. MDGA theme tokens)."""
        base = cls.system()
        return cls(
            name=name,
            background=data.get("background") or data.get("bg") or base.background,
            surface=data.get("surface") or base.surface,
            panel=data.get("panel") or base.panel,
            boost=data.get("boost") or base.boost,
            foreground=data.get("foreground") or data.get("ink") or base.foreground,
            muted=data.get("muted") or base.muted,
            accent=data.get("accent") or data.get("hi") or base.accent,
            primary=data.get("primary") or base.primary,
        )


BUILTIN_THEMES: dict[str, Theme] = {
    "textual-dark": Theme(
        name="textual-dark",
        primary="#0178D4",
        secondary="#004578",
        accent="#FFA62B",
        foreground="#E0E0E0",
        background="#121212",
        surface="#1E1E1E",
        panel="#262626",
        boost="#2E2E2E",
        muted="#8A8A8A",
        dark=True,
    ),
    "textual-light": Theme(
        name="textual-light",
        primary="#004578",
        secondary="#0178D4",
        accent="#0178D4",
        foreground="#1A1A1A",
        background="#F2F2F2",
        surface="#FFFFFF",
        panel="#E5E5E5",
        boost="#D8D8D8",
        muted="#6A6A6A",
        dark=False,
    ),
}
