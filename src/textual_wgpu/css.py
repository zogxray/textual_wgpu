"""TCSS-inspired stylesheet engine for textual-wgpu.

Supports (beyond the MDGA subset):
- combinators: descendant `` ``, child ``>``, adjacent ``+``, sibling ``~``
- cascade: specificity + source order + ``!important`` + layered sheets
  (app → screen → extra)
- classes: ``.foo`` with ``Widget.add_class`` / ``remove_class``
- layout: width/height/min-width, ``dock``, ``layer``, ``offset``, ``display``
- paint: border / background / color / opacity
- transitions: ``transition: background 200ms`` (color/opacity tween)
- theme vars: ``$primary``, ``$accent``, ``$text-muted``, …
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .theme import Theme
    from .widget import Widget

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_RULE_RE = re.compile(r"([^{]+)\{([^}]*)\}", re.DOTALL)
_DECL_RE = re.compile(r"([a-zA-Z_-]+)\s*:\s*([^;]+)")
_SIZE_RE = re.compile(
    r"^\s*(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>%|fr|ms|s)?\s*$",
    re.IGNORECASE,
)
# Split selector chain on combinators while keeping them.
_COMB_SPLIT_RE = re.compile(r"\s*(>|\+|~)\s*|\s+")

_THEME_VARS: dict[str, str] = {
    "primary": "primary",
    "secondary": "secondary",
    "warning": "warning",
    "error": "error",
    "success": "success",
    "accent": "accent",
    "boost": "boost",
    "background": "background",
    "surface": "surface",
    "panel": "panel",
    "foreground": "foreground",
    "text": "foreground",
    "text-muted": "muted",
    "muted": "muted",
    "primary-darken-1": "primary",
    "primary-darken-2": "primary",
    "primary-darken-3": "primary",
    "primary-lighten-1": "primary",
    "primary-lighten-2": "primary",
    "accent-darken-1": "accent",
    "accent-lighten-1": "accent",
}


class Styles(dict):
    """Dict-compatible styles object supporting Textual ``widget.styles.<prop>`` access."""

    def __init__(self, initial: dict[str, Any] | None = None, *, owner: Widget | None = None) -> None:
        super().__init__(initial or {})
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self:
            return self[name]
        alt = name.replace("_", "-")
        if alt in self:
            return self[alt]
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self[name] = value
        owner = object.__getattribute__(self, "_owner")
        if owner is not None and name == "display":
            owner.refresh(layout=True)
        elif owner is not None and name in ("width", "height", "dock"):
            if name == "dock":
                owner.layout_dock = value
            owner.refresh(layout=True)

# Named layers → paint order (higher on top).
_LAYER_ORDER: dict[str, int] = {
    "background": 0,
    "base": 10,
    "panel": 20,
    "overlay": 30,
    "tooltip": 40,
    "notification": 50,
}

SizeKind = Literal["cells", "percent", "fr", "auto", "none", "ms"]
Combinator = Literal[" ", ">", "+", "~"]


@dataclass(frozen=True)
class SizeValue:
    kind: SizeKind
    value: float = 0.0


@dataclass
class SimpleSelector:
    """One compound selector: Type.class#id:pseudo."""

    type_name: str | None = None
    widget_id: str | None = None
    classes: tuple[str, ...] = ()
    pseudo: str | None = None  # focus | focus-within | hover | disabled

    def specificity(self) -> tuple[int, int, int]:
        # (ids, classes+pseudos, types) — CSS-ish.
        return (
            1 if self.widget_id else 0,
            len(self.classes) + (1 if self.pseudo else 0),
            1 if self.type_name else 0,
        )


@dataclass
class Selector:
    """Chain of simple selectors with combinators.

    ``parts[0]`` has combinator ignored (anchor); later parts use the
    combinator that precedes them.
    """

    parts: list[tuple[Combinator, SimpleSelector]] = field(default_factory=list)

    @property
    def specificity(self) -> tuple[int, int, int]:
        a = b = c = 0
        for _comb, simp in self.parts:
            ia, ib, ic = simp.specificity()
            a += ia
            b += ib
            c += ic
        return (a, b, c)


