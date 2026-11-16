"""Phases 11–14 — live event emission, Widget.compose, modules, deep widget parity."""

from __future__ import annotations

from textual_wgpu import App, Binding, Screen, events
from textual_wgpu.command import SimpleProvider
from textual_wgpu.containers import Vertical
from textual_wgpu.fuzzy import Matcher
from textual_wgpu.notifications import Notification, Notifications
from textual_wgpu.selection import strips_plain
from textual_wgpu.suggester import SuggestFromList
from textual_wgpu.timer import Timer
from textual_wgpu.validation import Function, Integer, Length, Number, Regex
from textual_wgpu.widgets import Button, DataTable, Input, Static, TextArea, Tree
from textual_wgpu.widget import Widget


class CompositeCard(Widget):
    def __init__(self, title: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.title = title
        self._children: list[Widget] = []

    @property
    def children(self) -> list[Widget]:
        return self._children

    def compose(self):
        yield Static(self.title, id="card-title")
        yield Button("Press", id="card-btn")


async def test_phase11_events_and_widget_compose():
    log: list[str] = []

    class CardBox(Vertical):
        def on_descendant_focus(self, event: events.DescendantFocus) -> None:
            log.append(f"dfocus:{event.widget.id}")

        def on_descendant_blur(self, event: events.DescendantBlur) -> None:
            log.append(f"dblur:{event.widget.id}")

    class MyBtn(Button):
        def on_mount(self, _ev: events.Mount) -> None:
            log.append(f"mount:{self.id}")

        def on_focus(self, _ev: events.Focus) -> None:
            log.append(f"focus:{self.id}")

        def on_blur(self, _ev: events.Blur) -> None:
            log.append(f"blur:{self.id}")

        def on_enter(self, _ev: events.Enter) -> None:
            log.append(f"enter:{self.id}")

        def on_leave(self, _ev: events.Leave) -> None:
            log.append(f"leave:{self.id}")

        def on_mouse_down(self, _ev: events.MouseDown) -> None:
            log.append(f"down:{self.id}")

        def on_mouse_up(self, _ev: events.MouseUp) -> None:
            log.append(f"up:{self.id}")

        def on_resize(self, ev: events.Resize) -> None:
            log.append(f"resize:{self.id}:{ev.size.width}")

    class MyApp(App):
        def compose(self):
            with CardBox(id="box"):
                yield CompositeCard("Hello", id="card")
                yield MyBtn("A", id="ba")
                yield MyBtn("B", id="bb")

    app = MyApp()
    async with app.run_test(size=(700, 400)) as pilot:
        await pilot.pause()
        card = pilot.query_one("#card", CompositeCard)
        assert card.query_one("#card-title", Static).text == "Hello"
        card.title = "Updated"
        card.recompose()
        assert card.query_one("#card-title", Static).text == "Updated"

        assert "mount:ba" in log
        assert any(x.startswith("resize:ba:") for x in log)

        ba = pilot.query_one("#ba", MyBtn)
        bb = pilot.query_one("#bb", MyBtn)
        ba.focus()
        bb.focus()
        assert "focus:ba" in log and "blur:ba" in log and "focus:bb" in log
        assert "dfocus:ba" in log and "dblur:ba" in log

        # Hover Enter/Leave + MouseDown/Up
        app.on_pointer_move(ba.rect.x + 2, ba.rect.y + 2)
        app.on_pointer_move(bb.rect.x + 2, bb.rect.y + 2)
        assert "enter:ba" in log and "leave:ba" in log and "enter:bb" in log
        await pilot.click("#ba")
        app.on_pointer_up(ba.rect.x + 2, ba.rect.y + 2, 1)
        assert "down:ba" in log and "up:ba" in log

        # Show/Hide events
        ba.display = False
        ba.display = True


async def test_phase12_check_action_services_and_builtins():
    ran: list[str] = []

    class MyApp(App):
        BINDINGS = [
            Binding("x", "guarded", "Guarded"),
            Binding("h", "show_help_panel", "Help"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.allow_guarded = False

        def compose(self):
            yield Button("Target", id="t").with_tooltip("Hint!")

        def check_action(self, action: str, parameters) -> bool | None:
            if action == "guarded":
                return self.allow_guarded
            return True

        def action_guarded(self) -> None:
            ran.append("guarded")

    app = MyApp()
    async with app.run_test(size=(700, 400)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        assert ran == []
        app.allow_guarded = True
        await pilot.press("x")
        assert ran == ["guarded"]

        t = pilot.query_one("#t", Button)
        assert t.tooltip == "Hint!"
        t.set_loading(True)
        assert t.loading is True
        t.set_loading(False)

        app.action_toggle("#t")
        assert t.display is False
        app.action_toggle("#t")
        assert t.display is True

        app.action_show_help_panel()
        assert len(app.query("HelpPanel")) == 1
        app.action_hide_help_panel()
        assert app.query("HelpPanel").first().display is False

        was_dark = app.theme.dark
        app.action_toggle_dark()
        assert app.theme.dark is not was_dark


async def test_phase13_and_14_modules_and_deep_widgets():
    assert Number(0, 10).validate("5.5").is_valid
    assert not Integer(0, 10).validate("3.5").is_valid
    assert Length(2, 4).validate("abc").is_valid
    assert Regex(r"rs\d+").validate("rs123").is_valid
    assert Function(lambda s: s == "ok").validate("ok").is_valid

    sug = SuggestFromList(["alpha", "beta"])
    assert await sug.get_suggestion("al") == "alpha"

    m = Matcher("dt")
    assert m.match("DataTable") > 0
    assert "DataTable" in m.highlight("DataTable").plain

    prov = SimpleProvider(None, [("ping", lambda: None, "Ping")])
    hits = [h async for h in prov.search("pg")]
    assert len(hits) == 1

    tm = Timer(1.0)
    tm.pause()
    assert tm.paused
    tm.resume()
    tm.stop()
    assert not tm.active

    notifs = Notifications()
    notifs.add(Notification("hi"))
    assert len(notifs) == 1

    inp = Input("rs42", validators=[Regex(r"rs\d+")], suggester=sug)
    assert inp.is_valid
    inp.value = "bad"
    assert not inp.is_valid

    dt = DataTable()
    c1 = dt.add_column("Name", key="name")
    c2 = dt.add_column("Score", key="score")
    r1 = dt.add_row("Zed", 10, key="r1")
    r2 = dt.add_row("Ada", 99, key="r2")
    assert dt.coordinate_to_cell_key((0, 0)) == ("r1", "name")
    dt.update_cell(r1, c2, 50)
    assert dt.get_cell(0, 1) == 50
    dt.sort(c1)
    assert dt.get_cell(0, 0) == "Ada"
    dt.remove_row(r2)
    assert dt.row_count == 1

    tree = Tree("root")
    n1 = tree.root.add("One")
    n1.set_label("OneRenamed")
    assert n1.label == "OneRenamed"
    n1.remove()
    assert len(tree.root.children) == 0

    ta = TextArea("line one\nline two", show_line_numbers=True)
    lines = strips_plain(ta.render_lines()).splitlines()
    assert lines[0].strip().startswith("1") and lines[1].strip().startswith("2")
