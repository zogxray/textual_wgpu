"""FileMonitor — Textual ``textual.file_monitor`` parity."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class FileMonitor:
    """Lightweight mtime-based file watcher."""

    def __init__(self, paths: list[Path] | None = None, callback: Callable[[Path], None] | None = None) -> None:
        self.paths = list(paths or [])
        self.callback = callback
        self._mtimes: dict[Path, int] = {}

    def check(self) -> list[Path]:
        changed: list[Path] = []
        for p in self.paths:
            try:
                mt = p.stat().st_mtime_ns
            except OSError:
                continue
            if self._mtimes.get(p) != mt:
                self._mtimes[p] = mt
                changed.append(p)
                if self.callback is not None:
                    self.callback(p)
        return changed
