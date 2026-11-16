"""Features — Textual ``textual.features`` parity."""

from __future__ import annotations

from typing import Literal

FeatureFlag = Literal["debug", "devtools", "headless"]


def parse_features(raw: str = "") -> set[str]:
    return {p.strip().lower() for p in (raw or "").split(",") if p.strip()}
