"""Standalone ``python -m textual_wgpu`` demo builds, paints, and navigates all modes."""

from __future__ import annotations

from PIL import Image, ImageDraw
from rich.style import Style
from rich.text import Text

from textual_wgpu.demo import DEMO_LABELS, build_demo_app
from textual_wgpu.geometry import Rect
from textual_wgpu.widgets import DataTable, Digits, MarkdownViewer, OptionList, RichText


def test_demo_all_modes_layout_and_paint():
    app = build_demo_app()
    app._pixel_size = (1100, 720)
    assert app.TITLE == "textual-wgpu · demo"
    assert len(DEMO_LABELS) == 10

    for idx, (key, _title) in enumerate(DEMO_LABELS):
        app.set_mode(idx)
        assert app.mode_key == key
        screen = app._ensure_screen()
        screen.layout(Rect(0, 0, 1100, 720))
        img = Image.new("RGBA", (1100, 720))
        screen.paint(ImageDraw.Draw(img))
        assert img.getpixel((20, 20)) != (0, 0, 0, 0)
        assert app._focusables(), f"mode {key} has no focusable widgets"


def test_demo_interactive_controls_and_themes():
    app = build_demo_app()
    app._pixel_size = (1100, 720)

    # F1 Overview has MarkdownViewer with TOC
    screen1 = app._ensure_screen()
    screen1.layout(Rect(0, 0, 1100, 720))
    mv = screen1.query_one("#overview-viewer", MarkdownViewer)
    assert mv.show_table_of_contents is True

    # F2 Controls progress bump updates Digits
    app.set_mode(1)
    screen2 = app._ensure_screen()
    screen2.layout(Rect(0, 0, 1100, 720))
    screen2._bump_progress()
    assert screen2.query_one("#digits-pct", Digits).value == "74%"

    # F3 Data & Tree has populated DataTable & OptionList
    app.set_mode(2)
    screen3 = app._ensure_screen()
    screen3.layout(Rect(0, 0, 1100, 720))
    assert screen3.query_one("#mod-list", OptionList).option_count == 5
    assert len(screen3.query_one("#api-table", DataTable)._rows) == 5

    # F5 Themes switches theme cleanly
    app.set_mode(4)
    screen5 = app._ensure_screen()
    screen5.layout(Rect(0, 0, 1100, 720))
    screen5._apply_theme("amber-crt")
    assert app.theme.name == "amber-crt"


def test_richtext_custom_click_meta_keys():
    clicked: list[str] = []
    t = Text()
    t.append("Ping", style=Style.from_meta({"custom_click": "do_ping"}))
    rt = RichText(t, click_meta_keys=("custom_click", "@click"))
    rt.rect = Rect(0, 0, 300, 40)
    rt.on_action = clicked.append
    hits = rt.hits()
    assert hits
    hits[0][1](hits[0][0].x + 2, hits[0][0].y + 2)
    assert clicked == ["do_ping"]


async def test_demo_pilot_navigation_and_modals():
    app = build_demo_app()
    async with app.run_test(size=(1100, 720)) as pilot:
        await pilot.pause()
        assert app.mode_key == "overview"

        await pilot.press("tab")
        assert getattr(app._focus, "id", None) == "md-toc"
        await pilot.press("tab")
        assert getattr(app._focus, "id", None) == "md-scroll"

        await pilot.press("f2")
        await pilot.pause()
        assert app.mode_key == "controls"

        await pilot.press("f3")
        await pilot.pause()
        assert app.mode_key == "data"

        await pilot.press("f4")
        await pilot.pause()
        assert app.mode_key == "editor"
        app.screen._open_prompt()
        await pilot.pause()
        assert app._prompt is not None
        await pilot.press("escape")
        await pilot.pause()
        assert app._prompt is None

        await pilot.press("f5")
        await pilot.pause()
        assert app.mode_key == "themes"
        await pilot.press("b")
        await pilot.pause()
        assert app.screen._bloom == 0.45

        await pilot.press("f7")
        await pilot.pause()
        assert app.mode_key == "media"
        app.screen.set_photo("spectrum")
        rt = pilot.query_one("#media-photo", RichText)
        assert any(seg.meta and "@image" in seg.meta for st in rt.render_lines() for seg in st.segments)
        app.screen.toggle_audio(play_os=False)
        assert app.screen._audio_playing is True
        app.screen._media_tick()
        assert app.screen._audio_elapsed > 0
        app.screen.toggle_audio(play_os=False)
        assert app.screen._audio_playing is False

