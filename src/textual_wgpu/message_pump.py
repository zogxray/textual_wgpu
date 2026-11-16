"""MessagePump — Textual ``textual.message_pump`` parity."""

from __future__ import annotations

from typing import Any


class NoActiveAppError(RuntimeError):
    """Raised when accessing ``app`` without an active App."""


class MessagePump:
    """Base message pump contract."""

    def __init__(self) -> None:
        self._parent: Any = None

    def post_message(self, message: Any) -> bool:
        from .message import post_message as _post

        _post(self, message)
        return True