@dataclass
class Decl:
    prop: str
    value: str
    important: bool = False


@dataclass
class Rule:
    selectors: list[Selector]
    decls: list[Decl]
    order: int = 0
    source_layer: int = 0  # 0=app, 1=screen, 2=inline/extra

    def decls_map(self, *, important: bool | None = None) -> dict[str, str]:
        out: dict[str, str] = {}
        for d in self.decls:
            if important is None or d.important is important:
                out[d.prop] = d.value
        return out


@dataclass
class Stylesheet:
    rules: list[Rule] = field(default_factory=list)
    source: str = ""
    layer: int = 0

    @classmethod
    def parse(cls, css: str, *, layer: int = 0) -> Stylesheet:
        text = _COMMENT_RE.sub("", css or "")
        rules: list[Rule] = []
        for i, m in enumerate(_RULE_RE.finditer(text)):
            sel_raw = m.group(1).strip()
            body = m.group(2)
            if not sel_raw:
                continue
            decls = _parse_decls(body)
            if not decls:
                continue
            selectors = [_parse_selector_chain(s.strip()) for s in _split_selector_list(sel_raw)]
            selectors = [s for s in selectors if s is not None and s.parts]
            if selectors:
                rules.append(Rule(
                    selectors=selectors, decls=decls, order=i, source_layer=layer,
                ))
        return cls(rules=rules, source=css or "", layer=layer)

    def extend(self, other: Stylesheet) -> Stylesheet:
        """Return a new sheet with ``other``'s rules appended (higher cascade)."""
        merged = Stylesheet(
            rules=list(self.rules) + list(other.rules),
            source=self.source + "\n" + other.source,
            layer=max(self.layer, other.layer),
        )
        return merged

    def apply(
        self,
        root: Widget,
        *,
        focus: Widget | None = None,
        theme: Theme | None = None,
        hover: Widget | None = None,
    ) -> None:
        """Resolve matching rules onto ``widget.styles`` and layout hints."""
        nodes = list(_walk(root))
        prev_styles: dict[int, dict[str, Any]] = {}
        for n in nodes:
            prev_styles[id(n)] = dict(getattr(n, "styles", None) or {})
            styles = getattr(n, "styles", None)
            if not isinstance(styles, dict):
                n.styles = {}  # type: ignore[attr-defined]
            else:
                # Keep transition runtime state.
                keep = {k: v for k, v in styles.items() if k.startswith("_transition")}
                styles.clear()
                styles.update(keep)
            n.layout_hint_cells = None  # type: ignore[attr-defined]
            n.layout_frac = None  # type: ignore[attr-defined]
            n.layout_min_cells = None  # type: ignore[attr-defined]
            n.layout_hint_rows = None  # type: ignore[attr-defined]
            n.layout_width_fr = None  # type: ignore[attr-defined]
            n.layout_height_fr = None  # type: ignore[attr-defined]
            n._css_layout = False  # type: ignore[attr-defined]
            if hasattr(n, "layout_dock"):
                n.layout_dock = None  # type: ignore[attr-defined]
            if hasattr(n, "layout_layer"):
                n.layout_layer = None  # type: ignore[attr-defined]
            # Clear previous CSS size tokens from styles (keep transitions).
            for k in (
                "width_fr", "height_fr", "height_frac", "width_auto", "height_auto",
                "layout", "grid_size", "grid_columns", "dock", "layer", "offset",
                "display", "max_height_rows",
            ):
                styles_dict = getattr(n, "styles", None)
                if isinstance(styles_dict, dict) and k in styles_dict and not k.startswith("_"):
                    # Will be re-filled by matching rules; don't wipe transition keys.
                    pass

        for node in nodes:
            matched: list[tuple[tuple[int, int, int, int, int], list[Decl]]] = []
            for rule in self.rules:
                for sel in rule.selectors:
                    if _matches_chain(sel, node, focus=focus, hover=hover, root=root):
                        # Cascade key: important handled when merging.
                        # (layer, spec_a, spec_b, spec_c, order)
                        sa, sb, sc = sel.specificity
                        key = (rule.source_layer, sa, sb, sc, rule.order)
                        matched.append((key, rule.decls))
                        break
            if not matched:
                continue
            matched.sort(key=lambda t: t[0])
            # Non-important first, then important (both in cascade order).
            merged: dict[str, str] = {}
            important: dict[str, str] = {}
            for _key, decls in matched:
                for d in decls:
                    if d.important:
                        important[d.prop] = d.value
                    else:
                        merged[d.prop] = d.value
            merged.update(important)
            _apply_decls(node, merged, theme=theme, previous=prev_styles.get(id(node), {}))


