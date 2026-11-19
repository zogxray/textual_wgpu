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
    ("composer", "Composer"),
    ("player", "Player"),
]

_OVERVIEW_MD = """\
# textual-wgpu · Meta-OS GPU Runtime

> **Devices don't own apps, and apps don't own hardware.**
> Every physical machine is a symmetric peer exposing independent capabilities (`camera`, `mic`, `speaker`, `files`, `clipboard`, `apps`, `gpu`, `ai`) over a single authenticated SSH link — giving human and AI **Eyes**, **Knowledge**, and **Hands**.

## Meta-OS Workspace Modes

| Key | Mode | Purpose |
|-----|------|---------|
| `F1` | **Overview** | Architecture & live TOC documentation |
| `F2`–`F5` | **Widgets & FX** | Controls, DataTree, Editor/Modals, GPU CRT Themes |
| `F6` | **3D Scene** | `Viewport3D` with Client/Server GPU balancing |
| `F7` | **Media Studio** | Multi-cam independent planes (`C`/`B`), Mic (`M`), Speaker (`S`), Unified Files (`O`) |
| `F8` | **Nodes & Apps** | Saved nodes, unified **System Resources**, App Directory & Running Sessions (`N`/`T`) |
| `F9` | **Meta-OS Composer** | Compose **Eyes** (Cameras/Mic/Clip/Files/UI) + **Knowledge** (Local/Cloud AI) + **Hands** (Speaker/Clip/UI) |
| `F10` | **Winamp Player** | Cross-device file browser + multi-speaker output · one file to N speakers in parallel |

## Navigation

| Key | Action |
|-----|--------|
| `F1`–`F10` | Switch workspace |
| `Tab` / `Shift+Tab` | Cycle focus across panels |
| `↑` `↓` / `PgUp` `PgDn` / Wheel | Scroll or move selection |
| `Ctrl+C` / Drag | Select & copy text |
| `Q` / `Esc` | Quit confirmation modal |
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
    #media-right { width: 58; }
    #video-box { height: 1fr; border: round $primary; }
    #video-row { height: 1fr; }
    #media-video { width: 1fr; height: 1fr; }
    #media-video-2 { width: 1fr; height: 1fr; display: none; }
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
        self._cam2_streamer: Any = None
        self._cam2_mode: str | None = None
        self._mic_mode: str | None = None
        self._mic_cap: Any = None
        self._mic_env: list[float] = [0.08] * 48
        self._mic_buf = bytearray()
        self._mic_mon_buf = bytearray()
        self._mic_monitor = False
        self._mic_elapsed = 0.0
        self._speaker_target: Any = None
        self._speaker_sink: Any = None
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
        vid2 = Video(id="media-video-2")
        vid2.play()
        vid_row = Horizontal(vid, vid2, id="video-row")
        vid_box = Panel(vid_row, title="VIDEO / CAMERAS · C cam1 · B cam2 (independent HW planes)", id="video-box")

        btn_open = Button("Open File (O)", id="btn-open-file", variant="primary", on_press=self.open_media_file)
        btn_cam = Button("Camera (C)", id="btn-cam", on_press=self.toggle_camera)
        btn_cam2 = Button("Cam 2 (B)", id="btn-cam2", on_press=self.toggle_camera2)
        btn_mic = Button("Mic (M)", id="btn-mic", on_press=self.toggle_mic)
        row1 = Horizontal(btn_open, btn_cam, btn_cam2, btn_mic, id="media-btns-1")
        row1.layout_hint_h = 36

        btn_audio = Button("Play Audio (A)", id="btn-audio", on_press=self.toggle_audio)
        btn_spk = Button("Spk: Local (S)", id="btn-spk", on_press=self.select_speaker_target)
        btn_mon = Button("Monitor: Off", id="btn-mon", on_press=self.toggle_mic_monitor)
        btn_vid = Button("Pause (V)", id="btn-video", on_press=self.toggle_video)
        row2 = Horizontal(btn_audio, btn_spk, btn_mon, btn_vid, id="media-btns-2")
        row2.layout_hint_h = 36

        digits = Digits("00.0s", id="audio-clock")
        bar = ProgressBar(100, id="audio-bar")
        spark = Sparkline(self._audio_env, id="audio-spark")
        spark.layout_hint_h = 26
        audio_inner = Vertical(row1, row2, digits, bar, spark, id="audio-inner")
        audio_box = Panel(audio_inner, title="MEDIA · O file · C/B cams · M mic · S speaker", id="audio-box")

        right = Vertical(vid_box, audio_box, id="media-right")
        yield Horizontal(photo_box, right, id="main")
        yield KeyBar([
            ("O", "File", lambda: self.open_media_file()),
            ("C/B", "Cam 1/2", lambda: self.toggle_camera()),
            ("M", "Mic", lambda: self.toggle_mic()),
            ("S", "Speaker", lambda: self.select_speaker_target()),
            ("L", "Monitor", lambda: self.toggle_mic_monitor()),
            ("A", "Audio", lambda: self.toggle_audio()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F9", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F7 Media · Multi-Cam + Any-Device Files, Mic & Speaker", id="status")

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
        if self._cam_mode in ("local", "resource") and self._file_streamer is not None:
            self._file_streamer.close()
            self._file_streamer = None
        self._cam_mode = None
        try:
            self.query_one("#btn-cam", Button).label = "Camera (C)"
        except Exception:  # noqa: BLE001
            pass

    def _wake_cam_thread(self) -> None:
        if self.app is None:
            return
        loop = getattr(self.app, "_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._flush_local_cam)

    def _flush_local_cam(self) -> None:
        if self._cam_mode not in ("local", "resource") or self._file_streamer is None or not self._video_playing:
            return
        try:
            vid = self.query_one("#media-video", Video)
        except Exception:  # noqa: BLE001
            return
        is_remote = hasattr(getattr(self.app, "_driver", None), "on_audio_play")
        if is_remote:
            rot = getattr(self._file_streamer, "rotation", 0)
            for au, is_kf in self._file_streamer.pop_aus():
                vid.push_packet(0, au, keyframe=is_kf, codec_flag=self._file_streamer.codec_flag, rotation=rot)
        else:
            frame = self._file_streamer.pop_rgba()
            if frame is not None:
                fw, fh, rgba = frame
                vid.push_rgba_frame(fw, fh, rgba)

    def _start_camera_source(self, src: str) -> None:
        self._stop_camera()
        if self._file_streamer is not None:
            self._file_streamer.close()
            self._file_streamer = None
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        res_map = getattr(self, "_cam_resources", None) or {}
        res_entry = res_map.get(src)
        if res_entry is not None and res_entry.port > 0 and src.startswith("res:"):
            try:
                from .remote.media_hub import RemoteCameraSubscriber

                is_remote = hasattr(drv, "on_audio_play")
                self._file_streamer = RemoteCameraSubscriber(
                    res_entry.host,
                    res_entry.port,
                    res_entry.resource_id,
                    emit_rgba=not is_remote,
                    on_frame=self._wake_cam_thread,
                )
                self._cam_mode = "resource"
                self._video_playing = True
                self.query_one("#btn-cam", Button).label = "Stop Cam (C)"
                self.query_one("#btn-video", Button).label = "Pause Video (V)"
                self.query_one("#status", StatusBar).update(f"F7 Media · streaming {res_entry.display_label()}")
                return
            except Exception as exc:  # noqa: BLE001
                self.query_one("#status", StatusBar).update(f"F7 Media · camera error: {exc}")
                return
        if src.startswith("peer") and hasattr(drv, "on_request_peer_camera"):
            cid = src.split(":", 1)[1] if ":" in src else ""
            facing = 2 if (src in ("peer_back", "peer:2") or cid == "back") else 1
            vid = self.query_one("#media-video", Video)

            def _on_peer_nalu(pts_us: int, flags: int, raw: bytes) -> None:
                is_kf = bool(flags & 0x01)
                rot = (flags >> 2) & 0x03
                cflag = flags & 0xF0
                vid.push_packet(pts_us, raw, keyframe=is_kf, codec_flag=cflag, rotation=rot)

            drv.on_request_peer_camera(facing, _on_peer_nalu, cam_id=cid)
            self._cam_mode = "peer"
            self._video_playing = True
            self.query_one("#btn-cam", Button).label = "Stop Cam (C)"
            peer = getattr(drv, "peer_profile", None)
            pname = peer.name if peer is not None else "Peer"
            self.query_one("#status", StatusBar).update(
                f"F7 Media · streaming {pname} camera ({cid or ('back' if facing == 2 else 'front')})",
            )
            return
        try:
            from .remote.media_codec import LocalCameraStreamer

            cid = src.split(":", 1)[1] if ":" in src else "front"
            facing = 2 if cid == "back" else 1
            device = cid if cid.startswith("/") else None
            is_remote = hasattr(getattr(self.app, "_driver", None), "on_audio_play")
            self._file_streamer = LocalCameraStreamer(
                facing=facing,
                device=device,
                emit_rgba=not is_remote,
                on_frame=self._wake_cam_thread,
            )
            self._cam_mode = "local"
            self._video_playing = True
            self.query_one("#btn-cam", Button).label = "Stop Cam (C)"
            self.query_one("#btn-video", Button).label = "Pause Video (V)"
            self.query_one("#status", StatusBar).update(f"F7 Media · streaming camera ({cid})")
        except Exception as exc:  # noqa: BLE001
            self.query_one("#status", StatusBar).update(f"F7 Media · camera error: {exc}")

    def start_camera_resource(self, res: Any) -> None:
        local_id = None
        try:
            from .remote.directory import local_device_profile

            local_id = local_device_profile().device_id
        except Exception:  # noqa: BLE001
            pass
        if res.device_id == local_id:
            key = f"local:{res.resource_id}"
        elif res.port > 0:
            key = f"res:{res.uid}"
        else:
            key = f"peer:{res.resource_id}"
        self._cam_resources = {key: res}
        self._start_camera_source(key)

    def toggle_camera(self) -> None:
        from .remote.directory import discover_all_resources, local_device_profile

        if self._cam_mode is not None:
            self._stop_camera()
            self.query_one("#status", StatusBar).update("F7 Media · camera stopped")
            return
        local = local_device_profile()
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        cam_resources = discover_all_resources(kind="camera", driver=drv)

        options: list[tuple[str, str]] = []
        res_map: dict[str, Any] = {}
        for res in cam_resources:
            if res.device_id == local.device_id:
                key = f"local:{res.resource_id}"
            elif res.port > 0:
                key = f"res:{res.uid}"
            else:
                key = f"peer:{res.resource_id}"
            res_map[key] = res
            options.append((key, res.display_label()))
        if not options:
            options.append(("local:front", f"{local.icon} {local.name} · Camera"))
        self._cam_resources = res_map

        if len(options) > 1 and hasattr(self.app, "ask_pick"):
            self.app.ask_pick(
                "Select Independent Camera Resource",
                options,
                lambda val: self._start_camera_source(val) if val else None,
            )
        else:
            self._start_camera_source(options[0][0])

    def _stop_camera2(self) -> None:
        if self._cam2_streamer is not None:
            try:
                self._cam2_streamer.close()
            except Exception:  # noqa: BLE001
                pass
            self._cam2_streamer = None
        self._cam2_mode = None
        try:
            vid2 = self.query_one("#media-video-2", Video)
            vid2.styles["display"] = "none"
            self.query_one("#btn-cam2", Button).label = "Cam 2 (B)"
            if self.app is not None:
                self.app._layout_size = (0, 0)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _wake_cam2_thread(self) -> None:
        if self.app is None:
            return
        loop = getattr(self.app, "_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._flush_cam2)

    def _flush_cam2(self) -> None:
        if self._cam2_streamer is None or not self._video_playing:
            return
        try:
            vid2 = self.query_one("#media-video-2", Video)
        except Exception:  # noqa: BLE001
            return
        is_remote = hasattr(getattr(self.app, "_driver", None), "on_audio_play")
        if is_remote:
            rot = getattr(self._cam2_streamer, "rotation", 0)
            for au, is_kf in self._cam2_streamer.pop_aus():
                vid2.push_packet(0, au, keyframe=is_kf, codec_flag=self._cam2_streamer.codec_flag, rotation=rot)
        else:
            frame = self._cam2_streamer.pop_rgba()
            if frame is not None:
                fw, fh, rgba = frame
                vid2.push_rgba_frame(fw, fh, rgba)

    def _start_camera2_source(self, src: str) -> None:
        self._stop_camera2()
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        res_map = getattr(self, "_cam2_resources", None) or {}
        res_entry = res_map.get(src)
        is_remote = hasattr(drv, "on_audio_play")
        try:
            if res_entry is not None and res_entry.port > 0 and src.startswith("res:"):
                from .remote.media_hub import RemoteCameraSubscriber

                self._cam2_streamer = RemoteCameraSubscriber(
                    res_entry.host,
                    res_entry.port,
                    res_entry.resource_id,
                    emit_rgba=not is_remote,
                    on_frame=self._wake_cam2_thread,
                )
                self._cam2_mode = "resource"
            else:
                from .remote.media_codec import LocalCameraStreamer

                cid = src.split(":", 1)[1] if ":" in src else "front"
                facing = 2 if cid == "back" else 1
                device = cid if cid.startswith("/") else None
                self._cam2_streamer = LocalCameraStreamer(
                    facing=facing,
                    device=device,
                    emit_rgba=not is_remote,
                    on_frame=self._wake_cam2_thread,
                )
                self._cam2_mode = "local"
            vid2 = self.query_one("#media-video-2", Video)
            vid2.styles["display"] = "block"
            if self.app is not None:
                self.app._layout_size = (0, 0)  # type: ignore[attr-defined]
            self.query_one("#btn-cam2", Button).label = "Stop 2 (B)"
            lbl = res_entry.display_label() if res_entry is not None else src
            self.query_one("#status", StatusBar).update(f"F7 Media · Cam 2 streaming {lbl}")
        except Exception as exc:  # noqa: BLE001
            self.query_one("#status", StatusBar).update(f"F7 Media · Cam 2 error: {exc}")

    def toggle_camera2(self) -> None:
        from .remote.directory import discover_all_resources, local_device_profile

        if self._cam2_mode is not None:
            self._stop_camera2()
            self.query_one("#status", StatusBar).update("F7 Media · Cam 2 stopped")
            return
        local = local_device_profile()
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        cam_resources = discover_all_resources(kind="camera", driver=drv)
        options: list[tuple[str, str]] = []
        res_map: dict[str, Any] = {}
        for res in cam_resources:
            if res.device_id == local.device_id:
                key = f"local:{res.resource_id}"
            elif res.port > 0:
                key = f"res:{res.uid}"
            else:
                key = f"peer:{res.resource_id}"
            res_map[key] = res
            options.append((key, res.display_label()))
        if not options:
            options.append(("local:front", f"{local.icon} {local.name} · Camera"))
        self._cam2_resources = res_map
        if len(options) > 1 and hasattr(self.app, "ask_pick"):
            self.app.ask_pick(
                "Select Second Camera (Independent Plane)",
                options,
                lambda val: self._start_camera2_source(val) if val else None,
            )
        else:
            self._start_camera2_source(options[0][0])

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
        if self._mic_monitor:
            self._mic_mon_buf.extend(pcm)
            if len(self._mic_mon_buf) >= int(sr * ch * 2 * 0.32):
                chunk = bytes(self._mic_mon_buf)
                self._mic_mon_buf.clear()
                self._play_pcm_out(chunk, sample_rate=sr, channels=ch)

    def _start_mic_source(self, src: str) -> None:
        self._stop_mic()
        self._stop_audio_proc()
        self._audio_playing = False
        self._mic_buf.clear()
        self._mic_mon_buf.clear()
        self._mic_elapsed = 0.0
        self._mic_env = [0.08] * 48
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        mic_map = getattr(self, "_mic_resources", None) or {}
        res_entry = mic_map.get(src)
        if res_entry is not None and res_entry.port > 0 and src.startswith("res:"):
            try:
                from .remote.media_hub import RemoteMicSubscriber

                self._mic_cap = RemoteMicSubscriber(res_entry.host, res_entry.port, res_entry.resource_id)
                self._mic_mode = "resource"
                self.query_one("#btn-mic", Button).label = "Stop Mic (M)"
                self.query_one("#status", StatusBar).update(f"F7 Media · streaming {res_entry.display_label()}")
                return
            except Exception as exc:  # noqa: BLE001
                self.query_one("#status", StatusBar).update(f"F7 Media · mic error: {exc}")
                return
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

    def start_mic_resource(self, res: Any) -> None:
        local_id = None
        try:
            from .remote.directory import local_device_profile

            local_id = local_device_profile().device_id
        except Exception:  # noqa: BLE001
            pass
        if res.device_id == local_id:
            key = "local"
        elif res.port > 0:
            key = f"res:{res.uid}"
        else:
            key = "peer"
        self._mic_resources = {key: res}
        self._start_mic_source(key)

    def toggle_mic(self) -> None:
        from .remote.directory import discover_all_resources, local_device_profile

        if self._mic_mode is not None:
            self._stop_mic()
            self.query_one("#status", StatusBar).update("F7 Media · mic stopped (press A to play recording)")
            return
        local = local_device_profile()
        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        mic_resources = discover_all_resources(kind="mic", driver=drv)
        options: list[tuple[str, str]] = []
        mic_map: dict[str, Any] = {}
        for res in mic_resources:
            if res.device_id == local.device_id:
                key = "local"
            elif res.port > 0:
                key = f"res:{res.uid}"
            else:
                key = "peer"
            mic_map[key] = res
            options.append((key, res.display_label()))
        if not options:
            options.append(("local", f"{local.icon} {local.name} · Microphone"))
        self._mic_resources = mic_map
        if len(options) > 1 and hasattr(self.app, "ask_pick"):
            self.app.ask_pick(
                "Select Independent Microphone Resource",
                options,
                lambda val: self._start_mic_source(val) if val else None,
            )
        else:
            self._start_mic_source(options[0][0])

    def _play_pcm_out(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        if not pcm:
            return
        target = self._speaker_target
        if target is not None and getattr(target, "port", 0) > 0:
            import threading

            def _send() -> None:
                try:
                    from .remote.media_hub import RemoteSpeakerSink

                    sink = RemoteSpeakerSink(target.host, target.port, sample_rate=sample_rate, channels=channels)
                    try:
                        # Stream in 4096B chunks so playback starts immediately
                        for off in range(0, len(pcm), 4096):
                            sink.write_pcm(pcm[off : off + 4096])
                    finally:
                        sink.close()
                except Exception:  # noqa: BLE001
                    pass

            threading.Thread(target=_send, daemon=True).start()
            return
        if self.app is not None and hasattr(self.app, "play_audio_pcm"):
            self.app.play_audio_pcm(pcm, sample_rate=sample_rate, channels=channels)

    def set_speaker_resource(self, res: Any) -> None:
        self._speaker_target = res
        try:
            short = res.device_name[:10] if res else "Local"
            self.query_one("#btn-spk", Button).label = f"Spk: {short}"
            self.query_one("#status", StatusBar).update(f"F7 Media · output speaker = {res.display_label()}")
        except Exception:  # noqa: BLE001
            pass

    def select_speaker_target(self) -> None:
        from .remote.directory import discover_all_resources

        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        spks = discover_all_resources(kind="speaker", driver=drv)
        options: list[tuple[str, str]] = [("default", "🔊 Default Viewer Speaker")]
        spk_map: dict[str, Any] = {}
        for s in spks:
            if s.port > 0:
                options.append((s.uid, s.display_label()))
                spk_map[s.uid] = s

        def _pick(val: str | None) -> None:
            if not val:
                return
            if val == "default":
                self._speaker_target = None
                self.query_one("#btn-spk", Button).label = "Spk: Local (S)"
                self.query_one("#status", StatusBar).update("F7 Media · output speaker = Default")
            else:
                res = spk_map.get(val)
                self._speaker_target = res
                short = res.device_name[:10] if res else "Peer"
                self.query_one("#btn-spk", Button).label = f"Spk: {short}"
                if res is not None:
                    self.query_one("#status", StatusBar).update(f"F7 Media · output speaker = {res.display_label()}")

        if len(options) > 1 and hasattr(self.app, "ask_pick"):
            self.app.ask_pick("Select Output Speaker Resource", options, _pick)
        else:
            _pick("default")

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
            if play_os:
                if self._audio_pcm is not None:
                    pcm, sr, ch = self._audio_pcm, self._audio_sr, self._audio_ch
                else:
                    with wave.open(str(self._audio_path), "rb") as wf:
                        sr = wf.getframerate()
                        ch = wf.getnchannels()
                        pcm = wf.readframes(wf.getnframes())
                self._play_pcm_out(pcm, sample_rate=sr, channels=ch)
            self.query_one("#status", StatusBar).update("F7 Media · playing audio")
        else:
            self._stop_audio_proc()
            btn.label = "Play Audio (A)"
            self.query_one("#status", StatusBar).update("F7 Media · audio stopped")

    def on_screen_mount(self) -> None:
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(0.05, self._media_tick)

    def on_screen_pause(self) -> None:
        self._stop_camera()
        self._stop_camera2()
        self._stop_mic()
        if self._file_streamer is not None:
            self._file_streamer.close()
            self._file_streamer = None
        if self._audio_playing:
            self._audio_playing = False
            self._stop_audio_proc()

    def on_screen_unmount(self) -> None:
        self.on_screen_pause()

    def _media_tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "media":
            return
        if self._mic_mode in ("local", "resource") and self._mic_cap is not None:
            for pcm in self._mic_cap.pop_chunks():
                self._on_mic_pcm(self._mic_cap.sample_rate, self._mic_cap.channels, pcm)
        if self._video_playing:
            vid = self.query_one("#media-video", Video)
            if self._cam_mode == "peer":
                pass  # frames pushed directly by _on_peer_nalu callback
            elif self._file_streamer is not None:
                is_remote = hasattr(getattr(self.app, "_driver", None), "on_audio_play")
                if is_remote:
                    rot = getattr(self._file_streamer, "rotation", 0)
                    for au, is_kf in self._file_streamer.pop_aus():
                        vid.push_packet(0, au, keyframe=is_kf, codec_flag=self._file_streamer.codec_flag, rotation=rot)
                else:
                    frame = self._file_streamer.pop_rgba()
                    if frame is not None:
                        fw, fh, rgba = frame
                        vid.push_rgba_frame(fw, fh, rgba)
            else:
                import os

                if "ANDROID_ROOT" in os.environ:
                    # Throttle raw RGBA placeholder on mobile Python so UI/Camera/Mic stay 100% responsive
                    cnt = getattr(self, "_synth_skip", 0) + 1
                    self._synth_skip = cnt
                    if cnt % 5 == 0:
                        self._video_phase += 0.30
                        vid.push_rgba_frame(160, 96, _render_demo_video_frame(160, 96, self._video_phase))
                else:
                    self._video_phase += 0.12
                    vid.push_rgba_frame(240, 144, _render_demo_video_frame(240, 144, self._video_phase))
            if self._cam2_streamer is not None:
                self._flush_cam2()
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
        if key in ("o", "u"):
            self.open_media_file()
            return True
        if key == "c":
            self.toggle_camera()
            return True
        if key == "b":
            self.toggle_camera2()
            return True
        if key == "s":
            self.select_speaker_target()
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
    """F8 · Network OS Directory: saved nodes, independent cross-device resources, sessions & App Launcher."""

    DEFAULT_CSS = """
    #main { height: 1fr; }
    #nodes-col { width: 1fr; }
    #apps-col { width: 1fr; }
    #nodes-box { height: 1fr; border: round $primary; }
    #resources-box { height: 1fr; border: round $primary; }
    #apps-box { height: 1fr; border: round $primary; }
    #sessions-box { height: 1fr; border: round $primary; }
    #nodes-box:focus-within, #resources-box:focus-within, #apps-box:focus-within, #sessions-box:focus-within { border: round $accent; }
    """

    def __init__(self) -> None:
        super().__init__()
        from .remote.directory import NodeRegistry, discover_local_apps

        self._registry = NodeRegistry()
        self._apps = discover_local_apps()
        self._resources: list[Any] = []
        self._sessions: list[dict[str, Any]] = []
        self._relays: list[Any] = []

    def _resource_option_label(self, res: Any) -> str:
        ep = f"{res.host}:{res.port}" if getattr(res, "port", 0) else getattr(res, "source", "local")
        return f"[{res.kind.upper():<9}] {res.display_label()}  ({res.resource_id} · {ep})"

    def _collect_sessions(self) -> list[dict[str, Any]]:
        from .remote.media_hub import resource_list_sessions

        seen_eps: set[tuple[str, int]] = set()
        out: list[dict[str, Any]] = []
        for r in self._resources:
            if getattr(r, "port", 0) <= 0:
                continue
            ep = (r.host, r.port)
            if ep in seen_eps:
                continue
            seen_eps.add(ep)
            for s in resource_list_sessions(r.host, r.port):
                s_copy = dict(s)
                s_copy["res_host"] = r.host
                s_copy["res_port"] = r.port
                s_copy["device_name"] = r.device_name
                s_copy["icon"] = r.icon
                out.append(s_copy)
        return out

    def _session_option_label(self, s: dict[str, Any]) -> str:
        return f"{s.get('icon', '💻')} {s.get('device_name', 'Node')} · {s.get('name', s.get('app_id'))} (:{s.get('port')} #{s.get('session_id')})"

    def compose(self):
        yield _mode_bar(self, "nodes")

        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        from .remote.directory import discover_all_resources

        self._resources = discover_all_resources(driver=drv)
        self._sessions = self._collect_sessions()

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

        res_opts = [Option(self._resource_option_label(r), id=r.uid) for r in self._resources]
        res_list = OptionList(*res_opts, id="resources-list")
        btn_res_refresh = Button("Refresh (R)", id="btn-res-refresh", on_press=self.refresh_apps)
        btn_res_use = Button("Use Resource (U)", id="btn-res-use", variant="primary", on_press=self.use_selected_resource)
        res_btns = Horizontal(btn_res_refresh, btn_res_use, id="res-btns")
        res_btns.layout_hint_h = 36
        res_inner = Vertical(res_btns, res_list, id="res-inner")
        res_box = Panel(res_inner, title="SYSTEM RESOURCES · Cameras / Mics / Speakers / Clip / AI", id="resources-box")

        left_col = Vertical(nodes_box, res_box, id="nodes-col")

        app_opts = [
            Option(f"{a.name} [{a.kind}] — {a.description}", id=a.app_id)
            for a in self._apps
        ]
        app_list = OptionList(*app_opts, id="apps-list")
        btn_refresh = Button("Refresh (R)", id="btn-apps-refresh", on_press=self.refresh_apps)
        btn_launch = Button("Switch (Enter)", id="btn-apps-launch", variant="primary", on_press=self.launch_selected_app)
        btn_spawn = Button("Spawn New (N)", id="btn-apps-spawn", on_press=self.spawn_independent_session)
        app_btns = Horizontal(btn_refresh, btn_launch, btn_spawn, id="apps-btns")
        app_btns.layout_hint_h = 36
        apps_inner = Vertical(app_btns, app_list, id="apps-inner")
        apps_box = Panel(apps_inner, title="APP DIRECTORY · Switch Here or Spawn Independent Session", id="apps-box")

        sess_opts = [
            Option(self._session_option_label(s), id=str(s.get("session_id")))
            for s in self._sessions
        ] or [Option("(No spawned sessions yet — press N to spawn)", id="_none")]
        sess_list = OptionList(*sess_opts, id="sessions-list")
        btn_attach = Button("Attach / Open Here (T)", id="btn-sess-attach", variant="primary", on_press=self.attach_selected_session)
        btn_stop = Button("Stop", id="btn-sess-stop", on_press=self.stop_selected_session)
        sess_btns = Horizontal(btn_attach, btn_stop, id="sess-btns")
        sess_btns.layout_hint_h = 36
        sess_inner = Vertical(sess_btns, sess_list, id="sess-inner")
        sess_box = Panel(sess_inner, title="RUNNING SESSIONS · Independent App Mobility", id="sessions-box")

        right_col = Vertical(apps_box, sess_box, id="apps-col")

        yield Horizontal(left_col, right_col, id="main")
        yield KeyBar([
            ("Enter", "Switch", lambda: self.launch_selected_app()),
            ("N", "Spawn App", lambda: self.spawn_independent_session()),
            ("T", "Attach", lambda: self.attach_selected_session()),
            ("U", "Use Resource", lambda: self.use_selected_resource()),
            ("R", "Refresh", lambda: self.refresh_apps()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F9", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar(
            f"F8 Directory · {len(self._resources)} resources · {len(self._sessions)} sessions · {len(self._apps)} apps",
            id="status",
        )

    def on_screen_mount(self) -> None:
        self.query_one("#apps-list", OptionList).focus()

    def on_screen_resume(self) -> None:
        self.refresh_apps()

    def on_screen_unmount(self) -> None:
        for close_fn in self._relays:
            try:
                close_fn()
            except Exception:  # noqa: BLE001
                pass
        self._relays.clear()

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
        from .remote.directory import discover_all_resources, discover_local_apps

        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        self._apps = discover_local_apps()
        self._sessions = self._collect_sessions()
        al = self.query_one("#apps-list", OptionList)
        al.clear_options()
        for a in self._apps:
            al.add_option(Option(f"{a.name} [{a.kind}] — {a.description}", id=a.app_id))
        self.query_one("#status", StatusBar).update("Scanning resources…")

        def _worker() -> None:
            resources = discover_all_resources(driver=drv)
            sessions = self._collect_sessions()

            def _apply() -> None:
                self._resources = resources
                self._sessions = sessions
                rl = self.query_one("#resources-list", OptionList)
                rl.clear_options()
                for r in self._resources:
                    rl.add_option(Option(self._resource_option_label(r), id=r.uid))
                sl = self.query_one("#sessions-list", OptionList)
                sl.clear_options()
                if self._sessions:
                    for s in self._sessions:
                        sl.add_option(Option(self._session_option_label(s), id=str(s.get("session_id"))))
                else:
                    sl.add_option(Option("(No spawned sessions yet — press N to spawn)", id="_none"))
                self.query_one("#status", StatusBar).update(
                    f"F8 Directory · {len(self._resources)} resources · {len(self._sessions)} sessions · {len(self._apps)} apps",
                )

            loop = getattr(self.app, "_loop", None) if self.app is not None else None
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(_apply)
            else:
                _apply()

        import threading

        threading.Thread(target=_worker, daemon=True).start()

    def spawn_independent_session(self) -> None:
        from .remote.media_hub import resource_spawn_session

        al = self.query_one("#apps-list", OptionList)
        idx = al.highlighted
        entry = self._apps[idx] if (idx is not None and 0 <= idx < len(self._apps)) else self._apps[0]
        # Prefer spawning on a reachable ResourceServer
        targets: list[tuple[str, int, str]] = []
        seen: set[tuple[str, int]] = set()
        for r in self._resources:
            if getattr(r, "port", 0) > 0 and (r.host, r.port) not in seen:
                seen.add((r.host, r.port))
                targets.append((r.host, r.port, f"{r.icon} {r.device_name}"))
        if not targets:
            self.query_one("#status", StatusBar).update("F8 · No active Resource Service to spawn session")
            return

        def _do_spawn(ep_idx: int) -> None:
            host, port, dlabel = targets[ep_idx]
            sess = resource_spawn_session(host, port, entry.app_id)
            if sess is None:
                self.query_one("#status", StatusBar).update(f"F8 · Failed to spawn {entry.name} on {dlabel}")
                return
            self.refresh_apps()
            self.query_one("#status", StatusBar).update(
                f"F8 · Spawned {entry.name} on {dlabel} (:{sess.get('port')} #{sess.get('session_id')})",
            )

        if len(targets) > 1 and hasattr(self.app, "ask_pick"):
            self.app.ask_pick(
                f"Spawn Independent '{entry.name}' on Device",
                [(str(i), t[2]) for i, t in enumerate(targets)],
                lambda val: _do_spawn(int(val)) if val is not None else None,
            )
        else:
            _do_spawn(0)

    def attach_selected_session(self) -> None:
        sl = self.query_one("#sessions-list", OptionList)
        idx = sl.highlighted
        if idx is None or idx < 0 or idx >= len(self._sessions):
            return
        sess = self._sessions[idx]
        tport = int(sess.get("port") or 0)
        rhost = str(sess.get("res_host") or "127.0.0.1")
        rport = int(sess.get("res_port") or 7423)
        if tport <= 0:
            return
        from .remote.session_broker import start_session_tcp_relay

        local_port, close_fn = start_session_tcp_relay(rhost, rport, tport)
        self._relays.append(close_fn)
        viewer_bin = Path(__file__).resolve().parents[2] / "rust" / "target" / "debug" / "tw-viewer"
        if viewer_bin.is_file():
            subprocess.Popen(
                [str(viewer_bin), "--tcp", f"127.0.0.1:{local_port}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.query_one("#status", StatusBar).update(
                f"F8 · Opened {sess.get('name')} via SSH relay 127.0.0.1:{local_port}",
            )
        else:
            self.query_one("#status", StatusBar).update(
                f"F8 · Session tunnel ready at 127.0.0.1:{local_port}",
            )

    def stop_selected_session(self) -> None:
        from .remote.media_hub import resource_stop_session

        sl = self.query_one("#sessions-list", OptionList)
        idx = sl.highlighted
        if idx is None or idx < 0 or idx >= len(self._sessions):
            return
        sess = self._sessions[idx]
        ok = resource_stop_session(str(sess["res_host"]), int(sess["res_port"]), str(sess["session_id"]))
        self.refresh_apps()
        self.query_one("#status", StatusBar).update("F8 · Stopped session" if ok else "F8 · Stop failed")

    def use_selected_resource(self) -> None:
        rl = self.query_one("#resources-list", OptionList)
        idx = rl.highlighted
        if idx is None or idx < 0 or idx >= len(self._resources):
            return
        res = self._resources[idx]
        if self.app is None or not hasattr(self.app, "switch_mode"):
            return
        if res.kind in ("ai", "gpu"):
            self.app.switch_mode("composer")
            scr = getattr(self.app, "screen", None)
            if isinstance(scr, ComposerScreen):
                scr.select_resource(res)
            return
        if res.kind == "clipboard":
            txt = ""
            if getattr(res, "port", 0) > 0:
                from .remote.media_hub import resource_clip_get

                txt = resource_clip_get(res.host, res.port)
            self.app.switch_mode("composer")
            scr = getattr(self.app, "screen", None)
            if isinstance(scr, ComposerScreen):
                scr.select_resource(res, captured_text=txt)
            return
        self.app.switch_mode("media")
        scr = getattr(self.app, "screen", None)
        if isinstance(scr, MediaScreen):
            if res.kind == "camera":
                scr.start_camera_resource(res)
            elif res.kind == "mic":
                scr.start_mic_resource(res)
            elif res.kind == "speaker":
                scr.set_speaker_resource(res)
                from .remote.ai_resource import synthesize_speech_pcm

                scr._play_pcm_out(synthesize_speech_pcm("Speaker active", sample_rate=16000), sample_rate=16000, channels=1)
            elif res.kind == "files":
                scr.open_media_file()

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
        if key == "n":
            self.spawn_independent_session()
            return True
        if key == "t":
            self.attach_selected_session()
            return True
        if key == "u":
            self.use_selected_resource()
            return True
        if key == "enter":
            rl = self.query_one("#resources-list", OptionList)
            sl = self.query_one("#sessions-list", OptionList)
            foc = getattr(self.app, "_focus", None) if self.app is not None else None
            if foc is rl:
                self.use_selected_resource()
                return True
            if foc is sl:
                self.attach_selected_session()
                return True
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


class ComposerScreen(Screen):
    """F9 · Meta-OS Composer: wire Eyes (Sensors) + Knowledge (Local/Cloud AI) + Hands (Actuators)."""

    DEFAULT_CSS = _SHARED_CSS + """
    #comp-eyes-box { width: 1fr; border: round $primary; }
    #comp-brain-box { width: 1fr; border: round $primary; }
    #comp-hands-box { width: 1fr; border: round $primary; }
    #comp-eyes-box:focus-within, #comp-brain-box:focus-within, #comp-hands-box:focus-within { border: round $accent; }
    #comp-eyes-list { height: 1fr; }
    #comp-eye-preview { height: 10; }
    #comp-brain-list { height: 1fr; }
    #comp-log { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._eyes: list[Any] = []
        self._brains: list[Any] = []
        self._speakers: list[Any] = []
        self._clipboards: list[Any] = []
        self._spk_idx = 0
        self._clip_idx = 0
        self._last_image_b64 = ""
        self._last_context = ""
        self._last_eye_file_name = ""
        self._last_eye_file_bytes = b""

    def _reload_inventory(self) -> None:
        from .remote.directory import ResourceEntry, discover_all_resources, local_device_profile

        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        local = local_device_profile()

        def _worker() -> None:
            all_res = discover_all_resources(driver=drv)

            def _apply() -> None:
                self._eyes = [r for r in all_res if r.kind in ("camera", "clipboard", "files", "mic")]
                self._eyes.append(
                    ResourceEntry(
                        device_id=local.device_id,
                        device_name=local.name,
                        device_kind=local.kind,
                        icon="🖥",
                        kind="ui_tree",
                        resource_id="active",
                        label="Live App UI State",
                        source="local",
                    ),
                )
                self._brains = [r for r in all_res if r.kind == "ai"]
                self._speakers = [r for r in all_res if r.kind == "speaker"]
                self._clipboards = [r for r in all_res if r.kind == "clipboard"]
                if self._spk_idx >= len(self._speakers):
                    self._spk_idx = 0
                if self._clip_idx >= len(self._clipboards):
                    self._clip_idx = 0
                try:
                    el = self.query_one("#comp-eyes-list", OptionList)
                    el.clear_options()
                    for r in self._eyes:
                        el.add_option(Option(self._eye_label(r), id=r.uid))
                    bl = self.query_one("#comp-brain-list", OptionList)
                    bl.clear_options()
                    for r in self._brains:
                        bl.add_option(Option(self._brain_label(r), id=r.uid))
                    self.query_one("#btn-comp-spk", Button).label = self._speaker_btn_label()
                    self.query_one("#btn-comp-clip", Button).label = self._clip_btn_label()
                    self.query_one("#status", StatusBar).update(
                        f"F9 Composer · {len(self._eyes)} eyes · {len(self._brains)} AI brains · {len(self._speakers)} speakers",
                    )
                except Exception:  # noqa: BLE001
                    pass

            loop = getattr(self.app, "_loop", None) if self.app is not None else None
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(_apply)
            else:
                _apply()

        import threading

        threading.Thread(target=_worker, daemon=True).start()

    def _eye_label(self, r: Any) -> str:
        return f"[{r.kind.upper():<7}] {r.display_label()}"

    def _brain_label(self, r: Any) -> str:
        return f"🤖 {r.device_name} · {r.label}"

    def _speaker_btn_label(self) -> str:
        if not self._speakers:
            return "Target Speaker: Local"
        s = self._speakers[self._spk_idx]
        return f"Speaker: {s.icon} {s.device_name}"

    def _clip_btn_label(self) -> str:
        if not self._clipboards:
            return "Target Clip: Local"
        c = self._clipboards[self._clip_idx]
        return f"Clip: {c.icon} {c.device_name}"

    def _build_eye_preview_doc(self, summary: str = "Capture Eyes (E) to preview Camera / File / Clip", img_b64: str = "", ctx: str = "") -> Text:
        import base64

        t = Text()
        t.append(f" {summary}\n", style="bold")
        if img_b64:
            try:
                raw = base64.b64decode(img_b64)
                p = _media_asset_dir() / "composer_eye_preview.png"
                p.write_bytes(raw)
                img_txt = make_image_text(p, max_width_px=260, max_height_px=130, row_height_px=16, indent_px=8)
                if img_txt is not None:
                    t.append_text(img_txt)
                    t.append("\n")
            except Exception:  # noqa: BLE001
                pass
        if ctx.strip():
            snippet = ctx.strip().replace("\n", " ")
            if len(snippet) > 140:
                snippet = snippet[:137] + "..."
            t.append(f" {snippet}\n", style="dim")
        return t

    def compose(self):
        yield _mode_bar(self, "composer")
        self._reload_inventory()

        eye_opts = [Option(self._eye_label(r), id=r.uid) for r in self._eyes]
        eye_list = OptionList(*eye_opts, id="comp-eyes-list")
        btn_cap = Button("Capture Eyes (E)", id="btn-comp-cap", on_press=self.capture_selected_eyes)
        btn_ref = Button("Refresh (F)", id="btn-comp-ref", on_press=self.refresh_resources)
        eye_btns = Horizontal(btn_cap, btn_ref, id="comp-eye-btns")
        eye_btns.layout_hint_h = 36
        eye_preview = RichText(self._build_eye_preview_doc(), id="comp-eye-preview", can_focus=False)
        eyes_box = Panel(
            Vertical(eye_btns, eye_list, eye_preview, id="comp-eyes-inner"),
            title="1. EYES · Cross-Device Sensors & Preview",
            id="comp-eyes-box",
        )

        brain_opts = [Option(self._brain_label(r), id=r.uid) for r in self._brains]
        brain_list = OptionList(*brain_opts, id="comp-brain-list")
        inp = Input("Describe what you see and summarize clearly.", id="comp-prompt", placeholder="Ask AI or Human workflow…")
        p1 = Button("Look & Tell", id="btn-p-look", on_press=self.preset_look_and_tell)
        p2 = Button("Clip Bridge", id="btn-p-clip", on_press=self.preset_clip_bridge)
        p3 = Button("Listen", id="btn-p-listen", on_press=self.preset_listen_and_reply)
        p4 = Button("Co-Pilot UI", id="btn-p-ui", on_press=self.preset_copilot_ui)
        presets = Horizontal(p1, p2, p3, p4, id="comp-presets")
        presets.layout_hint_h = 36
        brain_box = Panel(
            Vertical(presets, inp, brain_list, id="comp-brain-inner"),
            title="2. KNOWLEDGE · Local & Cloud AI",
            id="comp-brain-box",
        )

        chk_spk = Checkbox("Speak -> Speaker", True, id="chk-hand-speak")
        btn_spk = Button(self._speaker_btn_label(), id="btn-comp-spk", on_press=self.cycle_speaker)
        chk_clip = Checkbox("Write -> Clipboard", True, id="chk-hand-clip")
        btn_clip = Button(self._clip_btn_label(), id="btn-comp-clip", on_press=self.cycle_clipboard)
        chk_ui = Checkbox("Allow UI Hands (switch/act)", False, id="chk-hand-ui")
        btn_run = Button("Run Pipeline (R)", id="btn-comp-run", variant="primary", on_press=self.run_pipeline)
        hands_btns = Horizontal(btn_spk, btn_clip, id="comp-hand-btns")
        hands_btns.layout_hint_h = 36
        comp_log = Log(id="comp-log")
        comp_log.write_line("Meta-OS Composer ready. Pick Eyes + Brain + Hands, or click a Preset.")
        hands_box = Panel(
            Vertical(chk_spk, chk_clip, chk_ui, hands_btns, btn_run, comp_log, id="comp-hands-inner"),
            title="3. HANDS · Cross-Device Actuation",
            id="comp-hands-box",
        )

        yield Horizontal(eyes_box, brain_box, hands_box, id="main")
        yield KeyBar([
            ("E", "Capture Eyes", lambda: self.capture_selected_eyes()),
            ("R", "Run Pipeline", lambda: self.run_pipeline()),
            ("F", "Refresh", lambda: self.refresh_resources()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F9", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F9 Meta-OS Composer · Eyes (Sensors) + Knowledge (AI) + Hands (Actuators)", id="status")

    def select_resource(self, res: Any, *, captured_text: str = "") -> None:
        self.refresh_resources()
        if res.kind == "ai":
            for i, b in enumerate(self._brains):
                if b.uid == res.uid:
                    self.query_one("#comp-brain-list", OptionList).highlighted = i
                    break
            self._log(f"[KNOWLEDGE] selected {res.display_label()}")
        elif res.kind == "gpu":
            self._log(f"[RESOURCE] active GPU: {res.display_label()}")
        elif res.kind == "clipboard":
            for i, e in enumerate(self._eyes):
                if e.uid == res.uid:
                    self.query_one("#comp-eyes-list", OptionList).highlighted = i
                    break
            self._last_context = captured_text
            self._update_eye_preview(f"Clipboard from {res.display_label()}", "", captured_text)
            self._log(f"[EYES] clipboard from {res.display_label()}: {captured_text[:80]}")

    def _update_eye_preview(self, summary: str, img_b64: str, ctx: str) -> None:
        try:
            self.query_one("#comp-eye-preview", RichText).update(
                self._build_eye_preview_doc(summary, img_b64, ctx),
                home=True,
            )
        except Exception:  # noqa: BLE001
            pass

    def refresh_resources(self) -> None:
        self._reload_inventory()
        el = self.query_one("#comp-eyes-list", OptionList)
        el.clear_options()
        for r in self._eyes:
            el.add_option(Option(self._eye_label(r), id=r.uid))
        bl = self.query_one("#comp-brain-list", OptionList)
        bl.clear_options()
        for r in self._brains:
            bl.add_option(Option(self._brain_label(r), id=r.uid))
        self.query_one("#btn-comp-spk", Button).label = self._speaker_btn_label()
        self.query_one("#btn-comp-clip", Button).label = self._clip_btn_label()
        self.query_one("#status", StatusBar).update(
            f"F9 Composer · {len(self._eyes)} eyes · {len(self._brains)} AI brains · {len(self._speakers)} speakers",
        )

    def cycle_speaker(self) -> None:
        if self._speakers:
            self._spk_idx = (self._spk_idx + 1) % len(self._speakers)
        self.query_one("#btn-comp-spk", Button).label = self._speaker_btn_label()

    def cycle_clipboard(self) -> None:
        if self._clipboards:
            self._clip_idx = (self._clip_idx + 1) % len(self._clipboards)
        self.query_one("#btn-comp-clip", Button).label = self._clip_btn_label()

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#comp-log", Log).write_line(msg)
        except Exception:  # noqa: BLE001
            pass

    def _capture_ui_tree_summary(self) -> str:
        if self.app is None:
            return "No active app"
        labels = [f"{k}:{t}" for k, t in getattr(self.app, "_labels", ())]
        scr = getattr(self.app, "screen", None)
        mode = getattr(self.app, "mode_key", "composer")
        return f"App={getattr(self.app, 'TITLE', 'textual-wgpu')}; mode={mode}; screen={type(scr).__name__}; modes=[{', '.join(labels)}]"

    def _capture_eye_resource(self, eye: Any) -> tuple[str, str, str]:
        """Return (summary_label, text_context, image_b64)."""
        import base64

        if eye.kind == "camera":
            from .remote.ai_resource import snapshot_camera_frame

            try:
                snap = snapshot_camera_frame(eye.host, eye.port, eye.resource_id, timeout=3.0)
            except Exception:  # noqa: BLE001
                snap = None
            if snap is not None:
                w, h, png_bytes = snap
                b64 = base64.b64encode(png_bytes).decode("ascii")
                return (f"Camera {w}x{h} PNG from {eye.display_label()}", "", b64)
            return (f"Camera metadata: {eye.display_label()} (no live frame)", f"Camera source: {eye.display_label()}", "")
        if eye.kind == "clipboard":
            if getattr(eye, "port", 0) > 0:
                from .remote.media_hub import resource_clip_get

                txt = resource_clip_get(eye.host, eye.port)
                return (f"Clipboard ({len(txt)} chars) from {eye.display_label()}", txt or "(empty clipboard)", "")
            drv = getattr(self.app, "_driver", None) if self.app is not None else None
            txt = getattr(drv, "remote_clipboard", "") or ""
            return (f"Clipboard from {eye.display_label()}", txt or "(empty clipboard)", "")
        if eye.kind == "files":
            if self._last_eye_file_bytes:
                name = self._last_eye_file_name or "file"
                ext = Path(name).suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                    import io as _io

                    try:
                        im = Image.open(_io.BytesIO(self._last_eye_file_bytes)).convert("RGBA")
                        buf = _io.BytesIO()
                        im.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                        return (f"Image file {name} ({im.width}x{im.height})", f"File: {name}", b64)
                    except Exception:  # noqa: BLE001
                        pass
                txt = self._last_eye_file_bytes[:4096].decode("utf-8", errors="replace")
                return (f"File {name} ({len(self._last_eye_file_bytes)}B)", txt, "")
            return (f"File source: {eye.display_label()} (press E to pick)", f"File source: {eye.display_label()}", "")
        if eye.kind == "mic":
            if getattr(eye, "port", 0) > 0:
                from .remote.media_codec import pcm_rms_level
                from .remote.media_hub import RemoteMicSubscriber
                import time as _t

                sub = RemoteMicSubscriber(eye.host, eye.port, eye.resource_id)
                try:
                    chunks: list[bytes] = []
                    for _ in range(15):
                        chunks.extend(sub.pop_chunks())
                        if chunks:
                            break
                        _t.sleep(0.04)
                    pcm = b"".join(chunks)
                    rms = pcm_rms_level(pcm) if pcm else 0.0
                    return (
                        f"Mic sample ({len(pcm)}B, rms={rms:.2f}) from {eye.display_label()}",
                        f"Microphone {eye.display_label()} level RMS={rms:.2f} ({len(pcm)} bytes)",
                        "",
                    )
                finally:
                    sub.close()
            try:
                from .remote.media_codec import LocalMicCapture, pcm_rms_level
                import time as _t

                cap = LocalMicCapture(sample_rate=16000, channels=1, chunk_ms=80)
                try:
                    chunks = []
                    for _ in range(10):
                        chunks.extend(cap.pop_chunks())
                        if chunks:
                            break
                        _t.sleep(0.04)
                    pcm = b"".join(chunks)
                    rms = pcm_rms_level(pcm) if pcm else 0.0
                    return (
                        f"Local Mic ({len(pcm)}B, rms={rms:.2f})",
                        f"Microphone {eye.display_label()} level RMS={rms:.2f} ({len(pcm)} bytes)",
                        "",
                    )
                finally:
                    cap.close()
            except Exception:  # noqa: BLE001
                return (f"Mic {eye.display_label()}", f"Microphone {eye.display_label()}", "")
        if eye.kind == "ui_tree":
            ctx = self._capture_ui_tree_summary()
            return ("Live UI State captured", ctx, "")
        return (f"Resource {eye.display_label()}", f"Resource: {eye.display_label()} ({eye.kind}:{eye.resource_id})", "")

    def _on_eye_file_bytes(self, name: str, data: bytes) -> None:
        from .remote.directory import ResourceEntry

        self._last_eye_file_name = name
        self._last_eye_file_bytes = data
        lbl, ctx, b64 = self._capture_eye_resource(
            ResourceEntry("local", "File", "desktop", "📁", "files", "fs", name),
        )
        self._last_context = ctx
        self._last_image_b64 = b64
        self._update_eye_preview(lbl, b64, ctx)
        self._log(f"[EYES] {lbl}")

    def capture_selected_eyes(self) -> None:
        el = self.query_one("#comp-eyes-list", OptionList)
        idx = el.highlighted
        if idx is None or idx < 0 or idx >= len(self._eyes):
            return
        eye = self._eyes[idx]
        if eye.kind == "files" and self.app is not None and hasattr(self.app, "ask_open_file"):
            dl = Path.home() / "Downloads"
            start_dir = dl if dl.is_dir() else Path.home()

            def _on_local_path(p: Path | None) -> None:
                if p is not None and p.is_file():
                    self._on_eye_file_bytes(p.name, p.read_bytes())

            self.app.ask_open_file(
                start_dir,
                _on_local_path,
                title="Select File for Eyes (Any Device)",
                unified_devices=True,
                peer_file_callback=self._on_eye_file_bytes,
            )
            return
        lbl, ctx, b64 = self._capture_eye_resource(eye)
        self._last_context = ctx
        self._last_image_b64 = b64
        self._update_eye_preview(lbl, b64, ctx)
        self._log(f"[EYES] {lbl}")

    def _run_with_eye(self, eye: Any, prompt: str) -> str:
        import threading

        bl = self.query_one("#comp-brain-list", OptionList)
        bidx = bl.highlighted
        brain = self._brains[bidx] if (bidx is not None and 0 <= bidx < len(self._brains)) else (self._brains[0] if self._brains else None)
        speak_on = bool(self.query_one("#chk-hand-speak", Checkbox).value)
        clip_on = bool(self.query_one("#chk-hand-clip", Checkbox).value)
        ui_on = bool(self.query_one("#chk-hand-ui", Checkbox).value)
        spk = self._speakers[self._spk_idx] if self._speakers else None
        clp = self._clipboards[self._clip_idx] if self._clipboards else None

        def _worker() -> None:
            from .remote.ai_resource import run_ai_inference, synthesize_speech_pcm
            from .remote.media_hub import RemoteSpeakerSink, resource_ai_infer, resource_clip_set

            lbl, ctx, b64 = self._capture_eye_resource(eye)
            if not ctx and not b64:
                ctx = self._last_context
                b64 = self._last_image_b64
            self._last_context = ctx
            self._last_image_b64 = b64
            loop = getattr(self.app, "_loop", None) if self.app is not None else None
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._update_eye_preview, lbl, b64, ctx)
            else:
                self._update_eye_preview(lbl, b64, ctx)
            self._emit_log(f"[EYES] {lbl}")

            if brain is not None and getattr(brain, "port", 0) > 0:
                answer = resource_ai_infer(
                    brain.host,
                    brain.port,
                    brain.resource_id,
                    prompt,
                    context=ctx,
                    image_b64=b64,
                )
            else:
                answer = run_ai_inference(None, prompt, context=ctx, image_b64=b64)
            self._emit_log(f"[KNOWLEDGE] {answer}")

            if clip_on and clp is not None and getattr(clp, "port", 0) > 0:
                ok = resource_clip_set(clp.host, clp.port, answer)
                self._emit_log(f"[HANDS/CLIP] -> {clp.display_label()} ({'ok' if ok else 'err'})")

            if speak_on:
                pcm = synthesize_speech_pcm(answer, sample_rate=16000)
                if spk is not None and getattr(spk, "port", 0) > 0:
                    try:
                        sink = RemoteSpeakerSink(spk.host, spk.port, sample_rate=16000, channels=1)
                        try:
                            for off in range(0, len(pcm), 4096):
                                sink.write_pcm(pcm[off : off + 4096])
                        finally:
                            sink.close()
                        self._emit_log(f"[HANDS/SPEAK] -> {spk.display_label()}")
                    except Exception as exc:  # noqa: BLE001
                        self._emit_log(f"[HANDS/SPEAK] error: {exc}")
                elif self.app is not None and hasattr(self.app, "play_audio_pcm"):
                    self.app.play_audio_pcm(pcm, sample_rate=16000, channels=1)
                    self._emit_log("[HANDS/SPEAK] -> Default Viewer Speaker")

            if ui_on:
                self._apply_ui_hands(prompt, answer)

        if self.app is not None and getattr(self.app, "_loop", None) is not None and self.app._loop.is_running():
            threading.Thread(target=_worker, daemon=True).start()
            return "running"
        _worker()
        return "done"

    def _emit_log(self, msg: str) -> None:
        loop = getattr(self.app, "_loop", None) if self.app is not None else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._log, msg)
        else:
            self._log(msg)

    def _apply_ui_hands(self, prompt: str, answer: str) -> None:
        low = (prompt + " " + answer).lower()
        target_mode = None
        for key in ("overview", "controls", "data", "editor", "themes", "planes", "media", "nodes"):
            if key in low:
                target_mode = key
                break

        def _act() -> None:
            if target_mode and self.app is not None and hasattr(self.app, "switch_mode"):
                self.app.switch_mode(target_mode)
                self._log(f"[HANDS/UI] switched app mode -> {target_mode}")
            elif self.app is not None and hasattr(self.app, "notify"):
                self.app.notify(answer[:80], severity="information", timeout=3.0)
                self._log("[HANDS/UI] showed toast notification on live canvas")

        loop = getattr(self.app, "_loop", None) if self.app is not None else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(_act)
        else:
            _act()

    def run_pipeline(self) -> None:
        el = self.query_one("#comp-eyes-list", OptionList)
        idx = el.highlighted
        eye = self._eyes[idx] if (idx is not None and 0 <= idx < len(self._eyes)) else (self._eyes[0] if self._eyes else None)
        if eye is None:
            self._log("[ERR] No Eyes selected")
            return
        prompt = self.query_one("#comp-prompt", Input).value or "Analyze active resource."
        self._run_with_eye(eye, prompt)

    def preset_look_and_tell(self) -> None:
        cam_idx = next((i for i, r in enumerate(self._eyes) if r.kind == "camera"), 0)
        self.query_one("#comp-eyes-list", OptionList).highlighted = cam_idx
        self.query_one("#comp-prompt", Input).value = "Look at this camera frame and describe what you see."
        self.query_one("#chk-hand-speak", Checkbox).value = True
        self.run_pipeline()

    def preset_clip_bridge(self) -> None:
        clip_idx = next((i for i, r in enumerate(self._eyes) if r.kind == "clipboard"), 0)
        self.query_one("#comp-eyes-list", OptionList).highlighted = clip_idx
        self.query_one("#comp-prompt", Input).value = "Summarize and clean up this clipboard content."
        self.query_one("#chk-hand-clip", Checkbox).value = True
        self.run_pipeline()

    def preset_listen_and_reply(self) -> None:
        mic_idx = next((i for i, r in enumerate(self._eyes) if r.kind == "mic"), 0)
        self.query_one("#comp-eyes-list", OptionList).highlighted = mic_idx
        self.query_one("#comp-prompt", Input).value = "Check microphone input level and reply on speaker."
        self.query_one("#chk-hand-speak", Checkbox).value = True
        self.run_pipeline()

    def preset_copilot_ui(self) -> None:
        ui_idx = next((i for i, r in enumerate(self._eyes) if r.kind == "ui_tree"), 0)
        self.query_one("#comp-eyes-list", OptionList).highlighted = ui_idx
        self.query_one("#comp-prompt", Input).value = "Inspect UI state and demonstrate switching to media or nodes."
        self.query_one("#chk-hand-ui", Checkbox).value = True
        self.run_pipeline()

    def on_key(self, key: str) -> bool:
        if key == "e":
            self.capture_selected_eyes()
            return True
        if key == "r":
            self.run_pipeline()
            return True
        if key == "f":
            self.refresh_resources()
            return True
        return super().on_key(key)


