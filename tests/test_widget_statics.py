"""Phase 0 (App.notify / open_url / Log) + Phase 1 static widgets."""

from __future__ import annotations

import webbrowser

from textual_wgpu import App, ModeApp, Screen
from textual_wgpu.geometry import Rect
from textual_wgpu.selection import strips_plain
from textual_wgpu.theme import Theme
from textual_wgpu.widgets import Link, Log, Placeholder, Pretty, Rule, Welcome


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


def test_app_notify_and_open_url(monkeypatch):
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(webbrowser, "open", lambda u, new=0: opened.append((u, new)) or True)

    app = App(theme=_theme())
    app.notify("Hello", title="Info", severity="warning", timeout=2.0)
    assert app._toast is not None and app._toast.visible
    assert "Info: Hello" in app._toast.message
    assert app._notifications[-1]["severity"] == "warning"

    app.clear_notifications()
    assert not app._toast.visible

    assert app.open_url("https://example.org") is True
    assert app._last_opened_url == "https://example.org"
    assert opened == [("https://example.org", 2)]


async def test_phase1_static_widgets_pilot(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda u, new=0: opened.append(u) or True)

    class Body(Screen):
        def compose(self):
            yield Rule(line_style="heavy", id="rule-h")
            yield Rule(orientation="vertical", line_style="double", id="rule-v")
            yield Placeholder("Demo", id="ph")
            yield Pretty({"alpha": [1, 2, 3]}, id="pr")
            yield Link("Open Docs", url="https://textual.textualize.io", id="lnk")
            yield Welcome("# Hi", id="wel")
            yield Log(id="log")

    app = ModeApp([("main", "Main", Body)], theme=_theme(), start="main")
    async with app.run_test(size=(800, 600)) as pilot:
        await pilot.pause()
        rh = pilot.query_one("#rule-h", Rule)
        rv = pilot.query_one("#rule-v", Rule)
        assert "━" in strips_plain(rh.render_lines())
        rv.layout(Rect(0, 0, 20, 60))
        assert "║" in strips_plain(rv.render_lines())

        ph = pilot.query_one("#ph", Placeholder)
        assert "Demo" in strips_plain(ph.render_lines())
        ph.cycle_variant()
        assert ph.variant == "size"
        ph.cycle_variant()
        assert "Lorem ipsum" in strips_plain(ph.render_lines())

        pr = pilot.query_one("#pr", Pretty)
        assert "alpha" in strips_plain(pr.render_lines())
        pr.update([10, 20])
        assert "20" in strips_plain(pr.render_lines())

        lnk = pilot.query_one("#lnk", Link)
        assert "Open Docs" in strips_plain(lnk.render_lines())
        assert all(seg.underline for seg in lnk.render_lines()[0].segments)
        lnk.focus()
        await pilot.press("enter")
        assert opened == ["https://textual.textualize.io"]

        wel = pilot.query_one("#wel", Welcome)
        assert len(wel.children) == 2
        log = pilot.query_one("#log", Log)
        log.write_line("[bold]ready[/bold]", markup=True)
        assert "ready" in strips_plain(log.render_lines())