# ── parse helpers ─────────────────────────────────────────────────────

def _split_selector_list(raw: str) -> list[str]:
    """Split on commas not inside functional notation (none expected)."""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_decls(body: str) -> list[Decl]:
    out: list[Decl] = []
    for dm in _DECL_RE.finditer(body):
        prop = dm.group(1).strip().lower()
        value = dm.group(2).strip()
        important = False
        if value.endswith("!important"):
            important = True
            value = value[: -len("!important")].strip()
        out.append(Decl(prop, value, important))
    return out


def _parse_selector_chain(raw: str) -> Selector | None:
    s = raw.strip()
    if not s:
        return None
    # Tokenize: alternate simple selectors and combinators.
    # Use regex to find combinators; pieces between are simple selectors.
    tokens: list[str] = []
    buf = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in ">+~":
            if buf.strip():
                tokens.append(buf.strip())
                buf = ""
            tokens.append(ch)
            i += 1
            while i < len(s) and s[i].isspace():
                i += 1
            continue
        if ch.isspace():
            if buf.strip():
                tokens.append(buf.strip())
                buf = ""
            # Collapse whitespace to descendant combinator unless next is >+~
            j = i
            while j < len(s) and s[j].isspace():
                j += 1
            if j < len(s) and s[j] in ">+~":
                i = j
                continue
            tokens.append(" ")
            i = j
            continue
        buf += ch
        i += 1
    if buf.strip():
        tokens.append(buf.strip())

    parts: list[tuple[Combinator, SimpleSelector]] = []
    comb: Combinator = " "
    for tok in tokens:
        if tok in (" ", ">", "+", "~"):
            comb = tok  # type: ignore[assignment]
            continue
        simp = _parse_simple(tok)
        if simp is None:
            return None
        parts.append((comb, simp))
        comb = " "
    if not parts:
        return None
    # First combinator is meaningless; normalize to " ".
    first = parts[0][1]
    parts[0] = (" ", first)
    return Selector(parts=parts)


def _parse_simple(raw: str) -> SimpleSelector | None:
    s = raw.strip()
    if not s:
        return None
    pseudo = None
    if ":" in s:
        # last :pseudo only
        base, _, ps = s.rpartition(":")
        s = base.strip()
        ps = ps.strip().lower()
        if ps in ("focus", "focus-within", "hover", "disabled"):
            pseudo = ps
        # else ignore unknown pseudo (match base)
    type_name = None
    widget_id = None
    classes: list[str] = []
    # Parse Type / #id / .class in any order (Type first if present).
    rest = s
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", rest)
    if m and not rest.startswith("#") and not rest.startswith("."):
        type_name = m.group(1)
        rest = rest[m.end():]
    while rest:
        if rest.startswith("#"):
            m = re.match(r"^#([A-Za-z0-9_-]+)", rest)
            if not m:
                return None
            widget_id = m.group(1)
            rest = rest[m.end():]
        elif rest.startswith("."):
            m = re.match(r"^\.([A-Za-z0-9_-]+)", rest)
            if not m:
                return None
            classes.append(m.group(1))
            rest = rest[m.end():]
        else:
            return None
    if type_name is None and widget_id is None and not classes and pseudo is None:
        return None
    return SimpleSelector(
        type_name=type_name,
        widget_id=widget_id,
        classes=tuple(classes),
        pseudo=pseudo,
    )