class PlayerScreen(Screen):
    """F10 · Winamp-style cross-device audio player.

    Browse files on ANY connected device, pick audio, and play it to one or more
    speaker resources in parallel — simultaneously. Each speaker output is independent.
    """

    DEFAULT_CSS = _SHARED_CSS + """
    #player-main { height: 1fr; }
    #player-left { width: 1fr; border: round $primary; }
    #player-right { width: 1fr; border: round $primary; }
    #player-left:focus-within, #player-right:focus-within { border: round $accent; }
    #player-file-list { height: 1fr; }
    #player-speaker-list { height: 1fr; }
    #player-visualizer { height: 4; }
    """

    AUDIO_SUFFIXES = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma")

    def __init__(self) -> None:
        super().__init__()
        self._files: list[Any] = []  # ResourceEntry list (kind=files)
        self._speakers: list[Any] = []  # ResourceEntry list (kind=speaker)
        self._file_entries: list[tuple[bool, str, str, int]] = []  # (is_dir, name, path, size)
        self._current_ep: tuple[str, int] | None = None  # (host, port) for current file device
        self._current_path: str = ""
        self._current_name: str = ""
        self._pcm: bytes = b""
        self._sr: int = 22050
        self._ch: int = 1
        self._dur: float = 0.0
        self._env: list[float] = [0.05] * 48
        self._playing: bool = False
        self._pos: float = 0.0
        self._active_sinks: list[Any] = []  # RemoteSpeakerSink instances
        self._active_ep: tuple[str, int] | None = None  # speaker endpoint currently playing
        self._tick_handle: Any = None
        # Playlist support
        self._playlist: list[Path] = []  # local paths to play in order
        self._playlist_idx: int = -1
        self._loading_playlist: bool = False
        self._pending_remote_files: list[tuple[str, str, int, str, str]] = []

    def compose(self):
        yield _mode_bar(self, "player")

        # Left: File browser / playlist (cross-device)
        btn_open = Button("Open File (O)", id="btn-player-open", variant="primary", on_press=self.open_file)
        btn_folder = Button("Open Folder (F)", id="btn-player-folder", variant="primary", on_press=self.open_folder)
        btn_ref = Button("Refresh (R)", id="btn-player-refresh", on_press=self.refresh_resources)
        btn_prev = Button("◀ Prev (,)", id="btn-player-prev", on_press=self.prev_track)
        btn_next = Button("Next (.)", id="btn-player-next", on_press=self.next_track)
        file_btns1 = Horizontal(btn_open, btn_folder, btn_ref, id="player-file-btns1")
        file_btns1.layout_hint_h = 36
        file_btns2 = Horizontal(btn_prev, btn_next, id="player-file-btns2")
        file_btns2.layout_hint_h = 36
        file_list = OptionList(id="player-file-list")
        file_info = Static("Press O for file or F for folder…", id="player-file-info")
        left_inner = Vertical(file_btns1, file_btns2, file_list, file_info, id="player-left-inner")
        left = Panel(left_inner, title="PLAYLIST · Any Device (O=file, F=folder)", id="player-left")

        # Right: Speaker selector + playback controls
        spk_list = OptionList(id="player-speaker-list")
        btn_play = Button("▶ Play (Space)", id="btn-player-play", variant="primary", on_press=self.toggle_play)
        btn_stop = Button("■ Stop (X)", id="btn-player-stop", on_press=self.stop_play)
        btn_add_spk = Button("+ Add Speaker (A)", id="btn-player-add", on_press=self.add_speaker_to_output)
        btn_clear_spk = Button("Clear (Z)", id="btn-player-clear", on_press=self.clear_speakers)
        ctrl_btns = Horizontal(btn_play, btn_stop, btn_add_spk, btn_clear_spk, id="player-ctrl-btns")
        ctrl_btns.layout_hint_h = 36
        now_playing = Static("No track loaded", id="player-now-playing")
        bar = ProgressBar(100, id="player-progress")
        spark = Sparkline(self._env, id="player-visualizer")
        spark.layout_hint_h = 26
        right_inner = Vertical(ctrl_btns, spk_list, now_playing, bar, spark, id="player-right-inner")
        right = Panel(right_inner, title="OUTPUT · Speakers (parallel) · Space=Play · X=Stop", id="player-right")

        yield Horizontal(left, right, id="player-main")
        yield KeyBar([
            ("O", "Open File", lambda: self.open_file()),
            ("F", "Open Folder", lambda: self.open_folder()),
            ("Space", "Play/Pause", lambda: self.toggle_play()),
            ("X", "Stop", lambda: self.stop_play()),
            (",", "Prev", lambda: self.prev_track()),
            (".", "Next", lambda: self.next_track()),
            ("A", "Add Speaker", lambda: self.add_speaker_to_output()),
            ("Z", "Clear Speakers", lambda: self.clear_speakers()),
            ("R", "Refresh", lambda: self.refresh_resources()),
            ("Tab", "Panel", lambda: _focus_next(self)),
            ("F1–F10", "Modes", None),
            ("Q", "Quit", lambda: _quit(self)),
        ])
        yield StatusBar("F10 Player · Winamp-style · Cross-device files + parallel speaker output", id="status")

    def on_screen_mount(self) -> None:
        if self.app is not None and hasattr(self.app, "set_interval"):
            self._tick_handle = self.app.set_interval(0.05, self._player_tick)
        self.refresh_resources()

    def on_screen_pause(self) -> None:
        self.stop_play()
        for s in self._active_sinks:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self._active_sinks.clear()

    def on_screen_unmount(self) -> None:
        self.on_screen_pause()

    def refresh_resources(self) -> None:
        from .remote.directory import discover_all_resources

        drv = getattr(self.app, "_driver", None) if self.app is not None else None
        self._speakers = discover_all_resources(kind="speaker", driver=drv)
        sl = self.query_one("#player-speaker-list", OptionList)
        sl.clear_options()
        for s in self._speakers:
            sl.add_option(Option(f"{s.icon} {s.device_name} · Speaker (:{s.port})", id=s.uid))
        self.query_one("#status", StatusBar).update(
            f"F10 Player · {len(self._speakers)} speakers available · press O to open a file",
        )

    def open_file(self) -> None:
        if self.app is None or not hasattr(self.app, "ask_open_file"):
            return
        suffixes = self.AUDIO_SUFFIXES
        dl = Path.home() / "Downloads"
        start_dir = dl if dl.is_dir() else Path.home()

        def _on_path(p: Path | None) -> None:
            if p is not None and p.is_file():
                self._playlist = [p]
                self._playlist_idx = 0
                self._refresh_playlist_ui()
                self._load_local_file(p)

        self.app.ask_open_file(
            start_dir,
            _on_path,
            title="Open Audio (Any Device)",
            subtitle="Pick an audio file from any connected device",
            suffixes=suffixes,
            ok_label="Play",
            unified_devices=True,
            peer_file_callback=self._on_peer_file,
        )

    def open_folder(self) -> None:
        if self.app is None or not hasattr(self.app, "ask_open_file"):
            return
        dl = Path.home() / "Downloads"
        start_dir = dl if dl.is_dir() else Path.home()

        def _on_folder(p: Path | None) -> None:
            if p is not None and p.is_dir():
                self._build_local_playlist(p)

        self.app.ask_open_file(
            start_dir,
            _on_folder,
            title="Open Folder for Playlist (Any Device)",
            subtitle="Select a folder — all audio files inside become the playlist",
            kind="folder",
            ok_label="Scan",
            unified_devices=True,
            peer_folder_callback=self._on_peer_folder,
        )

    def _on_peer_folder(self, source: str, host: str, port: int, folder_path: str) -> None:
        """Build playlist from a remote folder by listing + fetching audio files."""
        self._loading_playlist = True
        self.query_one("#status", StatusBar).update(f"Scanning {folder_path} for audio…")

        def _worker() -> None:
            if source == "resource":
                from .remote.media_hub import resource_fs_list

                entries = resource_fs_list(host, port, folder_path)
            elif source == "android":
                import json

                from com.textualwgpu.app import LocalMediaHelper  # type: ignore[import-not-found]

                raw = str(LocalMediaHelper.listMediaJson(folder_path) or "[]")
                items = json.loads(raw)
                entries = [
                    (bool(it.get("d")), str(it.get("n") or ""), str(it.get("p") or ""), int(it.get("s") or 0))
                    for it in items
                ]
            else:
                entries = []

            audio_files = [
                (name, fpath, size)
                for is_dir, name, fpath, size in entries
                if not is_dir and Path(name).suffix.lower() in self.AUDIO_SUFFIXES
            ]
            audio_files.sort(key=lambda x: x[0].lower())

            def _apply() -> None:
                self._playlist = []
                self._playlist_idx = -1
                self._pending_remote_files = [(source, host, port, name, fpath) for name, fpath, _ in audio_files]
                self._refresh_playlist_ui()
                self._loading_playlist = False
                if self._pending_remote_files:
                    self.query_one("#status", StatusBar).update(
                        f"F10 Player · {len(self._pending_remote_files)} tracks in playlist · press Space to play",
                    )
                    self.query_one("#player-file-info", Static).update(
                        f"{len(self._pending_remote_files)} tracks ready · Space to play",
                    )
                else:
                    self.query_one("#status", StatusBar).update("F10 Player · no audio files in folder")
            self.call_from_thread(_apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _build_local_playlist(self, folder: Path) -> None:
        self._playlist = []
        self._playlist_idx = -1
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() in self.AUDIO_SUFFIXES:
                self._playlist.append(p)
        self._pending_remote_files = []
        self._refresh_playlist_ui()
        if self._playlist:
            self.query_one("#status", StatusBar).update(
                f"F10 Player · {len(self._playlist)} tracks in {folder.name} · press Space to play",
            )
            self.query_one("#player-file-info", Static).update(
                f"{len(self._playlist)} tracks from {folder.name}",
            )
        else:
            self.query_one("#status", StatusBar).update(f"F10 Player · no audio files in {folder.name}")

    def _refresh_playlist_ui(self) -> None:
        fl = self.query_one("#player-file-list", OptionList)
        fl.clear_options()
        total = len(self._playlist) + len(getattr(self, "_pending_remote_files", []))
        for i, p in enumerate(self._playlist):
            marker = "▶ " if i == self._playlist_idx else "  "
            fl.add_option(Option(f"{marker}{i+1}. {p.name}", id=f"pl_{i}"))
        for i, rf in enumerate(getattr(self, "_pending_remote_files", [])):
            name = rf[3]
            fl.add_option(Option(f"  {len(self._playlist)+i+1}. {name} (remote)", id=f"rpl_{i}"))
        if total == 0:
            fl.add_option(Option("(empty playlist — press O for file or F for folder)", id="_empty"))

    def prev_track(self) -> None:
        if not self._playlist and not getattr(self, "_pending_remote_files", []):
            return
        if self._playlist_idx > 0:
            self._play_track_at(self._playlist_idx - 1)
        elif self._playlist_idx == 0 and self._pending_remote_files:
            # Go back to remote files
            self._play_next_remote()

    def next_track(self) -> None:
        if not self._playlist and not getattr(self, "_pending_remote_files", []):
            return
        if self._playlist_idx >= 0 and self._playlist_idx < len(self._playlist) - 1:
            self._play_track_at(self._playlist_idx + 1)
        elif self._pending_remote_files:
            self._play_next_remote()

    def _play_track_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._playlist):
            return
        self._playlist_idx = idx
        self._refresh_playlist_ui()
        self._load_local_file(self._playlist[idx])
        self.toggle_play()

    def _play_next_remote(self) -> None:
        if not self._pending_remote_files:
            return
        source, host, port, name, fpath = self._pending_remote_files.pop(0)
        self._refresh_playlist_ui()
        self.query_one("#status", StatusBar).update(f"Loading {name} from remote device…")

        def _fetch_and_play() -> None:
            if source == "resource":
                from .remote.media_hub import resource_fs_get

                res = resource_fs_get(host, port, fpath)
            elif source == "android":
                from com.textualwgpu.app import LocalMediaHelper  # type: ignore[import-not-found]
                from .remote.protocol import decode_client_file

                packed = LocalMediaHelper.readMediaPack(fpath)
                if packed is not None:
                    n, raw = decode_client_file(bytes(packed))
                    res = (n, raw)
                else:
                    res = None
            else:
                res = None
            if res is None:
                self.call_from_thread(self.next_track)
                return

            def _play() -> None:
                safe_name, data = res
                dst = _media_asset_dir() / f"player_{Path(safe_name).name}"
                dst.write_bytes(data)
                self._load_local_file(dst)
                self.toggle_play()

            self.call_from_thread(_play)

        threading.Thread(target=_fetch_and_play, daemon=True).start()

    def _on_peer_file(self, name: str, data: bytes) -> None:
        safe = Path(name or "audio.bin").name
        dst = _media_asset_dir() / f"player_{safe}"
        dst.write_bytes(data)
        self._load_local_file(dst)

    def _load_local_file(self, path: Path) -> None:
        self.stop_play()
        try:
            from .remote.media_codec import decode_audio_file_pcm

            pcm, sr, ch, env, dur = decode_audio_file_pcm(path, sample_rate=22050, channels=1)
            self._pcm = pcm
            self._sr = sr
            self._ch = ch
            self._env = env
            self._dur = dur
            self._current_name = path.name
            self._current_path = str(path)
            self._pos = 0.0
            self._playing = False
            self.query_one("#player-now-playing", Static).update(f"♪ {path.name} ({dur:.1f}s · {sr}Hz)")
            self.query_one("#player-visualizer", Sparkline).data = list(env)
            self.query_one("#player-progress", ProgressBar).progress = 0.0
            self.query_one("#btn-player-play", Button).label = "▶ Play (Space)"
            self.query_one("#status", StatusBar).update(f"F10 Player · loaded {path.name} · press Space to play")
        except Exception as exc:  # noqa: BLE001
            self.query_one("#status", StatusBar).update(f"F10 Player · load error: {exc}")

    def toggle_play(self) -> None:
        if not self._pcm:
            self.query_one("#status", StatusBar).update("F10 Player · no track loaded (press O)")
            return
        if self._playing:
            self._playing = False
            self._stop_all_sinks()
            self.query_one("#btn-player-play", Button).label = "▶ Play (Space)"
            self.query_one("#status", StatusBar).update("F10 Player · paused")
        else:
            self._start_playback()
            self.query_one("#btn-player-play", Button).label = "⏸ Pause (Space)"
            self.query_one("#status", StatusBar).update(f"F10 Player · playing {self._current_name}")

    def stop_play(self) -> None:
        self._playing = False
        self._pos = 0.0
        self._stop_all_sinks()
        try:
            self.query_one("#btn-player-play", Button).label = "▶ Play (Space)"
            self.query_one("#player-progress", ProgressBar).progress = 0.0
        except Exception:  # noqa: BLE001
            pass

    def _stop_all_sinks(self) -> None:
        for s in self._active_sinks:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self._active_sinks.clear()
        if self.app is not None and hasattr(self.app, "stop_audio"):
            self.app.stop_audio()

    def _start_playback(self) -> None:
        self._playing = True
        # Play to local speaker (app.play_audio_pcm)
        if self.app is not None and hasattr(self.app, "play_audio_pcm"):
            self.app.play_audio_pcm(self._pcm, sample_rate=self._sr, channels=self._ch)
        # Also play to any explicitly added remote speakers
        for s in self._active_sinks:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self._active_sinks.clear()
        for ep_host, ep_port in self._added_speaker_eps:
            try:
                from .remote.media_hub import RemoteSpeakerSink

                sink = RemoteSpeakerSink(ep_host, ep_port, sample_rate=self._sr, channels=self._ch)
                threading.Thread(
                    target=self._stream_to_sink,
                    args=(sink, self._pcm, self._sr, self._ch),
                    daemon=True,
                ).start()
                self._active_sinks.append(sink)
            except Exception:  # noqa: BLE001
                pass

    def _stream_to_sink(self, sink: Any, pcm: bytes, sr: int, ch: int) -> None:
        try:
            chunk = 4096
            for off in range(0, len(pcm), chunk):
                if not self._playing:
                    break
                sink.write_pcm(pcm[off:off + chunk])
        except Exception:  # noqa: BLE001
            pass

    _added_speaker_eps: list[tuple[str, int]] = []

    def add_speaker_to_output(self) -> None:
        sl = self.query_one("#player-speaker-list", OptionList)
        idx = sl.highlighted
        if idx is None or idx < 0 or idx >= len(self._speakers):
            self.query_one("#status", StatusBar).update("F10 Player · select a speaker first")
            return
        spk = self._speakers[idx]
        ep = (spk.host, spk.port)
        if ep in self._added_speaker_eps:
            self.query_one("#status", StatusBar).update(f"F10 Player · {spk.device_name} already added")
            return
        self._added_speaker_eps.append(ep)
        self.query_one("#status", StatusBar).update(
            f"F10 Player · + {spk.device_name} → {len(self._added_speaker_eps)} parallel speaker(s)",
        )
        # If currently playing, immediately start streaming to this new speaker
        if self._playing:
            try:
                from .remote.media_hub import RemoteSpeakerSink

                sink = RemoteSpeakerSink(spk.host, spk.port, sample_rate=self._sr, channels=self._ch)
                threading.Thread(
                    target=self._stream_to_sink,
                    args=(sink, self._pcm, self._sr, self._ch),
                    daemon=True,
                ).start()
                self._active_sinks.append(sink)
            except Exception:  # noqa: BLE001
                pass

    def clear_speakers(self) -> None:
        self._added_speaker_eps.clear()
        self._stop_all_sinks()
        self.query_one("#status", StatusBar).update("F10 Player · cleared parallel speakers")

    def _player_tick(self) -> None:
        if self.app is None or getattr(self.app, "mode_key", None) != "player":
            return
        if self._playing:
            self._pos += 0.05
            if self._pos >= self._dur:
                # Track finished — auto-advance to next track
                self._pos = self._dur
                self._playing = False
                self._stop_all_sinks()
                try:
                    self.query_one("#btn-player-play", Button).label = "▶ Play (Space)"
                except Exception:  # noqa: BLE001
                    pass
                # Auto-advance
                if self._playlist_idx >= 0 and self._playlist_idx < len(self._playlist) - 1:
                    self._play_track_at(self._playlist_idx + 1)
                elif self._pending_remote_files:
                    self._play_next_remote()
            try:
                pct = min(100.0, (self._pos / max(0.1, self._dur)) * 100.0)
                self.query_one("#player-progress", ProgressBar).progress = pct
                shift = int((self._pos / max(0.1, self._dur)) * len(self._env)) % len(self._env)
                self.query_one("#player-visualizer", Sparkline).data = self._env[shift:] + self._env[:shift]
            except Exception:  # noqa: BLE001
                pass

    def on_key(self, key: str) -> bool:
        if key == "o":
            self.open_file()
            return True
        if key == "f":
            self.open_folder()
            return True
        if key == "space":
            self.toggle_play()
            return True
        if key == "x":
            self.stop_play()
            return True
        if key == ",":
            self.prev_track()
            return True
        if key == ".":
            self.next_track()
            return True
        if key == "a":
            self.add_speaker_to_output()
            return True
        if key == "z":
            self.clear_speakers()
            return True
        if key == "r":
            self.refresh_resources()
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
        ("composer", "Composer", ComposerScreen),
        ("player", "Player", PlayerScreen),
    ]
    return ModeApp(
        modes,
        theme=theme or BUILTIN_THEMES["textual-dark"],
        title="textual-wgpu · demo",
        start=start,
    )


def main(theme: Theme | None = None) -> None:
    import os

    start = os.environ.get("TEXTUAL_WGPU_START", "").strip() or "overview"
    build_demo_app(theme=theme, start=start).run()


if __name__ == "__main__":
    main()
