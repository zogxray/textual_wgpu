"""Node discovery, saved peer registry (Workstation/Phone/Cloud), and app launcher directory."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

NODE_ROLES = ("workstation", "phone", "cloud")


@dataclass(slots=True)
class NodeEntry:
    id: str
    name: str
    role: str = "workstation"  # workstation | phone | cloud
    transport: str = "ssh"     # ssh | tcp | unix | local
    host: str = "127.0.0.1"
    port: int = 22
    user: str = "user"
    remote_cmd: str = "TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu"
    reverse_port: int = 0
    last_seen: float = 0.0

    def endpoint_label(self) -> str:
        if self.transport == "local":
            return "local://in-process"
        if self.transport == "unix":
            return f"unix://{self.host}"
        if self.transport == "tcp":
            return f"tcp://{self.host}:{self.port}"
        return f"ssh://{self.user}@{self.host}:{self.port}"


@dataclass(slots=True)
class AppEntry:
    app_id: str
    name: str
    description: str = ""
    kind: str = "python"  # python | mode | command
    target: str = ""
    args: list[str] = field(default_factory=list)

    def as_wire_tuple(self) -> tuple[str, str, str, str]:
        return (self.app_id, self.name, self.description, self.kind)


def default_config_dir() -> Path:
    return Path.home() / ".config" / "textual-wgpu"


def _default_nodes() -> list[NodeEntry]:
    return [
        NodeEntry(
            id="local",
            name="This Device",
            role="workstation",
            transport="local",
            host="127.0.0.1",
            port=7420,
            user="local",
            remote_cmd="python3 -m textual_wgpu",
        ),
        NodeEntry(
            id="workstation",
            name="Workstation GPU",
            role="workstation",
            transport="ssh",
            host="192.168.1.10",
            port=22,
            user="user",
            remote_cmd="TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu",
        ),
        NodeEntry(
            id="phone",
            name="Phone (Reverse / LAN)",
            role="phone",
            transport="tcp",
            host="127.0.0.1",
            port=7420,
            user="android",
            remote_cmd="python3 -m textual_wgpu",
            reverse_port=7420,
        ),
        NodeEntry(
            id="cloud",
            name="Cloud Compute Node",
            role="cloud",
            transport="ssh",
            host="cloud.example.net",
            port=22,
            user="ubuntu",
            remote_cmd="TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu",
        ),
    ]


class NodeRegistry:
    """Persistent registry of Workstation, Phone, and Cloud peer nodes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (default_config_dir() / "nodes.json")
        self._nodes: dict[str, NodeEntry] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self._nodes = {n.id: n for n in _default_nodes()}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._nodes = {n.id: n for n in _default_nodes()}
            return
        items = raw.get("nodes") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            self._nodes = {n.id: n for n in _default_nodes()}
            return
        loaded: dict[str, NodeEntry] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            nid = str(item.get("id") or "").strip()
            if not nid:
                continue
            role = str(item.get("role") or "workstation").lower()
            if role not in NODE_ROLES:
                role = "workstation"
            loaded[nid] = NodeEntry(
                id=nid,
                name=str(item.get("name") or nid),
                role=role,
                transport=str(item.get("transport") or "ssh"),
                host=str(item.get("host") or "127.0.0.1"),
                port=int(item.get("port") or 22),
                user=str(item.get("user") or "user"),
                remote_cmd=str(item.get("remote_cmd") or "TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu"),
                reverse_port=int(item.get("reverse_port") or 0),
                last_seen=float(item.get("last_seen") or 0.0),
            )
        self._nodes = loaded or {n.id: n for n in _default_nodes()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "nodes": [asdict(n) for n in self._nodes.values()]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def list_nodes(self, *, role: str | None = None) -> list[NodeEntry]:
        nodes = list(self._nodes.values())
        if role:
            r = role.lower()
            nodes = [n for n in nodes if n.role == r]
        return nodes

    def get(self, node_id: str) -> NodeEntry | None:
        return self._nodes.get(node_id)

    def upsert(self, node: NodeEntry, *, persist: bool = True) -> NodeEntry:
        if node.role not in NODE_ROLES:
            node.role = "workstation"
        self._nodes[node.id] = node
        if persist:
            self.save()
        return node

    def touch(self, node_id: str) -> None:
        n = self._nodes.get(node_id)
        if n is not None:
            n.last_seen = time.time()
            self.save()

    def remove(self, node_id: str, *, persist: bool = True) -> bool:
        existed = self._nodes.pop(node_id, None) is not None
        if existed and persist:
            self.save()
        return existed


def _builtin_apps() -> list[AppEntry]:
    return [
        AppEntry(
            app_id="demo",
            name="textual-wgpu Demo",
            description="Full multi-mode GPU desktop showcase",
            kind="python",
            target="textual_wgpu.demo:build_demo_app",
        ),
        AppEntry(
            app_id="demo:overview",
            name="Overview & Telemetry",
            description="System summary, Sparklines, Progress & RichText",
            kind="mode",
            target="overview",
        ),
        AppEntry(
            app_id="demo:data",
            name="Data & Tree Browser",
            description="DataTable, Tree hierarchy & live metrics",
            kind="mode",
            target="data",
        ),
        AppEntry(
            app_id="demo:editor",
            name="Code & Diff Editor",
            description="Syntax-highlighted TextArea & unified diff view",
            kind="mode",
            target="editor",
        ),
        AppEntry(
            app_id="demo:planes",
            name="3D GPU Viewport",
            description="Interactive 3D mesh plane with Client/Server balancing",
            kind="mode",
            target="planes",
        ),
        AppEntry(
            app_id="demo:media",
            name="Media Studio (Photo/Video/Audio)",
            description="H.265/H.264 video, native RGBA photos & audio playback",
            kind="mode",
            target="media",
        ),
    ]


def _load_registered_apps(apps_path: Path | None = None) -> list[AppEntry]:
    p = apps_path or (default_config_dir() / "apps.json")
    out: list[AppEntry] = []
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            items = raw.get("apps") if isinstance(raw, dict) else raw
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    aid = str(item.get("id") or item.get("app_id") or "").strip()
                    target = str(item.get("target") or "").strip()
                    if not aid or not target:
                        continue
                    out.append(
                        AppEntry(
                            app_id=aid,
                            name=str(item.get("name") or aid),
                            description=str(item.get("description") or ""),
                            kind=str(item.get("kind") or "python"),
                            target=target,
                            args=[str(x) for x in (item.get("args") or ())],
                        ),
                    )
        except (OSError, json.JSONDecodeError):
            pass

    # Discover installed Python packages declaring `[project.entry-points."textual_wgpu.apps"]`
    try:
        eps = importlib.metadata.entry_points(group="textual_wgpu.apps")
        for ep in eps:
            aid = ep.name
            out.append(
                AppEntry(
                    app_id=aid,
                    name=aid.replace("-", " ").replace("_", " ").title(),
                    description=f"Installed app ({ep.value})",
                    kind="python",
                    target=ep.value,
                ),
            )
    except Exception:  # noqa: BLE001
        pass
    return out


def discover_local_apps(*, apps_path: Path | None = None) -> list[AppEntry]:
    """Return deduplicated list of built-in, registered, and entry-point apps."""
    seen: dict[str, AppEntry] = {}
    for entry in _builtin_apps() + _load_registered_apps(apps_path):
        seen[entry.app_id] = entry
    return list(seen.values())


def resolve_app_entry(app_id: str, *, apps_path: Path | None = None) -> AppEntry | None:
    for entry in discover_local_apps(apps_path=apps_path):
        if entry.app_id == app_id:
            return entry
    return None


def instantiate_python_app(target: str, args: str = "") -> Any:
    """Import ``module:factory`` and call it to construct an ``App`` instance."""
    mod_name, _, attr = target.partition(":")
    attr = attr or "build_app"
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, attr)
    if args.strip():
        try:
            return fn(args.strip())
        except TypeError:
            return fn()
    return fn()