def parse_size(raw: str) -> SizeValue | None:
    s = (raw or "").strip().lower()
    if s in ("auto",):
        return SizeValue("auto")
    if s in ("none",):
        return SizeValue("none")
    m = _SIZE_RE.match(s)
    if not m:
        return None
    num = float(m.group("num"))
    unit = (m.group("unit") or "").lower()
    if unit == "%":
        return SizeValue("percent", num)
    if unit == "fr":
        return SizeValue("fr", num)
    if unit == "ms":
        return SizeValue("ms", num)
    if unit == "s":
        return SizeValue("ms", num * 1000.0)
    return SizeValue("cells", num)


def resolve_color(raw: str, theme: Theme | None) -> str | None:
    """Return #RRGGBB or None (transparent/none)."""
    s = (raw or "").strip()
    if not s or s.lower() in ("none", "transparent"):
        return None
    if s.startswith("$"):
        key = s[1:].strip().lower()
        field = _THEME_VARS.get(key)
        if field and theme is not None:
            return getattr(theme, field, None)
        if theme is not None:
            variables = getattr(theme, "variables", None) or {}
            if key in variables:
                return str(variables[key]).split()[0]
        return None
    if s.startswith("#"):
        return s
    return None


# ── matching ──────────────────────────────────────────────────────────

def _walk(root: Widget) -> list[Widget]:
    out: list[Widget] = []

    def visit(w: Widget) -> None:
        out.append(w)
        for c in getattr(w, "children", []) or []:
            visit(c)

    visit(root)
    return out


def _match_simple(
    simp: SimpleSelector,
    node: Widget,
    *,
    focus: Widget | None,
    hover: Widget | None,
) -> bool:
    if simp.widget_id is not None and getattr(node, "id", None) != simp.widget_id:
        return False
    if simp.type_name is not None and type(node).__name__ != simp.type_name:
        return False
    if simp.classes:
        node_classes = getattr(node, "classes", None) or set()
        for cls in simp.classes:
            if cls not in node_classes:
                return False
    if simp.pseudo == "focus":
        return focus is node
    if simp.pseudo == "focus-within":
        return _is_ancestor_or_self(node, focus)
    if simp.pseudo == "hover":
        return hover is node
    if simp.pseudo == "disabled":
        return bool(getattr(node, "disabled", False))
    return True


def _is_ancestor_or_self(node: Widget, tip: Widget | None) -> bool:
    if tip is None:
        return False
    w: Widget | None = tip
    while w is not None:
        if w is node:
            return True
        w = getattr(w, "parent", None)
    return False


