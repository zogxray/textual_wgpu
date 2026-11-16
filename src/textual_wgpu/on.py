"""``@on(MessageType, selector?)`` — Textual ``textual.on`` subset."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def on(message_type: type, selector: str | None = None) -> Callable[[F], F]:
    """Declare a message handler on a Screen/Widget/App method.

    Examples::

        @on(OptionList.OptionHighlighted, "#jobs")
        def _hi(self, event: OptionList.OptionHighlighted) -> None:
            ...

        @on(Input.Submitted, "#search")
        def _go(self) -> None:
            ...
    """

    def decorator(fn: F) -> F:
        meta = getattr(fn, "_tw_on", None)
        if meta is None:
            meta = []
            setattr(fn, "_tw_on", meta)
        meta.append((message_type, selector))
        # Invalidate handler cache on owning class when defined — cleared lazily
        # via first dispatch; also clear if already set on function's qualname class.
        return fn

    return decorator
