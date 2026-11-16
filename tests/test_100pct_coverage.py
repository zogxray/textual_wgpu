"""100% Textual API surface coverage guard (modules, widgets, containers, events, App/Screen/Widget)."""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil

import pytest

textual = pytest.importorskip("textual")
import textual.app
import textual.containers
import textual.events
import textual.screen
import textual.widget
import textual_wgpu
import textual_wgpu.app
import textual_wgpu.containers
import textual_wgpu.events
import textual_wgpu.screen
import textual_wgpu.widget
import textual_wgpu.widgets as ww


def _top_modules(pkg_name: str) -> set[str]:
    pkg = importlib.import_module(pkg_name)
    names = {m.name for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".")}
    return {n.split(".")[1] for n in names if n.count(".") >= 1 and not n.split(".")[1].startswith("_")}


def _pub_methods(cls: type) -> set[str]:
    return {
        n
        for c in cls.__mro__
        for n, v in c.__dict__.items()
        if not n.startswith("_") and callable(v)
    }


def test_100pct_modules_importable():
    tm = _top_modules("textual")
    wm = _top_modules("textual_wgpu")
    assert tm <= wm, f"Missing modules: {sorted(tm - wm)}"
    for name in sorted(tm):
        mod = importlib.import_module(f"textual_wgpu.{name}")
        assert mod is not None


