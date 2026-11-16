"""Standalone textual-wgpu widget & runtime showcase (``python -m textual_wgpu``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.style import Style
from rich.text import Text

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
)

DEMO_LABELS: list[tuple[str, str]] = [
    ("overview", "Overview"),
    ("controls", "Controls"),
    ("data", "Data & Tree"),
    ("editor", "Editor & Modals"),
    ("themes", "Themes & FX"),
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


def build_demo_app(theme: Theme | None = None) -> ModeApp:
    """Create the standalone ``textual-wgpu`` demo application."""
    modes: list[tuple[str, str, type[Screen]]] = [
        ("overview", "Overview", OverviewScreen),
        ("controls", "Controls", ControlsScreen),
        ("data", "Data & Tree", DataTreeScreen),
        ("editor", "Editor & Modals", EditorModalsScreen),
        ("themes", "Themes & FX", ThemesFxScreen),
    ]
    return ModeApp(
        modes,
        theme=theme or BUILTIN_THEMES["textual-dark"],
        title="textual-wgpu · demo",
        start="overview",
    )


def main(theme: Theme | None = None) -> None:
    build_demo_app(theme=theme).run()


if __name__ == "__main__":
    main()
