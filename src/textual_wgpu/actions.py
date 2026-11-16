"""Action system — Textual ``run_action`` / ``action_*`` subset."""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Callable
from typing import Any

_ACTION_RE = re.compile(
    r"^(?:(?P<ns>app|screen)\.)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?P<args>.*)\))?\s*$",
    re.DOTALL,
)


def parse_action_ns(action: str) -> tuple[str | None, str, tuple[Any, ...]]:
    """Parse ``\"app.foo('bar')\"`` → (namespace, name, args)."""
    raw = (action or "").strip()
    if raw.startswith("action_"):
        raw = raw[len("action_"):]
    m = _ACTION_RE.match(raw)
    if not m:
        raise ValueError(f"Invalid action: {action!r}")
    ns = m.group("ns")
    name = m.group("name")
    args_raw = (m.group("args") or "").strip()
    if not args_raw:
        return ns, name, ()
    try:
        parsed = ast.literal_eval(f"({args_raw},)" if not args_raw.endswith(",") else f"({args_raw})")
        if not isinstance(parsed, tuple):
            parsed = (parsed,)
        return ns, name, parsed
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid action args in {action!r}: {exc}") from exc


def parse_action(action: str) -> tuple[str, tuple[Any, ...]]:
    """Parse ``\"foo\"`` or ``\"foo('bar', 1)\"`` → (name, args)."""
    _ns, name, args = parse_action_ns(action)
    return name, args


def find_action(target: Any, name: str) -> Callable[..., Any] | None:
    """Return ``action_<name>`` or ``<name>`` callable on ``target``."""
    for candidate in (f"action_{name}", name):
        fn = getattr(target, candidate, None)
        if callable(fn):
            return fn
    return None


def call_action(fn: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    try:
        sig = inspect.signature(fn)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        # Bound methods already drop self.
        if len(params) == 0:
            return fn()
        return fn(*args[: len(params)])
    except TypeError:
        return fn(*args) if args else fn()


def run_action_on(targets: list[Any], action: str) -> bool:
    """Try each target until an action handler succeeds (respects ``app.`` / ``screen.``)."""
    ns, name, args = parse_action_ns(action)
    ordered = list(targets)
    if ns == "app" and len(ordered) >= 2:
        ordered = [ordered[-1]]
    elif ns == "screen" and ordered:
        ordered = [ordered[0]]
    for target in ordered:
        if target is None:
            continue
        fn = find_action(target, name)
        if fn is None:
            continue
        call_action(fn, args)
        return True
    return False


def list_actions(target: Any) -> list[tuple[str, str]]:
    """Discover ``action_*`` methods → [(name, doc_or_name), ...]."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for cls in type(target).__mro__:
        for attr, obj in vars(cls).items():
            if not attr.startswith("action_") or not callable(obj):
                continue
            name = attr[len("action_"):]
            if name in seen or name.startswith("_"):
                continue
            seen.add(name)
            doc = (inspect.getdoc(obj) or "").strip().split("\n")[0] or name
            out.append((name, doc))
    out.sort(key=lambda t: t[0])
    return out
