"""Logging handler & ``log`` callable — Textual ``textual.logging`` parity."""

from __future__ import annotations

import logging
from typing import Any


class TextualHandler(logging.Handler):
    """Minimal logging handler compatible with Textual ``TextualHandler``."""

    def emit(self, record: logging.LogRecord) -> None:
        _ = self.format(record)


class Logger:
    """Textual-style callable ``textual.log`` logger."""

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def info(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def debug(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def warning(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def error(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs


log = Logger()
