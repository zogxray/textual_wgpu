"""Input & lifecycle events — 100% Textual ``textual.events`` symbol & class parity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, Type, TypeVar

import rich
from rich.style import Style

from .geometry import Offset, Size
from .message import Message

CallbackType = Callable[..., Any]
MouseEventT = TypeVar("MouseEventT", bound="MouseEvent")


@dataclass
class Event(Message):
    """Base event (subclasses ``Message`` just like Textual)."""

    def __post_init__(self) -> None:
        super().__init__()


@dataclass
class Callback(Event):
    """Callback invocation event (Textual ``events.Callback``)."""

    callback: CallbackType | None = None


@dataclass
class InputEvent(Event):
    """Base class for user input events."""


@dataclass
class Key(InputEvent):
    """Keyboard event (Textual ``events.Key``)."""

    key: str = ""
    character: str | None = None
    name: str = ""
    is_printable: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.name:
            self.name = self.key
        if self.character is None and len(self.key) == 1 and self.key.isprintable():
            self.character = self.key
            self.is_printable = True
        elif self.key == "space":
            if self.character is None:
                self.character = " "
            self.is_printable = True
        if not self.aliases:
            self.aliases = (self.key,)

    @property
    def name_aliases(self) -> list[str]:
        seen: list[str] = []
        for item in (self.name, *self.aliases):
            if item and item not in seen:
                seen.append(item)
        return seen

    def copy(self) -> Key:
        return replace(self)


@dataclass
class MouseEvent(InputEvent):
    """Base pointer event."""

    widget: Any = None
    x: int = 0
    y: int = 0
    delta_x: int = 0
    delta_y: int = 0
    button: int = 0
    shift: bool = False
    meta: bool = False
    ctrl: bool = False
    screen_x: int = 0
    screen_y: int = 0
    style: Any = None
    _meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.meta, dict):
            self._meta = dict(self.meta)
            self.meta = False

    @property
    def offset(self) -> Offset:
        return Offset(self.x, self.y)

    @property
    def delta(self) -> Offset:
        return Offset(self.delta_x, self.delta_y)

    @property
    def screen_offset(self) -> Offset:
        return Offset(self.screen_x, self.screen_y)

    @property
    def pointer_x(self) -> float:
        return float(self.x)

    @property
    def pointer_y(self) -> float:
        return float(self.y)

    @property
    def pointer_screen_x(self) -> float:
        return float(self.screen_x)

    @property
    def pointer_screen_y(self) -> float:
        return float(self.screen_y)

    def get_content_offset(self, widget: Any) -> Offset | None:
        rect = getattr(widget, "rect", None)
        if rect is None:
            return None
        return Offset(self.x - rect.x, self.y - rect.y)

    def get_content_offset_capture(self, widget: Any) -> Offset:
        rect = getattr(widget, "rect", None)
        if rect is None:
            return Offset(self.x, self.y)
        return Offset(self.x - rect.x, self.y - rect.y)

    @classmethod
    def from_event(cls: type[MouseEventT], widget: Any, event: MouseEvent) -> MouseEventT:
        return cls(
            widget=widget,
            x=event.x,
            y=event.y,
            delta_x=event.delta_x,
            delta_y=event.delta_y,
            button=event.button,
            shift=event.shift,
            meta=event.meta,
            ctrl=event.ctrl,
            screen_x=event.screen_x,
            screen_y=event.screen_y,
            style=event.style,
            _meta=dict(event._meta),
        )


# Preserve ``event.meta`` dict convenience when ``style.meta`` or ``_meta`` is populated,
# while also keeping boolean modifier compatibility if accessed on raw MouseEvent.
@dataclass
class Click(MouseEvent):
    """Pointer click (with optional Rich ``style`` / ``meta``)."""

    button: int = 1
    chain: int = 1

    @property
    def meta(self) -> dict[str, Any]:  # type: ignore[override]
        st = getattr(self.style, "meta", None)
        if isinstance(st, dict) and st:
            return st
        cm = getattr(self, "_click_meta", None)
        if isinstance(cm, dict) and cm:
            return cm
        return self._meta

    @meta.setter
    def meta(self, value: Any) -> None:
        if isinstance(value, dict):
            self._click_meta = dict(value)


@dataclass
class MouseDown(MouseEvent):
    button: int = 1


@dataclass
class MouseUp(MouseEvent):
    button: int = 1


@dataclass
class MouseMove(MouseEvent):
    pass


@dataclass
class MouseScrollDown(MouseEvent):
    pass


@dataclass
class MouseScrollUp(MouseEvent):
    pass


@dataclass
class MouseScrollLeft(MouseEvent):
    pass


@dataclass
class MouseScrollRight(MouseEvent):
    pass


@dataclass
class MouseCapture(Event):
    mouse_position: Offset = field(default_factory=lambda: Offset(0, 0))


@dataclass
class MouseRelease(Event):
    mouse_position: Offset = field(default_factory=lambda: Offset(0, 0))


@dataclass
class Enter(Event):
    node: Any = None


@dataclass
class Leave(Event):
    node: Any = None


@dataclass
class Focus(Event):
    from_app_focus: bool = False


@dataclass
class Blur(Event):
    pass


@dataclass
class DescendantFocus(Event):
    widget: Any = None


@dataclass
class DescendantBlur(Event):
    widget: Any = None


@dataclass
class AppFocus(Event):
    pass


@dataclass
class AppBlur(Event):
    pass


@dataclass
class Mount(Event):
    pass


@dataclass
class Unmount(Event):
    pass


@dataclass
class Compose(Event):
    pass


@dataclass
class Ready(Event):
    pass


@dataclass
class Show(Event):
    pass


@dataclass
class Hide(Event):
    pass


@dataclass
class Resize(Event):
    size: Size = field(default_factory=lambda: Size(0, 0))
    virtual_size: Size = field(default_factory=lambda: Size(0, 0))
    container_size: Size = field(default_factory=lambda: Size(0, 0))
    pixel_size: Size | None = None

    @classmethod
    def from_dimensions(
        cls,
        cells: tuple[int, int],
        pixels: tuple[int, int] | None = None,
    ) -> Resize:
        sz = Size(cells[0], cells[1])
        px = Size(pixels[0], pixels[1]) if pixels is not None else None
        return cls(size=sz, virtual_size=sz, container_size=sz, pixel_size=px)


@dataclass
class Paste(Event):
    text: str = ""


@dataclass
class ScreenResume(Event):
    refresh_styles: bool = False


@dataclass
class ScreenSuspend(Event):
    pass


@dataclass
class Idle(Event):
    pass


@dataclass
class Action(Event):
    action: str = ""


@dataclass
class Load(Event):
    pass


@dataclass
class Print(Event):
    text: str = ""
    stderr: bool = False


@dataclass
class CursorPosition(Event):
    x: int = 0
    y: int = 0


@dataclass
class TextSelected(Event):
    pass


@dataclass
class DeliveryComplete(Event):
    key: str = ""
    path: Path | None = None
    name: str | None = None


@dataclass
class DeliveryFailed(Event):
    key: str = ""
    exception: BaseException | None = None
    name: str | None = None


@dataclass
class Timer(Event):
    timer: Any = None
    time: float = 0.0
    count: int = 0
    callback: CallbackType | None = None
