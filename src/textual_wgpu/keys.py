"""Keyboard helpers (layout aliases + Textual key names)."""

from __future__ import annotations

# QWERTY → Russian ЙЦУКЕН (same physical keys).
_EN_TO_RU = dict(zip("qwertyuiopasdfghjklzxcvbnm", "йцукенгшщзфывапролдячсмить"))
_RU_TO_EN = {ru: en for en, ru in _EN_TO_RU.items()}

_CHAR_KEY_ALIASES: dict[str, str] = {
    "/": "slash",
    "?": "question_mark",
    ".": "full_stop",
    ",": "comma",
    "-": "minus",
    "=": "equals",
    " ": "space",
}


def normalize_key(key: str) -> str:
    """Lowercase; map punctuation & Russian letters to Textual/QWERTY names."""
    k = (key or "").lower()
    if k in _CHAR_KEY_ALIASES:
        return _CHAR_KEY_ALIASES[k]
    if len(k) == 1 and k in _RU_TO_EN:
        return _RU_TO_EN[k]
    if k.startswith("shift+") and len(k) == 7:
        ch = k[-1]
        if ch in _RU_TO_EN:
            return f"shift+{_RU_TO_EN[ch]}"
    return k
