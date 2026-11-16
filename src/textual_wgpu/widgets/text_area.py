"""Re-export ``TextArea`` / ``Selection`` (Textual ``textual.widgets.text_area``)."""

from ..document import Selection
from .textarea import TextArea

__all__ = ["Selection", "TextArea"]
