"""Common types — Textual ``textual.types`` parity."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

AnimationLevel = Literal["none", "basic", "full"]
CallbackType = Callable[..., Any]
MessageTarget = Any
UnusedParameter = object()
