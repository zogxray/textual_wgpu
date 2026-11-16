"""Case conversion helpers (Textual ``textual.case``)."""

from __future__ import annotations

import re

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()
