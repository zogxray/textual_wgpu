"""100% textual.events parity: symbol check, field check, and live emission."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

te = pytest.importorskip("textual.events")
import textual_wgpu.events as we
from textual_wgpu import App, Screen
from textual_wgpu.message import Message
from textual_wgpu.widgets import Button, Input, MarkdownViewer


def test_events_100pct_symbol_and_field_parity():
    t_all = {n for n in dir(te) if not n.startswith("_")}
    w_all = {n for n in dir(we) if not n.startswith("_")}
    assert t_all - w_all == set(), f"Missing symbols: {t_all - w_all}"

    t_cls = {n: getattr(te, n) for n in t_all if inspect.isclass(getattr(te, n))}
    w_cls = {n: getattr(we, n) for n in w_all if inspect.isclass(getattr(we, n))}
    assert set(t_cls) <= set(w_cls)

    for name, tc in t_cls.items():
        wc = w_cls[name]
        if not issubclass(tc, te.Event):
            continue
        assert issubclass(wc, Message), f"{name} must subclass Message"
        try:
            tsig = set(inspect.signature(tc.__init__).parameters) - {"self"}
        except Exception:
            tsig = set()
        try:
            wsig = set(inspect.signature(wc.__init__).parameters) - {"self"}
        except Exception:
            wsig = set()
        assert not (tsig - wsig), f"{name} missing init params: {tsig - wsig}"

    # Verify helper methods/properties on Key, MouseEvent, Resize
    k = we.Key(key="a")
    assert "a" in k.name_aliases and k.copy().key == "a"
    m = we.Click(x=10, y=20, meta={"x": 1})
    assert m.offset == we.Offset(10, 20)
    assert m.pointer_x == 10.0 and m.meta == {"x": 1}
    r = we.Resize.from_dimensions((80, 24), (800, 480))
    assert r.size == we.Size(80, 24) and r.pixel_size == we.Size(800, 480)


async def test_all_new_runtime_events_fire_and_phase17_apis(tmp_path: Path):
    seen: set[str] = set()

    class SubScreen(Screen):
        def compose(self):
            yield Button("Close", id="close")

        def on_screen_resume(self, _ev: we.ScreenResume) -> None:
            seen.add("ScreenResume")

        def on_screen_suspend(self, _ev: we.ScreenSuspend) -> None:
            seen.add("ScreenSuspend")

    class MyApp(App):
        def compose(self):
            yield Input("", id="inp")
            yield Button("Go", id="btn")
            yield MarkdownViewer("# H1\n", id="mv")

        def on_compose(self, _ev: we.Compose) -> None:
            seen.add("Compose")

        def on_ready(self, _ev: we.Ready) -> None:
            seen.add("Ready")

        def on_idle(self, _ev: we.Idle) -> None:
            seen.add("Idle")

        def on_action(self, _ev: we.Action) -> None:
            seen.add("Action")

        def on_app_focus(self, _ev: we.AppFocus) -> None:
            seen.add("AppFocus")

        def on_app_blur(self, _ev: we.AppBlur) -> None:
            seen.add("AppBlur")

        def on_print(self, _ev: we.Print) -> None:
            seen.add("Print")

        def on_timer(self, _ev: we.Timer) -> None:
            seen.add("Timer")

        def on_delivery_complete(self, _ev: we.DeliveryComplete) -> None:
            seen.add("DeliveryComplete")

        def action_ping(self) -> None:
            seen.add("ping")

    app = MyApp()
    async with app.run_test(size=(700, 400)) as pilot:
        await pilot.pause()
        inp = pilot.query_one("#inp", Input)

        # Hook CursorPosition & Paste & Load
        orig_cp = inp.on_cursor_position if hasattr(inp, "on_cursor_position") else None
        _ = orig_cp
        inp.on_cursor_position = lambda ev: seen.add(f"CursorPosition:{ev.x}")  # type: ignore[attr-defined]
        inp.on_paste = lambda ev: seen.add(f"Paste:{ev.text}")  # type: ignore[attr-defined]
        inp.focus()
        app.copy_to_clipboard("abc")
        inp.paste_clipboard()

        # ScreenResume / ScreenSuspend
        sub = SubScreen()
        app.push_screen(sub)
        app.pop_screen()

        # AppFocus / AppBlur
        app._emit_app_focus(True)
        app._emit_app_focus(False)

        # Action
        app.run_action("ping")

        # Timer
        app.set_timer(0.0, lambda: None)
        await pilot.pause()

        # Print capture
        app.begin_capture_print(app)
        print("captured-line")
        app.end_capture_print(app)

        # Screenshot + DeliveryComplete
        svg = app.export_screenshot()
        assert "<svg" in svg
        png = app.save_screenshot("shot.png", path=str(tmp_path))
        assert Path(png).exists() and Path(png).stat().st_size > 100
        out_txt = app.deliver_text("hello", save_path=tmp_path / "out.txt", key="k1")
        assert out_txt is not None and out_txt.read_text() == "hello"

    assert "Ready" in seen
    assert "Idle" in seen
    assert "Action" in seen and "ping" in seen
    assert "AppFocus" in seen and "AppBlur" in seen
    assert "ScreenResume" in seen and "ScreenSuspend" in seen
    assert "Paste:abc" in seen
    assert any(x.startswith("CursorPosition:") for x in seen)
    assert "Print" in seen
    assert "Timer" in seen
    assert "DeliveryComplete" in seen
