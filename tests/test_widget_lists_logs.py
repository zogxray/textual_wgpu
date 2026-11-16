"""Phase 3 — SelectionList, ListView (+ListItem), RichLog."""

from __future__ import annotations

from rich.table import Table

from textual_wgpu import ModeApp, Screen, on
from textual_wgpu.selection import strips_plain
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import ListItem, ListView, RichLog, Selection, SelectionList


def _theme() -> Theme:
    return Theme.from_mapping({
        "background": "#1E1923",
        "foreground": "#F5F0E8",
        "accent": "#D4AD72",
        "muted": "#9A9088",
        "panel": "#2A2430",
        "boost": "#342C3C",
        "surface": "#241E2A",
        "primary": "#D4AD72",
    })


async def test_selection_list_pilot():
    toggled: list[int] = []
    changed_snapshots: list[list[str]] = []

    class Body(Screen):
        def compose(self):
            yield SelectionList(
                ("Alpha", "a", True),
                Selection("Beta", "b", False),
                ("Gamma", "g", False),
                id="sl",
            )

        @on(SelectionList.SelectionToggled, "#sl")
        def _tog(self, event: SelectionList.SelectionToggled) -> None:
            toggled.append(event.index)

        @on(SelectionList.SelectedChanged, "#sl")
        def _chg(self, event: SelectionList.SelectedChanged) -> None:
            changed_snapshots.append(list(event.selection_list.selected))

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(600, 400)) as pilot:
        await pilot.pause()
        sl = pilot.query_one("#sl", SelectionList)
        assert sl.selected == ["a"]
        lines = strips_plain(sl.render_lines())
        assert "[✓] Alpha" in lines and "[ ] Beta" in lines

        sl.focus()
        await pilot.press("down", "space")
        assert sl.selected == ["a", "b"]
        sl.select_all()
        assert sl.selected == ["a", "b", "g"]
        sl.deselect("a")
        assert sl.selected == ["b", "g"]
        sl.deselect_all()
        await pilot.pause()
        assert sl.selected == []
        assert toggled == [1, 0]
        assert len(changed_snapshots) >= 3


async def test_list_view_pilot():
    events: list[tuple[str, int]] = []

    class Body(Screen):
        def compose(self):
            yield ListView(
                ListItem("Row 0", id="r0"),
                ListItem("Row 1", id="r1"),
                ListItem("Row 2", id="r2", disabled=True),
                ListItem("Row 3", id="r3"),
                id="lv",
            )

        @on(ListView.Highlighted, "#lv")
        def _hi(self, event: ListView.Highlighted) -> None:
            if event.item is not None:
                events.append(("hi", event.list_view.index or 0))

        @on(ListView.Selected, "#lv")
        def _sel(self, event: ListView.Selected) -> None:
            events.append(("sel", event.index))

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(600, 400)) as pilot:
        await pilot.pause()
        lv = pilot.query_one("#lv", ListView)
        assert lv.index == 0
        lv.focus()
        await pilot.press("down")
        assert lv.index == 1
        # Skips disabled Row 2 -> lands on Row 3
        await pilot.press("down")
        assert lv.index == 3
        await pilot.press("enter")
        assert ("sel", 3) in events
        lv.append(ListItem("Row 4", id="r4"))
        assert len(lv._items) == 5
        assert "Row 4" in strips_plain(lv.render_lines())


async def test_rich_log_wrap_and_renderables():
    class Body(Screen):
        def compose(self):
            yield RichLog(wrap=True, markup=True, id="rlog")

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(400, 300)) as pilot:
        await pilot.pause()
        rl = pilot.query_one("#rlog", RichLog)
        rl.write("[bold]Header[/bold]")
        tbl = Table(title="Stats")
        tbl.add_column("K")
        tbl.add_column("V")
        tbl.add_row("hits", "42")
        rl.write(tbl)
        plain = strips_plain(rl.render_lines())
        assert "Header" in plain
        assert "hits" in plain and "42" in plain
        rl.clear()
        assert rl.render_lines() == []
