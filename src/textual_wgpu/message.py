"""Message bus — Textual ``Message`` / bubble / ``post_message`` subset."""

from __future__ import annotations

import inspect
import re
import time
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .widget import Widget


class Message:
    """Base event. Bubbles from ``sender`` up the parent chain unless stopped."""

    bubble: ClassVar[bool] = True
    verbose: ClassVar[bool] = False
    no_dispatch: ClassVar[bool] = False
    namespace: ClassVar[str] = ""
    ALLOW_SELECTOR_MATCH: ClassVar[set[str]] = set()

    def __init__(self) -> None:
        self._stopped = False
        self._prevent_default = False
        self.sender: Any = None
        self._control: Any = None
        self.time: float = time.monotonic()
        self.is_forwarded: bool = False

    def stop(self) -> None:
        """Stop bubbling to parents."""
        self._stopped = True

    def prevent_default(self) -> None:
        """Mark default action as prevented (widget-specific)."""
        self._prevent_default = True

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def is_prevented(self) -> bool:
        return self._prevent_default

    @property
    def control(self) -> Any:
        return self._control if self._control is not None else self.sender

    @control.setter
    def control(self, value: Any) -> None:
        self._control = value

    @property
    def handler_name(self) -> str:
        return _convention_handler_names(self)[0]

    def can_replace(self, message: Message) -> bool:
        _ = message
        return False

    def set_sender(self, sender: Any) -> Message:
        self.sender = sender
        return self

    def _set_sender(self, sender: Widget) -> None:
        self.sender = sender


def _call_handler(method: Any, message: Message) -> None:
    try:
        sig = inspect.signature(method)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
        ]
        # Bound method: first param is already bound (self not in signature of bound?).
        # inspect.signature on bound method omits self.
        n = len(params)
        if n == 0:
            method()
        else:
            method(message)
    except TypeError:
        # Fallback: try with message, then without.
        try:
            method(message)
        except TypeError:
            method()


def _selector_matches(selector: str | None, message: Message) -> bool:
    if not selector:
        return True
    sender = message.sender
    if sender is None:
        return False
    sel = selector.strip()
    if sel.startswith("#"):
        return getattr(sender, "id", None) == sel[1:]
    # Type name match (optional): "OptionList"
    if sel[:1].isupper():
        return type(sender).__name__ == sel
    return getattr(sender, "id", None) == sel


def _handlers_for(cls: type) -> list[tuple[type, str | None, str]]:
    """Cache: list of (MessageType, selector, method_name).

    Cache lives on ``cls.__dict__`` only — must not inherit an empty
    cache from a base like ``Static`` / ``Widget``.
    """
    cached = cls.__dict__.get("_tw_message_handlers")
    if cached is not None:
        return cached
    found: list[tuple[type, str | None, str]] = []
    seen: set[str] = set()
    for base in cls.__mro__:
        for name, obj in vars(base).items():
            if name in seen:
                continue
            meta = getattr(obj, "_tw_on", None)
            if not meta:
                continue
            seen.add(name)
            for msg_type, selector in meta:
                found.append((msg_type, selector, name))
    cls._tw_message_handlers = found  # type: ignore[attr-defined]
    return found


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


def _convention_handler_names(message: Message) -> list[str]:
    """Textual ``on_<widget>_<msg>`` / ``on_<msg>`` handler names."""
    cls = type(message)
    msg_snake = _snake(cls.__name__)
    names: list[str] = []
    sender = getattr(message, "sender", None)
    if sender is not None:
        names.append(f"on_{_snake(type(sender).__name__)}_{msg_snake}")
    qname = getattr(cls, "__qualname__", "") or ""
    if "." in qname:
        outer = qname.rsplit(".", 2)[-2]
        if outer and outer[0].isupper():
            cand = f"on_{_snake(outer)}_{msg_snake}"
            if cand not in names:
                names.append(cand)
    names.append(f"on_{msg_snake}")
    return names


def dispatch_message(
    target: Any,
    message: Message,
    *,
    exclude: set[str] | None = None,
) -> None:
    """Invoke ``@on`` and ``on_<message>`` handlers on ``target``."""
    called: set[str] = set(exclude or ())
    for msg_type, selector, name in _handlers_for(type(target)):
        if name in called:
            continue
        if not isinstance(message, msg_type):
            continue
        if not _selector_matches(selector, message):
            continue
        method = getattr(target, name, None)
        if callable(method):
            called.add(name)
            _call_handler(method, message)
            if message.is_stopped:
                return
    for name in _convention_handler_names(message):
        if name in called:
            continue
        method = getattr(target, name, None)
        if callable(method) and not hasattr(method, "_tw_on"):
            _call_handler(method, message)
            if message.is_stopped:
                return


def post_message_now(sender: Widget, message: Message) -> None:
    """Immediate bubble (no queue). Used by the async message pump."""
    message._set_sender(sender)
    node: Widget | None = sender
    while node is not None:
        dispatch_message(node, message)
        if message.is_stopped or not getattr(type(message), "bubble", True):
            break
        node = getattr(node, "parent", None)
    # App is not always in the parent chain; ModeApp mounts Screen as root.
    if not message.is_stopped and sender.app is not None:
        app = sender.app
        if app is not sender and app is not node:
            dispatch_message(app, message)


def post_message(sender: Widget, message: Message) -> None:
    """Bubble ``message`` — enqueue on the app asyncio pump when running."""
    app = getattr(sender, "app", None)
    queue = getattr(app, "_message_queue", None) if app is not None else None
    if (
        app is not None
        and queue is not None
        and getattr(app, "_running", False)
        and getattr(app, "_loop", None) is not None
    ):
        message._set_sender(sender)
        try:
            queue.put_nowait((sender, message))
            return
        except Exception:  # noqa: BLE001
            pass
    post_message_now(sender, message)
