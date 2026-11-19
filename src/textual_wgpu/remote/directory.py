"""Node discovery, saved peer registry (Workstation/Phone/Cloud), and app launcher directory."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import socket
import struct
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

NODE_ROLES = ("workstation", "phone", "cloud")
LAN_MCAST_GROUP = "239.255.74.20"
LAN_DISCOVERY_PORT = 7421


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
            name="Media Studio (Multi-Cam/Audio/Files)",
            description="Multi-camera HW video, native RGBA photos & cross-device audio",
            kind="mode",
            target="media",
        ),
        AppEntry(
            app_id="demo:composer",
            name="Meta-OS Composer (Eyes/Knowledge/Hands)",
            description="Wire cross-device sensors, Local/Cloud AI & actuators",
            kind="mode",
            target="composer",
        ),
        AppEntry(
            app_id="demo:player",
            name="Winamp Player (Cross-Device)",
            description="Cross-device file browser + parallel multi-speaker output",
            kind="mode",
            target="player",
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


def record_python_executable(exe_path: str | None = None) -> Path | None:
    """Persist ``sys.executable`` so SSH auto-start finds this Python venv instantly."""
    exe = exe_path or sys.executable
    if not exe:
        return None
    cache_dir = Path.home() / ".cache" / "textual-wgpu"
    target = cache_dir / "python_bin"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text(encoding="utf-8").strip() != exe:
            target.write_text(f"{exe}\n", encoding="utf-8")
        return target
    except OSError:
        return None


class LanPresenceBeacon:
    """Broadcasts a read-only LAN presence beacon (no open UI port required when using SSH)."""

    def __init__(
        self,
        *,
        app_title: str = "textual-wgpu",
        tcp_port: int = 7420,
        ssh_port: int = 22,
        node_name: str | None = None,
        interval_s: float = 1.2,
        mcast_group: str = LAN_MCAST_GROUP,
        disc_port: int = LAN_DISCOVERY_PORT,
    ) -> None:
        self.app_title = app_title
        self.tcp_port = int(tcp_port)
        self.ssh_port = int(ssh_port)
        self.node_name = node_name or socket.gethostname() or "node"
        self.interval_s = max(0.2, float(interval_s))
        self.mcast_group = mcast_group
        self.disc_port = int(disc_port)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def payload_bytes(self) -> bytes:
        return json.dumps(
            {
                "v": 1,
                "name": self.node_name,
                "ssh_port": self.ssh_port,
                "tcp_port": self.tcp_port,
                "app": self.app_title,
                "running": True,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass
        while not self._stop.is_set():
            data = self.payload_bytes()
            for dst in ((self.mcast_group, self.disc_port), ("255.255.255.255", self.disc_port)):
                try:
                    sock.sendto(data, dst)
                except OSError:
                    pass
            self._stop.wait(self.interval_s)
        try:
            sock.close()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


def discover_lan_nodes(
    *,
    timeout: float = 0.8,
    mcast_group: str = LAN_MCAST_GROUP,
    disc_port: int = LAN_DISCOVERY_PORT,
) -> list[dict[str, Any]]:
    """Listen briefly for ``LanPresenceBeacon`` packets on the local network."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", int(disc_port)))
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(mcast_group), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass
        found: dict[tuple[str, int], dict[str, Any]] = {}
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            sock.settimeout(max(0.02, deadline - time.monotonic()))
            try:
                raw, (src_ip, _src_port) = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                break
            try:
                info = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(info, dict):
                continue
            tcp_port = int(info.get("tcp_port") or 7420)
            info["host"] = src_ip
            found[(src_ip, tcp_port)] = info
        return list(found.values())
    except OSError:
        return []
    finally:
        try:
            sock.close()
        except OSError:
            pass


@dataclass(slots=True)
class DeviceProfile:
    """Real identity & media hardware advertised by each node (no 'if is_android' guessing)."""

    device_id: str
    name: str
    kind: str = "desktop"  # "android" | "desktop"
    cameras: list[tuple[str, str]] = field(default_factory=list)
    has_mic: bool = True

    @property
    def icon(self) -> str:
        return "📱" if self.kind == "android" else "💻"

    def cameras_spec(self) -> str:
        return ";".join(f"{cid}={label}" for cid, label in self.cameras)


