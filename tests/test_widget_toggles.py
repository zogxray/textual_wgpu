"""Phase 2 — Toggle family: Checkbox, RadioButton, RadioSet."""

from __future__ import annotations

from textual_wgpu import ModeApp, Screen, on
from textual_wgpu.selection import strips_plain
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Checkbox, RadioButton, RadioSet


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


async def test_checkbox_and_radiobutton_pilot():
    events_log: list[tuple[str, bool]] = []

    class Body(Screen):
        def compose(self):
            yield Checkbox("Accept", False, id="cb")
            yield Checkbox("Disabled", False, id="cb-dis", disabled=True)
            yield RadioButton("Solo", False, id="rb")

        @on(Checkbox.Changed, "#cb")
        def _cb(self, event: Checkbox.Changed) -> None:
            events_log.append(("cb", event.value))

        @on(RadioButton.Changed, "#rb")
        def _rb(self, event: RadioButton.Changed) -> None:
            events_log.append(("rb", event.value))

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(600, 400)) as pilot:
        await pilot.pause()
        cb = pilot.query_one("#cb", Checkbox)
        assert "[ ]" in strips_plain(cb.render_lines())
        await pilot.click("#cb")
        assert cb.value is True
        assert "[✓]" in strips_plain(cb.render_lines())
        await pilot.press("space")
        assert cb.value is False

        cb_dis = pilot.query_one("#cb-dis", Checkbox)
        cb_dis.toggle()
        assert cb_dis.value is False

        rb = pilot.query_one("#rb", RadioButton)
        await pilot.click("#rb")
        assert rb.value is True
        assert "(●)" in strips_plain(rb.render_lines())
        assert events_log == [("cb", True), ("cb", False), ("rb", True)]


async def test_radio_set_exclusivity_and_keys():
    seen: list[tuple[int, str]] = []

    class Body(Screen):
        def compose(self):
            yield RadioSet("First", "Second", RadioButton("Third", True), id="rs")

        @on(RadioSet.Changed, "#rs")
        def _changed(self, event: RadioSet.Changed) -> None:
            seen.append((event.index, event.pressed.label))

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(600, 400)) as pilot:
        await pilot.pause()
        rs = pilot.query_one("#rs", RadioSet)
        assert rs.pressed_index == 2
        assert rs.pressed_button is not None and rs.pressed_button.label == "Third"

        rs.focus()
        await pilot.press("down")
        assert rs.pressed_index == 0
        await pilot.press("down")
        assert rs.pressed_index == 1
        await pilot.press("up")
        assert rs.pressed_index == 0
        assert seen == [(0, "First"), (1, "Second"), (0, "First")]
