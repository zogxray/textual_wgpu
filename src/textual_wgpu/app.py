"""Minimal App shell: window, theme, bindings, pointer, draw hook."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Callable, Coroutine, Generator, Iterable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, TypeAlias, TypeVar

from .theme import BUILTIN_THEMES, Theme
from .timer import Timer
from .worker import Worker, WorkerState, _worker_tls

W = TypeVar("W")

# Textual ``ComposeResult`` — iterable of widgets from ``compose()``.
ComposeResult: TypeAlias = Iterable[Any]


class IntervalHandle(Timer):
    """Cancelable handle returned by ``App.set_interval``."""

    def __init__(self, interval: float = 0.0, *, name: str | None = None) -> None:
        super().__init__(interval, name=name)


@dataclass
class _ScreenStackEntry:
    screen: Any  # Screen
    callback: Callable[[Any], None] | None = None
    future: Any | None = None  # asyncio.Future for wait_for_dismiss


# rendercanvas/GLFW uses browser-style names; keep Textual-ish short keys internally.
_KEY_ALIASES = {
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "home": "home",
    "end": "end",
    "enter": "enter",
    "escape": "escape",
    "backspace": "backspace",
    "tab": "tab",
    " ": "space",
    "space": "space",
}


def _normalize_key(key: str) -> str:
    k = key.lower()
    if k.startswith("f") and k[1:].isdigit():
        return k
    return _KEY_ALIASES.get(k, k)


class _ActionResult:
    """Bool-compatible AND optionally awaitable return from ``App.run_action``."""

    def __init__(self, ok: bool) -> None:
        self._ok = bool(ok)

    def __bool__(self) -> bool:
        return self._ok

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return self._ok is other
        if isinstance(other, _ActionResult):
            return self._ok is other._ok
        return NotImplemented

    def __await__(self):
        async def _done() -> bool:
            return self._ok

        return _done().__await__()


class App:
    """Tiny Textual-flavoured shell.

    Not API-compatible with Textual. The application owns the Theme;
    this runtime only paints with it (same idea as Textual + terminal/app CSS).

    Runtime extras (Textual subset):
    - screen stack: ``push_screen`` / ``pop_screen`` / ``await push_screen_wait``
    - text selection: ``ALLOW_SELECT``, ``clear_selection``, ``copy_to_clipboard``
    - actions: ``run_action`` / ``action_*``
    - command palette: ``action_command_palette`` (ctrl+p)
    - asyncio message pump + ``Worker`` / ``@work``
    - Pilot: ``async with app.run_test() as pilot``
    - drivers: ``WgpuDriver`` / ``HeadlessDriver`` via ``run`` / ``run_async``
    - DOM wait: ``await app.wait_for("#id")`` / ``batch_update()``
    """

    TITLE = "textual-wgpu"
    SUB_TITLE = ""
    SIZE = (960, 600)
    # Global switch for screen-level drag selection (Textual ``ALLOW_SELECT``).
    ALLOW_SELECT: bool = True
    ALLOW_IN_MAXIMIZED_VIEW: str = ""
    BINDING_GROUP_TITLE: str | None = None
    CLICK_CHAIN_TIME_THRESHOLD: float = 0.5
    CLOSE_TIMEOUT: float | None = 5.0
    COMMAND_PALETTE_BINDING: str = "ctrl+p"
    COMMAND_PALETTE_DISPLAY: str = "^p"
    COMPONENT_CLASSES: set[str] = set()
    DEFAULT_CLASSES: str = ""
    DEFAULT_CSS: str = ""
    ENABLE_SELECT_AUTO_SCROLL: bool = True
    ESCAPE_TO_MINIMIZE: bool = True
    ExpectType = Any
    HELP: str | None = None
    HORIZONTAL_BREAKPOINTS: list[tuple[int, str]] = []
    INLINE_PADDING: int = 0
    NOTIFICATION_TIMEOUT: float = 5.0
    PAUSE_GC_ON_SCROLL: bool = True
    SCOPED_CSS: bool = False
    SELECT_AUTO_SCROLL_LINES: int = 1
    SELECT_AUTO_SCROLL_SPEED: float = 10.0
    SUSPENDED_SCREEN_CLASS: str = "-suspended"
    TOOLTIP_DELAY: float = 0.5
    VERTICAL_BREAKPOINTS: list[tuple[int, str]] = []
    WalkType = Any
    # App-wide TCSS (layer 0). Screens add DEFAULT_CSS on top (layer 1).
    CSS: str = ""
    CSS_PATH: Any = None
    AUTO_FOCUS: str | None = "*"
    COMMANDS: set[Any] = set()
    ENABLE_COMMAND_PALETTE: bool = True
    # Textual MODES / SCREENS registries.
    MODES: dict[str, Any] = {}
    DEFAULT_MODE: str | None = None
    SCREENS: dict[str, Any] = {}
    id: str | None = None
    name: str | None = None
    parent: Any = None
    classes: set[str] = set()
    is_modal: bool = False
    return_code: int | None = None
    return_value: Any = None
    task: Any = None
    # App-level key → action (``Binding`` or ``(key, action)`` tuples).
    BINDINGS: list[Any] = [
        ("ctrl+p", "command_palette"),
    ]
    _initial_theme: str | Theme | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "theme" in cls.__dict__ and not isinstance(cls.__dict__["theme"], property):
            cls._initial_theme = cls.__dict__["theme"]
            delattr(cls, "theme")

    def __init__(self, theme: Theme | str | None = None) -> None:
        self._themes: dict[str, Theme] = dict(BUILTIN_THEMES)
        sys_theme = Theme.system()
        self._themes[sys_theme.name] = sys_theme
        self._theme_obj: Theme = sys_theme
        self._pending_theme_name: str | None = None
        init_t = theme if theme is not None else getattr(type(self), "_initial_theme", None)
        if isinstance(init_t, Theme):
            self.register_theme(init_t)
            self._theme_obj = init_t
        elif isinstance(init_t, str):
            if init_t in self._themes:
                self._theme_obj = self._themes[init_t]
            else:
                self._pending_theme_name = init_t
        self._canvas: Any = None
        self._device: Any = None
        self._context: Any = None
        self._format: str | None = None
        self._running = False
        self._bindings: dict[str, Callable[[], None]] = {}
        self._intervals: list[IntervalHandle] = []
        self._busy: str | None = None
        self._busy_frac: float | None = None
        self._busy_t0: float | None = None
        self._busy_tick: IntervalHandle | None = None
        # exclusive workers: group → cancel Event (new start cancels previous).
        self._worker_cancel: dict[str, threading.Event] = {}
        self._worker_gen: dict[str, int] = {}
        self._workers: list[Worker] = []
        # Asyncio runtime (message pump + timers + async workers).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._message_queue: asyncio.Queue[tuple[Any, Any]] | None = None
        self._pump_task: asyncio.Task[None] | None = None
        # Stylesheets
        self.stylesheet = None  # parsed App.CSS (lazy)
        self._extra_stylesheet = None  # runtime add_stylesheet()
        self._hover: Any = None
        self._focus: Any = None
        self._mouse_captured: Any = None
        self._keymap: dict[str, str] = {}
        self._capture_targets: list[Any] = []
        self._orig_stdout: Any = None
        self._orig_stderr: Any = None
        # Screen stack + installed screens + modes.
        self._screen_stack: list[_ScreenStackEntry] = []
        self._installed_screens: dict[str, Any] = dict(getattr(type(self), "SCREENS", {}) or {})
        self._modes: dict[str, Any] = dict(getattr(type(self), "MODES", {}) or {})
        self._mode_screens: dict[str, Any] = {}
        self._current_mode: str | None = None
        self._default_screen: Any = None
        self._driver: Any = None
        # Refresh batching (Textual ``batch_update``).
        self._batch_count = 0
        self._batch_refresh_pending = False
        self._batch_layout = False
        self._layout_size: tuple[int, int] = (0, 0)
        # Local clipboard mirror (last copy_to_clipboard); system write is best-effort.
        self._clipboard: str = ""
        # Notifications / toast + opened URLs.
        self._toast: Any = None
        self._notifications: list[dict[str, Any]] = []
        self._last_opened_url: str | None = None

    @property
    def theme(self) -> Theme:
        return self._theme_obj

    @theme.setter
    def theme(self, value: Theme | str) -> None:
        if isinstance(value, Theme):
            self.register_theme(value)
            self._theme_obj = value
            self._pending_theme_name = None
        elif isinstance(value, str):
            if value in self._themes:
                self._theme_obj = self._themes[value]
                self._pending_theme_name = None
            else:
                self._pending_theme_name = value
        scr = self.screen
        if scr is not None and hasattr(scr, "apply_styles"):
            try:
                scr.apply_styles()
            except Exception:  # noqa: BLE001
                pass
        self.request_draw()

    @property
    def available_themes(self) -> dict[str, Theme]:
        return dict(self._themes)

    def register_theme(self, theme: Theme) -> None:
        self._themes[theme.name] = theme
        if self._pending_theme_name == theme.name:
            self._theme_obj = theme
            self._pending_theme_name = None

    def unregister_theme(self, name: str) -> None:
        self._themes.pop(name, None)

    def get_theme(self, name: str) -> Theme | None:
        return self._themes.get(name)

    def get_css_variables(self) -> dict[str, str]:
        t = self.theme
        out = {
            "primary": t.primary,
            "secondary": t.secondary or t.primary,
            "accent": t.accent,
            "foreground": t.foreground,
            "background": t.background,
            "surface": t.surface,
            "panel": t.panel,
            "boost": t.boost,
            "muted": t.muted,
            "warning": t.warning or t.accent,
            "error": t.error or "#B04A4A",
            "success": t.success or "#4E9A66",
        }
        out.update(t.variables or {})
        return out

    def add_stylesheet(self, css: str, *, layer: int = 2) -> None:
        """Append an extra stylesheet (highest cascade by default)."""
        from .css import Stylesheet

        sheet = Stylesheet.parse(css, layer=layer)
        if self._extra_stylesheet is None:
            self._extra_stylesheet = sheet
        else:
            self._extra_stylesheet = self._extra_stylesheet.extend(sheet)

    @property
    def CLEAR(self) -> tuple[float, float, float, float]:
        return self.theme.clear_rgba_f()

    def bind(self, key: str, callback: Callable[[], None]) -> None:
        self._bindings[key.lower()] = callback

    def request_draw(self) -> None:
        if self._batch_count > 0:
            self._batch_refresh_pending = True
            return
        if self._canvas is not None:
            self._canvas.request_draw(self.draw)

    @contextmanager
    def batch_update(self) -> Generator[None, None, None]:
        """Suspend paints until the block exits (coalesce refreshes)."""
        self._batch_count += 1
        try:
            yield
        finally:
            self._batch_count = max(0, self._batch_count - 1)
            if self._batch_count == 0 and self._batch_refresh_pending:
                layout = self._batch_layout
                self._batch_refresh_pending = False
                self._batch_layout = False
                if layout:
                    self._layout_size = (0, 0)
                # Bypass batch guard.
                if self._canvas is not None:
                    self._canvas.request_draw(self.draw)

    def show_busy(self, title: str, subtitle: str = "") -> None:
        self._busy = f"{title}" + (f" · {subtitle}" if subtitle else "")
        self._busy_frac = 0.0
        self._busy_t0 = time.monotonic()
        self._ensure_busy_tick()
        self.request_draw()

    def hide_busy(self) -> None:
        self._busy = None
        self._busy_frac = None
        self._busy_t0 = None
        if self._busy_tick is not None:
            self._busy_tick.stop()
            self._busy_tick = None
        self.request_draw()

    def _ensure_busy_tick(self) -> None:
        """Redraw ~5×/s while busy so elapsed time advances on the busy modal."""
        if self._busy_tick is not None and self._busy_tick.active:
            return

        def _tick() -> None:
            if not self._busy:
                if self._busy_tick is not None:
                    self._busy_tick.stop()
                    self._busy_tick = None
                return
            self.request_draw()

        self._busy_tick = self.set_interval(0.2, _tick)

    @property
    def clipboard(self) -> str:
        """Last text copied inside the app (not necessarily the OS clipboard)."""
        return self._clipboard

    def copy_to_clipboard(self, text: str) -> bool:
        """Write ``text`` to the system clipboard (wl-copy / xclip / pbcopy / clip).

        Always updates the in-app ``clipboard`` buffer; returns whether an OS
        clipboard tool accepted the write.
        """
        self._clipboard = text if text is not None else ""
        from .clipboard import write_clipboard
        return write_clipboard(self._clipboard)

    def read_clipboard(self) -> str | None:
        """Read text from the system clipboard, or fall back to in-app buffer."""
        from .clipboard import read_clipboard
        clip = read_clipboard()
        if clip:
            return clip
        return self._clipboard or None

    def clear_selection(self) -> None:
        """Clear text selection on the active screen."""
        screen = self.screen
        if screen is not None and hasattr(screen, "clear_selection"):
            screen.clear_selection()

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float = 3.0,
        markup: bool = True,
    ) -> None:
        """Display a transient toast notification (Textual ``App.notify``)."""
        from .widgets.toast import Toast

        _ = markup
        text = f"{title}: {message}" if title else str(message)
        sev = (severity or "information").lower()
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = 3.0
        timeout_f = max(0.4, timeout_f)
        if self._toast is None:
            self._toast = Toast(id="-toast")
        self._toast.app = self
        self._toast.show(text, severity=sev, timeout=timeout_f)
        self._notifications.append({
            "title": str(title),
            "message": str(message),
            "severity": sev,
            "timeout": timeout_f,
        })
        self.request_draw()

    def clear_notifications(self) -> None:
        """Clear active toast notification (Textual ``App.clear_notifications``)."""
        self._notifications.clear()
        if self._toast is not None:
            self._toast.hide()
        self.request_draw()

    def open_url(self, url: str, *, new_tab: bool = True) -> bool:
        """Open ``url`` in the default browser (Textual ``App.open_url``)."""
        import webbrowser

        target = str(url or "").strip()
        if not target:
            return False
        self._last_opened_url = target
        try:
            return bool(webbrowser.open(target, new=2 if new_tab else 0))
        except Exception:  # noqa: BLE001
            return False

    def busy_progress(self, detail: str, *, frac: float | None = None, **_kw: Any) -> None:
        if self._busy is None:
            self._busy = detail
            self._busy_t0 = time.monotonic()
            self._ensure_busy_tick()
        else:
            base = self._busy.split(" · ", 1)[0]
            self._busy = f"{base} · {detail}"
        if frac is not None:
            self._busy_frac = max(0.0, min(1.0, float(frac)))
        self.request_draw()

    # --- Asyncio runtime -----------------------------------------------------

    def _attach_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Bind this app to an asyncio loop and start the message pump."""
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = loop
        if self._loop is None:
            return
        if self._message_queue is None:
            self._message_queue = asyncio.Queue()
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = self._loop.create_task(
                self._message_pump(), name="textual-wgpu-pump",
            )

    async def _message_pump(self) -> None:
        """Drain posted messages on the asyncio loop."""
        from .message import post_message_now

        assert self._message_queue is not None
        while True:
            try:
                sender, message = await self._message_queue.get()
            except asyncio.CancelledError:
                break
            try:
                post_message_now(sender, message)
            except Exception as exc:  # noqa: BLE001
                print(f"message pump error: {exc}", flush=True)
            finally:
                self.request_draw()

    async def _drain_messages(self) -> None:
        """Process any queued messages (Pilot / tests)."""
        from .message import post_message_now

        q = self._message_queue
        if q is None:
            return
        while not q.empty():
            try:
                sender, message = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            post_message_now(sender, message)
        await asyncio.sleep(0)

    def call_later(self, delay: float, callback: Callable[[], None]) -> None:
        """Schedule ``callback`` after ``delay`` seconds on the UI loop."""
        delay = max(0.0, float(delay))

        def once() -> None:
            if not self._running and delay > 0:
                # Allow zero-delay scheduling before run; skip late fires after exit.
                if delay > 0 and not self._running:
                    return
            try:
                callback()
            except Exception as exc:  # noqa: BLE001
                print(f"call_later error: {exc}", flush=True)
            self.request_draw()

        if self._loop is not None:
            self._loop.call_later(delay, once)
            return
        if not self._running:
            if delay <= 0:
                once()
            return
        try:
            from rendercanvas.auto import loop
            loop.call_later(delay, once)
        except Exception:  # noqa: BLE001
            if delay <= 0:
                once()

    def call_from_thread(
        self, callback: Callable[..., Any], *args: Any, **kwargs: Any,
    ) -> None:
        """Schedule ``callback(*args, **kwargs)`` on the UI loop (thread-safe).

        UI updates from a cancelled exclusive worker are dropped (mid-flight stop).
        """
        cancel = getattr(_worker_tls, "cancel", None)

        def run() -> None:
            if cancel is not None and cancel.is_set():
                return
            try:
                callback(*args, **kwargs)
            finally:
                self.request_draw()

        # Prefer asyncio loop (headless / run_async / Pilot).
        if self._loop is not None:
            self._loop.call_soon_threadsafe(run)
            return
        # No live loop (unit tests / pre-run) → execute inline.
        if not getattr(self, "_running", False):
            run()
            return
        try:
            from rendercanvas.auto import loop
            loop.call_soon_threadsafe(run)
        except Exception:  # noqa: BLE001
            run()

    def spawn_worker(
        self,
        fn: Callable[[], Any] | Callable[[], Coroutine[Any, Any, Any]],
        *,
        name: str = "worker",
        exclusive: bool = False,
        group: str | None = None,
        thread: bool | None = None,
    ) -> Worker:
        """Start a Worker (async task or thread). Returns the handle."""
        key = group or name
        if thread is None:
            use_thread = not inspect.iscoroutinefunction(fn)
        else:
            use_thread = bool(thread)

        worker = Worker(self, name=name, group=key, exclusive=exclusive)
        self._workers.append(worker)

        if exclusive:
            # Cancel prior workers in the same group.
            for prev in list(self._workers):
                if prev is worker:
                    continue
                if prev.group == key and not prev.is_finished:
                    prev.cancel()
            cancel = worker._cancel_event
            self._worker_cancel[key] = cancel
            self._worker_gen[key] = self._worker_gen.get(key, 0) + 1
            gen = self._worker_gen[key]
        else:
            cancel = worker._cancel_event
            gen = 0

        if use_thread:
            def runner() -> None:
                _worker_tls.cancel = cancel if exclusive else None
                _worker_tls.group = key if exclusive else None
                _worker_tls.gen = gen if exclusive else 0
                _worker_tls.worker = worker
                worker.state = WorkerState.RUNNING
                try:
                    if exclusive and cancel.is_set():
                        worker.state = WorkerState.CANCELLED
                        return
                    result = fn()
                    if inspect.isawaitable(result):
                        # Accidentally async — run to completion on new loop.
                        result = asyncio.run(result)  # type: ignore[arg-type]
                    if cancel.is_set():
                        worker.state = WorkerState.CANCELLED
                    else:
                        worker._finish_ok(result)
                except Exception as exc:  # noqa: BLE001
                    worker._finish_err(exc)
                finally:
                    if exclusive and self._worker_cancel.get(key) is cancel:
                        self._worker_cancel.pop(key, None)
                    _worker_tls.cancel = None
                    _worker_tls.group = None
                    _worker_tls.gen = 0
                    _worker_tls.worker = None
                    worker._done.set()

            t = threading.Thread(target=runner, name=name, daemon=True)
            worker._thread = t
            t.start()
            return worker

        # Async path — needs a running loop.
        loop = self._loop
        try:
            loop = loop or asyncio.get_running_loop()
            self._loop = loop
        except RuntimeError:
            loop = None

        async def _async_runner() -> Any:
            worker.state = WorkerState.RUNNING
            worker._async_cancel = asyncio.Event()
            _worker_tls.worker = worker
            try:
                if exclusive and cancel.is_set():
                    worker.state = WorkerState.CANCELLED
                    return None
                result = fn()
                if inspect.isawaitable(result):
                    result = await result
                if cancel.is_set() or worker._async_cancel.is_set():
                    worker.state = WorkerState.CANCELLED
                    return None
                worker._finish_ok(result)
                return result
            except asyncio.CancelledError:
                worker.state = WorkerState.CANCELLED
                raise
            except Exception as exc:  # noqa: BLE001
                worker._finish_err(exc)
                raise
            finally:
                if exclusive and self._worker_cancel.get(key) is cancel:
                    self._worker_cancel.pop(key, None)
                _worker_tls.worker = None
                worker._done.set()

        if loop is not None:
            worker._fut = loop.create_future()
            worker._task = loop.create_task(_async_runner(), name=name)
            return worker

        # No loop yet — fall back to thread wrapping asyncio.run.
        return self.spawn_worker(fn, name=name, exclusive=exclusive, group=key, thread=True)

    def run_worker(
        self,
        fn: Callable[[], None],
        *,
        name: str = "worker",
        exclusive: bool = False,
        group: str | None = None,
    ) -> Worker:
        """Run ``fn`` as a Worker (thread by default for sync callables).

        ``exclusive=True`` cancels any previous worker in the same ``group``
        (default: ``name``). Poll ``app.worker_cancelled(group)`` /
        ``app.current_worker_cancelled()``; ``call_from_thread`` from a
        cancelled worker is a no-op.
        """
        return self.spawn_worker(
            fn, name=name, exclusive=exclusive, group=group, thread=True,
        )

    def worker_cancelled(self, group: str) -> bool:
        """True if the exclusive worker for ``group`` was superseded."""
        ev = self._worker_cancel.get(group)
        # Active worker: event not set. Superseded: previous event was set and
        # replaced — check generation vs thread-local, or event still in map set.
        if ev is not None and ev.is_set():
            return True
        # Thread that was superseded no longer owns the map entry; TLS cancel is set.
        tls_g = getattr(_worker_tls, "group", None)
        if tls_g == group:
            tls_c = getattr(_worker_tls, "cancel", None)
            if tls_c is not None and tls_c.is_set():
                return True
            tls_gen = getattr(_worker_tls, "gen", 0)
            return tls_gen != self._worker_gen.get(group, 0)
        return False

    def current_worker_cancelled(self) -> bool:
        """True if the calling exclusive worker thread was superseded."""
        cancel = getattr(_worker_tls, "cancel", None)
        if cancel is not None and cancel.is_set():
            return True
        group = getattr(_worker_tls, "group", None)
        if not group:
            return False
        gen = getattr(_worker_tls, "gen", 0)
        return gen != self._worker_gen.get(group, 0)

    def cancel_worker(self, group: str | None = None) -> int:
        """Signal exclusive worker(s) to stop. Returns how many were signalled."""
        n = 0
        if group is not None:
            ev = self._worker_cancel.get(group)
            if ev is not None and not ev.is_set():
                ev.set()
                n = 1
            return n
        for ev in list(self._worker_cancel.values()):
            if not ev.is_set():
                ev.set()
                n += 1
        return n

    def worker_generation(self, group: str) -> int:
        """Monotonic generation for ``group`` (bumps on each exclusive start)."""
        return int(self._worker_gen.get(group, 0))

    def set_interval(
        self,
        seconds: float,
        callback: Callable[[], None],
        *,
        pause: bool = False,
        name: str | None = None,
    ) -> IntervalHandle:
        """Call ``callback`` every ``seconds`` (asyncio or rendercanvas)."""
        _ = name
        handle = IntervalHandle()
        handle.paused = bool(pause)
        self._intervals.append(handle)
        seconds = max(0.05, float(seconds))

        def tick() -> None:
            if not handle.active or not self._running:
                return
            if not handle.paused:
                from .events import Timer as TimerEvent
                from .widget import dispatch_event

                handle._count = getattr(handle, "_count", 0) + 1
                try:
                    callback()
                except Exception as exc:  # noqa: BLE001
                    print(f"interval error: {exc}", flush=True)
                ev = TimerEvent(timer=handle, time=time.monotonic(), count=handle._count, callback=callback)
                dispatch_event(self, ev, "on_timer")
                if handle.active and self._running:
                    self.request_draw()
            if handle.active and self._running:
                self.call_later(seconds, tick)

        if self._running or self._loop is not None:
            self.call_later(seconds, tick)
        # else: tests can invoke manually / start after run
        return handle

    def set_timer(self, seconds: float, callback: Callable[[], None]) -> None:
        """Fire ``callback`` once after ``seconds`` (UI loop)."""
        seconds = max(0.0, float(seconds))

        def once() -> None:
            if not self._running and self._loop is None:
                # Pre-run sync tests: still fire.
                pass
            elif not self._running:
                return
            from .events import Timer as TimerEvent
            from .widget import dispatch_event

            try:
                callback()
            except Exception as exc:  # noqa: BLE001
                print(f"timer error: {exc}", flush=True)
            dispatch_event(self, TimerEvent(time=time.monotonic(), count=1, callback=callback), "on_timer")
            self.request_draw()

        if self._loop is not None or self._running:
            self.call_later(seconds, once)
        else:
            once()

    def call_after_refresh(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Schedule ``callback`` after the next refresh / on the next loop turn."""
        if args or kwargs:
            self.call_later(0.0, lambda: callback(*args, **kwargs))
        else:
            self.call_later(0.0, callback)

    def call_next(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.call_after_refresh(callback, *args, **kwargs)

    @property
    def focused(self) -> Any | None:
        return getattr(self, "_focus", None)

    def set_focus(self, widget: Any | None, scroll_visible: bool = True) -> None:
        from .events import Blur, DescendantBlur, DescendantFocus, Focus
        from .widget import dispatch_event

        prev = getattr(self, "_focus", None)
        if prev is widget:
            return
        if prev is not None and hasattr(prev, "_focused"):
            try:
                prev._focused = False
            except Exception:  # noqa: BLE001
                pass
            dispatch_event(prev, Blur(), "on_blur")
            anc = getattr(prev, "parent", None)
            while anc is not None:
                dispatch_event(anc, DescendantBlur(widget=prev), "on_descendant_blur")
                anc = getattr(anc, "parent", None)
        self._focus = widget
        if widget is not None and hasattr(widget, "_focused"):
            try:
                widget._focused = True
            except Exception:  # noqa: BLE001
                pass
            if scroll_visible and hasattr(widget, "scroll_visible"):
                widget.scroll_visible()
            dispatch_event(widget, Focus(), "on_focus")
            anc = getattr(widget, "parent", None)
            while anc is not None:
                dispatch_event(anc, DescendantFocus(widget=widget), "on_descendant_focus")
                anc = getattr(anc, "parent", None)
        scr = self.screen
        if scr is not None and hasattr(scr, "apply_styles"):
            try:
                scr.apply_styles()
            except Exception:  # noqa: BLE001
                pass
        self.request_draw()

    def _focusables(self) -> list[Any]:
        root = self.screen
        if root is None:
            return []
        out: list[Any] = []
        walk = getattr(root, "walk_descendants", None)
        nodes = walk(include_self=True) if callable(walk) else []
        for w in nodes:
            if not (getattr(w, "can_focus", False) or getattr(w, "_focusable", False)):
                continue
            if getattr(w, "disabled", False):
                continue
            if (getattr(w, "styles", None) or {}).get("display") == "none":
                continue
            out.append(w)
        return out

    def focus_next(self, selector: str = "*") -> Any | None:
        _ = selector
        items = self._focusables()
        if not items:
            return None
        cur = getattr(self, "_focus", None)
        if cur not in items:
            self.set_focus(items[0])
            return items[0]
        nxt = items[(items.index(cur) + 1) % len(items)]
        self.set_focus(nxt)
        return nxt

    def focus_previous(self, selector: str = "*") -> Any | None:
        _ = selector
        items = self._focusables()
        if not items:
            return None
        cur = getattr(self, "_focus", None)
        if cur not in items:
            self.set_focus(items[-1])
            return items[-1]
        prv = items[(items.index(cur) - 1) % len(items)]
        self.set_focus(prv)
        return prv

    # --- Modes & Installed Screens ------------------------------------------

    @property
    def current_mode(self) -> str | None:
        return self._current_mode

    def add_mode(self, mode: str, screen: Any) -> None:
        self._modes[str(mode)] = screen

    def remove_mode(self, mode: str) -> None:
        self._modes.pop(mode, None)
        self._mode_screens.pop(mode, None)

    def _resolve_screen_entry(self, entry: Any) -> Any:
        if isinstance(entry, str):
            entry = self._installed_screens.get(entry, entry)
        if callable(entry) and not hasattr(entry, "compose"):
            return entry()
        if isinstance(entry, type):
            return entry()
        return entry

    def switch_mode(self, mode: str) -> Any:
        from .events import ScreenResume, ScreenSuspend
        from .widget import dispatch_event

        if mode not in self._modes:
            raise KeyError(f"Unknown mode {mode!r}")
        prev_scr = self._mode_screens.get(self._current_mode) if self._current_mode else None
        if prev_scr is not None:
            dispatch_event(prev_scr, ScreenSuspend(), "on_screen_suspend")
        if mode not in self._mode_screens:
            scr = self._resolve_screen_entry(self._modes[mode])
            if getattr(scr, "app", None) is None:
                scr._attach(self)
            self._mode_screens[mode] = scr
        self._current_mode = mode
        scr = self._mode_screens[mode]
        dispatch_event(scr, ScreenResume(refresh_styles=True), "on_screen_resume")
        self._layout_size = (0, 0)
        size = getattr(self, "_pixel_size", None) or getattr(self._driver, "size", None)
        if size and hasattr(scr, "layout"):
            from .geometry import Rect

            scr.layout(Rect(0, 0, int(size[0]), int(size[1])))
            self._layout_size = (int(size[0]), int(size[1]))
        self.request_draw()
        return scr

    def action_mode(self, mode: str) -> None:
        self.switch_mode(mode)

    def action_switch_mode(self, mode: str) -> None:
        self.switch_mode(mode)

    def install_screen(self, screen: Any, name: str) -> None:
        self._installed_screens[str(name)] = screen

    def uninstall_screen(self, name: str) -> Any | None:
        return self._installed_screens.pop(name, None)

    def is_screen_installed(self, name: str) -> bool:
        return name in self._installed_screens

    def get_screen(self, name: str) -> Any:
        if name not in self._installed_screens:
            raise KeyError(f"Screen {name!r} is not installed")
        val = self._installed_screens[name]
        if callable(val) and not hasattr(val, "compose"):
            val = val()
            self._installed_screens[name] = val
        elif isinstance(val, type):
            val = val()
            self._installed_screens[name] = val
        return val

    # --- Screen stack --------------------------------------------------------

    @property
    def screen_stack(self) -> list[Any]:
        """Screens currently pushed (bottom → top)."""
        return [e.screen for e in self._screen_stack]

    @property
    def screen(self) -> Any | None:
        """Top of the push stack, else the active mode / composed screen."""
        if self._screen_stack:
            return self._screen_stack[-1].screen
        return self._base_screen()

    def compose(self) -> ComposeResult:
        yield from ()

    def _base_screen(self) -> Any | None:
        """Active mode screen, or default screen composed from App.compose()."""
        if self._current_mode and self._current_mode in self._mode_screens:
            return self._mode_screens[self._current_mode]
        def_mode = getattr(type(self), "DEFAULT_MODE", None)
        if def_mode and def_mode in self._modes:
            return self.switch_mode(def_mode)
        if self._default_screen is not None:
            return self._default_screen
        if type(self).compose is not App.compose:
            from .screen import Screen

            app_ref = self

            class _AppScreen(Screen):
                def compose(self):
                    return app_ref.compose()

            scr = _AppScreen()
            scr._attach(self)
            self._default_screen = scr
            return scr
        return None

    def push_screen(
        self,
        screen: Any,
        callback: Callable[[Any], None] | None = None,
        wait_for_dismiss: bool = False,
    ) -> Any:
        """Push a ``Screen`` onto the stack (covers underlay).

        When ``wait_for_dismiss=True``, returns an ``asyncio.Future`` that
        resolves with the dismiss/pop result (Textual analogue). Prefer
        ``await app.push_screen_wait(screen)``.
        """
        if screen is None:
            return None
        if isinstance(screen, str):
            screen = self.get_screen(screen)
        fut: asyncio.Future[Any] | None = None
        if wait_for_dismiss:
            loop = self._loop
            try:
                loop = loop or asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                loop = asyncio.new_event_loop()
                self._loop = loop
            fut = loop.create_future()
            user_cb = callback

            def _cb(result: Any) -> None:
                if user_cb is not None:
                    user_cb(result)
                if fut is not None and not fut.done():
                    fut.set_result(result)

            callback = _cb

        from .events import ScreenResume, ScreenSuspend
        from .widget import dispatch_event

        prev_top = self.screen
        if prev_top is not None and prev_top is not screen:
            dispatch_event(prev_top, ScreenSuspend(), "on_screen_suspend")
        if getattr(screen, "app", None) is None:
            if hasattr(screen, "_attach"):
                screen._attach(self)
            else:
                screen.mount(self)
        else:
            try:
                screen.app = self
            except Exception:  # noqa: BLE001
                pass
        self._screen_stack.append(
            _ScreenStackEntry(screen=screen, callback=callback, future=fut),
        )
        dispatch_event(screen, ScreenResume(refresh_styles=True), "on_screen_resume")
        self.request_draw()
        return fut if wait_for_dismiss else None

    async def push_screen_wait(self, screen: Any) -> Any:
        """``await`` until the pushed screen is dismissed; return its result."""
        fut = self.push_screen(screen, wait_for_dismiss=True)
        assert fut is not None
        return await fut

    def pop_screen(self, result: Any = None) -> Any | None:
        """Pop the top screen; invoke its callback / resolve wait future."""
        from .events import ScreenResume, ScreenSuspend
        from .widget import dispatch_event

        if not self._screen_stack:
            return None
        entry = self._screen_stack.pop()
        dispatch_event(entry.screen, ScreenSuspend(), "on_screen_suspend")
        if entry.callback is not None:
            try:
                entry.callback(result)
            except Exception as exc:  # noqa: BLE001
                print(f"pop_screen callback error: {exc}", flush=True)
        elif entry.future is not None and not entry.future.done():
            entry.future.set_result(result)
        exposed = self.screen
        if exposed is not None:
            dispatch_event(exposed, ScreenResume(refresh_styles=True), "on_screen_resume")
        self.request_draw()
        return entry.screen

    def switch_screen(self, screen: Any) -> Any | None:
        """Replace the top screen (or push if stack empty)."""
        prev = None
        if self._screen_stack:
            prev = self.pop_screen()
        self.push_screen(screen)
        return prev

    # --- Query / wait (Textual «подожди DOM») ---------------------------------

    def query(self, selector: str = "*") -> Any:
        """Query the active screen (empty DOMQuery if no screen)."""
        from .dom import DOMQuery

        screen = self.screen
        if screen is None:
            return DOMQuery([])
        return screen.query(selector)

    def query_one(self, selector: str, expect_type: type[W] | None = None) -> W:
        """``screen.query_one``; raises ``KeyError`` if missing."""
        screen = self.screen
        if screen is None:
            raise KeyError(f"No screen to query for {selector!r}")
        return screen.query_one(selector, expect_type)

    async def wait_for(
        self,
        selector: str,
        *,
        timeout: float = 5.0,
        expect_type: type[W] | None = None,
        interval: float = 0.01,
    ) -> W:
        """Poll until ``query_one(selector)`` succeeds (async DOM wait)."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        last_exc: Exception | None = None
        while True:
            try:
                return self.query_one(selector, expect_type)
            except KeyError as exc:
                last_exc = exc
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"wait_for({selector!r}) timed out after {timeout}s"
                ) from last_exc
            drain = getattr(self, "_drain_messages", None)
            if callable(drain):
                await drain()
            await asyncio.sleep(interval)

    async def wait_for_idle(self, delay: float = 0.0) -> None:
        """Drain the message pump and emit ``Idle`` (Pilot / worker settle)."""
        from .events import Idle
        from .widget import dispatch_event

        drain = getattr(self, "_drain_messages", None)
        if callable(drain):
            await drain()
        dispatch_event(self, Idle(), "on_idle")
        scr = self.screen
        if scr is not None:
            dispatch_event(scr, Idle(), "on_idle")
        await asyncio.sleep(max(0.0, float(delay)))

    # --- Actions -------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[Any, ...]) -> bool | None:
        _ = action, parameters
        return True

    def run_action(self, action: str) -> Any:
        """Dispatch ``action`` to top screen then app (honoring ``check_action``)."""
        from .actions import parse_action, run_action_on
        from .events import Action
        from .widget import dispatch_event

        try:
            name, params = parse_action(action)
        except ValueError:
            name, params = action, ()
        for target in (self.screen, self):
            if target is None:
                continue
            chk = getattr(target, "check_action", None)
            if callable(chk) and chk(name, params) is False:
                return _ActionResult(False)
        ev = Action(action=action)
        dispatch_event(self, ev, "on_action")
        if ev.is_prevented or ev.is_stopped:
            return _ActionResult(True)
        return _ActionResult(run_action_on([self.screen, self], action))

    def refresh_bindings(self) -> None:
        self.request_draw()

    def action_quit(self) -> None:
        """Default quit action — subclasses may confirm first."""
        self.exit()

    def action_pop_screen(self) -> None:
        self.pop_screen()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_previous(self) -> None:
        self.focus_previous()

    def action_toggle(self, selector: str) -> None:
        try:
            w = self.query_one(selector)
            w.display = not w.display
        except Exception:  # noqa: BLE001
            pass

    def action_change_theme(self, theme: str | None = None) -> None:
        if theme:
            self.theme = theme
            return
        names = list(self._themes.keys())
        if names:
            cur = self.theme.name
            nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
            self.theme = nxt

    def action_toggle_dark(self) -> None:
        if self.theme.dark and "textual-light" in self._themes:
            self.theme = "textual-light"
        elif not self.theme.dark and "textual-dark" in self._themes:
            self.theme = "textual-dark"
        else:
            self.action_change_theme()

    def action_show_help_panel(self) -> None:
        from .widgets.help_panel import HelpPanel

        scr = self.screen
        if scr is None:
            return
        existing = scr.query("HelpPanel").results()
        if existing:
            existing[0].display = True
        else:
            scr.mount(HelpPanel(id="-help-panel"))

    def action_hide_help_panel(self) -> None:
        scr = self.screen
        if scr is None:
            return
        for hp in scr.query("HelpPanel"):
            hp.display = False

    def get_widget_by_id(self, id: str, expect_type: type[W] | None = None) -> W:  # noqa: A002
        return self.query_one(f"#{id}", expect_type)

    def get_widget_at(self, x: int, y: int) -> tuple[Any, Any]:
        scr = self.screen
        if scr is None:
            raise KeyError(f"No screen at ({x}, {y})")
        return scr.get_widget_at(x, y)

    def walk_children(self, filter_type: type | None = None):
        scr = self.screen
        if scr is None:
            return iter(())
        return scr.walk_children(filter_type, with_self=True)

    def capture_mouse(self, widget: Any | None) -> None:
        from .events import MouseCapture, MouseRelease
        from .widget import dispatch_event

        prev = self._mouse_captured
        if prev is not None and prev is not widget:
            dispatch_event(prev, MouseRelease(), "on_mouse_release")
        self._mouse_captured = widget
        if widget is not None:
            dispatch_event(widget, MouseCapture(), "on_mouse_capture")

    def _emit_app_focus(self, focused: bool) -> None:
        from .events import AppBlur, AppFocus
        from .widget import dispatch_event

        ev = AppFocus() if focused else AppBlur()
        meth = "on_app_focus" if focused else "on_app_blur"
        dispatch_event(self, ev, meth)
        if self.screen is not None:
            dispatch_event(self.screen, ev, meth)

    def get_key_display(self, key: str) -> str:
        from .binding import Binding

        _ = Binding
        parts = [p.strip() for p in str(key).split("+") if p.strip()]
        return "+".join(p.upper() if len(p) == 1 else p.capitalize() for p in parts)

    def set_keymap(self, keymap: dict[str, str]) -> None:
        self._keymap = dict(keymap or {})

    def animate(
        self,
        attribute: str,
        value: Any,
        *,
        duration: float = 0.3,
        on_complete: Callable[[], None] | None = None,
        **_kw: Any,
    ) -> None:
        _ = duration
        setattr(self, attribute, value)
        if callable(on_complete):
            on_complete()
        self.request_draw()

    def stop_animation(self, attribute: str = "", send_message: bool = True) -> None:
        _ = attribute, send_message

    def begin_capture_print(
        self,
        target: Any = None,
        stdout: bool = True,
        stderr: bool = True,
    ) -> None:
        import sys

        tgt = target or self.screen or self
        if tgt not in self._capture_targets:
            self._capture_targets.append(tgt)
        app_ref = self

        class _PrintProxy:
            def __init__(self, orig: Any, is_err: bool) -> None:
                self._orig = orig
                self._is_err = is_err

            def write(self, data: str) -> int:
                if data:
                    from .events import Print
                    from .widget import dispatch_event

                    ev = Print(text=data, stderr=self._is_err)
                    for t in list(app_ref._capture_targets):
                        dispatch_event(t, ev, "on_print")
                return len(data)

            def flush(self) -> None:
                if hasattr(self._orig, "flush"):
                    self._orig.flush()

        if stdout and self._orig_stdout is None:
            self._orig_stdout = sys.stdout
            sys.stdout = _PrintProxy(self._orig_stdout, False)  # type: ignore[assignment]
        if stderr and self._orig_stderr is None:
            self._orig_stderr = sys.stderr
            sys.stderr = _PrintProxy(self._orig_stderr, True)  # type: ignore[assignment]

    def end_capture_print(self, target: Any = None) -> None:
        import sys

        tgt = target or self.screen or self
        if tgt in self._capture_targets:
            self._capture_targets.remove(tgt)
        if not self._capture_targets:
            if self._orig_stdout is not None:
                sys.stdout = self._orig_stdout
                self._orig_stdout = None
            if self._orig_stderr is not None:
                sys.stderr = self._orig_stderr
                self._orig_stderr = None

    def _render_image(self) -> Any:
        from PIL import Image, ImageDraw
        from .geometry import Rect

        w, h = getattr(self, "_pixel_size", None) or getattr(self._driver, "size", None) or self.SIZE
        img = Image.new("RGBA", (int(w), int(h)), self.theme.rgba("background"))
        draw = ImageDraw.Draw(img)
        scr = self.screen
        if scr is not None:
            if scr.rect.w <= 0 or scr.rect.h <= 0:
                scr.layout(Rect(0, 0, int(w), int(h)))
            scr.paint(draw)
        return img

    def export_screenshot(self, title: str | None = None, **_kw: Any) -> str:
        """Render current screen and return an SVG wrapping PNG data (or plain SVG)."""
        import base64
        import io

        img = self._render_image()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        ttl = title or self.TITLE or "textual-wgpu"
        w, h = img.size
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'data-title="{ttl}">'
            f'<image width="{w}" height="{h}" href="data:image/png;base64,{b64}"/></svg>'
        )

    def save_screenshot(
        self,
        filename: str | None = None,
        path: str | None = None,
        time_format: str | None = None,
    ) -> str:
        from pathlib import Path

        _ = time_format
        fname = filename or "screenshot.png"
        dest = (Path(path) / fname) if path else Path(fname)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.suffix.lower() == ".svg":
            dest.write_text(self.export_screenshot(), encoding="utf-8")
        else:
            img = self._render_image()
            img.save(dest)
        return str(dest)

    def deliver_text(
        self,
        text: str,
        *,
        save_path: Any = None,
        encoding: str = "utf-8",
        key: str = "",
        name: str | None = None,
    ) -> Any:
        from pathlib import Path
        from .events import DeliveryComplete, DeliveryFailed
        from .widget import dispatch_event

        try:
            dest = Path(save_path) if save_path is not None else None
            if dest is not None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding=encoding)
            ev = DeliveryComplete(key=key, path=dest, name=name)
            dispatch_event(self, ev, "on_delivery_complete")
            return dest
        except Exception as exc:  # noqa: BLE001
            dispatch_event(self, DeliveryFailed(key=key, exception=exc, name=name), "on_delivery_failed")
            return None

    def deliver_binary(
        self,
        data: bytes,
        *,
        save_path: Any = None,
        key: str = "",
        name: str | None = None,
    ) -> Any:
        from pathlib import Path
        from .events import DeliveryComplete, DeliveryFailed
        from .widget import dispatch_event

        try:
            dest = Path(save_path) if save_path is not None else None
            if dest is not None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            ev = DeliveryComplete(key=key, path=dest, name=name)
            dispatch_event(self, ev, "on_delivery_complete")
            return dest
        except Exception as exc:  # noqa: BLE001
            dispatch_event(self, DeliveryFailed(key=key, exception=exc, name=name), "on_delivery_failed")
            return None

    def deliver_screenshot(
        self,
        filename: str | None = None,
        path: str | None = None,
        key: str = "",
    ) -> str | None:
        from pathlib import Path
        from .events import DeliveryComplete, DeliveryFailed
        from .widget import dispatch_event

        try:
            out = self.save_screenshot(filename=filename, path=path)
            dispatch_event(self, DeliveryComplete(key=key, path=Path(out), name=filename), "on_delivery_complete")
            return out
        except Exception as exc:  # noqa: BLE001
            dispatch_event(self, DeliveryFailed(key=key, exception=exc, name=filename), "on_delivery_failed")
            return None

    def action_command_palette(self) -> None:
        """Open the command palette (ctrl+p)."""
        from .command_palette import CommandPaletteScreen, collect_commands

        # Don't nest palettes.
        top = self._screen_stack[-1].screen if self._screen_stack else None
        if top is not None and type(top).__name__ == "CommandPaletteScreen":
            return
        cmds = collect_commands(self)
        self.push_screen(CommandPaletteScreen(cmds))

    def _try_app_bindings(self, key: str, *, priority_only: bool | None = None) -> bool:
        from .binding import iter_bindings
        from .keys import normalize_key

        pressed = normalize_key(key)
        for bind_key, action in iter_bindings(
            getattr(type(self), "BINDINGS", []) or [],
            priority_only=priority_only,
        ):
            if pressed == normalize_key(bind_key):
                return self.run_action(action)
        return False

    def on_mount(self) -> None:
        """Override for setup after the GPU context exists."""

    def after_first_draw(self) -> None:
        """Override for work after the first painted frame, while the window is still hidden."""

    def _handle_key(self, key: str) -> None:
        """Route a key from Driver/Pilot, adapting ``App.on_key`` overrides."""
        from .events import Key
        from .widget import _wants_key_event, dispatch_on_key

        if type(self).on_key is not App.on_key:
            fn = self.on_key
            if _wants_key_event(fn):
                ev = Key(key=key)
                fn(ev)  # type: ignore[arg-type]
                if ev.is_stopped:
                    return
                App.on_key(self, key)
                return
            fn(key)
            return
        self.on_key(key)

    def on_key(self, key: str) -> None:
        """Override for key handling. Default: priority bindings → stack/screen → bind() → app BINDINGS."""
        from .widget import dispatch_on_key

        if self._try_app_bindings(key, priority_only=True):
            return
        # Top pushed screen (or active base screen) + focused widget.
        if self._screen_stack:
            top = self._screen_stack[-1].screen
            if dispatch_on_key(top, key):
                return
        else:
            focus = getattr(self, "_focus", None)
            if focus is not None and dispatch_on_key(focus, key):
                return
            scr = self.screen
            if scr is not None and dispatch_on_key(scr, key):
                return
        cb = self._bindings.get(key.lower())
        if cb is not None:
            cb()
            return
        if self._try_app_bindings(key, priority_only=False):
            return
        if key == "tab":
            self.focus_next()
        elif key == "shift+tab":
            self.focus_previous()

    def on_char(self, ch: str) -> None:
        """Override for text input (rendercanvas ``char`` events)."""
        focus = getattr(self, "_focus", None)
        if focus is not None and hasattr(focus, "insert"):
            if not getattr(focus, "disabled", False) and not getattr(focus, "read_only", False):
                focus.insert(ch)
                self.request_draw()
                return
        scr = self.screen
        if scr is not None and hasattr(scr, "on_char"):
            scr.on_char(ch)

    def on_pointer(self, x: float, y: float, button: int) -> None:
        """Override for mouse / touch. Coordinates are canvas pixels."""
        from types import SimpleNamespace
        from .compositor import meta_at_point
        from .events import Click, MouseDown
        from .widget import dispatch_event

        ix, iy = int(x), int(y)
        scr = self.screen
        if scr is None:
            return
        target_w: Any = None
        try:
            target_w, _ = scr.get_widget_at(ix, iy)
        except KeyError:
            target_w = scr
        md = MouseDown(x=ix, y=iy, screen_x=ix, screen_y=iy, button=button, widget=target_w)
        node = target_w
        while node is not None:
            if dispatch_event(node, md, "on_mouse_down"):
                break
            node = getattr(node, "parent", None)

        for rect, action in reversed(scr.hits()):
            if rect.contains(ix, iy):
                action(ix, iy)
                self.request_draw()
                break
        # Check Segment.meta click actions (e.g. Rich Style.from_meta) + Click bubble
        meta: dict[str, Any] = {}
        walk = getattr(scr, "walk_descendants", None)
        if callable(walk):
            for w in reversed(list(walk(include_self=True))):
                r = getattr(w, "rect", None)
                if r is not None and r.contains(ix, iy):
                    found = meta_at_point(w, ix, iy, scroll_y=int(getattr(w, "scroll_y", 0) or 0))
                    if found:
                        meta = found
                        target_w = w
                        break
        st_obj = SimpleNamespace(meta=meta) if meta else None
        ev = Click(
            x=ix, y=iy, screen_x=ix, screen_y=iy,
            button=button, style=st_obj, widget=target_w, _meta=meta,
        )
        node = target_w
        while node is not None:
            if dispatch_event(node, ev, "on_click"):
                return
            node = getattr(node, "parent", None)
        dispatch_event(self, ev, "on_click")

    def on_pointer_move(self, x: float, y: float) -> bool:
        """Emit Enter/Leave on hover transitions + MouseMove."""
        from .events import Enter, Leave, MouseMove
        from .widget import dispatch_event

        ix, iy = int(x), int(y)
        scr = self.screen
        if scr is None:
            return False
        captured = self._mouse_captured
        if captured is not None:
            dispatch_event(
                captured,
                MouseMove(x=ix, y=iy, screen_x=ix, screen_y=iy, widget=captured),
                "on_mouse_move",
            )
            return True
        hovered: Any = None
        try:
            hovered, _ = scr.get_widget_at(ix, iy)
        except KeyError:
            hovered = None
        changed = False
        if hovered is not self._hover:
            if self._hover is not None:
                dispatch_event(self._hover, Leave(node=self._hover), "on_leave")
            self._hover = hovered
            if hovered is not None:
                dispatch_event(hovered, Enter(node=hovered), "on_enter")
            if hasattr(scr, "apply_styles"):
                try:
                    scr.apply_styles()
                except Exception:  # noqa: BLE001
                    pass
            changed = True
        if hovered is not None:
            dispatch_event(
                hovered,
                MouseMove(x=ix, y=iy, screen_x=ix, screen_y=iy, widget=hovered),
                "on_mouse_move",
            )
        return changed

    def on_pointer_up(self, x: float, y: float, button: int) -> None:
        """Emit MouseUp."""
        from .events import MouseUp
        from .widget import dispatch_event

        ix, iy = int(x), int(y)
        target = self._mouse_captured
        if target is None and self.screen is not None:
            try:
                target, _ = self.screen.get_widget_at(ix, iy)
            except KeyError:
                target = self.screen
        if target is not None:
            dispatch_event(
                target,
                MouseUp(x=ix, y=iy, screen_x=ix, screen_y=iy, button=button, widget=target),
                "on_mouse_up",
            )

    def on_wheel(self, x: float, y: float, dx: float, dy: float) -> None:
        """Emit MouseScroll* events and scroll target."""
        from .events import MouseScrollDown, MouseScrollLeft, MouseScrollRight, MouseScrollUp
        from .widget import dispatch_event

        ix, iy = int(x), int(y)
        scr = self.screen
        if scr is None:
            return
        try:
            target, _ = scr.get_widget_at(ix, iy)
        except KeyError:
            target = scr
        if dy != 0:
            ev = (MouseScrollDown if dy > 0 else MouseScrollUp)(x=ix, y=iy, delta_y=int(dy), widget=target)
            meth = "on_mouse_scroll_down" if dy > 0 else "on_mouse_scroll_up"
            node = target
            while node is not None:
                if dispatch_event(node, ev, meth):
                    return
                if hasattr(node, "scroll"):
                    steps = 3 if dy > 0 else -3
                    node.scroll(steps)
                    self.request_draw()
                    return
                if hasattr(node, "move_highlight"):
                    steps = 3 if dy > 0 else -3
                    node.move_highlight(steps)
                    self.request_draw()
                    return
                node = getattr(node, "parent", None)
        if dx != 0:
            ev_x = (MouseScrollRight if dx > 0 else MouseScrollLeft)(x=ix, y=iy, delta_x=int(dx), widget=target)
            meth_x = "on_mouse_scroll_right" if dx > 0 else "on_mouse_scroll_left"
            node = target
            while node is not None:
                if dispatch_event(node, ev_x, meth_x):
                    return
                node = getattr(node, "parent", None)

    def on_resize(self, width: int, height: int) -> None:
        """Override when the canvas size changes."""
        self._layout_size = (0, 0)
        scr = self.screen
        if scr is not None and hasattr(scr, "layout"):
            from .geometry import Rect

            scr.layout(Rect(0, 0, int(width), int(height)))
            self._layout_size = (int(width), int(height))

    def draw(self) -> None:
        """Override to issue GPU work each frame. Paints active Screen if present."""
        scr = self.screen
        if scr is None or self._device is None or self._context is None:
            if self._device is not None and self._context is not None:
                self.clear()
            return
        from PIL import Image, ImageDraw

        from .blit import FrameBlitter
        from .geometry import Rect

        blitter = getattr(self, "_app_blitter", None)
        if blitter is None:
            blitter = FrameBlitter(self._device, self._format or "bgra8unorm")
            self._app_blitter = blitter
        w, h = getattr(self, "_pixel_size", None) or self.SIZE
        if hasattr(self._canvas, "get_physical_size"):
            try:
                pw, ph = self._canvas.get_physical_size()
                if pw > 0 and ph > 0:
                    w, h = int(pw), int(ph)
            except Exception:  # noqa: BLE001
                pass
        img = Image.new("RGBA", (w, h), self.theme.rgba("background"))
        draw = ImageDraw.Draw(img)
        if self._layout_size != (w, h):
            scr.layout(Rect(0, 0, w, h))
            self._layout_size = (w, h)
        scr.paint(draw)
        for entry in self._screen_stack:
            if entry.screen is not scr:
                entry.screen.layout(Rect(0, 0, w, h))
                entry.screen.paint(draw)
        if self._toast is not None and self._toast.visible:
            self._toast.paint_at(draw, Rect(0, 0, w, h))
        blitter.upload(img.tobytes("raw", "RGBA"), img.size)
        view = self._context.get_current_texture().create_view()
        blitter.draw(view)

    def _wgpu_surface(self) -> Any:
        """Underlying wgpu GPUCanvasContext (rendercanvas wraps it)."""
        ctx = self._context
        if ctx is None:
            return None
        return getattr(ctx, "_wgpu_context", None) or ctx

    def clear(
        self,
        color: tuple[float, float, float, float] | None = None,
        *,
        present: bool = True,
    ) -> None:
        """Fill the swapchain with an opaque color and optionally present it."""
        import wgpu

        assert self._device is not None and self._context is not None
        c = color or self.CLEAR
        # Force opaque — transparent flash is usually alpha=0 leftovers.
        if len(c) == 4 and c[3] < 1.0:
            c = (c[0], c[1], c[2], 1.0)
        encoder = self._device.create_command_encoder()
        view = self._context.get_current_texture().create_view()
        encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": view,
                    "clear_value": c,
                    "load_op": wgpu.LoadOp.clear,
                    "store_op": wgpu.StoreOp.store,
                }
            ]
        ).end()
        self._device.queue.submit([encoder.finish()])
        if present:
            surf = self._wgpu_surface()
            if surf is not None and hasattr(surf, "present"):
                surf.present()

    def _set_window_visible(self, visible: bool) -> None:
        try:
            import glfw
            win = getattr(self._canvas, "_window", None)
            if win is None:
                return
            if visible:
                glfw.show_window(win)
            else:
                glfw.hide_window(win)
        except Exception:  # noqa: BLE001
            pass

    # --- Full Textual App parity methods -------------------------------------

    def action_add_class(self, selector: str, class_name: str) -> None:
        for w in self.query(selector):
            w.add_class(class_name)

    def action_remove_class(self, selector: str, class_name: str) -> None:
        for w in self.query(selector):
            w.remove_class(class_name)

    def action_toggle_class(self, selector: str, class_name: str) -> None:
        for w in self.query(selector):
            w.toggle_class(class_name)

    def action_back(self) -> None:
        self.pop_screen()

    def action_bell(self) -> None:
        self.bell()

    def action_focus(self, widget_id: str | None = None) -> None:
        if widget_id:
            try:
                self.query_one(f"#{widget_id}").focus()
            except KeyError:
                pass
        else:
            self.focus_next()

    def action_help_quit(self) -> None:
        self.notify("Press Q to quit", timeout=2.0)

    def action_notify(self, message: str, title: str = "", severity: str = "information") -> None:
        self.notify(message, title=title, severity=severity)

    def action_push_screen(self, screen: str) -> None:
        self.push_screen(screen)

    def action_switch_screen(self, screen: str) -> None:
        self.switch_screen(screen)

    def action_screenshot(self, filename: str | None = None, path: str | None = None) -> None:
        self.save_screenshot(filename=filename, path=path)

    def action_simulate_key(self, key: str) -> None:
        self.simulate_key(key)

    def action_suspend_process(self) -> None:
        pass

    def bell(self) -> None:
        pass

    def panic(self, *renderables: Any) -> None:
        msg = " ".join(str(r) for r in renderables) or "App panic"
        raise RuntimeError(msg)

    def suspend(self):
        from contextlib import nullcontext

        return nullcontext()

    def simulate_key(self, key: str) -> None:
        self._handle_key(str(key).lower())

    def post_message(self, message: Any) -> None:
        from .message import post_message as _post

        _post(self, message)

    def post_display_hook(self) -> None:
        pass

    def delay_update(self, delay: float = 0.0) -> None:
        _ = delay

    def automatic_refresh(self) -> None:
        self.request_draw()

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

    def on_callback(self, event: Any) -> None:
        cb = getattr(event, "callback", None)
        if callable(cb):
            cb()

    def on_event(self, event: Any) -> None:
        from .message import dispatch_message

        dispatch_message(self, event)

    def on_timer(self, event: Any) -> None:
        _ = event

    def add_class(self, *names: str) -> None:
        if self.screen is not None:
            self.screen.add_class(*names)

    def remove_class(self, *names: str) -> None:
        if self.screen is not None:
            self.screen.remove_class(*names)

    def has_class(self, name: str) -> bool:
        return bool(self.screen and self.screen.has_class(name))

    def toggle_class(self, *names: str) -> None:
        if self.screen is not None:
            self.screen.toggle_class(*names)

    def set_class(self, name: str, add: bool = True) -> None:
        if self.screen is not None:
            self.screen.set_class(name, add)

    def set_classes(self, classes: str | Iterable[str]) -> None:
        if self.screen is not None:
            self.screen.set_classes(classes)

    def update_classes(self) -> None:
        if self.screen is not None:
            self.screen.update_classes()

    def get_pseudo_classes(self) -> set[str]:
        return self.screen.get_pseudo_classes() if self.screen else set()

    def has_pseudo_class(self, name: str) -> bool:
        return name in self.get_pseudo_classes()

    def has_pseudo_classes(self, names: Iterable[str]) -> bool:
        pcs = self.get_pseudo_classes()
        return all(n in pcs for n in names)

    def get_child_by_id(self, id: str, expect_type: type | None = None) -> Any:  # noqa: A002
        return self.screen.get_child_by_id(id, expect_type) if self.screen else None

    def get_child_by_type(self, expect_type: type) -> Any:
        return self.screen.get_child_by_type(expect_type) if self.screen else None

    def query_children(self, selector: str | type = "*"):
        from .dom import DOMQuery

        return self.screen.query_children(selector) if self.screen else DOMQuery([])

    def query_exactly_one(self, selector: str, expect_type: type | None = None) -> Any:
        if self.screen is None:
            raise KeyError(selector)
        return self.screen.query_exactly_one(selector, expect_type)

    def query_one_optional(self, selector: str, expect_type: type | None = None) -> Any | None:
        return self.screen.query_one_optional(selector, expect_type) if self.screen else None

    def query_ancestor(self, selector: str | type) -> Any:
        if self.screen is None:
            raise KeyError(selector)
        return self.screen.query_ancestor(selector)

    def sort_children(self, *, key: Callable[..., Any] | None = None, reverse: bool = False) -> None:
        if self.screen is not None:
            self.screen.sort_children(key=key, reverse=reverse)

    def mount(self, *widgets: Any, before: Any = None, after: Any = None) -> Any:
        if self.screen is not None:
            return self.screen.mount(*widgets, before=before, after=after)
        return None

    def mount_all(self, widgets: Iterable[Any]) -> Any:
        if self.screen is not None:
            return self.screen.mount_all(widgets)
        return None

    def compose_add_child(self, widget: Any) -> None:
        if self.screen is not None:
            self.screen.compose_add_child(widget)

    def recompose(self) -> None:
        if self.screen is not None:
            self.screen.recompose()

    def refresh(self, *, layout: bool = False, recompose: bool = False) -> None:
        if self.screen is not None:
            self.screen.refresh(layout=layout, recompose=recompose)
        else:
            self.request_draw()

    def refresh_css(self, animate: bool = True) -> None:
        _ = animate
        if self.screen is not None and hasattr(self.screen, "apply_styles"):
            self.screen.apply_styles()
        self.request_draw()

    def render(self) -> Any:
        return self.screen.render() if self.screen is not None else ""

    def is_mounted(self, widget: Any) -> bool:
        return bool(getattr(widget, "_mounted", False) and getattr(widget, "app", None) is self)

    async def wait_for_refresh(self) -> None:
        if self.screen is not None:
            await self.screen.wait_for_refresh()

    def get_component_styles(self, *names: str) -> dict[str, Any]:
        return self.screen.get_component_styles(*names) if self.screen else {}

    def set_styles(self, css: str = "", **kwargs: Any) -> None:
        if self.screen is not None:
            self.screen.set_styles(css, **kwargs)

    def reset_styles(self) -> None:
        if self.screen is not None:
            self.screen.reset_styles()

    def notify_style_update(self) -> None:
        self.request_draw()

    def update_node_styles(self, animate: bool = True) -> None:
        _ = animate
        self.refresh_css()

    def update_styles(self, node: Any = None) -> None:
        _ = node
        self.refresh_css()

    def data_bind(self, **kwargs: Any) -> App:
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self

    def mutate_reactive(self, reactive_attr: Any) -> None:
        name = getattr(reactive_attr, "name", str(reactive_attr))
        val = getattr(self, name, None)
        from .reactive import _run_watchers

        _run_watchers(self, name, val, val)
        self.request_draw()

    def set_reactive(self, reactive_attr: Any, value: Any) -> None:
        name = getattr(reactive_attr, "name", str(reactive_attr))
        setattr(self, f"_reactive_{name}", value)

    def watch(self, obj: Any, attribute_name: str, callback: Callable[..., Any]) -> None:
        from .reactive import watch as _watch

        _watch(obj, attribute_name, callback)

    def format_title(self, title: str, sub_title: str) -> str:
        return f"{title} — {sub_title}" if sub_title else str(title)

    def validate_title(self, title: str) -> str:
        return str(title)

    def validate_sub_title(self, sub_title: str) -> str:
        return str(sub_title)

    def get_default_screen(self) -> Any:
        return self._base_screen()

    def get_driver_class(self) -> type:
        from .driver import HeadlessDriver, WgpuDriver

        return HeadlessDriver if isinstance(self._driver, HeadlessDriver) else WgpuDriver

    def get_line_filters(self) -> list[Any]:
        return []

    def get_loading_widget(self) -> Any:
        from .widgets.loading_indicator import LoadingIndicator

        return LoadingIndicator()

    def get_screen_stack(self) -> list[Any]:
        return list(self._screen_stack)

    def get_system_commands(self, screen: Any = None) -> list[Any]:
        _ = screen
        from .command_palette import collect_commands

        return collect_commands(self)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return {}

    def handle_bindings_clash(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def search_commands(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        self.action_command_palette()

    def search_themes(self) -> None:
        self.action_change_theme()

    def trap_focus(self, trap: bool = True) -> None:
        self._trap_focus = bool(trap)

    def update_keymap(self, keymap: dict[str, str]) -> None:
        self._keymap.update(keymap or {})

    def watch_ansi_theme_dark(self, _old: Any, _new: Any) -> None:
        self.request_draw()

    def watch_ansi_theme_light(self, _old: Any, _new: Any) -> None:
        self.request_draw()

    # --- Full Textual App properties & attributes ----------------------------

    @property
    def app(self) -> App:
        return self

    @property
    def current_theme(self) -> Theme:
        return self.theme

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
    def default_screen(self) -> Any:
        return self._base_screen()

    @property
    def active_bindings(self) -> dict[str, Any]:
        return self.screen.active_bindings if self.screen else {}

    @property
    def ancestors(self) -> list[Any]:
        return []

    @property
    def ancestors_with_self(self) -> list[Any]:
        return [self]

    @property
    def children(self) -> list[Any]:
        return [self.screen] if self.screen is not None else []

    @property
    def displayed_children(self) -> list[Any]:
        return self.children

    @property
    def displayed_and_visible_children(self) -> list[Any]:
        return self.children

    @property
    def has_parent(self) -> bool:
        return False

    @property
    def is_dom_root(self) -> bool:
        return True

    @property
    def is_attached(self) -> bool:
        return True

    @property
    def is_empty(self) -> bool:
        return len(self.children) == 0

    @property
    def is_on_screen(self) -> bool:
        return True

    @property
    def is_parent_active(self) -> bool:
        return True

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    @property
    def is_headless(self) -> bool:
        from .driver import HeadlessDriver

        return isinstance(self._driver, HeadlessDriver)

    @property
    def is_inline(self) -> bool:
        return False

    @property
    def is_web(self) -> bool:
        return False

    @property
    def debug(self) -> bool:
        return False

    @property
    def display(self) -> bool:
        return True

    @property
    def visible(self) -> bool:
        return True

    @property
    def app_focus(self) -> bool:
        return True

    @property
    def escape_to_minimize(self) -> bool:
        return bool(self.ESCAPE_TO_MINIMIZE)

    @property
    def auto_refresh(self) -> float | None:
        return None

    @property
    def animator(self) -> Any:
        return None

    @property
    def ansi_color(self) -> bool:
        return True

    @property
    def native_ansi_color(self) -> bool:
        return True

    @property
    def ansi_theme(self) -> Any:
        return self.theme

    @property
    def ansi_theme_dark(self) -> Any:
        return self.theme

    @property
    def ansi_theme_light(self) -> Any:
        return self.theme

    @property
    def colors(self) -> tuple[str, str]:
        return (self.theme.foreground, self.theme.background)

    @property
    def background_colors(self) -> tuple[str, str]:
        return (self.theme.background, self.theme.surface)

    @property
    def console_options(self) -> Any:
        return None

    @property
    def css_identifier(self) -> str:
        return type(self).__name__

    @property
    def css_identifier_styled(self) -> Any:
        from rich.text import Text

        return Text(self.css_identifier)

    @property
    def css_path_nodes(self) -> list[Any]:
        return [self]

    @property
    def css_tree(self) -> str:
        return self.screen.css_tree if self.screen else self.css_identifier

    @property
    def tree(self) -> str:
        return self.css_tree

    @property
    def pseudo_classes(self) -> set[str]:
        return {"dark"} if self.theme.dark else {"light"}

    @property
    def size(self):
        from .geometry import Size

        w, h = getattr(self, "_pixel_size", None) or getattr(self._driver, "size", None) or self.SIZE
        return Size(int(w), int(h))

    @property
    def viewport_size(self):
        return self.size

    @property
    def rich_style(self):
        from rich.style import Style

        return Style()

    @property
    def text_style(self):
        from rich.style import Style

        return Style()

    @property
    def selection_style(self):
        from rich.style import Style

        return Style()

    @property
    def workers(self) -> list[Worker]:
        return list(self._workers)

    @property
    def message_queue_size(self) -> int:
        return self._message_queue.qsize() if self._message_queue is not None else 0

    @property
    def log(self) -> Any:
        from .logging import log as _log

        return _log

    def exit(self) -> None:
        self._running = False
        for h in self._intervals:
            h.stop()
        for w in list(self._workers):
            if not w.is_finished:
                w.cancel()
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
        if self._canvas is not None:
            try:
                self._canvas.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            from rendercanvas.auto import loop
            loop.stop()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        """Windowed entry via ``WgpuDriver`` (rendercanvas loop)."""
        from .driver import WgpuDriver

        WgpuDriver(self).run_blocking()

    async def run_async(self, *, headless: bool = False, size: tuple[int, int] | None = None) -> None:
        """Asyncio-native entry — message pump + timers + workers on one loop."""
        from .driver import run_app_async

        self._attach_loop()
        await run_app_async(self, headless=headless, size=size or self.SIZE)

    @asynccontextmanager
    async def run_test(
        self, *, size: tuple[int, int] = (1100, 700),
    ) -> AsyncIterator[Any]:
        """``async with app.run_test() as pilot:`` — headless Pilot on asyncio."""
        from .pilot import run_test as _run_test

        async with _run_test(self, size=size) as pilot:
            yield pilot
