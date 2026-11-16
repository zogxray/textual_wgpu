"""Screen — one mode's widget tree (Textual Screen analogue)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .css import Stylesheet
from .geometry import Rect
from .widget import Widget

if TYPE_CHECKING:
    from PIL import ImageDraw

R = TypeVar("R")


class Screen(Widget):
    """Override ``compose()`` to build the tree.

    Layout is CSS auto-layout (``height: 1fr`` / ``dock`` / ``height: 1``), not a
    ModeBar-position convention. App CSS typically sizes chrome::

        ModeBar { height: 1; }
        KeyBar { height: 1; }
        StatusBar { height: 1; }
        #main { height: 1fr; }

    Set ``DEFAULT_CSS`` for screen-local rules. Use ``recompose()`` / ``mount_child``
    for a live DOM.

    ``BINDINGS`` accepts ``(key, action)`` tuples or ``Binding`` objects.
    """

    BINDINGS: list[Any] = []  # Binding | (key, action)
    DEFAULT_CSS: str = ""
    CSS: str = ""
    CSS_PATH: Any = None
    TITLE: str = ""
    SUB_TITLE: str = ""
    AUTO_FOCUS: str | None = "*"
    COMMANDS: set[Any] = set()
    ALLOW_IN_MAXIMIZED_VIEW: str = ""
    ESCAPE_TO_MINIMIZE: bool = True
    HORIZONTAL_BREAKPOINTS: list[tuple[int, str]] = []
    VERTICAL_BREAKPOINTS: list[tuple[int, str]] = []
    _is_screen: bool = True
    # True for ModalScreen — ModeApp / App may treat as overlay.
    is_modal: bool = False
    selections: dict[Widget, Any] = {}

    @property
    def title(self) -> str:
        return str(getattr(self, "_title", None) or self.TITLE)

    @title.setter
    def title(self, value: str) -> None:
        self._title = str(value)

    @property
    def sub_title(self) -> str:
        return str(getattr(self, "_sub_title", None) or self.SUB_TITLE)

    @sub_title.setter
    def sub_title(self, value: str) -> None:
        self._sub_title = str(value)

    @property
    def is_active(self) -> bool:
        return self.app is not None and self.app.screen is self

    @property
    def is_current(self) -> bool:
        return self.is_active

    @property
    def focus_chain(self) -> list[Widget]:
        return [w for w in self.walk_descendants() if w.allow_focus()]

    @property
    def maximized(self) -> Widget | None:
        return getattr(self, "_maximized", None)

    @property
    def stack_updates(self) -> int:
        return 0

    def __init__(self) -> None:
        super().__init__()
        self._composed: list[Widget] = []
        self._built = False
        self._stylesheet: Stylesheet | None = None
        self._css_tick = None
        self.styles.setdefault("layout", "vertical")
        # Screen-level text selection (widget → Selection).
        self.selections: dict[Widget, Any] = {}

    def compose(self) -> Iterator[Widget]:
        yield from ()

    def _collect_compose(self) -> list[Widget]:
        from .widget import _COMPOSE_STACK

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

    def build(self) -> None:
        if self._built:
            return
        self._composed = self._collect_compose()
        self._built = True
        self._composed_built = True
        css = getattr(self, "DEFAULT_CSS", "") or ""
        self._stylesheet = Stylesheet.parse(css, layer=1) if css.strip() else None
        from .events import Compose
        from .widget import dispatch_event

        dispatch_event(self, Compose(), "on_compose")

    def recompose(self) -> None:
        """Rebuild the tree from ``compose()`` (unmount old → mount new)."""
        app = self.app
        for child in list(self._composed):
            child.unmount()
        self._composed = []
        self._built = False
        self._composed_built = False
        self.build()
        if app is not None:
            self.app = app
            self._mounted = True
            for child in self._composed:
                child._attach(app, self)
            self.apply_styles()
        self.refresh(layout=True)

    @property
    def children(self) -> list[Widget]:
        return self._composed

    def _attach(self, app: Any, parent: Widget | None = None) -> None:
        self.build()
        super()._attach(app, parent)
        self.apply_styles()
        self.on_screen_mount()

    def action_dismiss(self, result: Any = None) -> None:
        self.dismiss(result)

    def action_scroll_up(self) -> None:
        for w in self.walk_descendants():
            if hasattr(w, "scroll_up"):
                w.scroll_up()
                return

    def action_scroll_down(self) -> None:
        for w in self.walk_descendants():
            if hasattr(w, "scroll_down"):
                w.scroll_down()
                return

    def action_scroll_home(self) -> None:
        for w in self.walk_descendants():
            if hasattr(w, "scroll_home"):
                w.scroll_home()
                return

    def action_scroll_end(self) -> None:
        for w in self.walk_descendants():
            if hasattr(w, "scroll_end"):
                w.scroll_end()
                return

    def action_page_up(self) -> None:
        for w in self.walk_descendants():
            if hasattr(w, "scroll_page_up"):
                w.scroll_page_up()
                return

    def action_page_down(self) -> None:
        for w in self.walk_descendants():
            if hasattr(w, "scroll_page_down"):
                w.scroll_page_down()
                return

    def mount(
        self,
        *widgets: Any,
        before: Widget | int | str | None = None,
        after: Widget | int | str | None = None,
    ) -> Any:
        if widgets and not isinstance(widgets[0], Widget):
            app = widgets[0]
            parent = widgets[1] if len(widgets) > 1 else None
            self._attach(app, parent)
            return None
        return super().mount(*widgets, before=before, after=after)

    def on_screen_mount(self) -> None:
        """Hook after widgets are mounted (compat; prefer ``on_mount``)."""

    def on_screen_resume(self) -> None:
        """Hook when screen becomes active again."""

    def focus_next(self, selector: str = "*") -> Widget | None:
        _ = selector
        if self.app is not None and hasattr(self.app, "focus_next"):
            return self.app.focus_next()
        return None

    def focus_previous(self, selector: str = "*") -> Widget | None:
        _ = selector
        if self.app is not None and hasattr(self.app, "focus_previous"):
            return self.app.focus_previous()
        return None

    def set_focus(self, widget: Widget | None, scroll_visible: bool = True) -> None:
        _ = scroll_visible
        if self.app is not None and hasattr(self.app, "set_focus"):
            self.app.set_focus(widget)

    def dismiss(self, result: Any = None) -> None:
        """Pop this screen from the app stack (resolves ``push_screen_wait``)."""
        if self.app is None:
            return
        # Only pop if we are the top screen.
        stack = getattr(self.app, "_screen_stack", None) or []
        if stack and stack[-1].screen is self:
            self.app.pop_screen(result)
        elif hasattr(self.app, "pop_screen"):
            # Still try — ModeApp may route Esc here.
            top = stack[-1].screen if stack else None
            if top is self:
                self.app.pop_screen(result)

    def clear_selection(self) -> None:
        """Clear text selection on this screen."""
        if self.selections:
            self.selections = {}
            if self.app is not None:
                self.app.request_draw()

    def get_selected_text(self) -> str | None:
        """Plain text under the current selection, or None."""
        if not self.selections:
            return None
        parts: list[str] = []
        for widget, selection in list(self.selections.items()):
            if not getattr(widget, "is_mounted", True):
                continue
            getter = getattr(widget, "get_selection", None)
            if not callable(getter):
                continue
            got = getter(selection)
            if got is None:
                continue
            text, sep = got
            parts.append(text)
            parts.append(sep)
        if not parts:
            return None
        return "".join(parts).rstrip("\n") or None

    def text_select_all(self, widget: Widget | None = None) -> None:
        """Select all text in ``widget`` (or the first selectable under focus)."""
        from .selection import SELECT_ALL

        target = widget
        if target is None:
            for w in self._walk(self):
                if getattr(w, "allow_select", False):
                    target = w
                    break
        if target is None or not getattr(target, "allow_select", False):
            return
        self.selections = {target: SELECT_ALL}
        from .events import TextSelected
        from .widget import dispatch_event

        dispatch_event(target, TextSelected(), "on_text_selected")
        dispatch_event(self, TextSelected(), "on_text_selected")
        if self.app is not None:
            self.app.request_draw()

    @property
    def active_bindings(self) -> dict[str, Any]:
        from .binding import Binding, iter_bindings

        out: dict[str, Any] = {}
        targets = [getattr(self.app, "_focus", None), self, self.app]
        for tgt in targets:
            if tgt is None:
                continue
            for item in getattr(type(tgt), "BINDINGS", ()) or ():
                if isinstance(item, Binding):
                    for part in str(item.key).split(","):
                        k = part.strip()
                        if k and k not in out:
                            out[k] = item
                else:
                    for k, act in iter_bindings([item]):
                        if k not in out:
                            out[k] = Binding(k, act)
        return out

    def _merged_stylesheet(self) -> Stylesheet | None:
        """App CSS (layer 0) + App CSS_PATH + Screen DEFAULT_CSS (layer 1) + extras."""
        from pathlib import Path

        sheets: list[Stylesheet] = []
        app = self.app
        if app is not None:
            app_sheet = getattr(app, "stylesheet", None)
            if app_sheet is None:
                parts: list[str] = []
                app_css = getattr(type(app), "CSS", "") or getattr(app, "CSS", "") or ""
                if str(app_css).strip():
                    parts.append(str(app_css))
                css_path = getattr(type(app), "CSS_PATH", None) or getattr(app, "CSS_PATH", None)
                if css_path:
                    paths = [css_path] if isinstance(css_path, (str, Path)) else list(css_path)
                    for cp in paths:
                        p = Path(cp)
                        if p.is_file():
                            parts.append(p.read_text(encoding="utf-8"))
                joined = "\n".join(parts)
                if joined.strip():
                    app_sheet = Stylesheet.parse(joined, layer=0)
                    try:
                        app.stylesheet = app_sheet
                    except Exception:  # noqa: BLE001
                        pass
            if app_sheet is not None:
                sheets.append(app_sheet)
            extra = getattr(app, "_extra_stylesheet", None)
            if extra is not None:
                sheets.append(extra)
        if self._stylesheet is not None:
            sheets.append(self._stylesheet)
        if not sheets:
            return None
        merged = sheets[0]
        for s in sheets[1:]:
            merged = merged.extend(s)
        return merged

    def apply_styles(self) -> None:
        """Re-resolve app + screen stylesheets (focus / layout / class changes)."""
        if self.app is None:
            return
        sheet = self._merged_stylesheet()
        if sheet is None:
            return
        focus = getattr(self.app, "_focus", None)
        hover = getattr(self.app, "_hover", None)
        sheet.apply(self, focus=focus, hover=hover, theme=self.app.theme)
        self._ensure_css_tick()

    def _ensure_css_tick(self) -> None:
        """Drive CSS transitions while any are active."""
        if self.app is None or not hasattr(self.app, "set_interval"):
            return
        if self._css_tick is not None:
            return

        def tick() -> None:
            from .css import tick_transitions

            if tick_transitions(self):
                if self.app is not None:
                    self.app.request_draw()
            else:
                if self._css_tick is not None:
                    try:
                        self._css_tick.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    self._css_tick = None

        self._css_tick = self.app.set_interval(1 / 30, tick)

    def layout(self, rect: Rect) -> None:
        self.apply_styles()
        super().layout(rect)

    def layout_children(self) -> None:
        """CSS auto-layout (vertical flow + dock/fr/content-size)."""
        if not self._composed:
            return
        from .layout import layout_children as _layout

        _layout(self, self._composed)

    def on_key(self, key: str) -> bool:
        from .binding import iter_bindings
        from .keys import normalize_key

        pressed = normalize_key(key)
        for bind_key, action in iter_bindings(self.BINDINGS):
            if pressed != normalize_key(bind_key):
                continue
            # Direct method on the screen (legacy tuple style: action == method name).
            fn = getattr(self, action, None)
            if not callable(fn):
                bare = action.split("(", 1)[0]
                if not bare.startswith("action_"):
                    fn = getattr(self, f"action_{bare}", None)
            if callable(fn) and "(" not in action:
                # Explicit False = did not handle (e.g. empty filter clear on Escape).
                result = fn()
                if result is False:
                    continue
                return True
            if self.app is not None and hasattr(self.app, "run_action"):
                if self.app.run_action(action):
                    return True
            # Local action_* with args (dismiss(False) etc.) without going through app.
            from .actions import call_action, find_action, parse_action

            try:
                name, args = parse_action(action)
            except ValueError:
                continue
            local = find_action(self, name)
            if local is not None:
                call_action(local, args)
                return True
        return super().on_key(key)

    def paint(self, draw: ImageDraw.ImageDraw) -> None:
        t = self.theme
        draw.rectangle(
            [self.rect.x, self.rect.y, self.rect.x1, self.rect.y1],
            fill=t.rgba("background"),
        )
        super().paint(draw)

    def query_one(self, selector: str, cls: type | None = None) -> Widget:
        """``#id`` / ``Type`` / ``Type#id`` — delegates to DOM query."""
        from .dom import query_one as _query_one

        return _query_one(self, selector, cls)

    def query(self, selector: str = "*"):
        from .dom import query as _query

        return _query(self, selector)

    def _walk(self, w: Widget) -> Iterator[Widget]:
        yield w
        for c in w.children:
            yield from self._walk(c)

    def find_widget(self, widget: Widget) -> tuple[Widget, Any] | None:
        for w in self.walk_descendants(include_self=True):
            if w is widget:
                return (w, w.rect)
        return None

    def get_offset(self, widget: Widget):
        from .geometry import Offset

        return Offset(widget.rect.x - self.rect.x, widget.rect.y - self.rect.y)

    def get_focusable_widget_at(self, x: int, y: int) -> tuple[Widget, Rect] | None:
        for w, r in self.get_widgets_at(x, y):
            if w.allow_focus():
                return (w, r)
        return None

    def get_hover_widgets_at(self, x: int, y: int) -> list[tuple[Widget, Rect]]:
        return self.get_widgets_at(x, y)

    def get_widget_and_offset_at(self, x: int, y: int):
        from .geometry import Offset

        w, r = self.get_widget_at(x, y)
        return (w, Offset(x - r.x, y - r.y))

    def pop_until_active(self) -> None:
        if self.app is not None:
            while self.app.screen_stack and self.app.screen is not self:
                self.app.pop_screen()

    def update_pointer_shape(self) -> None:
        pass

    def validate_title(self, title: str) -> str:
        return str(title)

    def validate_sub_title(self, sub_title: str) -> str:
        return str(sub_title)

    def maximize(self, widget: Widget | None = None, container: bool = True) -> bool:
        _ = container
        self._maximized = widget
        self.refresh(layout=True)
        return True

    def minimize(self) -> bool:
        self._maximized = None
        self.refresh(layout=True)
        return True

    def action_blur(self) -> None:
        if self.app is not None:
            self.app.set_focus(None)

    def action_focus(self, widget_id: str | None = None) -> None:
        if widget_id:
            try:
                self.query_one(f"#{widget_id}").focus()
            except KeyError:
                pass
        else:
            self.focus_next()

    def action_copy_text(self) -> None:
        text = self.get_selected_text()
        if text and self.app is not None and hasattr(self.app, "copy_to_clipboard"):
            self.app.copy_to_clipboard(text)

    def action_maximize(self) -> None:
        focused = getattr(self.app, "focused", None) if self.app else None
        if focused is not None:
            self.maximize(focused)

    def action_minimize(self) -> None:
        self.minimize()


class ModalScreen(Screen, Generic[R]):
    """Modal overlay screen (Textual ``ModalScreen`` subset).

    Pushed via ``App.push_screen``; ``dismiss(result)`` pops and resolves
    callbacks / ``push_screen_wait`` futures.
    """

    is_modal = True
    DEFAULT_CSS = """
    ModalScreen {
        layout: vertical;
    }
    """

    def action_dismiss(self, result: Any = None) -> None:
        self.dismiss(result)
