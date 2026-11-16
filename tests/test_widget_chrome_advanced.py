"""Phases 4–6 — Chrome, Sparkline/Digits/MarkdownViewer/MaskedInput, Containers."""

from __future__ import annotations

from textual_wgpu import Binding, ModeApp, Screen, on
from textual_wgpu.containers import (
    Center,
    CenterMiddle,
    Container,
    HorizontalScroll,
    Middle,
    ScrollableContainer,
)
from textual_wgpu.selection import strips_plain
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import (
    Button,
    Digits,
    Footer,
    Header,
    HelpPanel,
    KeyPanel,
    MarkdownViewer,
    MaskedInput,
    Sparkline,
    Static,
)


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


async def test_chrome_header_footer_panels():
    ran: list[str] = []

    class FocusBtn(Button):
        BINDINGS = [Binding("x", "ping", "Ping")]

    class Body(Screen):
        BINDINGS = [Binding("r", "refresh_view", "Refresh")]
        TITLE = "Workspace"
        SUB_TITLE = "Phase 4"

        def compose(self):
            yield Header(show_clock=True, id="hdr")
            yield FocusBtn("Action", id="btn")
            yield HelpPanel(id="help")
            yield KeyPanel(id="keys")
            yield Footer(id="ftr")

        def action_refresh_view(self) -> None:
            ran.append("refresh")

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(900, 500)) as pilot:
        await pilot.pause()
        hdr = pilot.query_one("#hdr", Header)
        h_plain = strips_plain(hdr.render_lines())
        assert "Workspace" in h_plain and "Phase 4" in h_plain

        btn = pilot.query_one("#btn", FocusBtn)
        btn.focus()
        ftr = pilot.query_one("#ftr", Footer)
        f_plain = strips_plain(ftr.render_lines())
        assert "Ping" in f_plain and "Refresh" in f_plain and "palette" in f_plain

        hp = pilot.query_one("#help", HelpPanel)
        assert "Refresh" in strips_plain(hp.render_lines())
        kp = pilot.query_one("#keys", KeyPanel)
        assert "FocusBtn" in strips_plain(kp.render_lines())
        assert "Ping" in strips_plain(kp.render_lines())

        await pilot.press("r")
        assert ran == ["refresh"]


async def test_phase5_compound_widgets():
    changes: list[str] = []

    class Body(Screen):
        def compose(self):
            yield Sparkline([1, 4, 2, 8, 5], id="sp")
            yield Digits("42.5", id="dg")
            yield MaskedInput("00/00/0000", id="mi")
            long_tail = "\n\n".join(f"Paragraph {i}" for i in range(30))
            yield MarkdownViewer(f"# Intro\n\nHello\n\n## Details\n\n{long_tail}\n", id="mv")

        @on(MaskedInput.Changed, "#mi")
        def _mi_chg(self, event) -> None:
            changes.append(event.value)

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(900, 600)) as pilot:
        await pilot.pause()
        sp = pilot.query_one("#sp", Sparkline)
        sp_txt = strips_plain(sp.render_lines())
        assert any(ch in sp_txt for ch in "▁▂▃▄▅▆▇█")

        dg = pilot.query_one("#dg", Digits)
        dg_lines = dg.render_lines()
        assert len(dg_lines) == 3
        assert "┏" in dg_lines[0].plain()

        mi = pilot.query_one("#mi", MaskedInput)
        mi.focus()
        await pilot.type_chars("12a342026")  # 'a' rejected by digit mask
        assert mi.value == "12/34/2026"
        assert mi.is_valid is True
        await pilot.press("backspace")
        assert mi.value == "12/34/202"
        assert mi.is_valid is False
        assert "12/34/2026" in changes

        mv = pilot.query_one("#mv", MarkdownViewer)
        assert len(mv.table_of_contents.root.children) == 1
        assert len(mv.table_of_contents.root.children[0].children) == 1
        assert mv.table_of_contents.root.children[0].is_expanded is True
        assert mv.goto_anchor("details") is True
        # Smooth sub-line pixel scroll via wheel & scroll_pixels
        sc = mv._scroll
        sc.scroll_to(y=0)
        sc.scroll_pixels(5.0)
        assert 0.0 < sc.scroll_py < sc.LINE_H
        mv.update("# Only One\n\nBody")
        assert len(mv.table_of_contents.root.children) == 1
        assert len(mv.table_of_contents.root.children[0].children) == 0


async def test_phase6_containers_and_alignment():
    class Body(Screen):
        def compose(self):
            with_c = Container(
                CenterMiddle(Button("Centered", id="cbtn"), id="cm"),
                id="wrap",
            )
            yield with_c
            yield Center(Static("mid-x", id="sx"), id="ctr")
            yield Middle(Static("mid-y", id="sy"), id="mid")
            yield ScrollableContainer(
                Static("line1"),
                Static("line2"),
                id="sc",
            )
            yield HorizontalScroll(Static("A" * 160, id="wide"), id="hs")

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(800, 600)) as pilot:
        await pilot.pause()
        cm = pilot.query_one("#cm", CenterMiddle)
        cbtn = pilot.query_one("#cbtn", Button)
        # Centered button lies strictly inside CenterMiddle bounds
        assert cbtn.rect.x > cm.rect.x
        assert cbtn.rect.y > cm.rect.y

        hs = pilot.query_one("#hs", HorizontalScroll)
        hs.focus()
        assert hs.scroll_x == 0
        await pilot.press("right")
        assert hs.scroll_x > 0
        await pilot.press("home")
        assert hs.scroll_x == 0

        sc = pilot.query_one("#sc", ScrollableContainer)
        assert len(sc._raw_children) == 2
