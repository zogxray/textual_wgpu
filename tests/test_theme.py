"""Theme is supplied by app / system — not a baked-in brand."""

from textual_wgpu.theme import Theme


def test_system_theme_has_tokens():
    t = Theme.system()
    assert t.background.startswith("#")
    assert t.accent.startswith("#")
    r, g, b, a = t.rgba("foreground")
    assert a == 255
    assert 0 <= r <= 255


def test_from_mapping_overrides():
    t = Theme.from_mapping({
        "background": "#1E1923",
        "accent": "#D4AD72",
        "foreground": "#FBF6F0",
        "muted": "#AEA5B8",
        "surface": "#29242F",
        "panel": "#342E3C",
        "boost": "#534A5E",
        "primary": "#523B6D",
    })
    assert t.background == "#1E1923"
    assert t.accent == "#D4AD72"
    assert t.rgba("accent")[:3] == (0xD4, 0xAD, 0x72)


def test_shell_uses_injected_theme():
    from textual_wgpu.shell import ShellApp

    theme = Theme.from_mapping({
        "background": "#112233",
        "boost": "#445566",
        "accent": "#AABBCC",
        "panel": "#223344",
    })
    app = ShellApp(theme=theme)
    assert app.theme.background == "#112233"
    img = app._paint()
    # ModeBar uses boost; body panel below the bar.
    assert img.getpixel((0, 0))[:3] == (0x44, 0x55, 0x66)
    assert img.getpixel((20, app.BAR_H + 10))[:3] == (0x22, 0x33, 0x44)
