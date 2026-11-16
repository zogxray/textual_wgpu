"""Widget base — Textual-flavoured, painted by the app runtime."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from .geometry import Rect, Size

_COMPOSE_STACK: contextvars.ContextVar[list[list[Widget]]] = contextvars.ContextVar(
    "_tw_compose_stack", default=[],
)

if TYPE_CHECKING:
    from PIL import ImageDraw

    from .await_mount import AwaitMount
    from .selection import Selection
    from .strip import Strip
    from .theme import Theme


def _wants_key_event(fn: Any) -> bool:
    """True if ``fn`` expects ``events.Key`` rather than a raw ``key: str``."""
    import inspect

    cls_fn = getattr(fn, "__func__", fn)
    cached = getattr(cls_fn, "_tw_wants_key_event", None)
    if cached is not None:
        return bool(cached)
    wants = False
    try:
        sig = inspect.signature(fn)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if params:
            p0 = params[0]
            ann = str(p0.annotation)
            if "Key" in ann or p0.name == "event":
                wants = True
    except (TypeError, ValueError):
        pass
    try:
        setattr(cls_fn, "_tw_wants_key_event", wants)
    except Exception:  # noqa: BLE001
        pass
    return wants


def dispatch_on_key(target: Any, key: Any) -> bool:
    """Call ``target.on_key`` adapting ``str`` vs ``events.Key``."""
    from .events import Key

    fn = getattr(target, "on_key", None)
    if not callable(fn):
        return False
    key_str = key.key if isinstance(key, Key) else str(key)
    if _wants_key_event(fn):
        ev = key if isinstance(key, Key) else Key(key=key_str)
        res = fn(ev)
        return bool(ev.is_stopped or res)
    return bool(fn(key_str))


def dispatch_event(target: Any, event: Any, method_name: str | None = None) -> bool:
    """Invoke ``method_name`` (adapting 0-arg vs 1-arg event) and ``@on`` handlers."""
    from .message import dispatch_message

    if target is None:
        return False
    if method_name:
        fn = getattr(target, method_name, None)
        if callable(fn):
            import inspect

            try:
                sig = inspect.signature(fn)
                params = [
                    p for p in sig.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                ]
                res = fn() if len(params) == 0 else fn(event)
            except TypeError:
                try:
                    res = fn(event)
                except TypeError:
                    res = fn()
            if inspect.isawaitable(res):
                try:
                    asyncio.ensure_future(res)
                except RuntimeError:
                    pass
    dispatch_message(target, event, exclude={method_name} if method_name else None)
    return bool(getattr(event, "is_stopped", False))


class Widget:
    """Base node. Subclasses paint into a clipped rect.

    Preferred paint path (new widgets): override ``render_lines`` / ``render_line``
    to emit ``Strip`` rows; ``paint`` composites them via the cell compositor.
    Legacy widgets may still override ``paint`` directly.

    DOM lifecycle: ``mount`` / ``unmount`` / ``mount_child`` / ``remove`` /
    ``refresh`` / ``recompose`` (on Screen). Children lists are live when the
    subclass exposes a mutable ``_children`` / ``_composed`` list.

    Awaitables: ``mount_child`` / ``mount_widgets`` return ``AwaitMount``;
    ``wait_for_refresh`` waits for the next refresh cycle.

    Selection: opt in with ``ALLOW_SELECT = True`` (Markdown / RichText / …).
    TextArea keeps its own selection and sets ``ALLOW_SELECT = False``.
    """

    BINDINGS: list[Any] = []
    DEFAULT_CSS: str = ""
    BORDER_TITLE: str | None = None
    BORDER_SUBTITLE: str | None = None
    ALLOW_MAXIMIZE: bool = True
    BINDING_GROUP_TITLE: str | None = None
    BLANK: str = ""
    COMPONENT_CLASSES: set[str] = set()
    DEFAULT_CLASSES: str = ""
    ExpectType = Any
    FOCUS_ON_CLICK: bool | None = None
    HELP: str | None = None
    SCOPED_CSS: bool = True
    WalkType = Any
    # Default instance-level attrs declared on class for descriptor/introspection parity
    app: Any = None
    parent: Widget | None = None
    id: str | None = None
    name: str | None = None
    disabled: bool = False
    classes: set[str] = set()
    border_title: str | None = None
    border_subtitle: str | None = None
    tooltip: str | None = None
    scroll_x: float = 0
    scroll_y: float = 0
    scroll_target_x: float = 0
    scroll_target_y: float = 0
    is_modal: bool = False
    # Default cell font size for Strip compositor path.
    RENDER_FONT_SIZE = 13
    # Screen-level drag select (Textual ``ALLOW_SELECT``). Off by default —
    # content bodies (Markdown, RichText, Static, Log) opt in.
    ALLOW_SELECT: bool = False
    # Textual ``can_focus`` — Tab / click focus chain (override on interactive widgets).
    can_focus: bool = False
    can_focus_children: bool = True

    def __init_subclass__(
        cls,
        *,
        can_focus: bool | None = None,
        can_focus_children: bool | None = None,
        inherit_bindings: bool = True,
        **kwargs: Any,
    ) -> None:
        _ = inherit_bindings
        super().__init_subclass__(**kwargs)
        if can_focus is not None:
            cls.can_focus = bool(can_focus)
        if can_focus_children is not None:
            cls.can_focus_children = bool(can_focus_children)

    def __init__(
        self,
        *,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.id = id
        self.rect = Rect(0, 0, 0, 0)
        self.app: Any = None  # ModeApp, set on mount
        self.parent: Widget | None = None
        self._mounted = False
        self._mounted_event: asyncio.Event | None = None
        # CSS classes (``.foo`` selectors).
        self.classes: set[str] = {
            c.strip().lstrip(".") for c in str(classes or "").split() if c.strip()
        }
        # Resolved CSS declarations (Styles dict proxy: supports dict & attribute access).
        from .css import Styles

        self.styles: Any = Styles(owner=self)
        self.border_title: str | None = None
        self.border_subtitle: str | None = None
        # Layout hints (also set by CSS engine / imperative code).
        self.layout_hint_w: int | None = None
        self.layout_hint_h: int | None = None
        self.layout_hint_cells: int | None = None
        self.layout_hint_rows: int | None = None
        self.layout_min_cells: int | None = None
        self.layout_frac: float | None = None  # width percent 0-1
        self.layout_width_fr: float | None = None
        self.layout_height_fr: float | None = None
        self.layout_dock: str | None = None  # top|right|bottom|left
        self.layout_layer: int | None = None
        self.disabled: bool = bool(disabled)
        # Focus state (Textual ``has_focus`` / ``:focus``).
        self._focused: bool = False
        # Deferred callbacks after next refresh/layout.
        self._after_refresh: list[Callable[[], None]] = []
        # Compose & tooltip / loading state.
        self._composed: list[Widget] | None = None
        self._composed_built: bool = False
        self._prev_size: tuple[int, int] = (0, 0)
        self.tooltip: str | None = None
        self._loading: bool = False
        self._loading_widget: Any = None

    @property
    def size(self) -> Size:
        return Size(self.rect.w, self.rect.h)

    @property
    def content_size(self) -> Size:
        return Size(self.rect.w, self.rect.h)

    def __enter__(self) -> Widget:
        stack = list(_COMPOSE_STACK.get())
        collected: list[Widget] = []
        stack.append(collected)
        self._compose_token = _COMPOSE_STACK.set(stack)
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        stack = list(_COMPOSE_STACK.get())
        collected = stack.pop() if stack else []
        tok = getattr(self, "_compose_token", None)
        if tok is not None:
            _COMPOSE_STACK.reset(tok)
            delattr(self, "_compose_token")
        else:
            _COMPOSE_STACK.set(stack)
        store = self._child_store()
        if store is not None and collected:
            store.extend(collected)
        elif hasattr(self, "child") and len(collected) == 1 and hasattr(self, "_raw_children"):
            self._raw_children = list(collected)
            self.child = collected[0]
        elif hasattr(self, "child") and len(collected) > 1 and hasattr(self, "_raw_children"):
            from .widgets.containers import Vertical

            self._raw_children = list(collected)
            self.child = Vertical(*collected)
        # Attach this container to its enclosing compose block (Textual ``with Container():``).
        parent_stack = _COMPOSE_STACK.get()
        if parent_stack:
            parent_stack[-1].append(self)

    def add_class(self, *names: str) -> None:
        for n in names:
            n = str(n).strip().lstrip(".")
            if n:
                self.classes.add(n)
        self.refresh(layout=True)

    def remove_class(self, *names: str) -> None:
        for n in names:
            n = str(n).strip().lstrip(".")
            self.classes.discard(n)
        self.refresh(layout=True)

    def has_class(self, name: str) -> bool:
        return str(name).lstrip(".") in self.classes

    def set_class(self, name: str, add: bool = True) -> None:
        if add:
            self.add_class(name)
        else:
            self.remove_class(name)

    @property
    def focused(self) -> bool:
        """True if this widget currently has input focus."""
        return bool(self._focused)

    def focus(self, scroll_visible: bool = True) -> Widget:
        """Give focus to this widget (Textual ``Widget.focus``).

        ``scroll_visible`` is accepted for API parity; parent scroll-into-view
        is best-effort / future work.
        """
        _ = scroll_visible
        app = self.app
        if app is not None and hasattr(app, "set_focus"):
            app.set_focus(self)
        return self

    def blur(self) -> Widget:
        """Remove focus from this widget (Textual ``Widget.blur``)."""
        app = self.app
        if app is not None and getattr(app, "_focus", None) is self:
            if hasattr(app, "set_focus"):
                app.set_focus(None)
        return self

    @property
    def theme(self) -> Theme:
        if self.app is not None:
            return self.app.theme
        from .theme import Theme

        return Theme.system()

    @property
    def allow_select(self) -> bool:
        """Whether screen-level text selection is enabled for this widget."""
        if not self.ALLOW_SELECT:
            return False
        app = self.app
        if app is not None and not getattr(type(app), "ALLOW_SELECT", True):
            return False
        if self.disabled:
            return False
        return True

    @property
    def text_selection(self) -> Selection | None:
        """Current screen selection for this widget, if any."""
        app = self.app
        if app is None:
            return None
        screen = getattr(app, "screen", None)
        if screen is None:
            return None
        selections = getattr(screen, "selections", None)
        if not isinstance(selections, dict):
            return None
        return selections.get(self)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract ``(text, separator)`` under ``selection``, or None."""
        from .selection import strips_plain

        strips = self.render_lines()
        if not strips:
            return None
        text = strips_plain(strips)
        if not text:
            return None
        extracted = selection.extract(text)
        if not extracted:
            return None
        return extracted, "\n"

    def select_all(self) -> None:
        """Select all content of this widget (screen-level)."""
        from .selection import SELECT_ALL

        app = self.app
        if app is None or not self.allow_select:
            return
        screen = getattr(app, "screen", None)
        if screen is None or not hasattr(screen, "selections"):
            return
        screen.selections = {self: SELECT_ALL}
        if hasattr(app, "request_draw"):
            app.request_draw()

    @property
    def is_mounted(self) -> bool:
        return self._mounted

    @property
    def is_attached(self) -> bool:
        return bool(self._mounted and self.app is not None)

    @property
    def is_disabled(self) -> bool:
        return bool(self.disabled)

    @property
    def screen(self) -> Any:
        node: Any = self
        while node is not None:
            if getattr(node, "_is_screen", False):
                return node
            node = getattr(node, "parent", None)
        return getattr(self.app, "screen", None) if self.app is not None else None

    @property
    def ancestors(self) -> list[Widget]:
        out: list[Widget] = []
        node = self.parent
        while node is not None:
            out.append(node)
            node = getattr(node, "parent", None)
        return out

    @property
    def ancestors_with_self(self) -> list[Widget]:
        return [self, *self.ancestors]

    @property
    def siblings(self) -> list[Widget]:
        if self.parent is None:
            return []
        return [c for c in self.parent.children if c is not self]

    def _get_mounted_event(self) -> asyncio.Event:
        if self._mounted_event is None:
            self._mounted_event = asyncio.Event()
            if self._mounted:
                self._mounted_event.set()
        return self._mounted_event

    async def _wait_mounted(self) -> None:
        """Wait until this widget has finished ``mount`` / ``on_mount``."""
        if self._mounted:
            return
        await self._get_mounted_event().wait()

    def _child_store(self) -> list[Widget] | None:
        """Mutable child list if this node supports live DOM edits."""
        for attr in ("_children", "_composed"):
            store = getattr(self, attr, None)
            if isinstance(store, list):
                return store
        return None

    def compose(self) -> Iterable[Widget]:
        """Yield child widgets when subclassing ``Widget`` directly."""
        yield from ()

    def _collect_compose(self) -> list[Widget]:
        out: list[Widget] = []
        tok = _COMPOSE_STACK.set([out])
        try:
            for item in self.compose():
                stack = _COMPOSE_STACK.get()
                if stack:
                    stack[-1].append(item)
                else:
                    out.append(item)
        finally:
            _COMPOSE_STACK.reset(tok)
        return out

    def _ensure_composed(self) -> None:
        if self._composed_built:
            return
        self._composed_built = True
        if type(self).compose is not Widget.compose:
            items = self._collect_compose()
            if hasattr(self, "_children") and isinstance(self._children, list):
                self._children.extend(items)
            else:
                self._composed = items
            from .events import Compose

            dispatch_event(self, Compose(), "on_compose")

    def recompose(self) -> None:
        """Rebuild children from ``compose()`` (unmount old → mount new)."""
        app = self.app
        store = self._child_store()
        if store is not None:
            for child in list(store):
                child.unmount()
            store.clear()
        self._composed_built = False
        self._ensure_composed()
        if app is not None:
            for child in list(self.children):
                child._attach(app, self)
        self.refresh(layout=True)

    def _attach(self, app: Any, parent: Widget | None = None) -> None:
        """Attach to ``app`` / ``parent``, attach descendants, then ``on_mount``."""
        from .events import Mount

        self.app = app
        self.parent = parent
        self._mounted = True
        self._ensure_composed()
        for child in list(self.children):
            child._attach(app, self)
        try:
            dispatch_event(self, Mount(), "on_mount")
        except Exception as exc:  # noqa: BLE001
            print(f"on_mount error ({type(self).__name__}): {exc}", flush=True)
        self._get_mounted_event().set()

    def mount(
        self,
        *widgets: Any,
        before: Widget | int | str | None = None,
        after: Widget | int | str | None = None,
    ) -> Any:
        """Mount child widgets (Textual ``Widget.mount(*widgets)``) or attach to ``app``."""
        _ = after
        if widgets and not isinstance(widgets[0], Widget):
            app = widgets[0]
            parent = widgets[1] if len(widgets) > 1 else None
            self._attach(app, parent)
            return None
        b = before if isinstance(before, Widget) else None
        return self.mount_widgets(*widgets, before=b)

    def unmount(self) -> None:
        """Detach subtree from the live DOM (bottom-up ``on_unmount``)."""
        from .events import Unmount

        for child in list(self.children):
            child.unmount()
        try:
            dispatch_event(self, Unmount(), "on_unmount")
        except Exception as exc:  # noqa: BLE001
            print(f"on_unmount error ({type(self).__name__}): {exc}", flush=True)
        store = None
        if self.parent is not None:
            store = self.parent._child_store()
            if store is not None and self in store:
                store.remove(self)
        self._mounted = False
        if self._mounted_event is not None:
            self._mounted_event.clear()
        self.app = None
        self.parent = None

    def on_mount(self) -> None:
        """Hook after this node and its children are mounted."""

    def on_unmount(self) -> None:
        """Hook before this node is detached."""

    def mount_child(self, child: Widget, *, before: Widget | None = None) -> AwaitMount:
        """Append (or insert) a child into the live tree and mount it.

        Returns an ``AwaitMount`` (optionally awaitable) for the new child.
        """
        from .await_mount import AwaitMount

        store = self._child_store()
        if store is None:
            raise TypeError(
                f"{type(self).__name__} has no mutable child list "
                f"(need _children / _composed)"
            )
        if before is not None and before in store:
            store.insert(store.index(before), child)
        else:
            store.append(child)
        if self.app is not None:
            child._attach(self.app, self)
        else:
            child.parent = self
            # Not yet attached to an app — treat as mounted for AwaitMount.
            child._mounted = True
            child._get_mounted_event().set()
        self.refresh(layout=True)
        return AwaitMount(self, [child])

    def mount_widgets(
        self,
        *widgets: Widget,
        before: Widget | None = None,
    ) -> AwaitMount:
        """Mount one or more children (Textual ``Widget.mount`` analogue)."""
        from .await_mount import AwaitMount

        if not widgets:
            return AwaitMount(self, [])
        store = self._child_store()
        if store is None:
            raise TypeError(
                f"{type(self).__name__} has no mutable child list "
                f"(need _children / _composed)"
            )
        if before is not None and before in store:
            idx = store.index(before)
            for i, w in enumerate(widgets):
                store.insert(idx + i, w)
        else:
            store.extend(widgets)
        for w in widgets:
            if self.app is not None:
                w._attach(self.app, self)
            else:
                w.parent = self
                w._mounted = True
                w._get_mounted_event().set()
        self.refresh(layout=True)
        return AwaitMount(self, list(widgets))

    def mount_all(self, widgets: Iterable[Widget]) -> AwaitMount:
        """Mount an iterable of children; returns ``AwaitMount``."""
        return self.mount_widgets(*list(widgets))

    def remove(self) -> None:
        """Remove this widget from its parent (and unmount)."""
        if self.parent is None and not self._mounted:
            return
        self.unmount()
        # Parent refresh if still live.
        # (unmount already dropped parent link)

    def remove_children(self) -> None:
        store = self._child_store()
        if store is None:
            return
        for child in list(store):
            child.unmount()
        store.clear()
        self.refresh(layout=True)

    @property
    def children(self) -> list[Widget]:
        store = self._child_store()
        return list(store) if store is not None else []

    def layout(self, rect: Rect) -> None:
        old_size = self._prev_size
        self.rect = rect
        self.layout_children()
        new_size = (rect.w, rect.h)
        if new_size != old_size and new_size != (0, 0):
            self._prev_size = new_size
            if self._mounted and hasattr(self, "on_resize"):
                from .events import Resize

                sz = Size(rect.w, rect.h)
                dispatch_event(self, Resize(size=sz, virtual_size=sz, container_size=sz), "on_resize")
        if self._after_refresh:
            pending = list(self._after_refresh)
            self._after_refresh.clear()
            for cb in pending:
                try:
                    cb()
                except Exception as exc:  # noqa: BLE001
                    print(f"call_after_refresh error: {exc}", flush=True)

    def layout_children(self) -> None:
        kids = self.children
        if kids:
            from .layout import layout_children as _layout

            _layout(self, kids)

    def get_content_width(self) -> int | None:
        """Optional intrinsic width in px for ``height/width: auto``."""
        return None

    def get_content_height(self) -> int | None:
        """Optional intrinsic height in px for ``height/width: auto``."""
        return None

    def render_line(self, y: int) -> Strip | None:
        """Return the Strip for row ``y``, or None. Override for strip paint."""
        return None

    def render_lines(self) -> list[Strip]:
        """All content strips (preferred paint path). Default: gather ``render_line``."""
        out: list[Strip] = []
        line = self.render_line(0)
        if line is not None:
            out.append(line)
            y = 1
            while True:
                nxt = self.render_line(y)
                if nxt is None:
                    break
                out.append(nxt)
                y += 1
                if y > 10_000:
                    break
        return out

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        strips = self.render_lines()
        if strips:
            from .compositor import paint_widget_strips

            paint_widget_strips(
                draw,
                self,
                strips,
                font_size=getattr(self, "RENDER_FONT_SIZE", 13),
            )
            return
        kids = list(self.children)

        def _layer_key(item: tuple[int, Widget]) -> tuple[int, int]:
            i, c = item
            layer = getattr(c, "layout_layer", None)
            if layer is None:
                layer = (c.styles or {}).get("layer", 10)
            return (int(layer), i)

        for _i, child in sorted(enumerate(kids), key=_layer_key):
            if (child.styles or {}).get("display") == "none":
                continue
            child.paint(draw)
        if self._loading:
            lw = self.get_loading_widget()
            lw.app = self.app
            lw.layout(self.rect)
            lw.paint(draw)

    def hits(self) -> list[tuple[Rect, Callable[[int, int], None]]]:
        out: list[tuple[Rect, Callable[[int, int], None]]] = []
        for child in self.children:
            out.extend(child.hits())
        return out

    def on_key(self, key: str) -> bool:
        """Return True if handled."""
        for child in self.children:
            if dispatch_on_key(child, key):
                return True
        return False

    def run_worker(
        self,
        fn: Callable[..., Any],
        *,
        name: str = "worker",
        exclusive: bool = False,
        group: str | None = None,
        thread: bool | None = None,
    ) -> Any:
        if self.app is None:
            raise RuntimeError("Widget must be mounted to run a worker")
        return self.app.spawn_worker(
            fn, name=name, exclusive=exclusive, group=group, thread=thread,
        )

    def set_interval(self, seconds: float, callback: Callable[[], None], **kw: Any) -> Any:
        if self.app is None:
            raise RuntimeError("Widget must be mounted to set an interval")
        return self.app.set_interval(seconds, callback, **kw)

    def set_timer(self, seconds: float, callback: Callable[[], None]) -> Any:
        if self.app is None:
            raise RuntimeError("Widget must be mounted to set a timer")
        return self.app.set_timer(seconds, callback)

    def mutate_reactive(self, reactive_attr: Any) -> None:
        name = getattr(reactive_attr, "name", str(reactive_attr))
        val = getattr(self, name, None)
        from .reactive import _run_watchers

        _run_watchers(self, name, val, val)
        self.refresh()

    def set_reactive(self, reactive_attr: Any, value: Any) -> None:
        name = getattr(reactive_attr, "name", str(reactive_attr))
        priv = f"_reactive_{name}"
        setattr(self, priv, value)

    def watch(self, obj: Any, attribute_name: str, callback: Callable[..., Any]) -> None:
        from .reactive import watch as _watch

        _watch(obj, attribute_name, callback)

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float = 3.0,
        markup: bool = True,
    ) -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            self.app.notify(
                message, title=title, severity=severity, timeout=timeout, markup=markup,
            )

    def run_action(self, action: str) -> bool:
        if self.app is not None and hasattr(self.app, "run_action"):
            return bool(self.app.run_action(action))
        from .actions import run_action_on

        return run_action_on([self], action)

    def check_action(self, action: str, parameters: tuple[Any, ...]) -> bool | None:
        _ = action, parameters
        return True

    def call_later(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        fn = (lambda: callback(*args, **kwargs)) if (args or kwargs) else callback
        if self.app is not None and hasattr(self.app, "call_later"):
            self.app.call_later(0.0, fn)
        else:
            fn()

    def call_next(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.call_after_refresh(callback, *args, **kwargs)

    @property
    def loading(self) -> bool:
        return self._loading

    @loading.setter
    def loading(self, value: bool) -> None:
        self.set_loading(value)

    def get_loading_widget(self) -> Widget:
        if self._loading_widget is None:
            from .widgets.loading_indicator import LoadingIndicator

            self._loading_widget = LoadingIndicator()
            self._loading_widget.app = self.app
        return self._loading_widget

    def set_loading(self, loading: bool) -> Widget:
        self._loading = bool(loading)
        if self._loading:
            lw = self.get_loading_widget()
            lw.app = self.app
            if self.rect.w > 0 and self.rect.h > 0:
                lw.layout(self.rect)
        self.refresh()
        return self

    def with_tooltip(self, tooltip: str | None) -> Widget:
        self.tooltip = tooltip
        return self

    def capture_mouse(self, capture: bool = True) -> None:
        if self.app is not None and hasattr(self.app, "capture_mouse"):
            self.app.capture_mouse(self if capture else None)

    def release_mouse(self) -> None:
        if self.app is not None and hasattr(self.app, "capture_mouse"):
            self.app.capture_mouse(None)

    def get_widgets_at(self, x: int, y: int) -> list[tuple[Widget, Rect]]:
        out: list[tuple[Widget, Rect]] = []
        for w in self.walk_descendants(include_self=True):
            if not w.display:
                continue
            if w.rect.w > 0 and w.rect.h > 0 and w.rect.contains(x, y):
                out.append((w, w.rect))
        return list(reversed(out))

    def get_widget_at(self, x: int, y: int) -> tuple[Widget, Rect]:
        hits = self.get_widgets_at(x, y)
        if not hits:
            raise KeyError(f"No widget at ({x}, {y})")
        return hits[0]

    def can_view_entire(self, widget: Widget) -> bool:
        return (
            widget.rect.x >= self.rect.x
            and widget.rect.y >= self.rect.y
            and widget.rect.x1 <= self.rect.x1
            and widget.rect.y1 <= self.rect.y1
        )

    def scroll_to_widget(self, widget: Widget, *, animate: bool = True, top: bool = False) -> None:
        _ = animate, top
        widget.scroll_visible()

    def can_view_partial(self, widget: Widget) -> bool:
        return self.rect.overlaps(widget.rect)

    # --- Full Textual Widget/DOMNode/MessagePump API parity -------------------

    def query_children(self, selector: str | type = "*"):
        from .dom import DOMQuery, matches_selector

        return DOMQuery([c for c in self.children if matches_selector(c, selector)])

    def get_child_by_type(self, expect_type: type) -> Any:
        for c in self.children:
            if isinstance(c, expect_type):
                return c
        raise KeyError(f"No direct child of type {expect_type.__name__}")

    def move_child(self, child: Widget, *, before: Widget | int | None = None, after: Widget | int | None = None) -> None:
        store = self._child_store()
        if store is None or child not in store:
            return
        store.remove(child)
        if isinstance(before, Widget) and before in store:
            store.insert(store.index(before), child)
        elif isinstance(before, int):
            store.insert(max(0, min(len(store), before)), child)
        elif isinstance(after, Widget) and after in store:
            store.insert(store.index(after) + 1, child)
        elif isinstance(after, int):
            store.insert(max(0, min(len(store), after + 1)), child)
        else:
            store.append(child)
        self.refresh(layout=True)

    def sort_children(self, *, key: Callable[[Widget], Any] | None = None, reverse: bool = False) -> None:
        store = self._child_store()
        if store is not None:
            store.sort(key=key or (lambda w: w.id or ""), reverse=reverse)
            self.refresh(layout=True)

    def set_classes(self, classes: str | Iterable[str]) -> Widget:
        if isinstance(classes, str):
            self.classes = {c.strip().lstrip(".") for c in classes.split() if c.strip()}
        else:
            self.classes = {str(c).strip().lstrip(".") for c in classes if str(c).strip()}
        self.refresh(layout=True)
        return self

    def toggle_class(self, *names: str) -> Widget:
        for n in names:
            k = str(n).strip().lstrip(".")
            if k in self.classes:
                self.classes.discard(k)
            elif k:
                self.classes.add(k)
        self.refresh(layout=True)
        return self

    def update_classes(self) -> None:
        self.refresh(layout=True)

    def get_pseudo_classes(self) -> set[str]:
        out: set[str] = set()
        if self.focused:
            out.add("focus")
        if self.disabled:
            out.add("disabled")
        if getattr(self.app, "_hover", None) is self:
            out.add("hover")
        return out

    def get_pseudo_class_state(self) -> tuple[bool, bool, bool]:
        return (self.focused, getattr(self.app, "_hover", None) is self, self.disabled)

    def has_pseudo_class(self, class_name: str) -> bool:
        return class_name.lstrip(":") in self.get_pseudo_classes()

    def has_pseudo_classes(self, class_names: Iterable[str]) -> bool:
        pcs = self.get_pseudo_classes()
        return all(c.lstrip(":") in pcs for c in class_names)

    def allow_focus(self) -> bool:
        return bool(self.can_focus and not self.disabled and self.display)

    def allow_focus_children(self) -> bool:
        return bool(getattr(self, "can_focus_children", True) and not self.disabled and self.display)

    def focus_on_click(self) -> bool:
        return self.allow_focus()

    def trap_focus(self, trap: bool = True) -> None:
        self._trap_focus = bool(trap)

    def compose_add_child(self, widget: Widget) -> None:
        stack = _COMPOSE_STACK.get()
        if stack:
            stack[-1].append(widget)
        else:
            store = self._child_store()
            if store is not None:
                store.append(widget)

    def mount_compose(self) -> None:
        self._ensure_composed()

    def mount_composed_widgets(self, widgets: Iterable[Widget]) -> None:
        self.mount_all(widgets)

    def data_bind(self, **kwargs: Any) -> Widget:
        for attr, target in kwargs.items():
            setattr(self, attr, target)
        return self

    def batch(self):
        if self.app is not None and hasattr(self.app, "batch_update"):
            return self.app.batch_update()
        from contextlib import nullcontext

        return nullcontext()

    def automatic_refresh(self) -> None:
        self.refresh()

    def clear_cached_dimensions(self) -> None:
        self._prev_size = (0, 0)

    def arrange(self, size: Any = None, optimal: bool = False) -> Any:
        _ = size, optimal
        return self.layout_children()

    def pre_layout(self, layout: Any) -> None:
        _ = layout

    def process_layout(self, placements: Any) -> Any:
        return placements

    def pre_render(self) -> None:
        pass

    def post_render(self) -> None:
        pass

    def preflight_checks(self) -> None:
        pass

    def on_prune(self) -> None:
        pass

    def check_consume_key(self, key: str, character: str | None = None) -> bool:
        _ = key, character
        return False

    def check_idle(self) -> None:
        pass

    def check_message_enabled(self, message: Any) -> bool:
        disabled = getattr(self, "_disabled_messages", None)
        return not (disabled and type(message) in disabled)

    def disable_messages(self, *messages: type) -> None:
        s = getattr(self, "_disabled_messages", None)
        if s is None:
            s = set()
            self._disabled_messages = s
        s.update(messages)

    def enable_messages(self, *messages: type) -> None:
        s = getattr(self, "_disabled_messages", None)
        if s is not None:
            s.difference_update(messages)

    def prevent(self, *message_types: type):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            self.disable_messages(*message_types)
            try:
                yield
            finally:
                self.enable_messages(*message_types)

        return _ctx()

    def broker_event(self, event_name: str, event: Any) -> bool:
        return bool(dispatch_event(self, event, event_name))

    def on_event(self, event: Any) -> None:
        from .message import dispatch_message

        dispatch_message(self, event)

    def on_callback(self, event: Any) -> None:
        cb = getattr(event, "callback", None)
        if callable(cb):
            cb()

    def on_timer(self, event: Any) -> None:
        _ = event

    def handle_key(self, event: Any) -> bool:
        return dispatch_on_key(self, event)

    def begin_capture_print(self, stdout: bool = True, stderr: bool = True) -> None:
        if self.app is not None and hasattr(self.app, "begin_capture_print"):
            self.app.begin_capture_print(self, stdout=stdout, stderr=stderr)

    def end_capture_print(self) -> None:
        if self.app is not None and hasattr(self.app, "end_capture_print"):
            self.app.end_capture_print(self)

    def render(self) -> Any:
        lines = self.render_lines()
        return "\n".join(s.plain() for s in lines)

    def render_str(self, content: str = "") -> Any:
        from .content import markup_to_strips

        return markup_to_strips(str(content))

    def get_component_styles(self, *names: str) -> dict[str, Any]:
        _ = names
        return dict(self.styles or {})

    def get_component_rich_style(self, *names: str, partial: bool = False) -> Any:
        _ = names, partial
        from rich.style import Style

        return Style()

    def get_style_at(self, x: int, y: int) -> Any:
        _ = x, y
        from rich.style import Style

        return Style()

    def get_visual_style(self) -> Any:
        from rich.style import Style

        return Style()

    def get_line_filters(self) -> list[Any]:
        return []

    def set_styles(self, css: str = "", **kwargs: Any) -> Widget:
        if css:
            from .css import _apply_decls, _parse_decls

            decls = {d.prop: d.value for d in _parse_decls(css)}
            _apply_decls(self, decls, theme=self.theme, previous=dict(self.styles))
        for k, v in kwargs.items():
            self.styles[k] = v
        self.refresh(layout=True)
        return self

    def reset_styles(self) -> None:
        self.styles.clear()
        self.refresh(layout=True)

    def notify_style_update(self) -> None:
        self.refresh()

    def update_node_styles(self, animate: bool = True) -> None:
        _ = animate
        self.refresh()

    def refresh_bindings(self) -> None:
        if self.app is not None and hasattr(self.app, "refresh_bindings"):
            self.app.refresh_bindings()

    def animate(self, attribute: str, value: Any, *, duration: float = 0.3, on_complete: Callable[[], None] | None = None, **_kw: Any) -> None:
        _ = duration
        setattr(self, attribute, value)
        if callable(on_complete):
            on_complete()
        self.refresh()

    def stop_animation(self, attribute: str = "", send_message: bool = True) -> None:
        _ = attribute, send_message

    def scroll_up(self, animate: bool = True) -> None:
        _ = animate
        if hasattr(self, "scroll"):
            self.scroll(-1)

    def scroll_down(self, animate: bool = True) -> None:
        _ = animate
        if hasattr(self, "scroll"):
            self.scroll(1)

    def scroll_left(self, animate: bool = True) -> None:
        _ = animate

    def scroll_right(self, animate: bool = True) -> None:
        _ = animate

    def scroll_home(self, animate: bool = True) -> None:
        _ = animate

    def scroll_end(self, animate: bool = True) -> None:
        _ = animate

    def scroll_page_up(self, animate: bool = True) -> None:
        _ = animate

    def scroll_page_down(self, animate: bool = True) -> None:
        _ = animate

    def scroll_page_left(self, animate: bool = True) -> None:
        _ = animate

    def scroll_page_right(self, animate: bool = True) -> None:
        _ = animate

    def scroll_relative(self, x: int = 0, y: int = 0, *, animate: bool = True) -> None:
        _ = x, y, animate

    def scroll_to(self, x: int | None = None, y: int | None = None, *, animate: bool = True) -> None:
        _ = x, y, animate

    def scroll_to_center(self, widget: Widget, *, animate: bool = True) -> None:
        _ = animate
        self.scroll_to_widget(widget)

    def scroll_to_region(self, region: Rect, *, animate: bool = True) -> None:
        _ = region, animate

    def set_scroll(self, x: float | None = None, y: float | None = None) -> None:
        if x is not None and hasattr(self, "scroll_x"):
            self.scroll_x = int(x)
        if y is not None and hasattr(self, "scroll_y"):
            self.scroll_y = int(y)

    def anchor(self, anchor: bool = True) -> None:
        self._anchored = bool(anchor)

    def release_anchor(self) -> None:
        self._anchored = False

    def suppress_click(self) -> None:
        self._suppress_click = True

    def text_select_all(self) -> None:
        self.select_all()

    def selection_updated(self) -> None:
        self.refresh()

    def action_notify(self, message: str, title: str = "", severity: str = "information") -> None:
        self.notify(message, title=title, severity=severity)

    def action_toggle(self, selector: str = "") -> None:
        if selector and self.app is not None:
            self.app.action_toggle(selector)
        else:
            self.display = not self.display

    def action_scroll_up(self) -> None:
        self.scroll_up()

    def action_scroll_down(self) -> None:
        self.scroll_down()

    def action_scroll_left(self) -> None:
        self.scroll_left()

    def action_scroll_right(self) -> None:
        self.scroll_right()

    def action_scroll_home(self) -> None:
        self.scroll_home()

    def action_scroll_end(self) -> None:
        self.scroll_end()

    def action_page_up(self) -> None:
        self.scroll_page_up()

    def action_page_down(self) -> None:
        self.scroll_page_down()

    def action_page_left(self) -> None:
        self.scroll_page_left()

    def action_page_right(self) -> None:
        self.scroll_page_right()

    def validate_scroll_x(self, value: float) -> float:
        return max(0.0, float(value))

    def validate_scroll_y(self, value: float) -> float:
        return max(0.0, float(value))

    def validate_scroll_target_x(self, value: float) -> float:
        return max(0.0, float(value))

    def validate_scroll_target_y(self, value: float) -> float:
        return max(0.0, float(value))

    def watch_disabled(self, _old: bool, _new: bool) -> None:
        self.refresh()

    def watch_has_focus(self, _old: bool, _new: bool) -> None:
        self.refresh()

    def watch_hover_style(self, _old: Any, _new: Any) -> None:
        self.refresh()

    def watch_scroll_x(self, _old: float, _new: float) -> None:
        self.refresh()

    def watch_scroll_y(self, _old: float, _new: float) -> None:
        self.refresh()

    # --- Full Textual Widget/DOMNode properties & attributes -----------------

    @property
    def has_focus(self) -> bool:
        return self.focused

    @property
    def has_focus_within(self) -> bool:
        focus = getattr(self.app, "_focus", None) if self.app else None
        if focus is None:
            return False
        return focus is self or self in getattr(focus, "ancestors", ())

    @property
    def has_parent(self) -> bool:
        return self.parent is not None

    @property
    def is_dom_root(self) -> bool:
        return self.parent is None

    @property
    def is_empty(self) -> bool:
        return len(self.children) == 0

    @property
    def is_even(self) -> bool:
        sibs = self.parent.children if self.parent else [self]
        return (sibs.index(self) % 2) == 0 if self in sibs else True

    @property
    def is_odd(self) -> bool:
        return not self.is_even

    @property
    def is_on_screen(self) -> bool:
        return bool(self._mounted and self.display)

    @property
    def is_parent_active(self) -> bool:
        scr = self.screen
        return bool(scr is not None and self.app is not None and self.app.screen is scr)

    @property
    def is_running(self) -> bool:
        return bool(self.app is not None and getattr(self.app, "_running", False))

    @property
    def is_container(self) -> bool:
        return bool(self.children)

    @property
    def is_anchored(self) -> bool:
        return bool(getattr(self, "_anchored", False))

    @property
    def is_maximized(self) -> bool:
        scr = self.screen
        return bool(scr is not None and getattr(scr, "maximized", None) is self)

    @property
    def is_in_maximized_view(self) -> bool:
        scr = self.screen
        return bool(scr is not None and getattr(scr, "maximized", None) is not None)

    @property
    def is_mouse_over(self) -> bool:
        return getattr(self.app, "_hover", None) is self if self.app else False

    @property
    def mouse_hover(self) -> bool:
        return self.is_mouse_over

    @property
    def focusable(self) -> bool:
        return self.allow_focus()

    @property
    def pseudo_classes(self) -> set[str]:
        return self.get_pseudo_classes()

    @property
    def displayed_children(self) -> list[Widget]:
        return [c for c in self.children if c.display]

    @property
    def displayed_and_visible_children(self) -> list[Widget]:
        return [c for c in self.children if c.display and c.visible]

    @property
    def visible_siblings(self) -> list[Widget]:
        return [c for c in self.siblings if c.display and c.visible]

    @property
    def first_child(self) -> Widget | None:
        kids = self.children
        return kids[0] if kids else None

    @property
    def last_child(self) -> Widget | None:
        kids = self.children
        return kids[-1] if kids else None

    @property
    def first_of_type(self) -> bool:
        if self.parent is None:
            return True
        same = [c for c in self.parent.children if type(c) is type(self)]
        return bool(same and same[0] is self)

    @property
    def last_of_type(self) -> bool:
        if self.parent is None:
            return True
        same = [c for c in self.parent.children if type(c) is type(self)]
        return bool(same and same[-1] is self)

    def get_common_ancestor(self, widget: Widget) -> Widget | None:
        mine = set(self.ancestors_with_self)
        for anc in widget.ancestors_with_self:
            if anc in mine:
                return anc
        return None

    @property
    def css_identifier(self) -> str:
        t = type(self).__name__
        return f"{t}#{self.id}" if self.id else t

    @property
    def css_identifier_styled(self) -> Any:
        from rich.text import Text

        return Text(self.css_identifier)

    @property
    def css_path_nodes(self) -> list[Widget]:
        return list(reversed(self.ancestors_with_self))

    @property
    def css_tree(self) -> Any:
        return "\n".join(w.css_identifier for w in self.walk_descendants(include_self=True))

    @property
    def tree(self) -> Any:
        return self.css_tree

    @property
    def region(self):
        from .geometry import Region

        return Region(self.rect.x, self.rect.y, self.rect.w, self.rect.h)

    @property
    def outer_size(self) -> Size:
        return Size(self.rect.w, self.rect.h)

    @property
    def offset(self):
        from .geometry import Offset

        return Offset(self.rect.x, self.rect.y)

    @property
    def content_offset(self):
        from .geometry import Offset

        return Offset(0, 0)

    @property
    def content_region(self):
        from .geometry import Region

        return Region(self.rect.x, self.rect.y, self.rect.w, self.rect.h)

    @property
    def container_size(self) -> Size:
        return Size(self.rect.w, self.rect.h)

    @property
    def container_viewport(self):
        return self.region

    @property
    def container_scroll_offset(self):
        from .geometry import Offset

        return Offset(int(self.scroll_x), int(self.scroll_y))

    @property
    def scrollable_content_region(self):
        return self.region

    @property
    def scrollable_size(self) -> Size:
        return self.virtual_size

    @property
    def virtual_size(self) -> Size:
        return Size(self.rect.w, self.rect.h)

    @property
    def virtual_region(self):
        return self.region

    @property
    def virtual_region_with_margin(self):
        return self.region

    @property
    def window_region(self):
        return self.region

    @property
    def scroll_offset(self):
        from .geometry import Offset

        return Offset(int(self.scroll_x), int(self.scroll_y))

    @property
    def max_scroll_x(self) -> int:
        return 0

    @property
    def max_scroll_y(self) -> int:
        return 0

    @property
    def allow_horizontal_scroll(self) -> bool:
        return bool(getattr(self, "_allow_horizontal", False))

    @property
    def allow_vertical_scroll(self) -> bool:
        return bool(getattr(self, "_allow_vertical", False))

    @property
    def is_scrollable(self) -> bool:
        return self.allow_horizontal_scroll or self.allow_vertical_scroll

    @property
    def is_scrolling(self) -> bool:
        return False

    @property
    def is_horizontal_scroll_end(self) -> bool:
        return self.scroll_x >= self.max_scroll_x

    @property
    def is_vertical_scroll_end(self) -> bool:
        return self.scroll_y >= self.max_scroll_y

    @property
    def is_horizontal_scrollbar_grabbed(self) -> bool:
        return False

    @property
    def is_vertical_scrollbar_grabbed(self) -> bool:
        return False

    @property
    def show_horizontal_scrollbar(self) -> bool:
        return self.allow_horizontal_scroll

    @property
    def show_vertical_scrollbar(self) -> bool:
        return self.allow_vertical_scroll

    @property
    def horizontal_scrollbar(self) -> Any:
        return getattr(self, "_hbar", None)

    @property
    def vertical_scrollbar(self) -> Any:
        return getattr(self, "_scrollbar", None)

    @property
    def scrollbars_enabled(self) -> tuple[bool, bool]:
        return (self.allow_horizontal_scroll, self.allow_vertical_scroll)

    @property
    def scrollbars_space(self) -> tuple[int, int]:
        return (0, 0)

    @property
    def scrollbar_corner(self) -> Any:
        return None

    @property
    def scrollbar_gutter(self):
        from .geometry import Spacing

        return Spacing(0, 0, 0, 0)

    @property
    def scrollbar_size_horizontal(self) -> int:
        return 1

    @property
    def scrollbar_size_vertical(self) -> int:
        return 1

    @property
    def dock_gutter(self):
        from .geometry import Spacing

        return Spacing(0, 0, 0, 0)

    @property
    def gutter(self):
        from .geometry import Spacing

        return getattr(self, "_gutter", Spacing(0, 0, 0, 0))

    @gutter.setter
    def gutter(self, value) -> None:  # noqa: ANN001
        self._gutter = value

    @property
    def select_container(self) -> Widget:
        return self

    @property
    def expand(self) -> bool:
        return bool(getattr(self, "_expand", False))

    @expand.setter
    def expand(self, value: bool) -> None:
        self._expand = bool(value)

    @property
    def shrink(self) -> bool:
        return bool(getattr(self, "_shrink", False))

    @shrink.setter
    def shrink(self, value: bool) -> None:
        self._shrink = bool(value)

    @property
    def auto_links(self) -> bool:
        return True

    @property
    def auto_refresh(self) -> float | None:
        return None

    @property
    def highlight_link_id(self) -> str:
        return ""

    @property
    def layer(self) -> str:
        return str((self.styles or {}).get("layer") or "base")

    @property
    def layers(self) -> tuple[str, ...]:
        return ("base",)

    @property
    def opacity(self) -> float:
        return float((self.styles or {}).get("opacity", 1.0) or 1.0)

    @property
    def colors(self) -> tuple[Any, Any]:
        return (self.theme.foreground, self.theme.background)

    @property
    def background_colors(self) -> tuple[Any, Any]:
        return (self.theme.background, self.theme.surface)

    @property
    def rich_style(self) -> Any:
        return self.get_visual_style()

    @property
    def visual_style(self) -> Any:
        return self.get_visual_style()

    @property
    def text_style(self) -> Any:
        return self.get_visual_style()

    @property
    def hover_style(self) -> Any:
        return self.get_visual_style()

    @property
    def link_style(self) -> Any:
        return self.get_visual_style()

    @property
    def link_style_hover(self) -> Any:
        return self.get_visual_style()

    @property
    def selection_style(self) -> Any:
        return self.get_visual_style()

    @property
    def allow_maximize(self) -> bool:
        return bool(self.ALLOW_MAXIMIZE)

    @property
    def log(self) -> Any:
        from .logging import log as _log

        return _log

    @property
    def task(self) -> Any:
        return None

    @property
    def workers(self) -> Any:
        return getattr(self.app, "_workers", []) if self.app else []

    @property
    def message_queue_size(self) -> int:
        q = getattr(self.app, "_message_queue", None) if self.app else None
        return q.qsize() if q is not None else 0

    def post_message(self, message: Any) -> None:
        """Publish ``message`` and bubble toward the app (Textual-like)."""
        from .message import post_message as _post

        _post(self, message)

    def refresh(self, *, layout: bool = False, recompose: bool = False) -> None:
        """Request a redraw; optionally invalidate layout or recompose Screen.

        Inside ``App.batch_update()`` paints are coalesced until the batch ends.
        """
        if recompose and hasattr(self, "recompose"):
            try:
                self.recompose()  # type: ignore[attr-defined]
                return
            except Exception as exc:  # noqa: BLE001
                print(f"recompose error: {exc}", flush=True)
        app = self.app
        if layout and app is not None and hasattr(app, "_layout_size"):
            app._layout_size = (0, 0)
        # Styles may need re-resolve after class/DOM changes.
        root = self
        while root.parent is not None:
            root = root.parent
        if layout and hasattr(root, "apply_styles"):
            try:
                root.apply_styles()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        batched = bool(app is not None and getattr(app, "_batch_count", 0) > 0)
        if batched and app is not None:
            app._batch_refresh_pending = True
            if layout:
                app._batch_layout = True
        elif app is not None and hasattr(app, "request_draw"):
            app.request_draw()
        # Drain after-refresh hooks (even during batch — callers expect progress).
        pending = list(self._after_refresh)
        self._after_refresh.clear()
        for cb in pending:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                print(f"call_after_refresh error: {exc}", flush=True)

    def call_after_refresh(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Schedule ``callback`` after the next ``refresh`` or ``layout``."""
        if args or kwargs:
            self._after_refresh.append(lambda: callback(*args, **kwargs))
        else:
            self._after_refresh.append(callback)

    async def wait_for_refresh(self) -> None:
        """Await the next refresh cycle (Textual ``wait_for_refresh`` subset)."""
        done = asyncio.Event()
        self.call_after_refresh(done.set)
        # Kick a refresh so the callback runs (or lands in the next paint batch).
        self.refresh()
        if done.is_set():
            return
        # If still pending (e.g. batched), wait until app flushes or timeout via loop.
        app = self.app
        if app is not None and getattr(app, "_batch_count", 0) > 0:
            # Batch will flush hooks via refresh at end — poll briefly.
            for _ in range(100):
                if done.is_set():
                    return
                await asyncio.sleep(0)
            return
        await done.wait()

    @property
    def display(self) -> bool:
        return (self.styles or {}).get("display") != "none"

    @display.setter
    def display(self, value: bool) -> None:
        from .events import Hide, Show

        prev = self.display
        self.styles["display"] = "block" if value else "none"
        if bool(value) != prev and self._mounted:
            dispatch_event(self, Show() if value else Hide(), "on_show" if value else "on_hide")
        self.refresh(layout=True)

    @property
    def visible(self) -> bool:
        return self.display and (self.styles or {}).get("visibility") != "hidden"

    @visible.setter
    def visible(self, value: bool) -> None:
        self.styles["visibility"] = "visible" if value else "hidden"
        self.refresh()

    def scroll_visible(self, *_args: Any, **_kwargs: Any) -> None:
        """Best-effort scroll-into-view inside enclosing ScrollView/ScrollableContainer."""
        node = self.parent
        while node is not None:
            ensure = getattr(node, "ensure_visible_line", None)
            if callable(ensure) and self.rect.h > 0 and node.rect.h > 0:
                lh = max(1, int(getattr(node, "LINE_H", 18)))
                row = max(0, (self.rect.y - node.rect.y) // lh)
                ensure(row)
                return
            node = getattr(node, "parent", None)

    def walk_descendants(self, *, include_self: bool = False):
        """Depth-first iterator over the subtree."""
        from .dom import walk_descendants as _walk

        return _walk(self, include_self=include_self)

    def walk_children(self, filter_type: type | None = None, *, with_self: bool = False):
        for w in self.walk_descendants(include_self=with_self):
            if filter_type is None or isinstance(w, filter_type):
                yield w

    def get_child_by_id(self, id: str, expect_type: type | None = None) -> Any:  # noqa: A002
        for c in self.children:
            if getattr(c, "id", None) == id:
                if expect_type is None or isinstance(c, expect_type):
                    return c
        raise KeyError(f"No direct child with id {id!r}")

    def get_widget_by_id(self, id: str, expect_type: type | None = None) -> Any:  # noqa: A002
        return self.query_one(f"#{id}", expect_type)

    def query(self, selector: str = "*"):
        """CSS-ish query over this subtree (``#id``, ``Type``, ``Type#id``, descendants)."""
        from .dom import query as _query

        return _query(self, selector)

    def query_one(self, selector: str, expect_type: type | None = None) -> Any:
        """First matching descendant (or self). Raises ``KeyError`` if missing."""
        from .dom import query_one as _query_one

        return _query_one(self, selector, expect_type)

    def query_exactly_one(self, selector: str, expect_type: type | None = None) -> Any:
        matches = list(self.query(selector))
        if expect_type is not None:
            matches = [m for m in matches if isinstance(m, expect_type)]
        if len(matches) != 1:
            raise KeyError(f"Expected 1 match for {selector!r}, got {len(matches)}")
        return matches[0]

    def query_one_optional(self, selector: str, expect_type: type | None = None) -> Any | None:
        try:
            return self.query_one(selector, expect_type)
        except KeyError:
            return None

    def query_ancestor(self, selector: str | type) -> Any:
        node = self.parent
        while node is not None:
            if isinstance(selector, type):
                if isinstance(node, selector):
                    return node
            else:
                from .dom import matches_selector

                if matches_selector(node, selector):
                    return node
            node = node.parent
        raise KeyError(f"No ancestor matching {selector!r}")
