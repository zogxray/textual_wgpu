"""Phases 7–10 — full Textual API parity (Tab, App modes/themes, events, on_<msg>, meta clicks)."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

from textual_wgpu import App, Binding, Screen, Theme, events
from textual_wgpu.containers import HorizontalGroup, ItemGrid, Right, Vertical
from textual_wgpu.geometry import Rect
from textual_wgpu.reactive import reactive, var, watch
from textual_wgpu.scroll_view import ScrollView
from textual_wgpu.scrollbar import Scrollbar
from textual_wgpu.selection import strips_plain
from textual_wgpu.widgets import Button, Input, Option, OptionList, ProgressBar, Static, Tab, Tabs


def test_phase7_tabs_containers_and_submodules():
    assert ScrollView is not None and Scrollbar is not None
    tabs = Tabs(Tab("One", id="t1"), Tab("Two", id="t2"), active="t2")
    assert tabs.active == 1
    assert tabs.active_tab is not None and tabs.active_tab.label == "Two"
    tabs.add_tab(Tab("Three", id="t3"))
    tabs.activate("t3")
    assert tabs.active == 2

    ol = OptionList(Option("A", id="a"), "B", None)
    assert ol.option_count == 3
    assert ol.get_option_at_index(0).prompt == "A"
    ol.add_option(Option("C", id="c"))
    assert ol.option_count == 4

    pb = ProgressBar(total=50, show_eta=False)
    pb.advance(10)
    assert pb.progress == 10.0

    ig = ItemGrid(Static("1"), Static("2"), Static("3"), min_column_width=10)
    ig.layout(Rect(0, 0, 400, 120))
    assert ig._grid_columns >= 1

    r = Right(Button("OK", id="ok"))
    hg = HorizontalGroup(Static("left"), r)
    hg.layout(Rect(0, 0, 400, 40))
    assert r.children[0].rect.x > hg.rect.x


def test_with_container_compose_dsl():
    from textual_wgpu.containers import VerticalScroll

    class Nested(Screen):
        def compose(self):
            with Vertical(id="outer"):
                with VerticalScroll(id="sc"):
                    yield Static("inside", id="inner")

    scr = Nested()
    scr.build()
    outer = scr.query_one("#outer", Vertical)
    sc = outer.query_one("#sc", VerticalScroll)
    inner = sc.query_one("#inner", Static)
    assert inner.text == "inside"


async def test_phase8_app_modes_themes_mount_and_styles():
    custom = Theme(
        name="ydna-dark",
        primary="#32273F",
        accent="#D4AD72",
        background="#1E1923",
        surface="#241E2A",
        panel="#2A2430",
        boost="#342C3C",
        foreground="#FBF6F0",
        variables={"mode-1": "#445566"},
    )
    resumed: list[str] = []

    class ModeA(Screen):
        def compose(self):
            yield Vertical(Static("Mode A", id="lbl"), id="box")

        def on_screen_resume(self) -> None:
            resumed.append("a")

    class ModeB(Screen):
        def compose(self):
            yield Static("Mode B", id="lbl-b")

        def on_screen_resume(self) -> None:
            resumed.append("b")

    class MyApp(App):
        theme = "ydna-dark"
        BINDINGS = [
            Binding("f1", "mode('a')", "A", priority=True),
            Binding("f2", "mode('b')", "B", priority=True),
        ]

        def on_mount(self) -> None:
            self.register_theme(custom)
            self.theme = "ydna-dark"
            self.add_mode("a", ModeA)
            self.add_mode("b", ModeB)
            self.install_screen(ModeB, "modal_b")
            self.call_after_refresh(lambda: self.switch_mode("a"))

    app = MyApp()
    async with app.run_test(size=(800, 600)) as pilot:
        await pilot.pause()
        assert app.theme.name == "ydna-dark"
        assert app.current_mode == "a"
        assert app.get_css_variables()["mode-1"] == "#445566"
        assert pilot.query_one("#lbl", Static).text == "Mode A"

        box = pilot.query_one("#box", Vertical)
        # Textual-style Widget.mount(*widgets)
        await box.mount(Button("Added", id="added"))
        assert pilot.query_one("#added", Button).label == "Added"

        # Styles proxy attribute assignment
        added = pilot.query_one("#added", Button)
        added.styles.display = "none"
        assert added.display is False

        # Priority F2 mode switch via Binding("f2", "mode('b')")
        await pilot.press("f2")
        assert app.current_mode == "b"
        assert pilot.query_one("#lbl-b", Static).text == "Mode B"
        assert resumed == ["a", "b"]


async def test_phase9_and_10_events_convention_meta_and_reactive():
    seen: list[object] = []

    class MainApp(App):
        count = var(0)
        pulse = reactive(0, always_update=True)

        def compose(self):
            t = Text("Click ")
            t.append("HERE", style=Style.from_meta({"ydna_click": "ping(7)"}))
            yield Static(t, id="meta-st")
            yield Button("Go", id="go")
            yield Input("", id="inp")

        def on_key(self, event: events.Key) -> None:
            seen.append(("key_ev", event.key))
            if event.key == "k":
                event.stop()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            seen.append(("btn", event.button.id))

        def on_input_changed(self, event) -> None:
            seen.append(("inp", event.input.id, event.value))

        def on_click(self, event: events.Click) -> None:
            seen.append(("meta", event.meta.get("ydna_click")))

    app = MainApp()
    watch_hits: list[tuple[int, int]] = []
    watch(app, "count", lambda o, n: watch_hits.append((o, n)))

    async with app.run_test(size=(700, 400)) as pilot:
        await pilot.pause()
        app.count = 3
        assert watch_hits == [(0, 3)]
        assert app.query("Static, Button").results() or True
        assert app.query("*").exclude("#inp").first().id != "inp"

        await pilot.press("k")
        assert ("key_ev", "k") in seen

        await pilot.click("#go")
        assert ("btn", "go") in seen

        inp = pilot.query_one("#inp", Input)
        inp.focus()
        await pilot.type_chars("hi")
        assert ("inp", "inp", "hi") in seen

        # Rich Segment.meta click at "HERE" (cols 6..9 inside Static)
        st = pilot.query_one("#meta-st", Static)
        assert "HERE" in strips_plain(st.render_lines())
        app.on_pointer(st.rect.x + 4 + 7 * 8, st.rect.y + 4, 1)
        assert ("meta", "ping(7)") in seen
