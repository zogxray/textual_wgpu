"""MaskedInput — template-masked single-line input (Textual MaskedInput subset)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .input import Input, ValidationResult


@dataclass(frozen=True)
class _Slot:
    kind: str  # "literal" | "digit" | "digit_sign" | "alpha" | "alnum" | "nonspace" | "any"
    char: str = ""
    case: str = ""  # "" | "upper" | "lower"

    def accepts(self, ch: str) -> str | None:
        if self.kind == "literal":
            return ch if ch == self.char else None
        if not ch or len(ch) != 1:
            return None
        if self.kind == "digit":
            return ch if ch.isdigit() else None
        if self.kind == "digit_sign":
            return ch if (ch.isdigit() or ch in "+- ") else None
        if self.kind == "alpha":
            if not ch.isalpha():
                return None
        elif self.kind == "alnum":
            if not ch.isalnum():
                return None
        elif self.kind == "nonspace":
            if ch.isspace() or not ch.isprintable():
                return None
        elif self.kind == "any":
            if not ch.isprintable():
                return None
        if self.case == "upper":
            return ch.upper()
        if self.case == "lower":
            return ch.lower()
        return ch


def _compile_template(template: str) -> list[_Slot]:
    slots: list[_Slot] = []
    case = ""
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]
        if ch == "\\" and i + 1 < n:
            slots.append(_Slot("literal", template[i + 1]))
            i += 2
            continue
        if ch == ">":
            case = "upper"
            i += 1
            continue
        if ch == "<":
            case = "lower"
            i += 1
            continue
        if ch in ("0", "9"):
            slots.append(_Slot("digit", case=case))
        elif ch == "#":
            slots.append(_Slot("digit_sign", case=case))
        elif ch in ("A", "a"):
            slots.append(_Slot("alpha", case=case))
        elif ch in ("N", "n"):
            slots.append(_Slot("alnum", case=case))
        elif ch == "X":
            slots.append(_Slot("nonspace", case=case))
        elif ch == "x":
            slots.append(_Slot("any", case=case))
        else:
            slots.append(_Slot("literal", ch))
        i += 1
    return slots


class MaskedInput(Input):
    """Single-line input constrained by a mask ``template`` (e.g. ``00/00/0000``)."""

    def __init__(
        self,
        template: str,
        value: str | None = None,
        placeholder: str = "",
        *,
        validators: Callable[[str], Any] | Iterable[Callable[[str], Any]] | None = None,
        validate_on: Iterable[str] | None = None,
        valid_empty: bool = False,
        select_on_focus: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: str | None = None,
        compact: bool = False,
    ) -> None:
        self.template = str(template)
        self.validate_on = tuple(validate_on) if validate_on is not None else ("changed", "submitted")
        self._slots = _compile_template(self.template)
        super().__init__(
            "",
            id=id,
            placeholder=placeholder or self.template_placeholder(),
            validators=validators,
            valid_empty=valid_empty,
            select_on_focus=select_on_focus,
            name=name,
            classes=classes,
            disabled=disabled,
            tooltip=tooltip,
            compact=compact,
        )
        if value is not None:
            self.load_text(str(value))

    def template_placeholder(self) -> str:
        return "".join(s.char if s.kind == "literal" else "_" for s in self._slots)

    def _first_editable(self, start: int = 0) -> int:
        i = max(0, start)
        while i < len(self._slots) and self._slots[i].kind == "literal":
            i += 1
        return i

    def _prev_editable(self, before: int) -> int | None:
        i = min(len(self._slots) - 1, before - 1)
        while i >= 0:
            if self._slots[i].kind != "literal":
                return i
            i -= 1
        return None

    def _apply_masked(self, raw: str) -> str:
        if not raw:
            return ""
        out: list[str] = []
        ri = 0
        for slot in self._slots:
            if ri >= len(raw):
                break
            if slot.kind == "literal":
                out.append(slot.char)
                if raw[ri] == slot.char:
                    ri += 1
                continue
            accepted: str | None = None
            while ri < len(raw):
                accepted = slot.accepts(raw[ri])
                ri += 1
                if accepted is not None:
                    break
            if accepted is None:
                break
            out.append(accepted)
        return "".join(out)

    def load_text(self, text: str) -> None:
        masked = self._apply_masked(text)
        super().load_text(masked)
        self.cursor = min(len(self._text), self._first_editable(len(self._text)))

    def _emit_change(self) -> None:
        if self.on_change is not None:
            self.on_change(self._text)
        msg = self.Changed(self._text)
        msg.input = self  # type: ignore[attr-defined]
        msg.validation_result = self.validate(self._text)  # type: ignore[attr-defined]
        self.post_message(msg)

    def _emit_submit(self) -> None:
        if self.on_submit is not None:
            self.on_submit()
        msg = self.Submitted(self._text)
        msg.input = self  # type: ignore[attr-defined]
        msg.validation_result = self.validate(self._text)  # type: ignore[attr-defined]
        self.post_message(msg)

    def insert(self, ch: str) -> None:
        if not self._can_edit() or not ch:
            return
        if ch in ("\n", "\r"):
            self._emit_submit()
            return
        changed = False
        for c in ch:
            pos = self._first_editable(self.cursor)
            # Auto-fill intermediate literals up to editable pos.
            while len(self._text) < pos and len(self._text) < len(self._slots):
                slot = self._slots[len(self._text)]
                if slot.kind == "literal":
                    self._text += slot.char
                else:
                    break
            if pos >= len(self._slots):
                break
            slot = self._slots[pos]
            accepted = slot.accepts(c)
            if accepted is None:
                continue
            if pos < len(self._text):
                self._text = self._text[:pos] + accepted + self._text[pos + 1 :]
            else:
                self._text = self._text + accepted
            nxt = pos + 1
            while nxt < len(self._slots) and self._slots[nxt].kind == "literal":
                if len(self._text) == nxt:
                    self._text += self._slots[nxt].char
                nxt += 1
            self.cursor = min(nxt, len(self._slots))
            changed = True
        if changed:
            self._sel_anchor = None
            self._emit_change()
            if self.app is not None:
                self.app.request_draw()

    def on_key(self, key: str) -> bool:
        if self.disabled:
            return False
        if key == "backspace" and self._can_edit():
            prev = self._prev_editable(self.cursor)
            if prev is not None and prev < len(self._text):
                # Trim trailing literals when deleting from end.
                if prev + 1 >= len(self._text) or all(
                    self._slots[k].kind == "literal" for k in range(prev + 1, len(self._text))
                ):
                    self._text = self._text[:prev]
                else:
                    self._text = self._text[:prev] + " " + self._text[prev + 1 :]
                self.cursor = prev
                self._sel_anchor = None
                self._emit_change()
                if self.app is not None:
                    self.app.request_draw()
            return True
        return super().on_key(key)

    def validate(self, value: str) -> ValidationResult:
        if not value and self.valid_empty:
            return ValidationResult.success()
        failures: list[str] = []
        if len(value) != len(self._slots):
            failures.append("Value does not match mask length")
        else:
            for ch, slot in zip(value, self._slots):
                if slot.accepts(ch) is None:
                    failures.append(f"Invalid character {ch!r} in masked input")
                    break
        if not failures:
            base_res = super().validate(value)
            failures.extend(base_res.failures)
        return ValidationResult(failures)

    def validate_value(self, value: str) -> str:
        return self._apply_masked(value)
