"""Milestone 0: clear a green frame, quit with Q / Escape."""

from __future__ import annotations

from .app import App


class ClearDemo(App):
    TITLE = "textual-wgpu · clear"
    CLEAR = (0.05, 0.35, 0.18, 1.0)

    def on_mount(self) -> None:
        import sys
        print("textual-wgpu demo · Q / Esc to quit", file=sys.stderr, flush=True)


def main() -> None:
    ClearDemo().run()


if __name__ == "__main__":
    main()