def _matches_chain(
    sel: Selector,
    node: Widget,
    *,
    focus: Widget | None,
    hover: Widget | None,
    root: Widget,
) -> bool:
    """Right-to-left match of a selector chain against ``node``.

    Each part's combinator is the one *before* that simple selector
    (``#a + #b`` → parts ``[(' ', a), ('+', b)]``); when stepping left from
    ``b`` we use ``+`` to reach ``a``.
    """
    if not sel.parts:
        return False
    _comb, last = sel.parts[-1]
    if not _match_simple(last, node, focus=focus, hover=hover):
        return False
    current: Widget | None = node
    for i in range(len(sel.parts) - 2, -1, -1):
        if current is None:
            return False
        comb = sel.parts[i + 1][0]  # combinator linking parts[i] → parts[i+1]
        simp = sel.parts[i][1]
        if comb == ">":
            parent = getattr(current, "parent", None)
            if parent is None or not _match_simple(simp, parent, focus=focus, hover=hover):
                return False
            current = parent
        elif comb == "+":
            prev = _previous_sibling(current)
            if prev is None or not _match_simple(simp, prev, focus=focus, hover=hover):
                return False
            current = prev
        elif comb == "~":
            found = None
            for sib in _preceding_siblings(current):
                if _match_simple(simp, sib, focus=focus, hover=hover):
                    found = sib
                    break
            if found is None:
                return False
            current = found
        else:  # descendant
            anc = getattr(current, "parent", None)
            found = None
            while anc is not None:
                if _match_simple(simp, anc, focus=focus, hover=hover):
                    found = anc
                    break
                anc = getattr(anc, "parent", None)
            if found is None:
                return False
            current = found
    return True


def _previous_sibling(node: Widget) -> Widget | None:
    parent = getattr(node, "parent", None)
    if parent is None:
        return None
    kids = list(getattr(parent, "children", []) or [])
    try:
        i = kids.index(node)
    except ValueError:
        return None
    return kids[i - 1] if i > 0 else None


def _preceding_siblings(node: Widget) -> list[Widget]:
    parent = getattr(node, "parent", None)
    if parent is None:
        return []
    kids = list(getattr(parent, "children", []) or [])
    try:
        i = kids.index(node)
    except ValueError:
        return []
    return list(reversed(kids[:i]))


# ── apply declarations ────────────────────────────────────────────────

def _styles_dict(node: Widget) -> Styles:
    """Return the live styles mapping (never replace an empty dict via ``or {}``)."""
    styles = getattr(node, "styles", None)
    if not isinstance(styles, Styles):
        styles = Styles(styles if isinstance(styles, dict) else None, owner=node)
        node.styles = styles  # type: ignore[attr-defined]
    return styles


def _apply_decls(
    node: Widget,
    decls: dict[str, str],
    *,
    theme: Theme | None,
    previous: dict[str, Any],
) -> None:
    styles = _styles_dict(node)
    node._css_layout = True  # type: ignore[attr-defined]

    if "width" in decls:
        _apply_width(node, decls["width"])
    if "min-width" in decls:
        sz = parse_size(decls["min-width"])
        if sz and sz.kind == "cells":
            node.layout_min_cells = int(sz.value)  # type: ignore[attr-defined]
    if "height" in decls:
        _apply_height(node, decls["height"])
    if "max-height" in decls:
        sz = parse_size(decls["max-height"])
        if sz and sz.kind == "cells":
            styles["max_height_rows"] = int(sz.value)

    if "dock" in decls:
        dock = decls["dock"].strip().lower()
        if dock in ("top", "right", "bottom", "left"):
            styles["dock"] = dock
            node.layout_dock = dock  # type: ignore[attr-defined]
        elif dock in ("", "none"):
            styles["dock"] = None
            node.layout_dock = None  # type: ignore[attr-defined]

    if "layer" in decls:
        layer_raw = decls["layer"].strip()
        if layer_raw.isdigit() or (layer_raw.startswith("-") and layer_raw[1:].isdigit()):
            styles["layer"] = int(layer_raw)
        else:
            styles["layer"] = _LAYER_ORDER.get(layer_raw.lower(), 10)
        node.layout_layer = styles["layer"]  # type: ignore[attr-defined]

    if "offset" in decls:
        styles["offset"] = _parse_offset(decls["offset"])
    if "display" in decls:
        styles["display"] = decls["display"].strip().lower()

    if "border" in decls:
        _apply_border(node, decls["border"], theme=theme, styles=styles)
    if "background" in decls:
        new_bg = resolve_color(decls["background"], theme)
        styles["background_raw"] = decls["background"]
        styles["background"] = new_bg
    if "color" in decls:
        styles["color"] = resolve_color(decls["color"], theme)
    if "opacity" in decls:
        try:
            styles["opacity"] = float(decls["opacity"])
        except ValueError:
            pass

    for key in (
        "padding", "margin", "scrollbar-size-vertical",
        "scrollbar-size-horizontal", "overflow", "overflow-x", "overflow-y",
        "content-align", "text-align", "text-style", "border-title-color",
        "border-title-align",
    ):
        if key in decls:
            styles[key] = decls[key]

    if "layout" in decls:
        styles["layout"] = decls["layout"].strip().lower()
    if "grid-size" in decls:
        styles["grid_size"] = decls["grid-size"].strip()
    if "grid-columns" in decls:
        try:
            styles["grid_columns"] = int(decls["grid-columns"].strip())
        except ValueError:
            styles["grid_columns"] = decls["grid-columns"].strip()

    if "transition" in decls:
        styles["transition"] = _parse_transition(decls["transition"])
        _arm_transitions(node, styles, previous)


