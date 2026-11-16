"""ModeApp — F1–Fn screen host (Textual App + modes analogue)."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Collection, Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from .app import App
from .blit import FrameBlitter
from .geometry import Rect
from .modal_paint import chip, dialog, field_box, strip_line, strip_lines, veil
from .screen import Screen
from .theme import Theme

# Modal fields that participate in the Textual-like push/pop stack.
_MODAL_ATTRS = (
    "_prompt",
    "_text",
    "_pick",
    "_confirm",
    "_open_file",
    "_form",
    "_overlay",
)


class PlaceholderScreen(Screen):
    """Default stub body for modes not yet implemented by the app."""

    def __init__(self, key: str, title: str, labels: Sequence[tuple[str, str]]) -> None:
        super().__init__()
        self._key = key
        self._title = title
        self._labels = list(labels)

    def compose(self):
        from .widgets import KeyBar, ModeBar, Panel, Static, StatusBar

        yield ModeBar(
            self._key, self._labels,
            on_select=lambda i: self.app.set_mode(i) if self.app else None,
        )
        yield Panel(Static(f"{self._title}\n\n(placeholder)"), title=self._title.upper())
        yield KeyBar([
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
        ])
        yield StatusBar(f"mode · {self._key}")


class ModeApp(App):
    """Hosts named screens (modes). Application supplies theme + screen factories."""

    TITLE = "textual-wgpu"
    SIZE = (1100, 700)
    # App-wide TCSS (layer 0) — screens layer DEFAULT_CSS on top.
    CSS = """
    ModeBar { height: 1; }
    KeyBar { height: 1; }
    StatusBar { height: 1; background: $boost; }
    """
    BINDINGS = [
        ("ctrl+p", "command_palette"),
        ("q", "quit"),
    ]

    def __init__(
        self,
        modes: Sequence[tuple[str, str, Callable[[], Screen] | None]],
        *,
        theme: Theme | None = None,
        title: str | None = None,
        start: str | None = None,
        post_fx: dict[str, float] | None = None,
    ) -> None:
        super().__init__(theme=theme)
        if title:
            self.TITLE = title
        # e.g. {"bloom": 0.45, "scanlines": 0.10} — CRT post-pass before blit.
        self._post_fx = dict(post_fx) if post_fx else None
        self._mode_defs = list(modes)
        self._labels = [(k, t) for k, t, _ in self._mode_defs]
        self._factories = {k: fac for k, _t, fac in self._mode_defs}
        self._screens: dict[str, Screen] = {}
        self.mode_index = 0
        if start:
            for i, (k, _t, _f) in enumerate(self._mode_defs):
                if k == start:
                    self.mode_index = i
                    break
        self.quit_modal = False
        self._prompt: dict | None = None  # title, value, callback
        # Multiline text editor (EditScreen analogue): title, value, hint, callback.
        self._text: dict | None = None
        self._pick: dict | None = None  # title, options[(id,label)], index, callback
        self._confirm: dict | None = None  # title, detail, ok, danger, callback
        # OpenFileScreen analogue: path field + filtered directory list + Add/Cancel.
        self._open_file: dict | None = None
        # Multi-field form (FilterScreen analogue): fields[(id,label,value)], focus, callback.
        self._form: dict | None = None
        # Fullscreen custom overlay (MapScreen analogue): paint/on_key/on_wheel callbacks.
        self._overlay: dict | None = None
        # Nested modals (Textual push_screen stack): underlays beneath the active modal.
        self._modal_stack: list[dict[str, object]] = []
        self._focus: object | None = None
        self._notify: str = ""
        self._notify_severity: str = "information"
        self._notify_until: float | None = None
        from .widgets.toast import Toast
        from .widgets.tooltip import Tooltip

        self._toast = Toast(id="-toast")
        self._toast.app = self
        self._tooltip = Tooltip(id="-tooltip")
        self._tooltip.app = self
        self._blitter: FrameBlitter | None = None
        self._pixel_size = self.SIZE
        self._root: Screen | None = None
        self._hits: list[tuple[Rect, Callable[[int, int], None]]] = []
        self._frame: Image.Image | None = None
        self._layout_size: tuple[int, int] = (0, 0)
        # Screen-level text selection (Markdown / RichText drag → Ctrl+C).
        self._selecting = False
        self._select_widget: object | None = None
        self._select_start = None  # Offset | None
        self._select_moved = False
        self._select_click_n = 0
        self._select_click_t = 0.0
        self._select_click_xy: tuple[int, int] | None = None
        self._pending_hit: Callable[[int, int], None] | None = None
        self._pending_hit_xy: tuple[int, int] | None = None
        # First frames: splash + bar, then mount the start mode (no empty/busy flash).
        self._booting = True
        self._boot_frac = 0.05
        self._boot_label = "Starting…"

    @property
    def mode_key(self) -> str:
        return self._labels[self.mode_index][0]

    @property
    def mode_title(self) -> str:
        return self._labels[self.mode_index][1]

    def _busy_open(self) -> bool:
        return bool(getattr(self, "_busy", None))

    def _active_modal_snap(self) -> dict[str, object] | None:
        snap: dict[str, object] = {}
        for attr in _MODAL_ATTRS:
            val = getattr(self, attr)
            if val is not None:
                snap[attr] = val
        return snap or None

    def _clear_active_modals(self) -> None:
        for attr in _MODAL_ATTRS:
            setattr(self, attr, None)

    def _restore_modal_snap(self, snap: dict[str, object]) -> None:
        self._clear_active_modals()
        for attr, val in snap.items():
            setattr(self, attr, val)

    def _begin_modal(self) -> bool:
        """Push current modal (if any) and clear slot for a new one. False if busy/quit."""
        if self._busy_open() or self.quit_modal:
            return False
        snap = self._active_modal_snap()
        if snap is not None:
            self._modal_stack.append(snap)
            self._clear_active_modals()
        return True

    def _end_modal(self) -> None:
        """After clearing the active modal, restore the underlay if stacked."""
        if self._modal_stack:
            self._restore_modal_snap(self._modal_stack.pop())

    def _modal_open(self) -> bool:
        return (
            self.quit_modal
            or self._busy_open()
            or self._active_modal_snap() is not None
            or bool(self._modal_stack)
        )

    def _base_screen(self) -> Screen | None:
        """Active mode screen (underlay beneath push_screen stack)."""
        if self._root is not None:
            return self._root
        if self._screens:
            return self._screens.get(self.mode_key)
        return None

    def push_screen(self, screen, callback=None, wait_for_dismiss: bool = False):  # noqa: ANN001
        self._layout_size = (0, 0)
        return super().push_screen(
            screen, callback, wait_for_dismiss=wait_for_dismiss,
        )

    def pop_screen(self, result=None):  # noqa: ANN001
        self._layout_size = (0, 0)
        return super().pop_screen(result)

    def action_quit(self) -> None:
        """Q / action_quit → quit confirm modal (not hard exit)."""
        self.ask_quit()

    def set_mode(self, index: int) -> None:
        if self._modal_open() or self._screen_stack:
            return
        if 0 <= index < len(self._labels):
            prev = self.mode_index
            key = self._labels[index][0]
            was_cached = key in self._screens
            self.mode_index = index
            self._layout_size = (0, 0)
            self.set_focus(None)
            self.clear_selection()
            screen = self._ensure_screen()
            # Resume only when re-entering an already mounted mode (not first create).
            if index != prev and was_cached and hasattr(screen, "on_screen_resume"):
                try:
                    screen.on_screen_resume()
                except Exception as exc:  # noqa: BLE001
                    print(f"on_screen_resume error: {exc}", flush=True)
            self.request_draw()

    def set_focus(self, widget: object | None, scroll_visible: bool = True) -> None:
        super().set_focus(widget, scroll_visible=scroll_visible)

    def _focusables(self) -> list[object]:
        root = self.screen or self._root
        if root is None:
            return []
        out: list[object] = []
        for w in root._walk(root):
            # Textual-style: can_focus, or legacy _focusable flag.
            focusable = bool(getattr(w, "can_focus", False) or getattr(w, "_focusable", False))
            if not focusable:
                continue
            if getattr(w, "disabled", False):
                continue
            if (getattr(w, "styles", None) or {}).get("display") == "none":
                continue
            # Skip collapsed panes (e.g. Chat select-mode twin).
            if getattr(w, "layout_hint_h", None) == 0:
                continue
            out.append(w)
        return out

    def _cycle_focus(self) -> None:
        items = self._focusables()
        if not items:
            return
        if self._focus not in items:
            self.set_focus(items[0])
            return
        i = items.index(self._focus)  # type: ignore[arg-type]
        self.set_focus(items[(i + 1) % len(items)])

    def focus_next(self) -> None:
        """Textual ``app.focus_next`` analogue (KeyBar Tab)."""
        self._cycle_focus()

    def _ensure_screen(self) -> Screen:
        key = self.mode_key
        if key not in self._screens:
            fac = self._factories.get(key)
            if fac is None:
                title = self.mode_title
                screen: Screen = PlaceholderScreen(key, title, self._labels)
            else:
                screen = fac()
            screen.mount(self)
            self._screens[key] = screen
        self._root = self._screens[key]
        return self._root

    def on_resize(self, width: int, height: int) -> None:
        self._pixel_size = (width, height)
        self._layout_size = (0, 0)
        screen = self._screens.get(self.mode_key) if self._screens else None
        if screen is not None and hasattr(screen, "on_resize"):
            try:
                screen.on_resize()
            except Exception:  # noqa: BLE001
                pass
        self.request_draw()

    def ask_quit(self) -> None:
        if self._active_modal_snap() is not None or self._modal_stack:
            return
        self.quit_modal = True

    def notify(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 3.0,
        **_kw,
    ) -> None:
        """Toast strip (Textual notify). ``severity``: information / warning / error."""
        self._notify = message
        self._notify_severity = (severity or "information").lower()
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = 3.0
        timeout_f = max(0.4, timeout_f)
        self._notify_until = time.monotonic() + timeout_f
        self._toast.app = self
        self._toast.show(message, severity=self._notify_severity, timeout=timeout_f)
        self.request_draw()
        # When the loop is live, Toast schedules its own clear; otherwise
        # paint uses Toast.visible (_until) and drops stale legacy fields.


    def show_tooltip(self, text: str, x: int, y: int) -> None:
        """Show floating tooltip near ``(x, y)``."""
        self._tooltip.app = self
        self._tooltip.show_at(text, x, y)
        self.request_draw()

    def hide_tooltip(self) -> None:
        self._tooltip.hide()
        self.request_draw()

    def ask_prompt(
        self,
        title: str,
        initial: str,
        callback: Callable[[str | None], None],
        *,
        password: bool = False,
        placeholder: str = "",
        hint: str = "Enter apply · Esc cancel",
        esc_clears_if_empty: bool = False,
    ) -> None:
        """Modal text prompt (Enter = apply, Esc = cancel). ``password`` masks input.

        ``esc_clears_if_empty``: Esc on blank field → ``callback(\"\")`` (QueryScreen).
        Nested over an open modal (Textual push_screen stack).
        """
        if not self._begin_modal():
            return
        self._prompt = {
            "title": title,
            "value": initial,
            "callback": callback,
            "password": bool(password),
            "placeholder": str(placeholder or ""),
            "hint": str(hint or "Enter apply · Esc cancel"),
            "esc_clears_if_empty": bool(esc_clears_if_empty),
        }
        self.request_draw()

    def ask_text(
        self,
        title: str,
        initial: str,
        callback: Callable[[str | None], None],
        *,
        hint: str = "Enter = new line · Ctrl+S OK · Esc cancel",
    ) -> None:
        """Multiline modal editor (EditScreen analogue).

        Enter inserts a newline; Ctrl+S / OK applies; Esc cancels (callback(None)).
        """
        if callback is None or not self._begin_modal():
            return
        self._text = {
            "title": title,
            "value": str(initial or ""),
            "hint": hint,
            "callback": callback,
            "focus": 0,  # 0 = body, 1 = OK, 2 = Cancel
        }
        self.request_draw()

    def ask_pick(
        self,
        title: str,
        options: Sequence[tuple[str, str]],
        callback: Callable[[str | None], None],
        *,
        current: str | None = None,
    ) -> None:
        """Modal list pick (↑↓ Enter · Esc cancel). options: (id, label)."""
        if not options or not self._begin_modal():
            return
        idx = 0
        if current is not None:
            for i, (oid, _) in enumerate(options):
                if oid == current:
                    idx = i
                    break
        self._pick = {
            "title": title,
            "options": list(options),
            "index": idx,
            "callback": callback,
        }
        self.request_draw()

    def ask_confirm(
        self,
        title: str,
        detail: str = "",
        callback: Callable[[bool], None] | None = None,
        *,
        ok: str = "OK",
        danger: bool = False,
    ) -> None:
        """Yes/No confirm (Y/Enter · N/Esc). callback(True) on OK. ConfirmScreen analogue."""
        if callback is None or not self._begin_modal():
            return
        self._confirm = {
            "title": title,
            "detail": detail,
            "ok": ok,
            "danger": danger,
            "callback": callback,
            "focus": 0 if danger else 1,  # danger → Cancel focused by default
        }
        self.request_draw()

    def ask_open_file(
        self,
        start: Path | str,
        callback: Callable[[Path | None], None],
        *,
        title: str = "Add genotype file",
        subtitle: str = (
            "23andMe, Argenética, MyHeritage, FTDNA, Big Y CSV, AncestryDNA, VCF; "
            ".txt .csv .tsv .vcf .zip .gz .bz2"
        ),
        suffixes: Collection[str] | None = None,
        kind: str = "file",
        must_exist: bool = True,
        ok_label: str = "Add",
    ) -> None:
        """DirectoryTree + path field modal (OpenFileScreen / PathScreen analogue).

        ``kind``: ``\"file\"`` or ``\"folder\"``.
        ``must_exist``: False allows confirming a typed path that is not on disk yet.
        callback(path) on chosen; callback(None) on cancel.
        Nested over an open modal (Textual push_screen stack).
        """
        if callback is None or not self._begin_modal():
            return
        kind = "folder" if kind == "folder" else "file"
        seed = Path(start).expanduser() if start else Path.cwd()
        if seed.is_file():
            folder = seed.parent if seed.parent.is_dir() else Path.cwd()
            path_value = str(seed.resolve())
        elif seed.is_dir():
            folder = seed.resolve()
            path_value = str(folder) + ("/" if kind == "file" else "")
        else:
            folder = seed.parent if seed.parent.is_dir() else Path.cwd()
            folder = folder.resolve()
            path_value = str(seed)
            if not must_exist and kind == "folder" and not path_value.endswith("/"):
                pass
        suf = {s.lower() if s.startswith(".") else f".{s.lower()}" for s in (suffixes or ())}
        self._open_file = {
            "title": title,
            "subtitle": subtitle,
            "folder": folder,
            "path_value": path_value,
            "suffixes": suf,
            "kind": kind,
            "must_exist": bool(must_exist),
            "ok_label": ok_label or ("OK" if kind == "folder" else "Add"),
            "entries": [],
            "index": 0,
            "scroll": 0,
            "focus": "list",  # path | list | ok | cancel
            "callback": callback,
        }
        self._open_file_reload()
        self.request_draw()

    def ask_form(
        self,
        title: str,
        fields: Sequence[tuple[str, str, str]],
        callback: Callable[[dict[str, str] | None], None],
        *,
        hint: str = "empty field = no filter · Tab · Enter Apply · Esc cancel",
        show_reset: bool = True,
        ok_label: str = "Apply",
        box_w: int | None = None,
        password_fields: Collection[str] | None = None,
    ) -> None:
        """Multi-field modal (FilterScreen / GeminiConfigScreen analogue).

        fields: (id, label, initial_value).
        ``password_fields``: field ids rendered as ``•`` masks.
        callback(values) on Apply/OK; callback({}) on Reset; callback(None) on cancel.
        Nested over an open modal (Textual push_screen stack).
        """
        if callback is None or not fields or not self._begin_modal():
            return
        pw = {str(x) for x in (password_fields or ())}
        self._form = {
            "title": title,
            "hint": hint,
            "fields": [(fid, label, str(initial or "")) for fid, label, initial in fields],
            "focus": 0,  # field index, then Reset (optional), then Apply/OK
            "callback": callback,
            "show_reset": bool(show_reset),
            "ok_label": str(ok_label or "Apply"),
            "box_w": box_w,
            "password_fields": pw,
        }
        self.request_draw()

    def show_overlay(
        self,
        paint: Callable[[ImageDraw.ImageDraw, int, int], list[tuple[Rect, Callable[[int, int], None]]]],
        *,
        on_key: Callable[[str], str | None] | None = None,
        on_wheel: Callable[[int], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        """Fullscreen custom modal (MapScreen analogue).

        ``paint(draw, w, h) → hits``.
        ``on_key`` may return ``\"close\"`` / ``\"handled\"`` / None (fall through to Esc→close).
        Nested over an open modal (Textual push_screen stack).
        """
        if paint is None or not self._begin_modal():
            return
        self._overlay = {
            "paint": paint,
            "on_key": on_key,
            "on_wheel": on_wheel,
            "on_close": on_close,
        }
        self.request_draw()

    def show_doc(
        self,
        title: str,
        body: object,
        *,
        close_keys: Sequence[str] = ("escape", "q"),
        hint: str = "↑↓ / wheel scroll · Esc close",
        box_w: int | None = None,
        box_h: int | None = None,
    ) -> None:
        """Centered scrollable text modal (HelpScreen / IssuesScreen analogue).

        ``body`` is ``rich.text.Text`` or plain ``str``.
        Uses ``show_overlay`` (push-stack aware).
        """
        from .widgets import RichText as RtWidget

        state = {"scroll": 0, "max_scroll": 0}
        close = {k.lower() for k in close_keys} | {"escape"}

        def paint(draw: ImageDraw.ImageDraw, w: int, h: int) -> list:
            t = self.theme
            veil(draw, w, h)
            mw = box_w if box_w is not None else min(760, max(420, w - 80))
            mh = box_h if box_h is not None else min(int(h * 0.88), max(280, h - 60))
            mw = min(mw, w - 40)
            mh = min(mh, h - 40)
            mx, my = (w - mw) // 2, (h - mh) // 2
            dialog(draw, mx, my, mw, mh, t)
            strip_line(
                draw, Rect(mx + 16, my + 10, mw - 32, 24),
                str(title or "Info")[:72], t, font_size=15,
            )
            body_rect = Rect(mx + 12, my + 44, mw - 24, mh - 78)
            draw.rectangle(
                [body_rect.x, body_rect.y, body_rect.x1, body_rect.y1],
                fill=t.rgba("panel"),
            )
            rt = RtWidget(body)
            rt.app = self
            rt.fill_bg = False
            rt.LINE_H = 17
            rt.FONT_SIZE = 12
            rt.scroll_y = int(state["scroll"])
            rt.layout(body_rect)
            n_lines = len(rt._lines())
            vis = max(1, body_rect.h // rt.LINE_H)
            state["max_scroll"] = max(0, n_lines - vis)
            rt.scroll_y = max(0, min(rt.scroll_y, state["max_scroll"]))
            state["scroll"] = rt.scroll_y
            rt.paint(draw)
            strip_line(
                draw, Rect(mx + 16, my + mh - 30, mw - 32, 22),
                str(hint)[:90], t, fg=t.rgba("muted"), font_size=12,
            )
            return []

        def on_key(key: str) -> str | None:
            if key in close:
                return "close"
            step = 1 if key == "up" else (
                8 if key == "pageup" else (
                    -1 if key == "down" else (
                        -8 if key == "pagedown" else 0
                    )
                )
            )
            if key == "home":
                state["scroll"] = 0
                return "handled"
            if key == "end":
                state["scroll"] = int(state["max_scroll"])
                return "handled"
            if step:
                state["scroll"] = max(
                    0, min(int(state["max_scroll"]), int(state["scroll"]) - step),
                )
                return "handled"
            return None

        def on_wheel(steps: int) -> None:
            state["scroll"] = max(
                0, min(int(state["max_scroll"]), int(state["scroll"]) + steps),
            )

        self.show_overlay(paint, on_key=on_key, on_wheel=on_wheel)

    def close_overlay(self) -> None:
        if self._overlay is None:
            return
        cb = self._overlay.get("on_close")
        self._overlay = None
        self._end_modal()
        if callable(cb):
            cb()

    def _form_values(self) -> dict[str, str]:
        assert self._form is not None
        return {fid: val for fid, _label, val in self._form["fields"]}

    def _form_apply(self) -> None:
        if self._form is None:
            return
        cb = self._form["callback"]
        values = self._form_values()
        self._form = None
        self._end_modal()
        cb(values)

    def _form_reset(self) -> None:
        if self._form is None:
            return
        cb = self._form["callback"]
        self._form = None
        self._end_modal()
        cb({})

    def _form_cancel(self) -> None:
        if self._form is None:
            return
        cb = self._form["callback"]
        self._form = None
        self._end_modal()
        cb(None)

    def _open_file_reload(self) -> None:
        assert self._open_file is not None
        folder: Path = self._open_file["folder"]
        suf: set[str] = self._open_file["suffixes"]
        kind = self._open_file.get("kind") or "file"
        entries: list[tuple[str, str, str]] = []
        if folder.parent != folder:
            entries.append(("up", str(folder.parent), f"↑ ..  ({folder.parent})"))
        try:
            kids = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            kids = []
        for p in kids:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                entries.append(("dir", str(p), f"▸ {p.name}/"))
            elif kind == "file" and (not suf or p.suffix.lower() in suf):
                entries.append(("file", str(p), f"  {p.name}"))
            elif kind == "folder":
                # PathScreen: files still listed so picking fills parent folder.
                entries.append(("file", str(p), f"  {p.name}"))
        self._open_file["entries"] = entries
        self._open_file["index"] = min(self._open_file["index"], max(0, len(entries) - 1))
        # Keep typed path unless navigating purely by tree (caller sets path_value).
        if kind == "folder" and not str(self._open_file.get("path_value") or "").strip():
            self._open_file["path_value"] = str(folder)
        elif kind == "file" and self._open_file.get("path_value") in ("", None):
            self._open_file["path_value"] = str(folder) + "/"
        self._open_file_ensure_visible()

    def _open_file_ensure_visible(self) -> None:
        assert self._open_file is not None
        vis = int(self._open_file.get("visible") or 12)
        idx = int(self._open_file["index"])
        scroll = int(self._open_file["scroll"])
        if idx < scroll:
            scroll = idx
        elif idx >= scroll + vis:
            scroll = idx - vis + 1
        n = len(self._open_file["entries"])
        self._open_file["scroll"] = max(0, min(scroll, max(0, n - vis)))

    def _open_file_cancel(self) -> None:
        if self._open_file is None:
            return
        cb = self._open_file["callback"]
        self._open_file = None
        self._end_modal()
        cb(None)

    def _open_file_accept(self, path: Path) -> None:
        if self._open_file is None:
            return
        cb = self._open_file["callback"]
        self._open_file = None
        self._end_modal()
        cb(path.resolve())

    def _open_file_confirm_path(self, raw: str) -> None:
        """OK / Enter: validate and accept path (PathScreen._confirm)."""
        if self._open_file is None:
            return
        text = raw.strip()
        if not text:
            return
        p = Path(text).expanduser()
        kind = self._open_file.get("kind") or "file"
        must_exist = bool(self._open_file.get("must_exist", True))
        if p.is_dir() and kind == "file" and self._open_file.get("focus") == "path":
            # Enter in field on a folder → navigate tree (PathScreen).
            self._open_file["folder"] = p.resolve()
            self._open_file["index"] = 0
            self._open_file["scroll"] = 0
            self._open_file["path_value"] = str(p.resolve()) + "/"
            self._open_file["focus"] = "list"
            self._open_file_reload()
            return
        if kind == "folder" and p.is_file():
            self.notify("This source needs a folder, not a file")
            return
        if kind == "file" and p.is_dir():
            self.notify("This source needs a file, not a folder")
            return
        if must_exist and not p.exists():
            self.notify(f"Not found: {p}")
            return
        if kind == "file" and p.is_file():
            self._open_file_accept(p)
            return
        if kind == "folder":
            self._open_file_accept(p.resolve() if p.exists() else p)
            return
        if not must_exist:
            self._open_file_accept(p)
            return
        self.notify(f"Not found: {p}")

    def _open_file_go(self, raw: str) -> None:
        """Enter in path field: navigate folder or confirm."""
        if self._open_file is None:
            return
        focus = self._open_file.get("focus") or "list"
        if focus == "ok":
            self._open_file_confirm_path(raw)
            return
        p = Path(raw.strip()).expanduser()
        kind = self._open_file.get("kind") or "file"
        if p.is_dir():
            self._open_file["folder"] = p.resolve()
            self._open_file["index"] = 0
            self._open_file["scroll"] = 0
            self._open_file["path_value"] = (
                str(p.resolve()) if kind == "folder" else str(p.resolve()) + "/"
            )
            self._open_file["focus"] = "list"
            self._open_file_reload()
            return
        self._open_file_confirm_path(raw)

    def _open_file_activate(self) -> None:
        if self._open_file is None:
            return
        entries = self._open_file["entries"]
        if not entries:
            return
        idx = int(self._open_file["index"])
        entry_kind, path_s, _label = entries[idx]
        kind = self._open_file.get("kind") or "file"
        if entry_kind in ("dir", "up"):
            folder = Path(path_s)
            self._open_file["folder"] = folder
            self._open_file["index"] = 0
            self._open_file["scroll"] = 0
            if kind == "folder":
                self._open_file["path_value"] = str(folder)
            else:
                self._open_file["path_value"] = str(folder) + "/"
            self._open_file_reload()
            return
        # File row.
        p = Path(path_s)
        if kind == "folder":
            self._open_file["path_value"] = str(p.parent)
            return
        self._open_file_accept(p)

    def _confirm_yes(self) -> None:
        if self._confirm is None:
            return
        cb = self._confirm["callback"]
        self._confirm = None
        self._end_modal()
        cb(True)

    def _confirm_no(self) -> None:
        if self._confirm is None:
            return
        cb = self._confirm["callback"]
        self._confirm = None
        self._end_modal()
        cb(False)

    def _text_ok(self) -> None:
        if self._text is None:
            return
        cb = self._text["callback"]
        value = str(self._text.get("value") or "")
        self._text = None
        self._end_modal()
        cb(value)

    def _text_cancel(self) -> None:
        if self._text is None:
            return
        cb = self._text["callback"]
        self._text = None
        self._end_modal()
        cb(None)

    def _pick_accept(self, oid: str | None) -> None:
        if self._pick is None:
            return
        cb = self._pick["callback"]
        self._pick = None
        self._end_modal()
        cb(oid)

    def _prompt_finish(self, value: str | None) -> None:
        if self._prompt is None:
            return
        cb = self._prompt["callback"]
        self._prompt = None
        self._end_modal()
        cb(value)

    def on_escape(self) -> None:
        if self._confirm is not None:
            self._confirm_no()
            return
        if self._overlay is not None:
            self.close_overlay()
            return
        if self._form is not None:
            self._form_cancel()
            return
        if self._open_file is not None:
            self._open_file_cancel()
            return
        if self._pick is not None:
            self._pick_accept(None)
            return
        if self._text is not None:
            self._text_cancel()
            return
        if self._prompt is not None:
            val = str(self._prompt.get("value") or "")
            clears = bool(self._prompt.get("esc_clears_if_empty"))
            if clears and not val.strip():
                self._prompt_finish("")
            else:
                self._prompt_finish(None)
            return
        # Pop push_screen stack (command palette, custom screens).
        if self._screen_stack:
            self.pop_screen()
            return
        if self.quit_modal:
            self.quit_modal = False
        else:
            self.ask_quit()

    def confirm_quit_yes(self) -> None:
        if self.quit_modal:
            self.exit()

    def confirm_quit_no(self) -> None:
        if self.quit_modal:
            self.quit_modal = False

    def _chrome_hit(self, ix: int, iy: int) -> Callable[[int, int], None] | None:
        """Hit that should fire immediately (chrome / scrollbar / buttons / TextArea).

        Content hits (RichText ``@click``) are deferred so a drag can become a
        screen selection instead of activating a link.
        """
        from .widget import Widget
        from .widgets import (
            Button, KeyBar, ModeBar, OptionList, ScrollView, Scrollbar,
            TextArea, VerticalScroll,
        )

        root = self.screen or self._root
        if root is None:
            return None
        for w in root._walk(root):
            if (getattr(w, "styles", None) or {}).get("display") == "none":
                continue
            if isinstance(w, (ModeBar, KeyBar, Button, OptionList, TextArea, Scrollbar)):
                for rect, action in w.hits():
                    if rect.contains(ix, iy):
                        return action
            elif not getattr(w, "allow_select", False) and type(w).hits is not Widget.hits:
                for rect, action in w.hits():
                    if rect.contains(ix, iy):
                        return action
            if isinstance(w, (VerticalScroll, ScrollView)):
                bar = getattr(w, "_scrollbar", None)
                if bar is not None:
                    for rect, action in bar.hits():
                        if rect.contains(ix, iy):
                            return action
        return None

    def _selectable_at(self, ix: int, iy: int):
        """Deepest ``ALLOW_SELECT`` content widget under the pointer + content Offset."""
        from .geometry import Offset
        from .selection import pointer_to_offset
        from .widgets import RichText, ScrollView, VerticalScroll

        root = self.screen or self._root
        if root is None or not self.ALLOW_SELECT:
            return None, None
        candidates: list[tuple[object, Offset, int]] = []
        for depth, w in enumerate(root._walk(root)):
            if not getattr(w, "allow_select", False):
                continue
            scroll_y = 0
            pad_x, pad_y = 4, 2
            font_size = getattr(w, "RENDER_FONT_SIZE", getattr(w, "FONT_SIZE", 13))
            line_h = getattr(w, "LINE_H", None)
            rect = w.rect
            parent = getattr(w, "parent", None)
            if isinstance(parent, VerticalScroll) and parent.rect.contains(ix, iy):
                scroll_y = parent.scroll_y
                # Content is painted inset inside the scroll child rect.
                pad_x, pad_y = 4, 2
                rect = w.rect
            elif isinstance(w, RichText):
                scroll_y = w.scroll_y
                pad_x, pad_y = 6, 2
            elif isinstance(w, ScrollView):
                continue  # select on content subclasses, not empty ScrollView
            if not rect.contains(ix, iy) and not (
                isinstance(parent, VerticalScroll) and parent.rect.contains(ix, iy)
            ):
                continue
            hit_rect = rect
            if isinstance(parent, VerticalScroll):
                # Use content area of the scroll (exclude gutter).
                hit_rect = Rect(
                    parent.rect.x,
                    parent.rect.y,
                    max(0, parent.rect.w - getattr(parent, "BAR_W", 0) - 2),
                    parent.rect.h,
                )
                if not hit_rect.contains(ix, iy):
                    continue
            off = pointer_to_offset(
                ix, iy,
                rect=hit_rect if isinstance(parent, VerticalScroll) else rect,
                scroll_y=scroll_y,
                pad_x=pad_x,
                pad_y=pad_y,
                font_size=font_size,
                line_h=line_h,
            )
            if off is not None:
                candidates.append((w, off, depth))
        if not candidates:
            return None, None
        # Deepest in walk order.
        w, off, _ = max(candidates, key=lambda t: t[2])
        return w, off

    def _set_screen_selection(self, widget: object, start, end) -> None:  # noqa: ANN001
        from .selection import Selection

        screen = self.screen
        if screen is None or not hasattr(screen, "selections"):
            return
        screen.selections = {widget: Selection.from_offsets(start, end)}
        self.request_draw()

    def _copy_screen_selection(self) -> bool:
        """Copy ``Screen.get_selected_text`` if any. True when handled."""
        screen = self.screen
        if screen is None or not hasattr(screen, "get_selected_text"):
            return False
        try:
            sel = screen.get_selected_text()
        except Exception:  # noqa: BLE001
            sel = None
        if not sel:
            return False
        ok = self.copy_to_clipboard(sel)
        if ok:
            self.notify(f"Copied selection · {len(sel):,} chars")
        else:
            # In-app buffer still updated; OS tool may be missing.
            self.notify(
                f"Copied selection · {len(sel):,} chars (app buffer)",
                severity="warning",
            )
        return True

    def _begin_select(self, widget: object, offset, *, clicks: int = 1) -> None:  # noqa: ANN001
        from .geometry import Offset
        from .selection import SELECT_ALL, Selection, strips_plain, word_span_at

        screen = self.screen
        if screen is None:
            return
        self._select_widget = widget
        self._select_start = offset
        self._selecting = True
        self._select_moved = False
        if clicks >= 3:
            screen.selections = {widget: SELECT_ALL}
            self._selecting = False
            self.request_draw()
            return
        if clicks == 2:
            strips = list(widget.render_lines()) if hasattr(widget, "render_lines") else []
            lines = strips_plain(strips).splitlines() or [""]
            y = max(0, min(len(lines) - 1, offset.y))
            a, b = word_span_at(lines[y], offset.x)
            screen.selections = {
                widget: Selection(Offset(a, y), Offset(b, y)),
            }
            self._select_start = Offset(a, y)
            self.request_draw()
            return
        # Single click: caret-like empty selection until drag.
        screen.selections = {widget: Selection(offset, offset)}
        self.request_draw()

    def on_pointer(self, x: float, y: float, button: int) -> None:
        import time

        if button not in (0, 1):
            return
        if self._busy_open():
            return  # BusyModal blocks all pointer input
        ix, iy = int(x), int(y)
        self._pending_hit = None
        self._pending_hit_xy = None

        # Modal paints replace self._hits (confirm/quit/pick); prompt clears them.
        if self._modal_open() and not self._busy_open():
            hit = False
            for rect, action in reversed(self._hits):
                if rect.contains(ix, iy):
                    action(ix, iy)
                    hit = True
                    break
            if hit:
                return
            # Click outside dialog veil → dismiss (ModalDialog.on_click).
            if (
                self._prompt is not None
                or self._text is not None
                or self._pick is not None
                or self._confirm is not None
                or self._open_file is not None
                or self._form is not None
                or self.quit_modal
            ):
                self.on_escape()
            return

        chrome = self._chrome_hit(ix, iy)
        selectable, offset = self._selectable_at(ix, iy)

        # Chrome / TextArea / scrollbar: fire immediately; clear screen selection.
        if chrome is not None and selectable is None:
            self.clear_selection()
            chrome(ix, iy)
            return

        if selectable is not None and offset is not None:
            now = time.monotonic()
            clicks = 1
            if (
                self._select_click_xy is not None
                and abs(self._select_click_xy[0] - ix) < 6
                and abs(self._select_click_xy[1] - iy) < 6
                and now - self._select_click_t < 0.4
            ):
                clicks = min(3, self._select_click_n + 1)
            else:
                clicks = 1
            self._select_click_n = clicks
            self._select_click_t = now
            self._select_click_xy = (ix, iy)
            # Defer content hits (RichText @click) until pointer-up without drag.
            if chrome is not None:
                self._pending_hit = chrome
                self._pending_hit_xy = (ix, iy)
            else:
                for rect, action in reversed(self._hits):
                    if rect.contains(ix, iy):
                        self._pending_hit = action
                        self._pending_hit_xy = (ix, iy)
                        break
            self._begin_select(selectable, offset, clicks=clicks)
            return

        # Nothing selectable — fire chrome/hit and clear selection.
        self.clear_selection()
        if chrome is not None:
            chrome(ix, iy)
            return
        for rect, action in reversed(self._hits):
            if rect.contains(ix, iy):
                action(ix, iy)
                return

    def on_pointer_move(self, x: float, y: float) -> bool:
        from .widgets import ScrollView, TextArea, VerticalScroll

        if self._busy_open():
            return False
        if self._modal_open():
            return False
        ix, iy = int(x), int(y)

        if self._selecting and self._select_widget is not None and self._select_start is not None:
            w, off = self._selectable_at(ix, iy)
            if w is self._select_widget and off is not None:
                if off != self._select_start:
                    self._select_moved = True
                    self._pending_hit = None
                self._set_screen_selection(w, self._select_start, off)
                return True
            # Pointer left widget — clamp to last start/end via edge offset.
            if hasattr(self._select_widget, "rect"):
                from .selection import pointer_to_offset

                rect = self._select_widget.rect
                parent = getattr(self._select_widget, "parent", None)
                scroll_y = 0
                pad_x, pad_y = 4, 2
                if isinstance(parent, VerticalScroll):
                    scroll_y = parent.scroll_y
                    rect = Rect(
                        parent.rect.x, parent.rect.y,
                        max(0, parent.rect.w - getattr(parent, "BAR_W", 0) - 2),
                        parent.rect.h,
                    )
                elif hasattr(self._select_widget, "scroll_y"):
                    scroll_y = int(getattr(self._select_widget, "scroll_y") or 0)
                    pad_x, pad_y = 6, 2
                cx = min(max(ix, rect.x), rect.x1 - 1)
                cy = min(max(iy, rect.y), rect.y1 - 1)
                off = pointer_to_offset(
                    cx, cy, rect=rect, scroll_y=scroll_y,
                    pad_x=pad_x, pad_y=pad_y,
                    font_size=getattr(self._select_widget, "RENDER_FONT_SIZE", 13),
                    line_h=getattr(self._select_widget, "LINE_H", None),
                )
                if off is not None:
                    self._select_moved = True
                    self._pending_hit = None
                    self._set_screen_selection(self._select_widget, self._select_start, off)
                    return True

        # TextArea drag-select first (focused or any dragging field).
        if isinstance(self._focus, TextArea) and self._focus.on_pointer_move(ix, iy):
            return True
        root = self.screen or self._root
        if root is None:
            return False
        for w in root._walk(root):
            if isinstance(w, TextArea) and w.on_pointer_move(ix, iy):
                return True
            if isinstance(w, (VerticalScroll, ScrollView)) and w.on_pointer_move(ix, iy):
                return True
        # Track deepest hovered widget for :hover CSS & hover-aware widgets (e.g. Link).
        hovered: object | None = None
        for w in root._walk(root):
            if (getattr(w, "styles", None) or {}).get("display") == "none":
                continue
            r = getattr(w, "rect", None)
            if r is not None and r.w > 0 and r.h > 0 and r.contains(ix, iy):
                hovered = w
        if hovered is not getattr(self, "_hover", None):
            self._hover = hovered
            if hasattr(root, "apply_styles"):
                try:
                    root.apply_styles()
                except Exception:  # noqa: BLE001
                    pass
            return True
        return False

    def on_pointer_up(self, x: float, y: float, button: int) -> None:
        from .widgets import ScrollView, TextArea, VerticalScroll

        ix, iy = int(x), int(y)
        if self._selecting:
            self._selecting = False
            # Collapse zero-width selection unless a multi-click word/all select.
            screen = self.screen
            if (
                screen is not None
                and not self._select_moved
                and self._select_click_n == 1
                and self._select_widget is not None
            ):
                screen.selections = {}
            if not self._select_moved and self._pending_hit is not None:
                hx, hy = self._pending_hit_xy or (ix, iy)
                try:
                    self._pending_hit(hx, hy)
                except Exception:  # noqa: BLE001
                    pass
            elif self._select_moved and self._select_widget is not None:
                from .events import TextSelected
                from .widget import dispatch_event

                dispatch_event(self._select_widget, TextSelected(), "on_text_selected")
                if screen is not None:
                    dispatch_event(screen, TextSelected(), "on_text_selected")
            self._pending_hit = None
            self._pending_hit_xy = None
            self.request_draw()

        root = self._root
        if root is None:
            return
        for w in root._walk(root):
            if isinstance(w, TextArea):
                w.on_pointer_up(ix, iy)
            if isinstance(w, (VerticalScroll, ScrollView)):
                w.on_pointer_up(ix, iy)

    def on_wheel(self, x: float, y: float, dx: float, dy: float) -> None:
        if self._busy_open():
            return
        steps = int(dy / 100) * 3
        if steps == 0 and dy != 0:
            steps = 3 if dy > 0 else -3
        if self._overlay is not None:
            wheel = self._overlay.get("on_wheel")
            if callable(wheel):
                wheel(steps)
            return
        if self._form is not None:
            return
        if self._open_file is not None:
            n = len(self._open_file["entries"])
            if n:
                self._open_file["index"] = max(
                    0, min(n - 1, int(self._open_file["index"]) + steps),
                )
                self._open_file_ensure_visible()
            return
        if (self.quit_modal or self._prompt is not None or self._text is not None
                or self._pick is not None or self._confirm is not None):
            return
        from .widgets import OptionList, RichText, ScrollView, VerticalScroll
        from .widgets.log import _LogBase

        ix, iy = int(x), int(y)
        root = self._ensure_screen()
        for w in root._walk(root):
            if isinstance(w, OptionList) and w.rect.contains(ix, iy):
                w.move_highlight(steps)
                return
            if isinstance(w, (RichText, _LogBase)) and w.rect.contains(ix, iy):
                w.scroll(steps)
                return
            focusable = bool(getattr(w, "can_focus", False) or getattr(w, "_focusable", False))
            if focusable and w.rect.contains(ix, iy) and hasattr(w, "move"):
                w.move(getattr(w, "cursor", 0) + steps)  # type: ignore[misc]
                return
            # Tree / DataTable / other ScrollView without custom move().
            if isinstance(w, ScrollView) and w.rect.contains(ix, iy):
                w.scroll(steps)
                return
        target: VerticalScroll | None = None
        for w in root._walk(root):
            if isinstance(w, VerticalScroll) and w.rect.contains(ix, iy):
                target = w
                break
        if target is None:
            for w in root._walk(root):
                if isinstance(w, VerticalScroll):
                    target = w
                    break
        if target is not None:
            target.scroll(steps)

    def on_char(self, ch: str) -> None:
        from .widgets import TextArea

        if self._busy_open():
            return
        # Pushed screens (e.g. command palette) receive typed chars first.
        if self._screen_stack:
            top = self._screen_stack[-1].screen
            if hasattr(top, "on_char") and top.on_char(ch):
                return
        if self._overlay is not None:
            return
        if self._form is not None:
            if ch and ch >= " ":
                fi = int(self._form["focus"])
                fields = self._form["fields"]
                if 0 <= fi < len(fields):
                    fid, label, val = fields[fi]
                    fields[fi] = (fid, label, val + ch)
            return
        if self._open_file is not None:
            if self._open_file.get("focus") == "path" and ch and ch >= " ":
                self._open_file["path_value"] += ch
            return
        if self._pick is not None:
            return
        if self._text is not None:
            if int(self._text.get("focus") or 0) == 0 and ch and ch >= " ":
                self._text["value"] = str(self._text.get("value") or "") + ch
            return
        if self._prompt is not None:
            if ch and ch >= " ":
                self._prompt["value"] += ch
            return
        if isinstance(self._focus, TextArea) and ch and ch >= " ":
            if getattr(self._focus, "read_only", False) or self._focus.disabled:
                return
            self._focus.insert(ch)

    def _paste_into(self, current: str) -> str | None:
        """Append clipboard text to ``current``. None if clipboard empty/unavailable."""
        clip = self.read_clipboard()
        if clip is None or clip == "":
            return None
        return current + clip

    def on_key(self, key: str) -> None:
        """Central key router — modals, stack, focus, F-keys/q, then screen bindings."""
        from .widgets import TextArea

        # BusyModal: block all keys (including F-keys / Q) until hide_busy.
        if self._busy_open():
            return

        # App BINDINGS that must work even with a pushed screen (palette).
        if key == "ctrl+p" and self._try_app_bindings(key):
            return

        # Top of push_screen stack owns keys (command palette, custom screens).
        if self._screen_stack and not self._modal_open():
            top = self._screen_stack[-1].screen
            if key == "escape":
                # Prefer screen binding; fall back to pop.
                if hasattr(top, "on_key") and top.on_key(key):
                    return
                self.pop_screen()
                return
            if hasattr(top, "on_key") and top.on_key(key):
                return
            # Don't fall through to mode F-keys while a stack screen is up.
            return

        if self._confirm is not None:
            if key in ("y",):
                self._confirm_yes()
            elif key in ("n", "escape"):
                self._confirm_no()
            elif key in ("left", "right", "tab"):
                self._confirm["focus"] = 1 - int(self._confirm.get("focus") or 0)
            elif key in ("enter", "return"):
                if int(self._confirm.get("focus") or 0) == 0:
                    self._confirm_yes()
                else:
                    self._confirm_no()
            return

        if self._overlay is not None:
            handler = self._overlay.get("on_key")
            result = handler(key) if callable(handler) else None
            if result == "close" or key in ("escape",):
                self.close_overlay()
            # "handled" / None: stay open (Esc already closed above)
            return

        if self._form is not None:
            fields = self._form["fields"]
            n = len(fields)
            show_reset = bool(self._form.get("show_reset", True))
            n_btns = 2 if show_reset else 1
            last = n + n_btns - 1
            fi = int(self._form["focus"])
            if key == "escape":
                self._form_cancel()
            elif key == "ctrl+v" and 0 <= fi < n:
                fid, label, val = fields[fi]
                pasted = self._paste_into(val)
                if pasted is not None:
                    # Single-line fields: flatten newlines.
                    fields[fi] = (fid, label, pasted.replace("\r\n", " ").replace("\n", " ").replace("\r", " "))
            elif key == "tab":
                self._form["focus"] = (fi + 1) % (n + n_btns)
            elif key in ("up",) and fi > 0:
                self._form["focus"] = fi - 1
            elif key in ("down",) and fi < last:
                self._form["focus"] = fi + 1
            elif key == "backspace" and 0 <= fi < n:
                fid, label, val = fields[fi]
                fields[fi] = (fid, label, val[:-1])
            elif key in ("enter", "return"):
                if show_reset and fi == n:  # Reset
                    self._form_reset()
                elif fi >= n + (1 if show_reset else 0):  # Apply/OK
                    self._form_apply()
                elif fi < n - 1:
                    self._form["focus"] = fi + 1
                else:
                    self._form_apply()
            elif key in ("left", "right") and show_reset and fi >= n:
                self._form["focus"] = n if fi == n + 1 else n + 1
            return

        if self._open_file is not None:
            focus = self._open_file.get("focus") or "list"
            entries = self._open_file["entries"]
            if key == "escape":
                self._open_file_cancel()
            elif key == "tab":
                order = ["path", "list", "ok", "cancel"]
                self._open_file["focus"] = order[(order.index(focus) + 1) % len(order)]
            elif focus == "path":
                if key in ("enter", "return"):
                    self._open_file_go(self._open_file["path_value"])
                elif key == "ctrl+v":
                    pasted = self._paste_into(self._open_file["path_value"])
                    if pasted is not None:
                        self._open_file["path_value"] = (
                            pasted.replace("\r\n", "").replace("\n", "").replace("\r", "")
                        )
                elif key == "backspace":
                    self._open_file["path_value"] = self._open_file["path_value"][:-1]
                elif key == "down":
                    self._open_file["focus"] = "list"
            elif focus == "list":
                if key in ("u", "backspace"):
                    # PathScreen go_up — first entry is ↑ parent when available.
                    if entries and entries[0][0] == "up":
                        self._open_file["index"] = 0
                        self._open_file_activate()
                elif key == "up":
                    self._open_file["index"] = max(0, int(self._open_file["index"]) - 1)
                    self._open_file_ensure_visible()
                elif key == "down":
                    if entries:
                        self._open_file["index"] = min(
                            len(entries) - 1, int(self._open_file["index"]) + 1,
                        )
                        self._open_file_ensure_visible()
                elif key in ("enter", "return"):
                    self._open_file_activate()
                elif key == "pageup":
                    vis = int(self._open_file.get("visible") or 12)
                    self._open_file["index"] = max(0, int(self._open_file["index"]) - vis)
                    self._open_file_ensure_visible()
                elif key == "pagedown":
                    vis = int(self._open_file.get("visible") or 12)
                    if entries:
                        self._open_file["index"] = min(
                            len(entries) - 1, int(self._open_file["index"]) + vis,
                        )
                        self._open_file_ensure_visible()
                elif key == "home":
                    self._open_file["index"] = 0
                    self._open_file_ensure_visible()
                elif key == "end":
                    if entries:
                        self._open_file["index"] = len(entries) - 1
                        self._open_file_ensure_visible()
            elif focus == "ok":
                if key in ("enter", "return"):
                    self._open_file_confirm_path(self._open_file["path_value"])
                elif key in ("left", "right"):
                    self._open_file["focus"] = "cancel"
            elif focus == "cancel":
                if key in ("enter", "return"):
                    self._open_file_cancel()
                elif key in ("left", "right"):
                    self._open_file["focus"] = "ok"
            return

        if self._pick is not None:
            opts = self._pick["options"]
            if key in ("escape",):
                self.on_escape()
            elif key in ("up",):
                self._pick["index"] = max(0, self._pick["index"] - 1)
            elif key in ("down",):
                self._pick["index"] = min(len(opts) - 1, self._pick["index"] + 1)
            elif key in ("enter", "return"):
                self._pick_accept(opts[self._pick["index"]][0])
            return

        if self._text is not None:
            focus = int(self._text.get("focus") or 0)
            if key == "escape":
                self._text_cancel()
            elif key in ("ctrl+s",):
                self._text_ok()
            elif key == "ctrl+v" and focus == 0:
                pasted = self._paste_into(str(self._text.get("value") or ""))
                if pasted is not None:
                    self._text["value"] = pasted
            elif key == "tab":
                self._text["focus"] = (focus + 1) % 3
            elif key in ("left", "right") and focus >= 1:
                self._text["focus"] = 1 if focus == 2 else 2
            elif key in ("enter", "return"):
                if focus == 0:
                    self._text["value"] = str(self._text.get("value") or "") + "\n"
                elif focus == 1:
                    self._text_ok()
                else:
                    self._text_cancel()
            elif key == "backspace" and focus == 0:
                val = str(self._text.get("value") or "")
                self._text["value"] = val[:-1]
            return

        if self._prompt is not None:
            if key in ("escape",):
                self.on_escape()
            elif key in ("enter", "return"):
                self._prompt_finish(self._prompt["value"])
            elif key == "ctrl+v":
                pasted = self._paste_into(self._prompt["value"])
                if pasted is not None:
                    self._prompt["value"] = (
                        pasted.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
                    )
            elif key in ("backspace",):
                self._prompt["value"] = self._prompt["value"][:-1]
            return

        if self.quit_modal:
            if key in ("y", "enter", "return"):
                self.confirm_quit_yes()
            elif key in ("n", "escape"):
                self.confirm_quit_no()
            return

        if key.startswith("f") and key[1:].isdigit():
            idx = int(key[1:]) - 1
            self.set_mode(idx)
            return
        if key == "escape":
            screen = self._ensure_screen()
            for name in ("clear_chrom_filter", "clear_search", "back"):
                clearer = getattr(screen, name, None)
                if callable(clearer) and clearer():
                    return
            self.on_escape()
            return
        if key == "tab":
            screen = self._ensure_screen()
            toggle = getattr(screen, "toggle_panel", None)
            if callable(toggle) and toggle():
                return
            self._cycle_focus()
            return

        # Text input focus: typing keys stay in the field; send shortcuts bubble.
        if isinstance(self._focus, TextArea):
            if key in ("ctrl+s", "alt+enter", "meta+enter", "ctrl+enter"):
                self._ensure_screen().on_key(key)
                return
            if key == "q" and not getattr(self._focus, "read_only", False):
                # Type q into the field (char event); do not quit.
                return
            if key == "ctrl+c":
                # TextArea selection wins; else screen selection (Markdown/RichText).
                if self._focus.selected_text():
                    n = len(self._focus.selected_text())
                    ok = self._focus.copy_selection()
                    if ok:
                        self.notify(f"Copied selection · {n:,} chars")
                    else:
                        self.notify("Clipboard unavailable", severity="warning")
                    return
                if self._copy_screen_selection():
                    return
                if getattr(self._focus, "clip_strict", False):
                    self.notify(
                        "Select a fragment first · drag or Shift+arrows · Ctrl+A = all",
                    )
                    return
                ok = self._focus.copy_selection()
                self.notify("Copied" if ok else "Clipboard unavailable")
                return
            if key == "ctrl+v":
                if getattr(self._focus, "read_only", False) or self._focus.disabled:
                    return
                if self._focus.paste_clipboard():
                    return
                self.notify("Clipboard unavailable", severity="warning")
                return
            if self._focus.on_key(key):
                return
            # Single-letter keys are handled by on_char — don't fire screen bindings.
            # Read-only Select mode: letters fall through to screen bindings (S, …).
            if getattr(self._focus, "read_only", False):
                pass
            elif len(key) == 1 or key == "space":
                return

        # Screen-level selection copy (Help Markdown / Chat thread).
        if key == "ctrl+c":
            if self._copy_screen_selection():
                return

        # Focused list/chrom: navigation first, then screen bindings for letters.
        if self._focus is not None and hasattr(self._focus, "on_key"):
            if self._focus.on_key(key):  # type: ignore[operator]
                return

        if key == "q":
            self.run_action("quit")
            return

        if self._try_app_bindings(key):
            return

        self._ensure_screen().on_key(key)

    def on_mount(self) -> None:
        assert self._device is not None and self._format is not None
        self._blitter = FrameBlitter(self._device, self._format)
        fx = getattr(self, "_post_fx", None)
        if fx:
            self._blitter.set_fx(
                bloom=float(fx.get("bloom", 0.0)),
                scanlines=float(fx.get("scanlines", 0.0)),
            )
        self._booting = True
        self._boot_frac = 0.12
        self._boot_label = "Starting…"
        print("ModeApp · F-keys · Q quit · screen bindings", file=sys.stderr, flush=True)

    def after_first_draw(self) -> None:
        """Splash is on the buffer — mount the start mode while the window is still hidden."""
        self._boot_frac = 0.4
        self._boot_label = f"Opening {self.mode_title}…"
        try:
            self._ensure_screen()
            self._boot_frac = 0.95
            self._boot_label = "Ready"
        except Exception as exc:  # noqa: BLE001
            print(f"boot error: {exc}", flush=True)
            self._boot_label = f"Start failed: {exc}"
        self._boot_frac = 1.0
        self._booting = False

    def draw(self) -> None:
        if self._blitter is None or self._context is None:
            return
        w, h = self._pixel_size
        if w <= 0 or h <= 0:
            w, h = self.SIZE
        if self._frame is None or self._frame.size != (w, h):
            self._frame = Image.new("RGBA", (w, h))
        img = self._frame
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, w, h], fill=self.theme.rgba("background"))

        if self._booting:
            self._paint_boot(draw, w, h)
            self._hits = []
            self._blitter.upload(img.tobytes("raw", "RGBA"), img.size)
            view = self._context.get_current_texture().create_view()
            self._blitter.draw(view)
            return

        root = self._ensure_screen()
        if self._layout_size != (w, h):
            root.layout(Rect(0, 0, w, h))
            for entry in self._screen_stack:
                try:
                    entry.screen.layout(Rect(0, 0, w, h))
                except Exception:  # noqa: BLE001
                    pass
            self._layout_size = (w, h)
        else:
            for entry in self._screen_stack:
                if entry.screen.rect.w != w or entry.screen.rect.h != h:
                    try:
                        entry.screen.layout(Rect(0, 0, w, h))
                    except Exception:  # noqa: BLE001
                        pass
        root.paint(draw)
        self._hits = list(root.hits())
        # Overlay push_screen stack (topmost last).
        for entry in self._screen_stack:
            scr = entry.screen
            if scr.rect.w <= 0 or scr.rect.h <= 0:
                scr.layout(Rect(0, 0, w, h))
            scr.paint(draw)
            try:
                self._hits = list(scr.hits())
            except Exception:  # noqa: BLE001
                self._hits = []
        # Toast overlay (ModeApp.notify → Toast widget).
        if self._toast.visible:
            self._toast.app = self
            self._toast.paint_at(draw, Rect(0, 0, w, h))
            self._notify = self._toast.message
        elif self._notify:
            # Legacy fields cleared once toast expires.
            self._notify = ""
            self._notify_until = None
        if self._tooltip.visible:
            self._tooltip.app = self
            self._tooltip.paint(draw)
        if self.quit_modal:
            self._paint_quit_modal(draw, w, h)
        if self._confirm is not None:
            self._paint_confirm(draw, w, h)
        if self._form is not None:
            self._paint_form(draw, w, h)
        if self._open_file is not None:
            self._paint_open_file(draw, w, h)
        if self._overlay is not None:
            self._paint_overlay(draw, w, h)
        if self._prompt is not None:
            self._paint_prompt(draw, w, h)
        if self._text is not None:
            self._paint_text(draw, w, h)
        if self._pick is not None:
            self._paint_pick(draw, w, h)
        if getattr(self, "_busy", None):
            self._paint_busy(draw, w, h)
            self._hits = []  # BusyModal: no click-through
        self._blitter.upload(img.tobytes("raw", "RGBA"), img.size)
        view = self._context.get_current_texture().create_view()
        self._blitter.draw(view)

    def _paint_progress_bar(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        frac: float | None,
    ) -> None:
        t = self.theme
        draw.rectangle(
            [x, y, x + width, y + height],
            fill=t.rgba("boost"), outline=t.rgba("muted"),
        )
        if frac is None:
            # Indeterminate pulse from wall clock.
            import time
            phase = (time.monotonic() % 1.6) / 1.6
            thumb = max(24, width // 4)
            tx = x + 2 + int((width - thumb - 4) * phase)
            draw.rectangle(
                [tx, y + 2, tx + thumb, y + height - 2],
                fill=t.rgba("accent"),
            )
        else:
            fill_w = max(0, int((width - 4) * max(0.0, min(1.0, frac))))
            if fill_w > 0:
                draw.rectangle(
                    [x + 2, y + 2, x + 2 + fill_w, y + height - 2],
                    fill=t.rgba("accent"),
                )

    def _paint_boot(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        t = self.theme
        title = self.TITLE or "MDGA"
        label = self._boot_label or "Starting…"
        frac = self._boot_frac
        cx = max(24, (w - 280) // 2)
        strip_line(
            draw, Rect(cx, h // 2 - 48, min(400, w - cx - 24), 28),
            title, t, font_size=22,
        )
        strip_line(
            draw, Rect(cx, h // 2 - 14, min(400, w - cx - 24), 22),
            label, t, fg=t.rgba("muted"), font_size=14,
        )
        bar_w = min(420, w - 80)
        bar_x = max(24, (w - bar_w) // 2)
        self._paint_progress_bar(
            draw, x=bar_x, y=h // 2 + 18, width=bar_w, height=14, frac=frac,
        )
        pct = f"{frac:.0%}" if frac is not None else ""
        if pct:
            strip_line(
                draw, Rect(bar_x + bar_w + 10, h // 2 + 16, 64, 20),
                pct, t, fg=t.rgba("accent"), font_size=12,
            )

    def _paint_quit_modal(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        t = self.theme
        veil(draw, w, h)
        mw, mh = 360, 160
        mx, my = (w - mw) // 2, (h - mh) // 2
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 24, my + 24, mw - 48, 28),
            "Quit?", t, font_size=18,
        )
        strip_line(
            draw, Rect(mx + 24, my + 60, mw - 48, 22),
            "Y / Enter — yes    N / Esc — no", t,
            fg=t.rgba("muted"), font_size=13,
        )
        bw, bh = 100, 34
        yes = Rect(mx + 40, my + mh - 52, bw, bh)
        no = Rect(mx + mw - 40 - bw, my + mh - 52, bw, bh)
        chip(draw, yes, "Yes", t, active=True, font_size=14)
        chip(draw, no, "No", t, outlined=True, font_size=14)
        self._hits = [
            (frame, lambda _x, _y: None),
            (yes, lambda _x, _y: self.confirm_quit_yes()),
            (no, lambda _x, _y: self.confirm_quit_no()),
        ]

    def _paint_confirm(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        assert self._confirm is not None
        t = self.theme
        title = str(self._confirm.get("title") or "Confirm")
        detail = str(self._confirm.get("detail") or "")
        ok_label = str(self._confirm.get("ok") or "OK")
        focus = int(self._confirm.get("focus") or 0)
        veil(draw, w, h)
        mw, mh = min(520, w - 40), 180 if detail else 150
        mx, my = (w - mw) // 2, (h - mh) // 2
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 20, my + 18, mw - 40, 24),
            title[:64], t, font_size=15,
        )
        if detail:
            strip_line(
                draw, Rect(mx + 20, my + 48, mw - 40, 22),
                detail[:72], t, fg=t.rgba("muted"), font_size=13,
            )
        bw, bh = 120, 34
        yes = Rect(mx + 28, my + mh - 56, bw, bh)
        no = Rect(mx + mw - 28 - bw, my + mh - 56, bw, bh)
        chip(draw, yes, ok_label[:10], t, active=(focus == 0), outlined=True, font_size=13)
        chip(draw, no, "Cancel", t, active=(focus == 1), outlined=True, font_size=13)
        strip_line(
            draw, Rect(mx + 20, my + mh - 90, mw - 40, 20),
            "Y / Enter · N / Esc", t, fg=t.rgba("muted"), font_size=12,
        )
        self._hits = [
            (frame, lambda _x, _y: None),
            (yes, lambda _x, _y: self._confirm_yes()),
            (no, lambda _x, _y: self._confirm_no()),
        ]

    def _paint_overlay(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        assert self._overlay is not None
        paint = self._overlay["paint"]
        try:
            hits = paint(draw, w, h) or []
        except Exception as exc:  # noqa: BLE001
            t = self.theme
            draw.rectangle([0, 0, w, h], fill=t.rgba("background"))
            strip_line(
                draw, Rect(24, 24, max(40, w - 48), 24),
                f"Overlay error: {exc}", t, fg=t.rgba("accent"), font_size=13,
            )
            hits = []
        self._hits = list(hits)

    def _paint_form(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        assert self._form is not None
        t = self.theme
        fields: list[tuple[str, str, str]] = self._form["fields"]
        fi = int(self._form["focus"])
        n = len(fields)
        show_reset = bool(self._form.get("show_reset", True))
        ok_label = str(self._form.get("ok_label") or "Apply")
        row_h = 52
        want_w = int(self._form["box_w"]) if self._form.get("box_w") else 520
        mw = min(want_w, w - 40)
        mh = min(h - 40, 90 + n * row_h + 70)
        mx, my = (w - mw) // 2, (h - mh) // 2
        veil(draw, w, h)
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 16, my + 12, mw - 32, 24),
            str(self._form["title"])[:72], t, font_size=15,
        )
        pw_ids = self._form.get("password_fields") or set()
        hits: list[tuple[Rect, Callable[[int, int], None]]] = [
            (frame, lambda _x, _y: None),
        ]
        y = my + 48
        for i, (_fid, label, val) in enumerate(fields):
            strip_line(
                draw, Rect(mx + 16, y, mw - 32, 18),
                label[:70], t, fg=t.rgba("muted"), font_size=12,
            )
            box = Rect(mx + 20, y + 18, mw - 40, 28)
            masked = ("•" * len(val)) if _fid in pw_ids else val
            shown = masked + ("▌" if fi == i else "")
            field_box(draw, box, shown[-72:], t, focused=(fi == i), font_size=13)

            def _focus_field(_x: int, _y: int, j: int = i) -> None:
                if self._form is not None:
                    self._form["focus"] = j

            hits.append((box, _focus_field))
            y += row_h

        hint = str(self._form.get("hint") or "")
        if hint:
            strip_line(
                draw, Rect(mx + 16, my + mh - 80, mw - 32, 18),
                hint[:84], t, fg=t.rgba("muted"), font_size=11,
            )

        bw, bh = 100, 32
        apply = Rect(mx + mw - 28 - bw, my + mh - 48, bw, bh)
        ok_i = n + (1 if show_reset else 0)
        if show_reset:
            reset = Rect(mx + mw - 28 - bw * 2 - 12, my + mh - 48, bw, bh)
            chip(draw, reset, "Reset", t, active=(fi == n), outlined=True, font_size=13)
            hits.append((reset, lambda _x, _y: self._form_reset()))
        chip(draw, apply, ok_label[:10], t, active=(fi == ok_i), outlined=True, font_size=13)
        hits.append((apply, lambda _x, _y: self._form_apply()))
        self._hits = hits

    def _paint_open_file(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        """OpenFileScreen analogue: title · path · directory list · Add/Cancel."""
        assert self._open_file is not None
        t = self.theme
        focus = self._open_file.get("focus") or "list"
        entries: list[tuple[str, str, str]] = self._open_file["entries"]
        idx = int(self._open_file["index"])
        veil(draw, w, h)
        mw = min(720, w - 32)
        mh = min(520, h - 32)
        mx, my = (w - mw) // 2, (h - mh) // 2
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 16, my + 10, mw - 32, 24),
            str(self._open_file["title"])[:72], t, font_size=15,
        )
        sub = str(self._open_file.get("subtitle") or "")
        if sub:
            strip_line(
                draw, Rect(mx + 16, my + 34, mw - 32, 20),
                sub[:90], t, fg=t.rgba("muted"), font_size=12,
            )

        path_box = Rect(mx + 20, my + 62, mw - 40, 34)
        val = str(self._open_file["path_value"])
        caret = "▌" if focus == "path" else ""
        field_box(
            draw, path_box, (val + caret)[-78:], t,
            focused=(focus == "path"), font_size=13,
        )

        list_top = my + 108
        list_bot = my + mh - 78
        list_h = max(40, list_bot - list_top)
        row_h = 22
        visible = max(1, list_h // row_h)
        self._open_file["visible"] = visible
        self._open_file_ensure_visible()
        scroll = int(self._open_file["scroll"])
        list_outline = t.rgba("accent") if focus == "list" else t.rgba("boost")
        draw.rectangle(
            [mx + 20, list_top, mx + mw - 20, list_bot],
            fill=t.rgba("panel"), outline=list_outline,
        )

        hits: list[tuple[Rect, Callable[[int, int], None]]] = [
            (frame, lambda _x, _y: None),
        ]

        def _focus_path(_x: int, _y: int) -> None:
            if self._open_file is not None:
                self._open_file["focus"] = "path"

        hits.append((path_box, _focus_path))

        if not entries:
            strip_line(
                draw, Rect(mx + 28, list_top + 10, mw - 56, 22),
                "(empty · type a path above · Enter)", t,
                fg=t.rgba("muted"), font_size=13,
            )
        else:
            for i in range(scroll, min(len(entries), scroll + visible)):
                y = list_top + 4 + (i - scroll) * row_h
                kind, _path_s, label = entries[i]
                row = Rect(mx + 24, y, mw - 48, row_h - 2)
                hi = i == idx and focus == "list"
                if hi:
                    draw.rectangle(
                        [row.x, row.y, row.x1 - 1, row.y1 - 1],
                        fill=t.rgba("accent"),
                    )
                    fill = t.rgba("background")
                else:
                    fill = t.rgba("muted") if kind in ("dir", "up") else t.rgba("foreground")
                strip_line(
                    draw, row, label[:70], t, fg=fill, font_size=13, pad_x=8, pad_y=2,
                )

                def _row(_x: int, _y: int, j: int = i) -> None:
                    if self._open_file is None:
                        return
                    self._open_file["focus"] = "list"
                    if j != int(self._open_file["index"]):
                        self._open_file["index"] = j
                        self._open_file_ensure_visible()
                        return
                    self._open_file_activate()

                hits.append((row, _row))

        kind = self._open_file.get("kind") or "file"
        ok_label = str(self._open_file.get("ok_label") or ("OK" if kind == "folder" else "Add"))
        hint = (
            "U/↑ parent · click fills path · Enter path: open folder · OK confirm · Esc"
            if kind == "folder"
            else "Click/Enter file · Enter path: open or go · ↑↓ list · Tab · Esc cancel"
        )
        strip_line(
            draw, Rect(mx + 16, my + mh - 66, mw - 32, 18),
            hint[:90], t, fg=t.rgba("muted"), font_size=11,
        )

        bw, bh = 110, 34
        ok = Rect(mx + mw - 28 - bw * 2 - 12, my + mh - 48, bw, bh)
        cancel = Rect(mx + mw - 28 - bw, my + mh - 48, bw, bh)
        chip(draw, ok, ok_label[:10], t, active=(focus == "ok"), outlined=True, font_size=13)
        chip(draw, cancel, "Cancel", t, active=(focus == "cancel"), outlined=True, font_size=13)

        def _ok(_x: int, _y: int) -> None:
            if self._open_file is not None:
                self._open_file_confirm_path(self._open_file["path_value"])

        def _cancel(_x: int, _y: int) -> None:
            self._open_file_cancel()

        hits.append((ok, _ok))
        hits.append((cancel, _cancel))
        self._hits = hits

    def _paint_prompt(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        assert self._prompt is not None
        t = self.theme
        veil(draw, w, h)
        mw, mh = 520, 140
        mx, my = (w - mw) // 2, (h - mh) // 2
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 16, my + 12, mw - 32, 24),
            str(self._prompt["title"])[:72], t, font_size=15,
        )
        box = Rect(mx + 20, my + 48, mw - 40, 36)
        raw = self._prompt["value"]
        ph = str(self._prompt.get("placeholder") or "")
        if self._prompt.get("password"):
            shown = "•" * len(raw)
        else:
            shown = raw
        if shown:
            field_box(draw, box, (shown + "▌")[-64:], t, focused=True, font_size=14)
        elif ph:
            field_box(draw, box, (ph + "▌")[:64], t, focused=True, muted=True, font_size=14)
        else:
            field_box(draw, box, "▌", t, focused=True, font_size=14)
        hint = str(self._prompt.get("hint") or "Enter apply · Esc cancel")
        strip_line(
            draw, Rect(mx + 16, my + mh - 34, mw - 32, 20),
            hint[:64], t, fg=t.rgba("muted"), font_size=12,
        )
        self._hits = [(frame, lambda _x, _y: None)]

    def _paint_text(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        """Multiline EditScreen-style modal."""
        assert self._text is not None
        t = self.theme
        veil(draw, w, h)
        mw, mh = min(720, w - 40), min(420, h - 40)
        mx, my = (w - mw) // 2, (h - mh) // 2
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 16, my + 10, mw - 32, 24),
            str(self._text.get("title") or "Edit")[:72], t, font_size=15,
        )
        body = Rect(mx + 16, my + 44, mw - 32, mh - 110)
        draw.rectangle(
            [body.x, body.y, body.x1 - 1, body.y1 - 1],
            fill=t.rgba("panel"), outline=t.rgba("accent"),
        )
        raw = str(self._text.get("value") or "") + "▌"
        lines = raw.split("\n")
        from .fonts import cell_height

        line_h = cell_height(13)
        pad = 8
        max_lines = max(1, (body.h - 2 * pad) // line_h)
        shown = lines[-max_lines:]
        strip_lines(
            draw, body, [ln[:96] for ln in shown], t,
            font_size=13, pad_x=pad, pad_y=pad,
        )
        hint = str(self._text.get("hint") or "")
        if hint:
            strip_line(
                draw, Rect(mx + 16, my + mh - 60, mw - 32, 20),
                hint[:80], t, fg=t.rgba("muted"), font_size=12,
            )
        focus = int(self._text.get("focus") or 0)
        ok = Rect(mx + mw - 200, my + mh - 40, 80, 28)
        cancel = Rect(mx + mw - 100, my + mh - 40, 80, 28)
        chip(draw, ok, "OK", t, active=(focus == 1), outlined=True, font_size=13)
        chip(draw, cancel, "Cancel", t, active=(focus == 2), outlined=True, font_size=13)

        def _body(_x: int, _y: int) -> None:
            if self._text is not None:
                self._text["focus"] = 0

        def _ok(_x: int, _y: int) -> None:
            self._text_ok()

        def _cancel(_x: int, _y: int) -> None:
            self._text_cancel()

        self._hits = [(frame, lambda _x, _y: None), (body, _body), (ok, _ok), (cancel, _cancel)]

    def _paint_busy(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        """BusyModal analogue: dim veil + card + elapsed; input blocked by ModeApp."""
        from .fonts import cell_width

        t = self.theme
        msg = (self._busy or "").strip() or "Working…"
        pad_x, pad_top, pad_bot = 20, 16, 14
        gap = 8
        cw = cell_width(15)
        max_cols = max(20, (min(w - 48, 720) - 2 * pad_x) // cw)

        def _wrap(text: str, limit: int) -> list[str]:
            if len(text) <= limit:
                return [text]
            words = text.replace(" · ", " ·\u00a0").split(" ")
            lines: list[str] = []
            cur = ""
            for word in words:
                word = word.replace("\u00a0", " ")
                trial = f"{cur} {word}".strip() if cur else word
                if cur and len(trial) > limit:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
                while len(cur) > limit and len(cur) > 8:
                    lines.append(cur[:limit])
                    cur = cur[limit:]
            if cur:
                lines.append(cur)
            return lines or [text]

        lines = _wrap(msg, max_cols)
        line_h = 18
        t0 = getattr(self, "_busy_t0", None) or time.monotonic()
        elapsed = max(0.0, time.monotonic() - t0)
        sub = f"{elapsed:.1f}s"
        bar_h = 12
        inner_w = max(len(s) for s in lines) * cw if lines else 0
        inner_w = max(inner_w, len(sub) * cell_width(12), 220)
        mw = min(w - 24, max(240, inner_w + 2 * pad_x))
        mh = pad_top + len(lines) * line_h + gap + bar_h + gap + 14 + pad_bot
        mx = max(12, (w - mw) // 2)
        my = max(12, (h - mh) // 2)
        veil(draw, w, h, alpha=140)
        dialog(draw, mx, my, mw, mh, t)
        strip_lines(
            draw, Rect(mx + pad_x, my + pad_top, mw - 2 * pad_x, len(lines) * line_h),
            lines, t, font_size=15, pad_x=0, pad_y=0,
        )
        y = my + pad_top + len(lines) * line_h + gap // 2
        self._paint_progress_bar(
            draw,
            x=mx + pad_x,
            y=y,
            width=mw - 2 * pad_x,
            height=bar_h,
            frac=getattr(self, "_busy_frac", None),
        )
        y += bar_h + gap // 2
        strip_line(
            draw, Rect(mx + pad_x, y, mw - 2 * pad_x, 18),
            sub, t, fg=t.rgba("muted"), font_size=12, pad_x=0, pad_y=0,
        )

    def _paint_pick(self, draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        assert self._pick is not None
        t = self.theme
        opts: list[tuple[str, str]] = self._pick["options"]
        idx = self._pick["index"]
        veil(draw, w, h)
        row_h = 22
        visible = min(16, len(opts))
        mh = 70 + visible * row_h
        mw = min(840, max(560, w - 48))
        mx, my = (w - mw) // 2, (h - mh) // 2
        frame = dialog(draw, mx, my, mw, mh, t)
        strip_line(
            draw, Rect(mx + 16, my + 10, mw - 32, 24),
            str(self._pick["title"])[:72], t, font_size=15,
        )
        start = max(0, min(idx - visible // 2, len(opts) - visible))
        hits: list[tuple[Rect, Callable[[int, int], None]]] = [
            (frame, lambda _x, _y: None),
        ]
        max_chars = max(40, (mw - 48) // 8)
        for i in range(start, start + visible):
            y = my + 44 + (i - start) * row_h
            label = opts[i][1]
            row = Rect(mx + 12, y, mw - 24, row_h - 2)
            if i == idx:
                draw.rectangle(
                    [row.x, row.y, row.x1 - 1, row.y1 - 1],
                    fill=t.rgba("accent"),
                )
                fill = t.rgba("background")
            else:
                fill = t.rgba("foreground")
            strip_line(
                draw, row, label[:max_chars], t, fg=fill, font_size=13, pad_x=8, pad_y=2,
            )

            def _pick(_x: int, _y: int, j: int = i) -> None:
                if self._pick is None:
                    return
                if j != self._pick["index"]:
                    self._pick["index"] = j
                    return
                self._pick_accept(self._pick["options"][j][0])

            hits.append((row, _pick))
        strip_line(
            draw, Rect(mx + 16, my + mh - 26, mw - 32, 20),
            "↑↓ · Enter / click select · Esc cancel", t,
            fg=t.rgba("muted"), font_size=12,
        )
        self._hits = hits


_DEMO_HELP = """\
# textual-wgpu

F10-style Help screen using lib widgets:

- `ModeApp` hosts F1–Fn screens
- `Markdown` + `VerticalScroll` for the body
- Theme is app-owned (`Theme.from_mapping` / `Theme.system`)

## Keys

| Key | Action |
|-----|--------|
| ↑↓ / PgUp/PgDn | Scroll |
| F1–F10 | Switch mode |
| Q | Quit modal |
"""


def main(theme: Theme | None = None) -> None:
    from .widgets import KeyBar, Markdown, ModeBar, Panel, StatusBar, VerticalScroll

    labels = [
        ("genome", "Genome"),
        ("data", "Data"),
        ("tree", "Tree"),
        ("pgs", "PGS"),
        ("gwas", "GWAS"),
        ("reference", "Reference"),
        ("chat", "Chat"),
        ("jobs", "Jobs"),
        ("samples", "Samples"),
        ("help", "Help"),
    ]

    class DemoHelp(Screen):
        def compose(self):
            yield ModeBar(
                "help", labels,
                on_select=lambda i: self.app.set_mode(i) if self.app else None,
            )
            yield Panel(
                VerticalScroll(Markdown(_DEMO_HELP), id="help-scroll"),
                title="HELP · demo",
            )
            yield KeyBar([
                ("↑↓", "Scroll", None),
                ("F1–F10", "Modes", None),
                ("Q", "Quit", lambda: self.app.ask_quit() if self.app else None),
            ])
            yield StatusBar("demo · F10 Help path")

    modes = [(k, t, DemoHelp if k == "help" else None) for k, t in labels]
    ModeApp(modes, theme=theme or Theme.system(), title="textual-wgpu", start="help").run()


if __name__ == "__main__":
    main()