def test_100pct_widgets_and_containers():
    with open(textual.__path__[0] + "/widgets/__init__.py", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    t_widgets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    t_widgets = set(ast.literal_eval(node.value))
    w_widgets = set(ww.__all__)
    assert t_widgets <= w_widgets, f"Missing widgets: {sorted(t_widgets - w_widgets)}"

    t_cont = set(getattr(textual.containers, "__all__", ())) or {
        n for n in dir(textual.containers)
        if not n.startswith("_")
        and (n == "reactive" or inspect.isclass(getattr(textual.containers, n)))
        and n not in ("Widget", "Binding", "Layout", "GridLayout")
    }
    w_cont = set(textual_wgpu.containers.__all__)
    assert t_cont <= w_cont, f"Missing containers: {sorted(t_cont - w_cont)}"


def test_100pct_events_and_core_classes():
    t_ev = {n for n in dir(textual.events) if not n.startswith("_")}
    w_ev = {n for n in dir(textual_wgpu.events) if not n.startswith("_")}
    assert t_ev <= w_ev, f"Missing events symbols: {sorted(t_ev - w_ev)}"

    for label, tc, wc in (
        ("App", textual.app.App, textual_wgpu.app.App),
        ("Screen", textual.screen.Screen, textual_wgpu.screen.Screen),
        ("Widget", textual.widget.Widget, textual_wgpu.widget.Widget),
    ):
        miss = _pub_methods(tc) - _pub_methods(wc)
        assert not miss, f"{label} missing methods: {sorted(miss)}"


def test_new_modules_smoke():
    from textual_wgpu.cache import LRUCache
    from textual_wgpu.case import camel_to_snake
    from textual_wgpu.clock import MockClock
    from textual_wgpu.color import Color
    from textual_wgpu.coordinate import Coordinate
    from textual_wgpu.document import Document
    from textual_wgpu.eta import ETA
    from textual_wgpu.signal import Signal
    from textual_wgpu.walk import walk_breadth_first

    lru: LRUCache[str, int] = LRUCache(2)
    lru["a"] = 1
    lru["b"] = 2
    assert lru.get("a") == 1
    assert camel_to_snake("ButtonPressed") == "button_pressed"
    clk = MockClock(10.0)
    clk.advance(2.5)
    assert clk.get_time() == 12.5
    assert Color.parse("#D4AD72").hex == "#d4ad72"
    assert Coordinate(1, 2).down == Coordinate(2, 2)
    doc = Document("hello\nworld")
    assert doc.line_count == 2
    assert ETA().get_eta(0.5) is not None
    got: list[int] = []
    sig: Signal[int] = Signal(doc, "changed")
    sig.subscribe(doc, got.append)
    sig.publish(42)
    assert got == [42]
    assert list(walk_breadth_first(ww.Static("x")))


def test_top_level_log_css_path_default_mode_and_widget_apis(tmp_path):
    from textual_wgpu import log as tw_log
    from textual_wgpu.widgets.text_area import Selection as DocSelection, TextArea as TA

    tw_log("hello")
    assert TA is ww.TextArea and DocSelection is not None

    tcss = tmp_path / "app.tcss"
    tcss.write_text("#box { background: #112233; }\n", encoding="utf-8")

    class Home(textual_wgpu.Screen):
        def compose(self):
            with textual_wgpu.containers.Vertical(id="box"):
                yield ww.Button.success("OK", id="ok")
                yield ww.Rule.horizontal(id="r")
                yield ww.Select.from_values(["x", "y"], id="sel")
                yield ww.OptionList(ww.Option("One", id="o1"), ww.Option("Two", id="o2"), id="ol")
                yield ww.TabbedContent(
                    ww.TabPane("P1", ww.Static("s1"), id="p1"),
                    id="tc",
                )

    class AppWithDefaults(textual_wgpu.App):
        CSS_PATH = tcss
        MODES = {"home": Home}
        DEFAULT_MODE = "home"

    app = AppWithDefaults()
    scr = app.screen
    assert scr is not None and app.current_mode == "home"
    scr.apply_styles()
    box = scr.query_one("#box")
    assert box.styles.get("background") == "#112233"

    ok = scr.query_one("#ok", ww.Button)
    assert ok.screen is scr
    assert box in ok.ancestors and ok in ok.ancestors_with_self
    assert len(ok.siblings) == 4

    sel = scr.query_one("#sel", ww.Select)
    assert sel.is_blank()
    sel.set_value("x")
    assert not sel.is_blank()
    sel.clear()
    assert sel.is_blank()

    ol = scr.query_one("#ol", ww.OptionList)
    assert ol.get_option("o1").label == "One"
    ol.disable_option("o1")
    assert ol.get_option_at_index(0).disabled is True
    ol.enable_option("o1")
    assert ol.get_option_at_index(0).disabled is False

    tc = scr.query_one("#tc", ww.TabbedContent)
    tc.add_pane(ww.TabPane("P2", ww.Static("s2"), id="p2"))
    assert tc.get_pane("p2") is not None
    tc.disable_tab("p2")
    assert tc.get_tab("p2").disabled is True
    tc.remove_pane("p2")

    tr = ww.Tree("root")
    tr.add_json({"k": [1, 2]})
    tr.root.expand_all()
    assert tr.get_node_at_line(1) is not None

    ta = ww.TextArea("abcdef")
    ta.cursor = 3
    ta.action_delete_left_all()
    assert ta.text == "def"


def test_100pct_all_public_attrs_and_per_widget_methods():
    def all_pub(cls):
        return {n for c in cls.__mro__ for n in c.__dict__ if not n.startswith("_")}

    for label, tc, wc in (
        ("App", textual.app.App, textual_wgpu.app.App),
        ("Screen", textual.screen.Screen, textual_wgpu.screen.Screen),
        ("Widget", textual.widget.Widget, textual_wgpu.widget.Widget),
    ):
        miss = all_pub(tc) - all_pub(wc)
        assert not miss, f"{label} missing attrs/props: {sorted(miss)}"

    with open(textual.__path__[0] + "/widgets/__init__.py", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    t_widgets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    t_widgets = set(ast.literal_eval(node.value))

    tbase = {n for n in dir(textual.widget.Widget) if not n.startswith("_")}
    for name in sorted(t_widgets):
        tc_w = getattr(textual.widgets, name)
        wc_w = getattr(ww, name)
        tm_w = {n for n in dir(tc_w) if not n.startswith("_") and callable(getattr(tc_w, n, None))} - tbase
        wm_w = {n for n in dir(wc_w) if not n.startswith("_") and callable(getattr(wc_w, n, None))}
        miss = tm_w - wm_w
        assert not miss, f"{name} missing methods: {sorted(miss)}"