def _parse_offset(raw: str) -> tuple[int, int]:
    parts = raw.replace(",", " ").split()
    if len(parts) >= 2:
        from .fonts import cells_to_px, rows_to_px

        sx = parse_size(parts[0])
        sy = parse_size(parts[1])
        x = cells_to_px(int(sx.value)) if sx and sx.kind == "cells" else 0
        y = rows_to_px(int(sy.value)) if sy and sy.kind == "cells" else 0
        return (x, y)
    return (0, 0)


def _parse_transition(raw: str) -> list[dict[str, Any]]:
    """``background 200ms`` / ``opacity 0.3s`` / comma-separated list."""
    out: list[dict[str, Any]] = []
    for part in raw.split(","):
        toks = part.strip().split()
        if len(toks) < 2:
            continue
        prop = toks[0].lower()
        dur = parse_size(toks[1])
        ms = 200.0
        if dur and dur.kind == "ms":
            ms = dur.value
        elif dur and dur.kind == "cells":
            # bare number → treat as ms if large, else seconds-ish; Textual uses time.
            ms = dur.value if dur.value > 10 else dur.value * 1000
        out.append({"prop": prop, "duration_ms": ms})
    return out


def _arm_transitions(
    node: Widget,
    styles: dict[str, Any],
    previous: dict[str, Any],
) -> None:
    specs = styles.get("transition") or []
    if not specs:
        return
    now = time.monotonic()
    for spec in specs:
        prop = spec["prop"]
        dur = float(spec["duration_ms"]) / 1000.0
        old = previous.get(prop)
        new = styles.get(prop)
        if old == new or new is None:
            continue
        if prop in ("background", "color", "border_color") and isinstance(old, str) and isinstance(new, str):
            styles[f"_transition_{prop}"] = {
                "from": old,
                "to": new,
                "t0": now,
                "dur": max(0.01, dur),
            }
            # Start at old color; paint interpolates.
            styles[prop] = old
        elif prop == "opacity":
            styles["_transition_opacity"] = {
                "from": float(previous.get("opacity", 1.0) or 1.0),
                "to": float(new),
                "t0": now,
                "dur": max(0.01, dur),
            }
            styles["opacity"] = styles["_transition_opacity"]["from"]


def tick_transitions(root: Widget) -> bool:
    """Advance transitions under ``root``. Returns True if any still running."""
    now = time.monotonic()
    busy = False
    for node in _walk(root):
        styles = getattr(node, "styles", None)
        if not isinstance(styles, dict):
            continue
        for key in list(styles.keys()):
            if not key.startswith("_transition_"):
                continue
            prop = key[len("_transition_"):]
            tr = styles[key]
            t0, dur = tr["t0"], tr["dur"]
            u = (now - t0) / dur
            if u >= 1.0:
                styles[prop] = tr["to"]
                del styles[key]
                continue
            busy = True
            if prop == "opacity":
                styles[prop] = tr["from"] + (tr["to"] - tr["from"]) * u
            else:
                styles[prop] = _lerp_hex(tr["from"], tr["to"], u)
    return busy


