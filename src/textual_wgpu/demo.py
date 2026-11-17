"""Standalone textual-wgpu widget & runtime showcase (``python -m textual_wgpu``)."""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from rich.style import Style
from rich.text import Text

from .compositor import make_image_text
from .mode_app import ModeApp
from .screen import Screen
from .theme import BUILTIN_THEMES, Theme
from .widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Digits,
    Horizontal,
    Input,
    KeyBar,
    Log,
    MarkdownViewer,
    MaskedInput,
    ModeBar,
    Option,
    OptionList,
    Panel,
    ProgressBar,
    RadioSet,
    RichText,
    Rule,
    Select,
    Sparkline,
    Static,
    StatusBar,
    Switch,
    TextArea,
    Tree,
    Vertical,
    VerticalScroll,
    Video,
    Viewport3D,
)

DEMO_LABELS: list[tuple[str, str]] = [
    ("overview", "Overview"),
    ("controls", "Controls"),
    ("data", "Data & Tree"),
    ("editor", "Editor & Modals"),
    ("themes", "Themes & FX"),
    ("planes", "3D Scene"),
    ("media", "Media"),
    ("nodes", "Nodes & Apps"),
]

_OVERVIEW_MD = """\
# textual-wgpu

A Textual-compatible GPU UI runtime powered by **wgpu** and Pillow strip composition.

## Highlights

- **App & Screen model**: `App`, `ModalScreen`, `ModeApp`, `compose()`, reactive state, `Pilot` headless testing
- **42 / 42 Widgets**: `Button`, `Checkbox`, `RadioSet`, `Switch`, `Select`, `Input`, `MaskedInput`, `TextArea`, `DataTable`, `Tree`, `OptionList`, `MarkdownViewer`, `Sparkline`, `Digits`, `ProgressBar`, `Log`, `Collapsible`, and more
- **Smooth Scrolling & TOC**: Pixel-level wheel scrolling, expanded `MarkdownViewer` table of contents, exact heading anchors
- **CRT Shader Post-FX**: Optional GPU bloom and scanline passes in `FrameBlitter`
- **Themes**: Built-in `textual-dark`, `textual-light`, `system`, or custom `Theme.from_mapping(...)`

## Navigation

| Key | Action |
|-----|--------|
| `F1`–`F5` | Switch demo workspace |
| `Tab` / `Shift+Tab` | Cycle focus across panels |
| `↑` `↓` / `PgUp` `PgDn` / Wheel | Scroll or move selection |
| `Ctrl+C` / Drag | Select & copy text |
| `Q` / `Esc` | Quit confirmation modal |

## Demo Screens

### F1 Overview
This `MarkdownViewer` screen demonstrates the live TOC tree, anchor jumping, and smooth pixel scrolling.

### F2 Controls
Interactive `Button`, `Checkbox`, `RadioSet`, `Switch`, `Select`, `Input`, `MaskedInput`, `ProgressBar`, `Sparkline`, `Digits`, and `Collapsible`.

### F3 Data & Tree
A three-panel workspace combining `OptionList`, `DataTable`, and an expandable `Tree` with a clickable `@click` `RichText` inspector.

### F4 Editor & Modals
Multiline `TextArea` + append-only `Log` + triggers for `ask_prompt`, `ask_confirm`, `ask_pick`, `ask_open_file`, and `show_doc`.

### F5 Themes & FX
Switch between built-in/custom themes at runtime and toggle GPU CRT bloom / scanlines.
"""

_SHARED_CSS = """
ModeBar { height: 1; }
KeyBar { height: 1; }
StatusBar { height: 1; padding: 0 1; background: $boost; }
#main { height: 1fr; }
"""


def _mode_bar(screen: Screen, active: str) -> ModeBar:
    return ModeBar(
        active,
        DEMO_LABELS,
        on_select=lambda i: screen.app.set_mode(i) if screen.app else None,
    )


def _focus_next(screen: Screen) -> None:
    if screen.app is not None and hasattr(screen.app, "focus_next"):
        screen.app.focus_next()


def _quit(screen: Screen) -> None:
    if screen.app is not None and hasattr(screen.app, "ask_quit"):
        screen.app.ask_quit()


class OverviewScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #overview-viewer { height: 1fr; }
    """

    def compose(self):
        yield _mode_bar(self, "overview")
        yield MarkdownViewer(
            _OVERVIEW_MD,
            show_table_of_contents=True,
            toc_title="CONTENTS",
            doc_title="TEXTUAL-WGPU · OVERVIEW",
            id="overview-viewer",
        )
        yield KeyBar([
            ("↑↓", "Scroll", None),
            ("T", "Toggle TOC", lambda: self.toggle_toc()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F5", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F1 Overview · click headings in CONTENTS or drag-select prose", id="status")

    def toggle_toc(self) -> None:
        v = self.query_one("#overview-viewer", MarkdownViewer)
        v.show_table_of_contents = not v.show_table_of_contents
        v.refresh()

    def on_key(self, key: str) -> bool:
        if key == "t":
            self.toggle_toc()
            return True
        return super().on_key(key)


class ControlsScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #left-col { width: 1fr; }
    #mid-col { width: 1fr; }
    #right-col { width: 1fr; }
    #ctrl-box { height: 1fr; border: round $primary; }
    #form-box { height: 1fr; border: round $primary; }
    #meter-box { height: 1fr; border: round $primary; }
    #ctrl-box:focus-within, #form-box:focus-within, #meter-box:focus-within { border: round $accent; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._progress = 64.0
        self._series = [12, 19, 15, 28, 34, 29, 44, 38, 52, 48, 61, 64]

    def compose(self):
        yield _mode_bar(self, "controls")

        chk_a = Checkbox("Enable GPU vsync", True, id="chk-vsync", on_change=lambda v: self._report(f"vsync={v}"))
        chk_b = Checkbox("Smooth pixel wheel", True, id="chk-smooth", on_change=lambda v: self._report(f"smooth={v}"))
        sw = Switch(True, id="sw-live", on_change=lambda v: self._report(f"live_updates={v}"))
        radios = RadioSet("Low latency", "Balanced", "High quality", id="rad-profile")
        radios.on_change = lambda idx, lbl: self._report(f"profile={lbl}")
        btn_inc = Button("+10% Progress", id="btn-inc", variant="primary", on_press=self._bump_progress)
        btn_rst = Button("Reset", id="btn-rst", on_press=self._reset_progress)
        btn_row = Horizontal(btn_inc, btn_rst, id="btn-row")
        btn_row.layout_hint_h = 38
        left = Panel(
            Vertical(chk_a, chk_b, sw, radios, btn_row, id="ctrl-inner"),
            title="TOGGLES & ACTIONS",
            id="ctrl-box",
        )

        inp = Input("textual-wgpu", id="inp-name", placeholder="Project name", on_change=lambda v: self._report(f"name={v!r}"))
        masked = MaskedInput(template="9999-99-99", value="20261116", id="inp-date")
        sel = Select(
            [("wgpu", "wgpu (hardware)"), ("headless", "Headless (Pilot)"), ("software", "CPU Pillow")],
            value="wgpu",
            id="sel-driver",
            on_change=lambda v: self._report(f"driver={v}"),
        )
        col = Collapsible(
            Static("Collapsible section body — stores secondary options cleanly.", id="col-body"),
            title="Advanced notes",
            collapsed=False,
            id="col-adv",
        )
        mid = Panel(
            Vertical(inp, masked, sel, col, id="form-inner"),
            title="INPUTS & SELECT",
            id="form-box",
        )

        digits = Digits(f"{int(self._progress)}%", id="digits-pct")
        bar = ProgressBar(100, id="prog-bar")
        bar.advance(self._progress)
        spark = Sparkline(self._series, id="spark-load")
        spark.layout_hint_h = 26
        links = RichText(self._links_text(), id="ctrl-links")
        links.on_action = self._run_action
        right = Panel(
            Vertical(digits, bar, Rule(), spark, Rule(), links, id="meter-inner"),
            title="METRICS & LINKS",
            id="meter-box",
        )

        yield Horizontal(left, mid, right, id="main")
        yield KeyBar([
            ("Space/Enter", "Activate", None),
            ("+", "+10%", lambda: self._bump_progress()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F5", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F2 Controls · interact with widgets or click @click links", id="status")

    def _links_text(self) -> Text:
        t = Text("Quick actions (Rich @click):\n")
        t.append(" [ +10% ] ", style=Style.from_meta({"@click": "bump"}) + Style(bold=True, reverse=True))
        t.append("  ")
        t.append(" [ Reset ] ", style=Style.from_meta({"@click": "reset"}) + Style(bold=True))
        return t

    def _run_action(self, action: str) -> None:
        if action == "bump":
            self._bump_progress()
        elif action == "reset":
            self._reset_progress()

    def _bump_progress(self) -> None:
        self._progress = 10.0 if self._progress >= 100.0 else min(100.0, self._progress + 10.0)
        self._series = [*self._series[1:], int(self._progress)]
        self._sync_meters()
        self._report(f"progress={int(self._progress)}%")

    def _reset_progress(self) -> None:
        self._progress = 20.0
        self._series = [*self._series[1:], 20]
        self._sync_meters()
        self._report("progress=20% (reset)")

    def _sync_meters(self) -> None:
        self.query_one("#digits-pct", Digits).update(f"{int(self._progress)}%")
        bar = self.query_one("#prog-bar", ProgressBar)
        bar.progress = self._progress
        self.query_one("#spark-load", Sparkline).data = list(self._series)

    def _report(self, msg: str) -> None:
        try:
            self.query_one("#status", StatusBar).update(f"F2 Controls · {msg}")
        except KeyError:
            pass

    def on_key(self, key: str) -> bool:
        if key in ("plus", "equal"):
            self._bump_progress()
            return True
        return super().on_key(key)


class DataTreeScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #opt-box { width: 28; border: round $primary; }
    #tbl-box { width: 1fr; border: round $primary; }
    #tree-box { width: 34; border: round $primary; }
    #opt-box:focus-within, #tbl-box:focus-within, #tree-box:focus-within { border: round $accent; }
    """

    _MODULES = [
        ("core", "Core Runtime", "App, Screen, ModeApp, Pilot, Worker, Events"),
        ("layout", "CSS & Layout", "TCSS parser, Dock, Grid, Vertical, Horizontal"),
        ("render", "Paint Pipeline", "Content, Strip, Segment, TrueType, FrameBlitter"),
        ("widgets", "Widget Library", "42 Textual-compatible widgets + ScrollView"),
        ("testing", "Headless Pilot", "Async run_test, press, click, pause, query"),
    ]

    def compose(self):
        yield _mode_bar(self, "data")

        ol = OptionList(
            *[Option(f"{title}\n{desc}", id=key, data=(key, title, desc)) for key, title, desc in self._MODULES],
            id="mod-list",
            row_lines=2,
            on_highlight=self._on_module_hi,
        )
        opt_box = Panel(ol, title="MODULES", id="opt-box")

        table = DataTable(id="api-table")
        table.add_columns("Category", "Implemented", "Coverage", "Notes")
        table.add_rows([
            ("Top-level modules", "71 / 71", "100%", "Full public exports"),
            ("Widgets", "42 / 42", "100%", "Strip + compositor"),
            ("Containers", "15 / 15", "100%", "Dock, Grid, Scroll"),
            ("Event classes", "42 / 42", "100%", "56 module symbols"),
            ("App / Screen / Widget", "863 / 863", "100%", "Attributes & props"),
        ])
        detail = RichText(self._detail_for(self._MODULES[0]), id="data-detail", can_focus=False)
        detail.layout_hint_h = 56
        detail.on_action = self._on_detail_action
        mid_inner = Vertical(table, detail, id="tbl-inner")
        tbl_box = Panel(mid_inner, title="PARITY TABLE", id="tbl-box")

        tree: Tree[str] = Tree("textual_wgpu", id="pkg-tree")
        tree.root.expand()
        runtime = tree.root.add("runtime", data="App, Screen, ModeApp, Pilot")
        runtime.add_leaf("app.py", data="App lifecycle, bindings, run_test")
        runtime.add_leaf("mode_app.py", data="ModeBar host + built-in modals")
        runtime.add_leaf("driver.py", data="WgpuDriver + HeadlessDriver")
        runtime.expand()
        widgets = tree.root.add("widgets", data="42 Strip-painted widgets")
        widgets.add_leaf("markdown_viewer.py", data="TOC tree + smooth anchor scroll")
        widgets.add_leaf("data_table.py", data="Row/cell cursor + zebra stripes")
        widgets.add_leaf("textarea.py", data="Multiline editing + clipboard")
        widgets.expand()
        tree_box = Panel(tree, title="PACKAGE TREE", id="tree-box")

        yield Horizontal(opt_box, tbl_box, tree_box, id="main")
        yield KeyBar([
            ("↑↓", "Navigate", None),
            ("Enter/Space", "Expand / Select", None),
            ("S", "Sort table", lambda: self._sort_table()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F5", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F3 Data & Tree · OptionList + DataTable + Tree", id="status")

    def _detail_for(self, item: tuple[str, str, str]) -> Text:
        key, title, desc = item
        t = Text()
        t.append(f"{title} ", style="bold")
        t.append(f"({key})\n", style="dim")
        t.append(f"{desc}  ")
        t.append("[ Sort Table ]", style=Style.from_meta({"@click": "sort"}) + Style(bold=True))
        return t

    def _on_module_hi(self, opt: Option) -> None:
        if isinstance(opt.data, tuple) and len(opt.data) == 3:
            self.query_one("#data-detail", RichText).update(self._detail_for(opt.data))
            self.query_one("#status", StatusBar).update(f"F3 Data & Tree · selected {opt.data[1]}")

    def _on_detail_action(self, action: str) -> None:
        if action == "sort":
            self._sort_table()

    def _sort_table(self) -> None:
        tbl = self.query_one("#api-table", DataTable)
        tbl.sort(0)
        self.query_one("#status", StatusBar).update("F3 Data & Tree · sorted table by Category")

    def on_key(self, key: str) -> bool:
        if key == "s":
            self._sort_table()
            return True
        return super().on_key(key)


class EditorModalsScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #edit-box { width: 1fr; border: round $primary; }
    #log-box { width: 1fr; border: round $primary; }
    #edit-box:focus-within, #log-box:focus-within { border: round $accent; }
    """

    def compose(self):
        yield _mode_bar(self, "editor")

        editor = TextArea(
            "Edit this buffer directly.\n\n"
            "- Shift+Arrows or mouse drag selects text\n"
            "- Ctrl+A selects all · Ctrl+C copies · Ctrl+V pastes\n"
            "- Use the KeyBar or buttons below to test ModeApp modals\n",
            id="demo-editor",
        )
        b_prompt = Button("Prompt", id="btn-prompt", on_press=self._open_prompt)
        b_pick = Button("Pick", id="btn-pick", on_press=self._open_pick)
        b_file = Button("Open File", id="btn-file", on_press=self._open_file)
        b_doc = Button("Show Doc", id="btn-doc", on_press=self._open_doc)
        modal_bar = Horizontal(b_prompt, b_pick, b_file, b_doc, id="modal-btns")
        modal_bar.layout_hint_h = 38
        edit_box = Panel(Vertical(editor, modal_bar, id="edit-inner"), title="TEXTAREA & MODALS", id="edit-box")

        log = Log(id="demo-log")
        log.write("Ready — modal results and editor actions appear here.")
        log_box = Panel(log, title="EVENT LOG", id="log-box")

        yield Horizontal(edit_box, log_box, id="main")
        yield KeyBar([
            ("Ctrl+A/C/V", "Edit", None),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F5", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F4 Editor & Modals · try Prompt / Pick / Open File / Show Doc", id="status")

    def _log(self, msg: str) -> None:
        self.query_one("#demo-log", Log).write(msg)
        self.query_one("#status", StatusBar).update(f"F4 Editor & Modals · {msg}")

    def _open_prompt(self) -> None:
        if self.app is None:
            return
        self.app.ask_prompt(
            "Enter a greeting",
            "Hello from textual-wgpu!",
            lambda val: self._log(f"Prompt result: {val!r}"),
        )

    def _open_pick(self) -> None:
        if self.app is None:
            return
        self.app.ask_pick(
            "Choose a preset",
            [("fast", "Fast refresh (60 Hz)"), ("balanced", "Balanced"), ("crt", "Retro CRT preset")],
            lambda choice: self._log(f"Picked: {choice!r}"),
            current="balanced",
        )

    def _open_file(self) -> None:
        if self.app is None:
            return
        self.app.ask_open_file(
            Path.cwd(),
            lambda path: self._log(f"Chosen path: {path}"),
            title="Select a file",
            subtitle="Directory tree + path input modal",
            ok_label="Select",
        )

    def _open_doc(self) -> None:
        if self.app is None:
            return
        self.app.show_doc(
            "About textual-wgpu",
            "textual-wgpu renders Textual-compatible screens on wgpu.\n\n"
            "Modals push onto ModeApp's modal stack and support keyboard + pointer input.\n"
            "Press Esc or Q to close this modal.",
        )


class ThemesFxScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #theme-box { width: 36; border: round $primary; }
    #fx-box { width: 1fr; border: round $primary; }
    #theme-box:focus-within, #fx-box:focus-within { border: round $accent; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._bloom = 0.0
        self._scanlines = 0.0

    def compose(self):
        yield _mode_bar(self, "themes")

        theme_list = OptionList(
            Option("textual-dark  (Built-in dark)", id="textual-dark", data="textual-dark"),
            Option("textual-light (Built-in light)", id="textual-light", data="textual-light"),
            Option("system        (Neutral dark)", id="system", data="system"),
            Option("amber-crt     (Custom amber)", id="amber-crt", data="amber-crt"),
            Option("ocean-slate   (Custom cyan)", id="ocean-slate", data="ocean-slate"),
            id="theme-list",
            on_activate=lambda opt: self._apply_theme(str(opt.data or "textual-dark")),
            on_highlight=lambda opt: self._apply_theme(str(opt.data or "textual-dark")),
        )
        theme_box = Panel(theme_list, title="THEMES", id="theme-box")

        chk_bloom = Checkbox("GPU CRT Bloom (0.45)", False, id="chk-bloom", on_change=self._toggle_bloom)
        chk_scan = Checkbox("GPU CRT Scanlines (0.12)", False, id="chk-scan", on_change=self._toggle_scan)
        preview = RichText(self._preview_text(), id="theme-preview")
        fx_box = Panel(Vertical(chk_bloom, chk_scan, Rule(), preview, id="fx-inner"), title="CRT POST-FX & PREVIEW", id="fx-box")

        yield Horizontal(theme_box, fx_box, id="main")
        yield KeyBar([
            ("↑↓/Enter", "Theme", None),
            ("B", "Bloom", lambda: self._flip("#chk-bloom")),
            ("S", "Scanlines", lambda: self._flip("#chk-scan")),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F5", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F5 Themes & FX · select a theme or toggle GPU CRT shaders", id="status")

    def _preview_text(self) -> Text:
        t = self.theme
        return Text(
            f"Active theme: {t.name}\n"
            f"  background : {t.background}\n"
            f"  surface    : {t.surface}\n"
            f"  panel      : {t.panel}\n"
            f"  primary    : {t.primary}\n"
            f"  accent     : {t.accent}\n"
            f"  foreground : {t.foreground}\n"
            f"  muted      : {t.muted}\n\n"
            f"CRT Post-FX: bloom={self._bloom:.2f} · scanlines={self._scanlines:.2f}",
        )

    def _apply_theme(self, key: str) -> None:
        if self.app is None:
            return
        if key == "system":
            self.app.theme = Theme.system()
        elif key == "amber-crt":
            self.app.theme = Theme(
                name="amber-crt",
                background="#18120B",
                surface="#211910",
                panel="#292014",
                boost="#362A1B",
                primary="#8C6229",
                accent="#FFB347",
                foreground="#F5E6CC",
                muted="#A69072",
                dark=True,
            )
        elif key == "ocean-slate":
            self.app.theme = Theme(
                name="ocean-slate",
                background="#0F172A",
                surface="#16213A",
                panel="#1E293B",
                boost="#273549",
                primary="#334155",
                accent="#38BDF8",
                foreground="#E2E8F0",
                muted="#94A3B8",
                dark=True,
            )
        elif key in BUILTIN_THEMES:
            self.app.theme = key
        self._refresh_preview()

    def _toggle_bloom(self, enabled: bool) -> None:
        self._bloom = 0.45 if enabled else 0.0
        self._apply_fx()

    def _toggle_scan(self, enabled: bool) -> None:
        self._scanlines = 0.12 if enabled else 0.0
        self._apply_fx()

    def _apply_fx(self) -> None:
        blitter = getattr(self.app, "_blitter", None) if self.app is not None else None
        if blitter is not None and hasattr(blitter, "set_fx"):
            blitter.set_fx(bloom=self._bloom, scanlines=self._scanlines)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            self.query_one("#theme-preview", RichText).update(self._preview_text())
            self.query_one("#status", StatusBar).update(
                f"F5 Themes & FX · theme={self.theme.name} · bloom={self._bloom:.2f} · scanlines={self._scanlines:.2f}",
            )
        except KeyError:
            pass
        if self.app is not None:
            self.app.request_draw()

    def _flip(self, selector: str) -> None:
        cb = self.query_one(selector, Checkbox)
        cb.toggle()

    def on_key(self, key: str) -> bool:
        if key == "b":
            self._flip("#chk-bloom")
            return True
        if key == "s":
            self._flip("#chk-scan")
            return True
        return super().on_key(key)


class PlanesScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #vp-box { width: 1fr; border: round $primary; }
    #notes3d-box { width: 38; border: round $primary; }
    #vp-box:focus-within, #notes3d-box:focus-within { border: round $accent; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._spinning = True
        self._tick_handle = None

    def compose(self):
        yield _mode_bar(self, "planes")
        vp = Viewport3D(id="demo-3d")
        vp_box = Panel(vp, title="3D VIEWPORT (drag / ←→↑↓ Orbit · wheel/+/- Zoom · Space Spin)", id="vp-box")
        notes = TextArea(
            "3D Viewport & UI run concurrently:\n"
            "- Client GPU mode: camera uniforms (64B) stream at 120Hz\n"
            "- Server GPU mode: viewport-only frames on Priority P2\n"
            "- Type here without stuttering the 3D scene.\n",
            id="demo-3d-notes",
        )
        notes_box = Panel(notes, title="PARALLEL NOTES", id="notes3d-box")
        yield Horizontal(vp_box, notes_box, id="main")
        yield KeyBar([
            ("←→↑↓/Drag", "Orbit 3D", None),
            ("Wheel/+/-", "Zoom", None),
            ("Space", "Spin", lambda: self.toggle_spin()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F7", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F6 3D Scene · dual-mode Client/Server GPU plane + UI", id="status")

    def on_screen_mount(self) -> None:
        self.query_one("#demo-3d", Viewport3D).focus()
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(1 / 30, self._spin_tick)

    def on_screen_resume(self) -> None:
        self.query_one("#demo-3d", Viewport3D).focus()

    def _spin_tick(self) -> None:
        if not self._spinning or self.app is None or getattr(self.app, "mode_key", None) != "planes":
            return
        try:
            vp = self.query_one("#demo-3d", Viewport3D)
        except KeyError:
            return
        if vp.rect.w > 8 and vp.rect.h > 8:
            vp.orbit(0.03, 0.0)
            self.app.request_draw()

    def toggle_spin(self) -> None:
        self._spinning = not self._spinning

    def on_key(self, key: str) -> bool:
        if key == "space":
            self.toggle_spin()
            return True
        return super().on_key(key)


def _media_asset_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "textual-wgpu-demo-media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_demo_photo(kind: str) -> Path:
    d = _media_asset_dir()
    path = d / f"photo_{kind}.png"
    if path.is_file():
        return path
    w, h = 440, 260
    im = Image.new("RGBA", (w, h), (16, 18, 28, 255))
    draw = ImageDraw.Draw(im)
    if kind == "horizon":
        for y in range(h):
            t = y / max(1, h - 1)
            if y < 165:
                s = y / 165.0
                r = int(28 + 185 * (s ** 1.6))
                g = int(34 + 92 * (s ** 1.4))
                b = int(88 + 90 * (1.0 - s * 0.5))
                draw.line([(0, y), (w - 1, y)], fill=(r, g, b, 255))
            else:
                s = (y - 165) / max(1, h - 165)
                draw.line([(0, y), (w - 1, y)], fill=(int(18 + 14 * (1 - s)), int(14 + 10 * (1 - s)), int(32 + 24 * s), 255))
        draw.ellipse([160, 74, 280, 194], fill=(255, 196, 92, 255))
        # Retro horizon stripes over sun & ground perspective grid
        for sy in range(132, 166, 6):
            draw.rectangle([155, sy, 285, sy + 2], fill=(140, 68, 92, 255))
        for gx in range(-6, 7):
            draw.line([(w // 2 + gx * 18, 165), (w // 2 + gx * 85, h - 1)], fill=(96, 184, 240, 170), width=1)
        for gy in (172, 184, 200, 222, 248):
            draw.line([(0, gy), (w - 1, gy)], fill=(96, 184, 240, 150), width=1)
    elif kind == "spectrum":
        bars = [
            (235, 78, 88), (242, 154, 62), (240, 212, 78),
            (108, 204, 112), (78, 188, 224), (112, 124, 240), (198, 108, 226),
        ]
        bw = w // len(bars)
        for i, col in enumerate(bars):
            draw.rectangle([i * bw, 0, (i + 1) * bw - 1, 185], fill=(*col, 255))
        for x in range(w):
            v = int(255 * x / max(1, w - 1))
            draw.line([(x, 190), (x, h - 1)], fill=(v, v, v, 255))
    else:
        for y in range(0, h, 4):
            for x in range(0, w, 4):
                r = int(127 + 127 * math.sin(x * 0.032))
                g = int(127 + 127 * math.sin(y * 0.038 + 1.2))
                b = int(127 + 127 * math.cos((x + y) * 0.025))
                draw.rectangle([x, y, x + 3, y + 3], fill=(r, g, b, 255))
    im.save(path, format="PNG")
    return path


def _ensure_demo_audio() -> tuple[Path, list[float], float]:
    """Synthesize a clean 4-second C-major9 ambient arpeggio WAV + RMS envelope."""
    d = _media_asset_dir()
    path = d / "synth_arpeggio.wav"
    sr = 22050
    dur_s = 4.0
    n_samples = int(sr * dur_s)
    notes = [261.63, 329.63, 392.00, 493.88, 587.33, 493.88, 392.00, 329.63]
    step_len = n_samples // len(notes)

    buckets = 48
    bucket_len = max(1, n_samples // buckets)
    envelope: list[float] = []

    if not path.is_file():
        pcm = bytearray(n_samples * 2)
        for i in range(n_samples):
            idx = min(len(notes) - 1, i // step_len)
            local = (i % step_len) / max(1, step_len)
            env = math.sin(math.pi * min(1.0, local / 0.12)) * math.exp(-2.4 * local)
            freq = notes[idx]
            sig = 0.55 * math.sin(2.0 * math.pi * freq * i / sr) + 0.25 * math.sin(2.0 * math.pi * freq * 2.0 * i / sr)
            val = max(-32767, min(32767, int(sig * env * 22000)))
            struct.pack_into("<h", pcm, i * 2, val)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(bytes(pcm))

    for b in range(buckets):
        mid = b * bucket_len + bucket_len // 2
        idx = min(len(notes) - 1, mid // step_len)
        local = (mid % step_len) / max(1, step_len)
        env = math.sin(math.pi * min(1.0, local / 0.12)) * math.exp(-2.4 * local)
        envelope.append(round(0.15 + 0.85 * env, 3))
    return path, envelope, dur_s


def _render_demo_video_frame(width: int, height: int, phase: float) -> bytes:
    """Fast procedural RGBA video frame (moving plasma wave + timecode bar)."""
    w = max(16, int(width))
    h = max(16, int(height))
    im = Image.new("RGBA", (w, h), (14, 18, 28, 255))
    draw = ImageDraw.Draw(im)
    bands = 24
    bh = max(2, h // bands)
    for bi in range(bands):
        y0 = bi * bh
        y1 = min(h - 1, y0 + bh - 1)
        s = bi / bands
        r = int(40 + 165 * (0.5 + 0.5 * math.sin(phase * 1.4 + s * 4.5)))
        g = int(50 + 155 * (0.5 + 0.5 * math.sin(phase * 1.1 + s * 3.1 + 1.4)))
        b = int(90 + 150 * (0.5 + 0.5 * math.cos(phase * 0.9 + s * 5.0)))
        draw.rectangle([0, y0, w - 1, y1], fill=(r, g, b, 255))
    cx = int(w * (0.5 + 0.34 * math.sin(phase * 1.3)))
    cy = int(h * (0.5 + 0.28 * math.cos(phase * 1.7)))
    rad = max(10, min(w, h) // 6)
    draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(255, 240, 200, 210), outline=(255, 255, 255, 255), width=2)
    return im.tobytes("raw", "RGBA")


class MediaScreen(Screen):
    DEFAULT_CSS = _SHARED_CSS + """
    #photo-box { width: 1fr; border: round $primary; }
    #media-right { width: 52; }
    #video-box { height: 1fr; border: round $primary; }
    #audio-box { height: 14; border: round $primary; }
    #photo-box:focus-within, #video-box:focus-within, #audio-box:focus-within { border: round $accent; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._photo_kind = "horizon"
        self._custom_photo: Path | None = None
        self._video_playing = True
        self._video_phase = 0.0
        self._file_streamer: Any = None
        self._cam_mode: str | None = None
        self._mic_mode: str | None = None
        self._mic_cap: Any = None
        self._mic_env: list[float] = [0.08] * 48
        self._mic_buf = bytearray()
        self._mic_mon_buf = bytearray()
        self._mic_monitor = False
        self._mic_elapsed = 0.0
        self._audio_playing = False
        self._audio_elapsed = 0.0
        self._audio_proc: subprocess.Popen | None = None
        self._audio_path, self._audio_env, self._audio_dur = _ensure_demo_audio()
        self._audio_pcm: bytes | None = None
        self._audio_sr = 22050
        self._audio_ch = 1
        self._tick_handle = None

    def compose(self):
        yield _mode_bar(self, "media")

        photo_body = RichText(self._build_photo_doc(), id="media-photo", can_focus=False)
        photo_body.on_action = self._on_photo_action
        photo_scroll = VerticalScroll(photo_body, id="media-photo-scroll")
        photo_box = Panel(photo_scroll, title="PHOTO · native RGBA (@image)", id="photo-box")

        vid = Video(id="media-video")
        vid.play()
        vid.push_rgba_frame(240, 144, _render_demo_video_frame(240, 144, 0.0))
        vid_box = Panel(vid, title="VIDEO / CAMERA · H.265/H.264 HW plane (V pause · C cam)", id="video-box")

        btn_open = Button("Open File (O)", id="btn-open-file", variant="primary", on_press=self.open_media_file)
        btn_cam = Button("Camera (C)", id="btn-cam", on_press=self.toggle_camera)
        btn_mic = Button("Mic (M)", id="btn-mic", on_press=self.toggle_mic)
        row1 = Horizontal(btn_open, btn_cam, btn_mic, id="media-btns-1")
        row1.layout_hint_h = 36

        btn_audio = Button("Play Audio (A)", id="btn-audio", on_press=self.toggle_audio)
        btn_vid = Button("Pause Video (V)", id="btn-video", on_press=self.toggle_video)
        btn_mon = Button("Monitor: Off", id="btn-mon", on_press=self.toggle_mic_monitor)
        row2 = Horizontal(btn_audio, btn_vid, btn_mon, id="media-btns-2")
        row2.layout_hint_h = 36

        digits = Digits("00.0s", id="audio-clock")
        bar = ProgressBar(100, id="audio-bar")
        spark = Sparkline(self._audio_env, id="audio-spark")
        spark.layout_hint_h = 26
        audio_inner = Vertical(row1, row2, digits, bar, spark, id="audio-inner")
        audio_box = Panel(audio_inner, title="MEDIA · O file · C camera · M mic · L monitor", id="audio-box")

        right = Vertical(vid_box, audio_box, id="media-right")
        yield Horizontal(photo_box, right, id="main")
        yield KeyBar([
            ("O", "Open File", lambda: self.open_media_file()),
            ("C", "Camera", lambda: self.toggle_camera()),
            ("M", "Mic", lambda: self.toggle_mic()),
            ("L", "Monitor", lambda: self.toggle_mic_monitor()),
            ("V", "Video", lambda: self.toggle_video()),
            ("A", "Audio", lambda: self.toggle_audio()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F8", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F7 Media · Any-device Files, Camera & Mic + H.265/H.264", id="status")

    def _build_photo_doc(self) -> Text:
        path = self._custom_photo if (self._custom_photo and self._custom_photo.is_file()) else _ensure_demo_photo(self._photo_kind)
        t = Text()
        t.append("  Gallery: ")
        for idx, (k, label) in enumerate((("horizon", "1 Sunset"), ("spectrum", "2 Bars"), ("plasma", "3 Pattern")), start=1):
            active = (self._custom_photo is None) and (k == self._photo_kind)
            sty = Style.from_meta({"@click": f"photo:{k}"}) + (Style(bold=True, reverse=True) if active else Style(bold=True))
            t.append(f" {label} ", style=sty)
            if idx < 3:
                t.append("  ")
        t.append("\n\n")
        img_txt = make_image_text(path, max_width_px=440, max_height_px=340, row_height_px=18, indent_px=16)
        if img_txt is not None:
            t.append_text(img_txt)
        t.append(f"\n  File: {path.name} (native RGBA, no half-blocks)\n", style="dim")
        return t

    def _on_photo_action(self, action: str) -> None:
        if action.startswith("photo:"):
            self.set_photo(action.split(":", 1)[1])

    def set_photo(self, kind: str) -> None:
        if kind not in ("horizon", "spectrum", "plasma"):
            return
        self._custom_photo = None
        self._photo_kind = kind
        self.query_one("#media-photo", RichText).update(self._build_photo_doc(), home=True)
        self.query_one("#status", StatusBar).update(f"F7 Media · photo={kind}")

    def _on_peer_file_bytes(self, name: str, data: bytes) -> None:
        if not data:
            return
        safe = Path(name or "media.bin").name
        dst = _media_asset_dir() / f"client_{safe}"
        dst.write_bytes(data)
        self.load_media_file(dst)

    def open_media_file(self) -> None:
        if self.app is None or not hasattr(self.app, "ask_open_file"):
            return
        suffixes = (
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
            ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v",
            ".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac",
        )
        dl = Path.home() / "Downloads"
        start_dir = dl if dl.is_dir() else Path.home()
        self.app.ask_open_file(
            start_dir,
            lambda p: self.load_media_file(p) if p is not None else None,
            title="Open Media (Photo / Video / Audio)",
            subtitle="Pick a device (Phone / Workstation) then select any image, video, or audio file",
            suffixes=suffixes,
            ok_label="Open",
            unified_devices=True,
            peer_file_callback=self._on_peer_file_bytes,
        )

    def pick_server_file(self) -> None:
        self.open_media_file()

    def pick_client_file(self) -> None:
        if self.app is None or not hasattr(self.app, "request_client_file"):
            return
        self.query_one("#status", StatusBar).update("F7 Media · waiting for file picker…")
        self.app.request_client_file(self._on_peer_file_bytes, accept="*/*")

    def load_media_file(self, path: Path) -> None:
        if not path.is_file():
            return
        ext = path.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            self._custom_photo = path
            self.query_one("#media-photo", RichText).update(self._build_photo_doc(), home=True)
            self.query_one("#status", StatusBar).update(f"F7 Media · loaded photo {path.name}")
            return
        if ext in (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"):
            if self._file_streamer is not None:
                self._file_streamer.close()
                self._file_streamer = None
            try:
                from .remote.media_codec import FileVideoStreamer, decode_audio_file_pcm

                self._file_streamer = FileVideoStreamer(path)
                self._video_playing = True
                self.query_one("#btn-video", Button).label = "Pause Video"
                self.query_one("#status", StatusBar).update(f"F7 Media · playing video {path.name}")
                try:
                    pcm, sr, ch, env, dur = decode_audio_file_pcm(path)
                    self._audio_pcm = pcm
                    self._audio_sr = sr
                    self._audio_ch = ch
                    self._audio_env = env
                    self._audio_dur = dur
                    self._audio_elapsed = 0.0
                    self._audio_playing = True
                    self.query_one("#btn-audio", Button).label = "Stop Audio"
                    self.query_one("#audio-spark", Sparkline).data = list(env)
                    if self.app is not None and hasattr(self.app, "play_audio_pcm"):
                        self.app.play_audio_pcm(pcm, sample_rate=sr, channels=ch)
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                self.query_one("#status", StatusBar).update(f"F7 Media · video error: {exc}")
            return
        if ext in (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac"):
            try:
                from .remote.media_codec import decode_audio_file_pcm

                pcm, sr, ch, env, dur = decode_audio_file_pcm(path)
                self._audio_pcm = pcm
                self._audio_sr = sr
                self._audio_ch = ch
                self._audio_env = env
                self._audio_dur = dur
                self._audio_elapsed = 0.0
                self._audio_playing = True
                self.query_one("#btn-audio", Button).label = "Stop Audio"
                self.query_one("#audio-spark", Sparkline).data = list(env)
                if self.app is not None and hasattr(self.app, "play_audio_pcm"):
                    self.app.play_audio_pcm(pcm, sample_rate=sr, channels=ch)
                self.query_one("#status", StatusBar).update(f"F7 Media · playing audio {path.name}")
            except Exception as exc:  # noqa: BLE001
                self.query_one("#status", StatusBar).update(f"F7 Media · audio error: {exc}")

    def toggle_video(self) -> None:
        self._video_playing = not self._video_playing
        vid = self.query_one("#media-video", Video)
        btn = self.query_one("#btn-video", Button)
        if self._video_playing:
            vid.play()
            btn.label = "Pause Video"
        else:
            vid.pause()
            btn.label = "Play Video"

    def _stop_audio_proc(self) -> None:
        if self.app is not None and hasattr(self.app, "stop_audio"):
            self.app.stop_audio()
        if self._audio_proc is not None:
            try:
                self._audio_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._audio_proc = None

    def _stop_camera(self) -> None:
        if self._cam_mode == "peer":
            drv = getattr(self.app, "_driver", None) if self.app is not None else None
            if hasattr(drv, "on_request_peer_camera"):
                drv.on_request_peer_camera(0, None)
        if self._cam_mode == "local" and self._file_streamer is not None:
            self._file_streamer.close()
            self._file_streamer = None
        self._cam_mode = None
        try:
            self.query_one("#btn-cam", Button).label = "Camera (C)"
        except Exception:  # noqa: BLE001
            pass

    def _start_camera_source(self, src: str) -> None:
        self._stop_camera()
        if self._file_streamer is not None:
            self._file_streamer.close()
            self._file_streamer = None
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        if src in ("peer_front", "peer_back") and hasattr(drv, "on_request_peer_camera"):
            facing = 2 if src == "peer_back" else 1
            vid = self.query_one("#media-video", Video)

            def _on_peer_nalu(pts_us: int, flags: int, raw: bytes) -> None:
                is_kf = bool(flags & 0x01)
                cflag = flags & 0xF0
                vid.push_packet(pts_us, raw, keyframe=is_kf, codec_flag=cflag)

            drv.on_request_peer_camera(facing, _on_peer_nalu)
            self._cam_mode = "peer"
            self._video_playing = True
            self.query_one("#btn-cam", Button).label = "Stop Cam (C)"
            self.query_one("#status", StatusBar).update(
                f"F7 Media · streaming connected device camera ({'back' if facing == 2 else 'front'})",
            )
            return
        try:
            from .remote.media_codec import LocalCameraStreamer

            self._file_streamer = LocalCameraStreamer(facing=1)
            self._cam_mode = "local"
            self._video_playing = True
            self.query_one("#btn-cam", Button).label = "Stop Cam (C)"
            self.query_one("#btn-video", Button).label = "Pause Video (V)"
            self.query_one("#status", StatusBar).update("F7 Media · streaming local camera")
        except Exception as exc:  # noqa: BLE001
            self.query_one("#status", StatusBar).update(f"F7 Media · camera error: {exc}")

    def toggle_camera(self) -> None:
        if self._cam_mode is not None:
            self._stop_camera()
            self.query_one("#status", StatusBar).update("F7 Media · camera stopped")
            return
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        if hasattr(drv, "on_request_peer_camera") and hasattr(self.app, "ask_pick"):
            self.app.ask_pick(
                "Select Camera Source",
                [
                    ("local", "💻 Local Device Camera"),
                    ("peer_front", "📱 Connected Device Camera (Front)"),
                    ("peer_back", "📱 Connected Device Camera (Back)"),
                ],
                lambda val: self._start_camera_source(val) if val else None,
            )
        else:
            self._start_camera_source("local")

    def _stop_mic(self) -> None:
        if self._mic_mode == "peer":
            drv = getattr(self.app, "_driver", None) if self.app is not None else None
            if hasattr(drv, "on_request_peer_mic"):
                drv.on_request_peer_mic(False, None)
        if self._mic_cap is not None:
            self._mic_cap.close()
            self._mic_cap = None
        if self._mic_buf:
            from .remote.media_codec import compute_pcm_envelope

            pcm = bytes(self._mic_buf)
            self._audio_pcm = pcm
            self._audio_sr = 16000
            self._audio_ch = 1
            self._audio_env = compute_pcm_envelope(pcm)
            self._audio_dur = max(0.2, len(pcm) / (16000 * 2))
        self._mic_mode = None
        try:
            self.query_one("#btn-mic", Button).label = "Mic (M)"
        except Exception:  # noqa: BLE001
            pass

    def _on_mic_pcm(self, sr: int, ch: int, pcm: bytes) -> None:
        from .remote.media_codec import pcm_rms_level

        if not pcm:
            return
        self._mic_buf.extend(pcm)
        if len(self._mic_buf) > 16000 * 2 * 15:
            del self._mic_buf[: len(self._mic_buf) - 16000 * 2 * 15]
        self._mic_elapsed += len(pcm) / max(1, sr * ch * 2)
        rms = pcm_rms_level(pcm)
        self._mic_env = self._mic_env[1:] + [round(max(0.06, rms), 3)]
        try:
            self.query_one("#audio-spark", Sparkline).data = list(self._mic_env)
            self.query_one("#audio-bar", ProgressBar).progress = min(100.0, rms * 100.0)
            self.query_one("#audio-clock", Digits).update(f"{self._mic_elapsed:04.1f}s")
        except Exception:  # noqa: BLE001
            pass
        if self._mic_monitor and self.app is not None and hasattr(self.app, "play_audio_pcm"):
            self._mic_mon_buf.extend(pcm)
            if len(self._mic_mon_buf) >= int(sr * ch * 2 * 0.32):
                chunk = bytes(self._mic_mon_buf)
                self._mic_mon_buf.clear()
                self.app.play_audio_pcm(chunk, sample_rate=sr, channels=ch)

    def _start_mic_source(self, src: str) -> None:
        self._stop_mic()
        self._stop_audio_proc()
        self._audio_playing = False
        self._mic_buf.clear()
        self._mic_mon_buf.clear()
        self._mic_elapsed = 0.0
        self._mic_env = [0.08] * 48
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        if src == "peer" and hasattr(drv, "on_request_peer_mic"):
            drv.on_request_peer_mic(True, self._on_mic_pcm)
            self._mic_mode = "peer"
            self.query_one("#btn-mic", Button).label = "Stop Mic (M)"
            self.query_one("#status", StatusBar).update("F7 Media · streaming connected device mic")
            return
        try:
            from .remote.media_codec import LocalMicCapture

            self._mic_cap = LocalMicCapture(sample_rate=16000, channels=1, chunk_ms=80)
            self._mic_mode = "local"
            self.query_one("#btn-mic", Button).label = "Stop Mic (M)"
            self.query_one("#status", StatusBar).update("F7 Media · streaming local microphone")
        except Exception as exc:  # noqa: BLE001
            self.query_one("#status", StatusBar).update(f"F7 Media · mic error: {exc}")

    def toggle_mic(self) -> None:
        if self._mic_mode is not None:
            self._stop_mic()
            self.query_one("#status", StatusBar).update("F7 Media · mic stopped (press A to play recording)")
            return
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        if hasattr(drv, "on_request_peer_mic") and hasattr(self.app, "ask_pick"):
            self.app.ask_pick(
                "Select Microphone Source",
                [
                    ("local", "💻 Local Device Microphone"),
                    ("peer", "📱 Connected Device Microphone"),
                ],
                lambda val: self._start_mic_source(val) if val else None,
            )
        else:
            self._start_mic_source("local")

    def toggle_mic_monitor(self) -> None:
        self._mic_monitor = not self._mic_monitor
        self._mic_mon_buf.clear()
        try:
            self.query_one("#btn-mon", Button).label = "Monitor: On" if self._mic_monitor else "Monitor: Off"
        except Exception:  # noqa: BLE001
            pass

    def toggle_audio(self, *, play_os: bool = True) -> None:
        self._audio_playing = not self._audio_playing
        btn = self.query_one("#btn-audio", Button)
        if self._audio_playing:
            self._audio_elapsed = 0.0
            btn.label = "Stop Audio"
            if play_os and self.app is not None and hasattr(self.app, "play_audio_pcm"):
                if self._audio_pcm is not None:
                    pcm, sr, ch = self._audio_pcm, self._audio_sr, self._audio_ch
                else:
                    with wave.open(str(self._audio_path), "rb") as wf:
                        sr = wf.getframerate()
                        ch = wf.getnchannels()
                        pcm = wf.readframes(wf.getnframes())
                self.app.play_audio_pcm(pcm, sample_rate=sr, channels=ch)
            self.query_one("#status", StatusBar).update("F7 Media · playing audio")
        else:
            self._stop_audio_proc()
            btn.label = "Play Audio (A)"
            self.query_one("#status", StatusBar).update("F7 Media · audio stopped")

    def on_screen_mount(self) -> None:
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(0.05, self._media_tick)

    def _media_tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "media":
            return
        if self._mic_mode == "local" and self._mic_cap is not None:
            for pcm in self._mic_cap.pop_chunks():
                self._on_mic_pcm(self._mic_cap.sample_rate, self._mic_cap.channels, pcm)
        if self._video_playing:
            vid = self.query_one("#media-video", Video)
            if self._cam_mode == "peer":
                pass  # frames pushed directly by _on_peer_nalu callback
            elif self._file_streamer is not None:
                is_remote = hasattr(getattr(self.app, "_driver", None), "on_audio_play")
                if is_remote:
                    for au, is_kf in self._file_streamer.pop_aus():
                        vid.push_packet(0, au, keyframe=is_kf, codec_flag=self._file_streamer.codec_flag)
                else:
                    frame = self._file_streamer.pop_rgba()
                    if frame is not None:
                        fw, fh, rgba = frame
                        vid.push_rgba_frame(fw, fh, rgba)
            else:
                self._video_phase += 0.12
                vid.push_rgba_frame(240, 144, _render_demo_video_frame(240, 144, self._video_phase))
        if self._audio_playing:
            self._audio_elapsed += 0.05
            if self._audio_elapsed >= self._audio_dur:
                self._audio_playing = False
                self._audio_elapsed = 0.0
                self._stop_audio_proc()
                self.query_one("#btn-audio", Button).label = "Play Audio (A)"
            pct = min(100.0, (self._audio_elapsed / max(0.1, self._audio_dur)) * 100.0)
            self.query_one("#audio-clock", Digits).update(f"{self._audio_elapsed:04.1f}s")
            self.query_one("#audio-bar", ProgressBar).progress = pct
            shift = int((self._audio_elapsed / max(0.1, self._audio_dur)) * len(self._audio_env)) % len(self._audio_env)
            self.query_one("#audio-spark", Sparkline).data = self._audio_env[shift:] + self._audio_env[:shift]

    def on_key(self, key: str) -> bool:
        if key == "1":
            self.set_photo("horizon")
            return True
        if key == "2":
            self.set_photo("spectrum")
            return True
        if key == "3":
            self.set_photo("plasma")
            return True
        if key == "o":
            self.pick_server_file()
            return True
        if key == "u":
            self.pick_client_file()
            return True
        if key == "c":
            self.toggle_camera()
            return True
        if key == "m":
            self.toggle_mic()
            return True
        if key == "l":
            self.toggle_mic_monitor()
            return True
        if key in ("v", "space"):
            self.toggle_video()
            return True
        if key == "a":
            self.toggle_audio()
            return True
        return super().on_key(key)


class NodesScreen(Screen):
    """F8 · Network OS Directory: saved Workstation/Phone/Cloud nodes & zero-shell App Launcher."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #nodes-box { width: 1fr; }
    #apps-box { width: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        from .remote.directory import NodeRegistry, discover_local_apps

        self._registry = NodeRegistry()
        self._apps = discover_local_apps()

    def compose(self):
        yield _mode_bar(self, "nodes")

        node_opts = [
            Option(f"[{n.role.upper()}] {n.name} — {n.endpoint_label()}", id=n.id)
            for n in self._registry.list_nodes()
        ]
        node_list = OptionList(*node_opts, id="nodes-list")
        btn_add_ws = Button("+ Workstation", id="btn-add-ws", on_press=lambda: self._add_node_prompt("workstation"))
        btn_add_ph = Button("+ Phone", id="btn-add-ph", on_press=lambda: self._add_node_prompt("phone"))
        btn_add_cl = Button("+ Cloud", id="btn-add-cl", on_press=lambda: self._add_node_prompt("cloud"))
        node_btns = Horizontal(btn_add_ws, btn_add_ph, btn_add_cl, id="nodes-btns")
        node_btns.layout_hint_h = 36
        nodes_inner = Vertical(node_btns, node_list, id="nodes-inner")
        nodes_box = Panel(nodes_inner, title="SAVED NODES · Workstation / Phone / Cloud", id="nodes-box")

        app_opts = [
            Option(f"{a.name} [{a.kind}] — {a.description}", id=a.app_id)
            for a in self._apps
        ]
        app_list = OptionList(*app_opts, id="apps-list")
        btn_refresh = Button("Refresh Apps (R)", id="btn-apps-refresh", on_press=self.refresh_apps)
        btn_launch = Button("Launch App (Enter)", id="btn-apps-launch", variant="primary", on_press=self.launch_selected_app)
        app_btns = Horizontal(btn_refresh, btn_launch, id="apps-btns")
        app_btns.layout_hint_h = 36
        apps_inner = Vertical(app_btns, app_list, id="apps-inner")
        apps_box = Panel(apps_inner, title="APP DIRECTORY · Browse & Launch Without Shell Commands", id="apps-box")

        yield Horizontal(nodes_box, apps_box, id="main")
        yield KeyBar([
            ("Enter", "Launch App", lambda: self.launch_selected_app()),
            ("R", "Refresh", lambda: self.refresh_apps()),
            ("W/P/C", "Add Node", None),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F8", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F8 Nodes & Apps · Network OS Directory (Workstation / Phone / Cloud)", id="status")

    def on_screen_mount(self) -> None:
        self.query_one("#apps-list", OptionList).focus()

    def _reload_node_list(self) -> None:
        nl = self.query_one("#nodes-list", OptionList)
        nl.clear_options()
        for n in self._registry.list_nodes():
            nl.add_option(Option(f"[{n.role.upper()}] {n.name} — {n.endpoint_label()}", id=n.id))

    def _add_node_prompt(self, role: str) -> None:
        if self.app is None or not hasattr(self.app, "ask_prompt"):
            return
        from .remote.directory import NodeEntry

        default_ep = "ubuntu@cloud.example.net" if role == "cloud" else (
            "127.0.0.1:7420" if role == "phone" else "user@192.168.1.10"
        )

        def _save(val: str | None) -> None:
            if not val or not val.strip():
                return
            raw = val.strip()
            user = "user"
            host = raw
            port = 7420 if role == "phone" else 22
            transport = "tcp" if role == "phone" else "ssh"
            if "@" in host:
                user, _, host = host.partition("@")
            if ":" in host:
                h, _, ps = host.partition(":")
                host = h
                if ps.isdigit():
                    port = int(ps)
            nid = f"{role}_{len(self._registry.list_nodes()) + 1}"
            self._registry.upsert(
                NodeEntry(
                    id=nid,
                    name=f"{role.title()} ({host})",
                    role=role,
                    transport=transport,
                    host=host,
                    port=port,
                    user=user,
                ),
            )
            self._reload_node_list()
            self.query_one("#status", StatusBar).update(f"Saved {role} node: {host}:{port}")

        self.app.ask_prompt(
            f"Add {role.title()} node (user@host or host:port):",
            _save,
            initial=default_ep,
            title=f"Save {role.title()} Node",
        )

    def refresh_apps(self) -> None:
        from .remote.directory import discover_local_apps

        self._apps = discover_local_apps()
        al = self.query_one("#apps-list", OptionList)
        al.clear_options()
        for a in self._apps:
            al.add_option(Option(f"{a.name} [{a.kind}] — {a.description}", id=a.app_id))
        self.query_one("#status", StatusBar).update(f"F8 Directory · {len(self._apps)} apps available")

    def launch_selected_app(self) -> None:
        al = self.query_one("#apps-list", OptionList)
        idx = al.highlighted
        if idx is None or idx < 0 or idx >= len(self._apps):
            return
        entry = self._apps[idx]
        if entry.kind == "mode" and self.app is not None and hasattr(self.app, "switch_mode"):
            self.app.switch_mode(entry.target)
            return
        if entry.kind == "python":
            self.query_one("#status", StatusBar).update(f"Ready: {entry.name} ({entry.app_id})")
            return

    def on_key(self, key: str) -> bool:
        if key == "r":
            self.refresh_apps()
            return True
        if key == "enter":
            self.launch_selected_app()
            return True
        if key == "w":
            self._add_node_prompt("workstation")
            return True
        if key == "p":
            self._add_node_prompt("phone")
            return True
        if key == "c":
            self._add_node_prompt("cloud")
            return True
        return super().on_key(key)


def build_demo_app(theme: Theme | None = None, *, start: str = "overview") -> ModeApp:
    """Create the standalone ``textual-wgpu`` demo application."""
    modes: list[tuple[str, str, type[Screen]]] = [
        ("overview", "Overview", OverviewScreen),
        ("controls", "Controls", ControlsScreen),
        ("data", "Data & Tree", DataTreeScreen),
        ("editor", "Editor & Modals", EditorModalsScreen),
        ("themes", "Themes & FX", ThemesFxScreen),
        ("planes", "3D Scene", PlanesScreen),
        ("media", "Media", MediaScreen),
        ("nodes", "Nodes & Apps", NodesScreen),
    ]
    return ModeApp(
        modes,
        theme=theme or BUILTIN_THEMES["textual-dark"],
        title="textual-wgpu · demo",
        start=start,
    )


def main(theme: Theme | None = None) -> None:
    build_demo_app(theme=theme).run()


if __name__ == "__main__":
    main()
