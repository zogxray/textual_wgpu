"""``reactive`` / ``var`` / ``watch`` — Textual-style state with auto refresh."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class reactive:
    """Descriptor: assign → optional watchers → ``request_draw`` / relayout."""

    def __init__(
        self,
        default: Any = None,
        *,
        repaint: bool = True,
        layout: bool = False,
        init: bool = False,
        always_update: bool = False,
    ) -> None:
        self._default = default
        self.repaint = repaint
        self.layout = layout
        self.init = init
        self.always_update = always_update
        self.name = ""
        self.private_name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        self.private_name = f"_reactive_{name}"

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, self.private_name, self._default)

    def __set__(self, obj: Any, value: Any) -> None:
        old = getattr(obj, self.private_name, self._default)
        if not self.always_update and old == value:
            return
        setattr(obj, self.private_name, value)
        _run_watchers(obj, self.name, old, value)
        app = getattr(obj, "app", None)
        if self.layout and app is not None:
            if hasattr(app, "_layout_size"):
                app._layout_size = (0, 0)
        if self.repaint and app is not None and hasattr(app, "request_draw"):
            app.request_draw()


def var(default: Any = None, *, init: bool = False) -> reactive:
    """Textual ``var`` — reactive state without automatic repaint or relayout."""
    return reactive(default, repaint=False, layout=False, init=init)


def watch(*args: Any) -> Any:
    """Either ``@watch("attr")`` decorator OR ``watch(obj, "attr", callback)``."""
    if len(args) == 3 and not isinstance(args[0], str):
        obj, name, callback = args
        dyn = getattr(obj, "_tw_dyn_watchers", None)
        if dyn is None:
            dyn = {}
            setattr(obj, "_tw_dyn_watchers", dyn)
        dyn.setdefault(str(name), []).append(callback)
        return None

    names = tuple(str(a) for a in args)

    def decorator(fn: F) -> F:
        existing = getattr(fn, "_tw_watch", None)
        if existing is None:
            existing = []
            setattr(fn, "_tw_watch", existing)
        existing.extend(names)
        return fn

    return decorator


def _run_watchers(obj: Any, name: str, old: Any, new: Any) -> None:
    method = getattr(obj, f"watch_{name}", None)
    if callable(method):
        _call_watch(method, old, new)

    for base in type(obj).__mro__:
        for attr_name, attr in vars(base).items():
            names = getattr(attr, "_tw_watch", None)
            if not names or name not in names:
                continue
            bound = getattr(obj, attr_name, None)
            if callable(bound):
                _call_watch(bound, old, new)

    dyn = getattr(obj, "_tw_dyn_watchers", None)
    if isinstance(dyn, dict):
        for cb in list(dyn.get(name, ())):
            _call_watch(cb, old, new)


def _call_watch(method: Any, old: Any, new: Any) -> None:
    try:
        sig = inspect.signature(method)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        n = len(params)
        if n == 0:
            method()
        elif n == 1:
            method(new)
        else:
            method(old, new)
    except TypeError:
        try:
            method(old, new)
        except TypeError:
            try:
                method(new)
            except TypeError:
                method()