def parse_cameras_spec(spec: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            cid, _, label = part.partition("=")
            out.append((cid.strip(), label.strip() or cid.strip()))
        else:
            out.append((part, part))
    return out


def _read_linux_machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            val = Path(p).read_text(encoding="utf-8").strip()
            if val:
                return val
        except OSError:
            pass
    return socket.gethostname() or "desktop"


def local_device_profile() -> DeviceProfile:
    """Discover real local device ID, human name, kind, and attached cameras."""
    is_android = (
        "ANDROID_ROOT" in os.environ
        or hasattr(sys, "getandroidapilevel")
        or Path("/system/build.prop").exists()
    )
    if is_android:
        try:
            from com.textualwgpu.app import LocalMediaHelper  # type: ignore[import-not-found]

            raw = str(LocalMediaHelper.deviceProfileJson() or "{}")
            data = json.loads(raw)
            did = str(data.get("id") or "android")
            name = str(data.get("name") or "Android Phone")
            cams = parse_cameras_spec(str(data.get("cameras") or "front=Front Camera;back=Back Camera"))
            return DeviceProfile(device_id=did, name=name, kind="android", cameras=cams)
        except Exception:  # noqa: BLE001
            return DeviceProfile(
                device_id=f"android-{socket.gethostname() or 'phone'}",
                name="Android Phone",
                kind="android",
                cameras=[("front", "Front Camera"), ("back", "Back Camera")],
            )

    from .media_codec import list_linux_cameras

    host = socket.gethostname() or "Workstation"
    cams = list_linux_cameras()
    return DeviceProfile(
        device_id=_read_linux_machine_id(),
        name=host,
        kind="desktop",
        cameras=cams,
    )


@dataclass(slots=True)
class ResourceEntry:
    """An independent device resource (camera, mic, files) usable in any order by any app."""

    device_id: str
    device_name: str
    device_kind: str  # "desktop" | "android"
    icon: str
    kind: str         # "camera" | "mic" | "files"
    resource_id: str
    label: str
    host: str = "127.0.0.1"
    port: int = 0
    source: str = "resource_svc"  # "resource_svc" | "local" | "peer"

    @property
    def uid(self) -> str:
        return f"{self.device_id}:{self.kind}:{self.resource_id}"

    def display_label(self) -> str:
        return f"{self.icon} {self.device_name} · {self.label}"


def discover_all_resources(
    kind: str | None = None,
    *,
    driver: Any = None,
    ports: tuple[int, ...] | None = None,
    probe_timeout: float = 0.25,
    include_local_fallback: bool = True,
) -> list[ResourceEntry]:
    """Discover all independent resources across local and SSH-bridged devices."""
    from concurrent.futures import ThreadPoolExecutor

    from .media_hub import ALL_RESOURCE_PORTS, query_resource_server

    target_ports = tuple(ports) if ports is not None else ALL_RESOURCE_PORTS
    local = local_device_profile()
    out: list[ResourceEntry] = []
    seen: set[tuple[str, str, str]] = set()

    def _probe(port: int) -> tuple[int, dict[str, Any] | None]:
        return port, query_resource_server("127.0.0.1", port, timeout=probe_timeout)

    results: list[tuple[int, dict[str, Any] | None]] = []
    if target_ports:
        with ThreadPoolExecutor(max_workers=min(8, len(target_ports))) as pool:
            results = list(pool.map(_probe, target_ports))

    # Prefer local resource service port (7423) before peer ports when deduplicating
    for port, payload in results:
        if not payload:
            continue
        dev = payload.get("device") or {}
        did = str(dev.get("device_id") or f"tcp-{port}")
        dname = str(dev.get("name") or did)
        dkind = str(dev.get("kind") or "desktop")
        icon = str(dev.get("icon") or ("📱" if dkind == "android" else "💻"))
        for r in payload.get("resources") or ():
            rkind = str(r.get("kind") or "")
            rid = str(r.get("id") or "")
            rlabel = str(r.get("label") or rid)
            if not rkind or not rid:
                continue
            key = (did, rkind, rid)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ResourceEntry(
                    device_id=did,
                    device_name=dname,
                    device_kind=dkind,
                    icon=icon,
                    kind=rkind,
                    resource_id=rid,
                    label=rlabel,
                    host="127.0.0.1",
                    port=port,
                    source="resource_svc",
                ),
            )

    if include_local_fallback:
        for cid, clabel in local.cameras:
            key = (local.device_id, "camera", cid)
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=local.device_id,
                        device_name=local.name,
                        device_kind=local.kind,
                        icon=local.icon,
                        kind="camera",
                        resource_id=cid,
                        label=clabel,
                        host="127.0.0.1",
                        port=0,
                        source="local",
                    ),
                )
        if local.has_mic:
            key = (local.device_id, "mic", "default")
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=local.device_id,
                        device_name=local.name,
                        device_kind=local.kind,
                        icon=local.icon,
                        kind="mic",
                        resource_id="default",
                        label="Microphone",
                        host="127.0.0.1",
                        port=0,
                        source="local",
                    ),
                )
        key = (local.device_id, "files", "fs")
        if key not in seen:
            seen.add(key)
            out.append(
                ResourceEntry(
                    device_id=local.device_id,
                    device_name=local.name,
                    device_kind=local.kind,
                    icon=local.icon,
                    kind="files",
                    resource_id="fs",
                    label="Files & Media",
                    host="127.0.0.1",
                    port=0,
                    source="local",
                ),
            )
        for rkind, rid, rlabel in (
            ("speaker", "default", "Speaker"),
            ("clipboard", "default", "Clipboard"),
            ("gpu", "vulkan", "Local GPU"),
        ):
            key = (local.device_id, rkind, rid)
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=local.device_id,
                        device_name=local.name,
                        device_kind=local.kind,
                        icon=local.icon,
                        kind=rkind,
                        resource_id=rid,
                        label=rlabel,
                        host="127.0.0.1",
                        port=0,
                        source="local",
                    ),
                )
        from .ai_resource import discover_local_ai_providers

        for ai_p in discover_local_ai_providers():
            key = (local.device_id, "ai", ai_p.resource_id)
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=local.device_id,
                        device_name=local.name,
                        device_kind=local.kind,
                        icon=local.icon,
                        kind="ai",
                        resource_id=ai_p.resource_id,
                        label=ai_p.label,
                        host="127.0.0.1",
                        port=0,
                        source="local",
                    ),
                )

    peer = driver.distinct_peer_profile(local) if hasattr(driver, "distinct_peer_profile") else None
    if peer is not None:
        peer_cams = peer.cameras or [("1", "Camera")]
        for cid, clabel in peer_cams:
            key = (peer.device_id, "camera", cid)
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=peer.device_id,
                        device_name=peer.name,
                        device_kind=peer.kind,
                        icon=peer.icon,
                        kind="camera",
                        resource_id=cid,
                        label=clabel,
                        host="",
                        port=0,
                        source="peer",
                    ),
                )
        if peer.has_mic:
            key = (peer.device_id, "mic", "default")
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=peer.device_id,
                        device_name=peer.name,
                        device_kind=peer.kind,
                        icon=peer.icon,
                        kind="mic",
                        resource_id="default",
                        label="Microphone",
                        host="",
                        port=0,
                        source="peer",
                    ),
                )
        key = (peer.device_id, "files", "fs")
        if key not in seen:
            seen.add(key)
            out.append(
                ResourceEntry(
                    device_id=peer.device_id,
                    device_name=peer.name,
                    device_kind=peer.kind,
                    icon=peer.icon,
                    kind="files",
                    resource_id="fs",
                    label="Files & Media",
                    host="",
                    port=0,
                    source="peer",
                ),
            )
        for rkind, rid, rlabel in (
            ("speaker", "default", "Speaker"),
            ("clipboard", "default", "Clipboard"),
            ("gpu", "vulkan", f"{peer.name} GPU"),
        ):
            key = (peer.device_id, rkind, rid)
            if key not in seen:
                seen.add(key)
                out.append(
                    ResourceEntry(
                        device_id=peer.device_id,
                        device_name=peer.name,
                        device_kind=peer.kind,
                        icon=peer.icon,
                        kind=rkind,
                        resource_id=rid,
                        label=rlabel,
                        host="",
                        port=0,
                        source="peer",
                    ),
                )

    if kind:
        return [r for r in out if r.kind == kind]
    return out
