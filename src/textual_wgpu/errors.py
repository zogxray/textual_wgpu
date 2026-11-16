"""Errors — Textual ``textual.errors`` parity."""

from __future__ import annotations


class TextualError(Exception):
    """Base error for textual-wgpu."""


class RenderError(TextualError):
    """Raised when rendering fails."""


class NoWidget(TextualError):
    """Raised when a widget cannot be found."""


class DuplicateIds(TextualError):
    """Raised on duplicate widget IDs."""


class MissingDependency(TextualError):
    """Raised when an optional dependency is missing."""