def _lerp_hex(a: str, b: str, u: float) -> str:
    def rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return (0, 0, 0)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    ar, ag, ab = rgb(a)
    br, bg, bb = rgb(b)
    r = int(ar + (br - ar) * u)
    g = int(ag + (bg - ag) * u)
    b_ = int(ab + (bb - ab) * u)
    return f"#{r:02x}{g:02x}{b_:02x}"


def _apply_width(node: Widget, raw: str) -> None:
    sz = parse_size(raw)
    if sz is None:
        return
    styles = _styles_dict(node)
    if hasattr(node, "layout_hint_w"):
        node.layout_hint_w = None  # type: ignore[attr-defined]
    if hasattr(node, "layout_hint_cells"):
        node.layout_hint_cells = None  # type: ignore[attr-defined]
    if hasattr(node, "layout_frac"):
        node.layout_frac = None  # type: ignore[attr-defined]
    if hasattr(node, "layout_width_fr"):
        node.layout_width_fr = None  # type: ignore[attr-defined]
    styles.pop("width_fr", None)
    styles.pop("width_auto", None)

    if sz.kind == "cells":
        node.layout_hint_cells = int(sz.value)  # type: ignore[attr-defined]
    elif sz.kind == "percent":
        node.layout_frac = float(sz.value) / 100.0  # type: ignore[attr-defined]
    elif sz.kind == "fr":
        node.layout_width_fr = float(sz.value)  # type: ignore[attr-defined]
        styles["width_fr"] = float(sz.value)
    elif sz.kind == "auto":
        styles["width_auto"] = True
        styles["width"] = "auto"
    elif sz.kind == "none":
        pass


def _apply_height(node: Widget, raw: str) -> None:
    sz = parse_size(raw)
    if sz is None:
        return
    styles = _styles_dict(node)
    # Don't wipe imperative layout_hint_h unless CSS sets a concrete height.
    if hasattr(node, "layout_hint_rows"):
        node.layout_hint_rows = None  # type: ignore[attr-defined]
    if hasattr(node, "layout_height_fr"):
        node.layout_height_fr = None  # type: ignore[attr-defined]
    styles.pop("height_fr", None)
    styles.pop("height_frac", None)
    styles.pop("height_auto", None)

    if sz.kind == "cells":
        node.layout_hint_h = None  # type: ignore[attr-defined]
        node.layout_hint_rows = int(sz.value)  # type: ignore[attr-defined]
    elif sz.kind == "fr":
        node.layout_hint_h = None  # type: ignore[attr-defined]
        node.layout_height_fr = float(sz.value)  # type: ignore[attr-defined]
        styles["height_fr"] = float(sz.value)
    elif sz.kind == "percent":
        node.layout_hint_h = None  # type: ignore[attr-defined]
        styles["height_frac"] = float(sz.value) / 100.0
    elif sz.kind == "auto":
        node.layout_hint_h = None  # type: ignore[attr-defined]
        styles["height_auto"] = True
        styles["height"] = "auto"
    elif sz.kind == "none":
        pass


def _apply_border(
    node: Widget,
    raw: str,
    *,
    theme: Theme | None,
    styles: dict[str, Any],
) -> None:
    s = (raw or "").strip()
    if not s or s.lower() == "none":
        styles["border"] = False
        styles["border_color"] = None
        return
    styles["border"] = True
    parts = s.split()
    color_tok = parts[-1] if parts else ""
    if color_tok.startswith("$") or color_tok.startswith("#"):
        styles["border_color"] = resolve_color(color_tok, theme)
    else:
        styles["border_color"] = resolve_color("$primary", theme)
    styles["border_style"] = parts[0].lower() if parts else "round"
