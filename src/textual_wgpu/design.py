"""Design / ColorSystem — Textual ``textual.design`` parity."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ColorSystem:
    primary: str = "#0178D4"
    secondary: str = "#004578"
    warning: str = "#FFA62B"
    error: str = "#BA3C5B"
    success: str = "#4EBF71"
    accent: str = "#FFA62B"
    foreground: str = "#E0E0E0"
    background: str = "#121212"
    surface: str = "#1E1E1E"
    panel: str = "#262626"
    boost: str = "#2E2E2E"
    dark: bool = True
    variables: dict[str, str] = field(default_factory=dict)
