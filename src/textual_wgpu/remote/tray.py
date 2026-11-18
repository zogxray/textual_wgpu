"""Ubuntu / GNOME top-bar status panel (AyatanaAppIndicator3) for textual-wgpu Meta-OS."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RESOURCE_PORTS = (7423, 7424, 7425, 7426, 7427)
PHONE_TUNNEL_PORT = 7422
LOCAL_UI_PORT = 7420

_TRAY_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <rect x="2" y="2" width="20" height="20" rx="5" fill="#1F1B26" stroke="#D4AD72" stroke-width="1.6"/>
  <circle cx="8" cy="8.5" r="2.1" fill="#D4AD72"/>
  <circle cx="16" cy="8.5" r="2.1" fill="#7CC6FE"/>
  <circle cx="12" cy="16" r="2.3" fill="#9AE6B4"/>
  <path d="M9.8 9.6L14.2 9.6M8.8 10.4L11.2 14.1M15.2 10.4L12.8 14.1" stroke="#F5F0E8" stroke-width="1.3" stroke-linecap="round"/>
</svg>
"""


def cache_dir() -> Path:
    d = Path.home() / ".cache" / "textual-wgpu"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_tray_icon() -> Path:
    p = cache_dir() / "textual-wgpu-tray.svg"
    if not p.is_file() or p.read_text(encoding="utf-8") != _TRAY_SVG:
        p.write_text(_TRAY_SVG, encoding="utf-8")
    return p


def find_app_python() -> str:
    cached = cache_dir() / "python_bin"
    if cached.is_file():
        p = cached.read_text(encoding="utf-8").strip()
        if p and Path(p).is_file():
            return p
    return sys.executable or "python3"


def find_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def find_viewer_command() -> list[str]:
    root = find_repo_root()
    for rel in ("rust/target/release/tw-viewer", "rust/target/debug/tw-viewer"):
        cand = root / rel
        if cand.is_file():
            return [str(cand)]
    which_v = shutil.which("tw-viewer")
    if which_v:
        return [which_v]
    cargo = shutil.which("cargo")
    manifest = root / "rust" / "Cargo.toml"
    if cargo and manifest.is_file():
        return [cargo, "run", "--manifest-path", str(manifest), "-p", "tw-viewer", "--"]
    return []


def _listening_ports_proc() -> set[int] | None:
    """Read local TCP LISTEN ports from /proc/net/tcp without opening dummy connections."""
    p = Path("/proc/net/tcp")
    if not p.is_file():
        return None
    ports: set[int] = set()
    try:
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            cols = ln.split()
            if len(cols) >= 4 and cols[3] == "0A":  # TCP_LISTEN
                _ip_hex, _, port_hex = cols[1].partition(":")
                if port_hex:
                    ports.add(int(port_hex, 16))
        return ports
    except Exception:  # noqa: BLE001
        return None


def _tcp_open(host: str, port: int, timeout: float = 0.15) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _query_json_line(host: str, port: int, req: dict[str, Any], timeout: float = 0.25) -> dict[str, Any] | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = bytearray()
        while len(buf) < 262144:
            ch = s.recv(1)
            if not ch or ch == b"\n":
                break
            buf.extend(ch)
        if not buf:
            return None
        data = json.loads(buf.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


@dataclass(slots=True)
class TrayDevice:
    device_id: str
    name: str
    kind: str
    icon: str
    port: int
    resources: list[dict[str, str]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TraySnapshot:
    local_res_up: bool
    local_ui_up: bool
    phone_tunnel_up: bool
    devices: list[TrayDevice] = field(default_factory=list)

    def label_text(self) -> str:
        cams = sum(1 for d in self.devices for r in d.resources if r.get("kind") == "camera")
        peers = len([d for d in self.devices if d.port != 7423])
        if self.phone_tunnel_up and peers > 0:
            return f"TW · {peers} peer · {cams} cam"
        if self.phone_tunnel_up:
            return f"TW · Phone :7422"
        if peers > 0:
            return f"TW · {peers} peer · {cams} cam"
        if self.local_res_up:
            total = sum(len(d.resources) for d in self.devices)
            return f"TW · {total} res"
        return "TW · Offline"

    def fingerprint(self) -> str:
        return json.dumps(
            {
                "lr": self.local_res_up,
                "lu": self.local_ui_up,
                "pt": self.phone_tunnel_up,
                "devs": [
                    (d.device_id, d.port, [(r.get("kind"), r.get("id")) for r in d.resources], [s.get("session_id") for s in d.sessions])
                    for d in self.devices
                ],
            },
            sort_keys=True,
        )


def collect_status_snapshot(
    *,
    ports: tuple[int, ...] = RESOURCE_PORTS,
    check_proc_listen: bool = False,
) -> TraySnapshot:
    from concurrent.futures import ThreadPoolExecutor

    listening = _listening_ports_proc() if check_proc_listen else None
    if listening is not None:
        local_ui_up = LOCAL_UI_PORT in listening
        phone_tunnel_up = PHONE_TUNNEL_PORT in listening
        probe_ports = tuple(p for p in ports if p in listening)
    else:
        local_ui_up = False
        phone_tunnel_up = False
        probe_ports = tuple(ports)

    local_res_up = False
    devices: list[TrayDevice] = []
    seen_ids: set[str] = set()

    def _query_port(port: int) -> tuple[int, dict[str, Any] | None, list[dict[str, Any]]]:
        info = _query_json_line("127.0.0.1", port, {"op": "list"}, timeout=0.9)
        if not info or not info.get("ok"):
            return port, None, []
        sess_resp = _query_json_line("127.0.0.1", port, {"op": "sessions"}, timeout=0.5) or {}
        sessions = list(sess_resp.get("sessions") or ()) if sess_resp.get("ok") else []
        return port, info, sessions

    results: list[tuple[int, dict[str, Any] | None, list[dict[str, Any]]]] = []
    if probe_ports:
        with ThreadPoolExecutor(max_workers=min(8, len(probe_ports))) as pool:
            results = list(pool.map(_query_port, probe_ports))

    for port, info, sessions in results:
        if not info or not info.get("ok"):
            continue
        if port == 7423:
            local_res_up = True
        dev = info.get("device") or {}
        did = str(dev.get("device_id") or f"tcp-{port}")
        if did in seen_ids:
            continue
        seen_ids.add(did)
        devices.append(
            TrayDevice(
                device_id=did,
                name=str(dev.get("name") or did),
                kind=str(dev.get("kind") or "desktop"),
                icon=str(dev.get("icon") or ("📱" if dev.get("kind") == "android" else "💻")),
                port=port,
                resources=[r for r in (info.get("resources") or ()) if isinstance(r, dict)],
                sessions=sessions,
            ),
        )

    return TraySnapshot(
        local_res_up=local_res_up,
        local_ui_up=local_ui_up,
        phone_tunnel_up=phone_tunnel_up,
        devices=devices,
    )


def install_desktop_entries(*, autostart: bool = True) -> tuple[Path, Path | None]:
    icon_path = ensure_tray_icon()
    py_sys = shutil.which("python3") or sys.executable
    tray_script = str(Path(__file__).resolve())
    src_root = str(Path(__file__).resolve().parents[2])
    exec_tray = f"env PYTHONPATH={src_root} {py_sys} {tray_script}"

    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    app_desktop = apps_dir / "textual-wgpu-tray.desktop"
    app_desktop.write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=textual-wgpu Status Panel",
                "Comment=Meta-OS top bar status panel & device resource switcher",
                f"Exec={exec_tray}",
                f"Icon={icon_path}",
                "Terminal=false",
                "Categories=Utility;System;",
                "",
            ],
        ),
        encoding="utf-8",
    )

    auto_desktop: Path | None = None
    if autostart:
        auto_dir = Path.home() / ".config" / "autostart"
        auto_dir.mkdir(parents=True, exist_ok=True)
        auto_desktop = auto_dir / "textual-wgpu-tray.desktop"
        auto_desktop.write_text(app_desktop.read_text(encoding="utf-8"), encoding="utf-8")
    return app_desktop, auto_desktop


def _send_unix_ctl(sock_name: str, req: dict[str, Any], timeout: float = 0.25) -> bool:
    sock_path = cache_dir() / sock_name
    if not sock_path.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = bytearray()
        while len(buf) < 4096:
            b = s.recv(1)
            if not b or b == b"\n":
                break
            buf.extend(b)
        if not buf:
            return False
        data = json.loads(buf.decode("utf-8", errors="replace"))
        return bool(isinstance(data, dict) and data.get("ok"))
    except (OSError, json.JSONDecodeError):
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _navigate_or_open(start_mode: str = "overview", *, resource: dict[str, Any] | None = None) -> None:
    if resource is not None:
        if _send_unix_ctl("ui_ctl.sock", {"op": "use_resource", "resource": resource}):
            return
    if _send_unix_ctl("ui_ctl.sock", {"op": "switch_mode", "mode": start_mode}):
        return
    if _send_unix_ctl("viewer_ctl.sock", {"op": "switch_mode", "mode": start_mode}):
        return
    _spawn_app(start_mode)


def _focus_or_spawn_viewer(host: str, port: int) -> None:
    if port == PHONE_TUNNEL_PORT and _send_unix_ctl("viewer_ctl.sock", {"op": "focus"}):
        return
    _spawn_viewer(host, port)


def _spawn_app(start_mode: str = "overview") -> None:
    py = find_app_python()
    env = dict(os.environ)
    env["TEXTUAL_WGPU_START"] = start_mode
    env.pop("TEXTUAL_WGPU_REMOTE", None)
    subprocess.Popen(
        [py, "-m", "textual_wgpu"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _spawn_viewer(host: str, port: int) -> None:
    cmd = find_viewer_command()
    if not cmd:
        return
    subprocess.Popen(
        [*cmd, "--tcp", f"{host}:{int(port)}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _ensure_resource_service_bg() -> None:
    if _tcp_open("127.0.0.1", 7423):
        return
    py = find_app_python()
    subprocess.Popen(
        [py, "-m", "textual_wgpu.remote.media_hub", "--serve-resources"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _test_speaker_chime(port: int) -> None:
    import math
    import struct

    def _run() -> None:
        sr = 16000
        dur = int(sr * 0.28)
        pcm = bytearray(dur * 2)
        for i in range(dur):
            freq = 587.33 if i < dur // 2 else 880.0
            local = (i % (dur // 2)) / max(1, dur // 2)
            env = math.sin(math.pi * min(1.0, local / 0.12)) * math.exp(-3.2 * local)
            val = max(-32767, min(32767, int(math.sin(2.0 * math.pi * freq * i / sr) * env * 14000)))
            struct.pack_into("<h", pcm, i * 2, val)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        try:
            s.connect(("127.0.0.1", int(port)))
            s.sendall(b'{"op":"play","kind":"speaker","sr":16000,"ch":1}\n')
            s.recv(256)
            pkt = struct.pack("<QBI", 0, 1, len(pcm)) + bytes(pcm)
            s.sendall(pkt)
        except OSError:
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass

    threading.Thread(target=_run, daemon=True).start()


def _copy_clipboard_from_port(src_port: int) -> None:
    def _run() -> None:
        resp = _query_json_line("127.0.0.1", src_port, {"op": "clip_get"}, timeout=1.5)
        if not resp or not resp.get("ok"):
            return
        txt = str(resp.get("text") or "")
        _query_json_line("127.0.0.1", 7423, {"op": "clip_set", "text": txt}, timeout=1.5)

    threading.Thread(target=_run, daemon=True).start()


def run_tray() -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3, GLib, Gtk  # type: ignore[import-not-found]
    except Exception:
        # If invoked inside a virtualenv lacking PyGObject, re-exec under system python3 cleanly.
        sys_py = "/usr/bin/python3"
        if sys.executable != sys_py and Path(sys_py).is_file():
            env = dict(os.environ)
            src_root = str(Path(__file__).resolve().parents[2])
            prev_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{src_root}:{prev_pp}" if prev_pp else src_root
            os.execvpe(sys_py, [sys_py, str(Path(__file__).resolve())], env)
        raise

    icon_path = ensure_tray_icon()
    ind = AyatanaAppIndicator3.Indicator.new(
        "textual-wgpu-metaos",
        str(icon_path),
        AyatanaAppIndicator3.IndicatorCategory.SYSTEM_SERVICES,
    )
    ind.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
    ind.set_title("textual-wgpu Meta-OS")

    menu = Gtk.Menu()
    state: dict[str, Any] = {"fp": "", "label": "", "menu_open": False, "pending": None}

    def _populate_menu(snap: TraySnapshot) -> None:
        for child in list(menu.get_children()):
            menu.remove(child)

        def _add_label(text: str) -> None:
            it = Gtk.MenuItem(label=text)
            it.set_sensitive(False)
            menu.append(it)

        def _add_item(text: str, cb: Any) -> None:
            it = Gtk.MenuItem(label=text)
            it.connect("activate", lambda *_a: cb())
            menu.append(it)

        _add_label(f"textual-wgpu Meta-OS · {len(snap.devices)} node(s)")
        _add_label(f"  Resource Service (:7423): {'Running' if snap.local_res_up else 'Stopped'}")
        _add_label(f"  Phone Tunnel (:7422): {'Connected' if snap.phone_tunnel_up else 'Waiting'}")
        menu.append(Gtk.SeparatorMenuItem())

        if snap.devices:
            for dev in snap.devices:
                sub = Gtk.Menu()
                parent = Gtk.MenuItem(label=f"{dev.icon} {dev.name} (:{dev.port}) · {len(dev.resources)} res")
                parent.set_submenu(sub)
                for r in dev.resources:
                    rkind = str(r.get("kind") or "")
                    rlabel = str(r.get("label") or r.get("id") or rkind)
                    res_payload = {
                        "device_id": dev.device_id,
                        "device_name": dev.name,
                        "device_kind": dev.kind,
                        "icon": dev.icon,
                        "kind": rkind,
                        "id": str(r.get("id") or ""),
                        "label": rlabel,
                        "host": "127.0.0.1",
                        "port": dev.port,
                    }
                    rit = Gtk.MenuItem(label=f"[{rkind.upper()}] {rlabel}")
                    if rkind == "speaker":
                        rit.connect(
                            "activate",
                            lambda *_a, p=dev.port, rp=res_payload: (
                                _test_speaker_chime(p),
                                _navigate_or_open("media", resource=rp),
                            ),
                        )
                    elif rkind == "clipboard":
                        rit.connect(
                            "activate",
                            lambda *_a, p=dev.port, rp=res_payload: (
                                _copy_clipboard_from_port(p),
                                _navigate_or_open("composer", resource=rp),
                            ),
                        )
                    elif rkind in ("ai", "gpu"):
                        rit.connect("activate", lambda *_a, rp=res_payload: _navigate_or_open("composer", resource=rp))
                    elif rkind in ("camera", "mic", "files"):
                        rit.connect("activate", lambda *_a, rp=res_payload: _navigate_or_open("media", resource=rp))
                    else:
                        rit.connect("activate", lambda *_a: _navigate_or_open("nodes"))
                    sub.append(rit)
                if dev.sessions:
                    sub.append(Gtk.SeparatorMenuItem())
                    for s in dev.sessions:
                        sit = Gtk.MenuItem(label=f"▶ Attach {s.get('name', s.get('app_id'))} (:{s.get('port')})")
                        sit.connect("activate", lambda *_a, p=int(s.get("port") or 7420): _focus_or_spawn_viewer("127.0.0.1", p))
                        sub.append(sit)
                menu.append(parent)
            menu.append(Gtk.SeparatorMenuItem())

        _add_item("🖥 Open Meta-OS Demo (F1)", lambda: _navigate_or_open("overview"))
        _add_item("🎥 Open Media Studio (F7)", lambda: _navigate_or_open("media"))
        _add_item("🌐 Open Nodes & Resources (F8)", lambda: _navigate_or_open("nodes"))
        _add_item("🧩 Open Composer · Eyes/Knowledge/Hands (F9)", lambda: _navigate_or_open("composer"))
        _add_item("📱 Attach Phone Viewer (:7422)", lambda: _focus_or_spawn_viewer("127.0.0.1", PHONE_TUNNEL_PORT))
        menu.append(Gtk.SeparatorMenuItem())
        _add_item("▶ Start Local Resource Service (:7423)", _ensure_resource_service_bg)
        _add_item("⚙ Install Ubuntu Autostart", lambda: install_desktop_entries(autostart=True))
        menu.append(Gtk.SeparatorMenuItem())
        _add_item("Quit Status Panel", Gtk.main_quit)

        menu.show_all()

    def _on_menu_show(*_a: Any) -> None:
        state["menu_open"] = True

    def _on_menu_hide(*_a: Any) -> None:
        state["menu_open"] = False
        pend = state.pop("pending", None)
        if pend is not None:
            _apply_snapshot(pend)

    menu.connect("show", _on_menu_show)
    menu.connect("hide", _on_menu_hide)
    ind.set_menu(menu)

    def _apply_snapshot(snap: TraySnapshot) -> bool:
        lbl = snap.label_text()
        if lbl != state["label"]:
            state["label"] = lbl
            ind.set_label(lbl, "")
        fp = snap.fingerprint()
        if fp != state["fp"]:
            if state["menu_open"] or menu.get_visible():
                state["pending"] = snap
                return False
            state["fp"] = fp
            _populate_menu(snap)
        return False

    def _poll_loop() -> None:
        cached_by_port: dict[int, TrayDevice] = {}
        misses_by_port: dict[int, int] = {}
        while True:
            snap = collect_status_snapshot(check_proc_listen=True)
            listening = _listening_ports_proc()
            seen_ports = {d.port for d in snap.devices}
            for d in snap.devices:
                cached_by_port[d.port] = d
                misses_by_port[d.port] = 0
            # Keep peer devices across brief Wi-Fi SSH timeouts while the tunnel port is still LISTEN
            for port, prev_dev in list(cached_by_port.items()):
                if port in seen_ports:
                    continue
                if listening is not None and port not in listening:
                    cached_by_port.pop(port, None)
                    misses_by_port.pop(port, None)
                    continue
                misses_by_port[port] = misses_by_port.get(port, 0) + 1
                if misses_by_port[port] < 3:
                    snap.devices.append(prev_dev)
                else:
                    cached_by_port.pop(port, None)
                    misses_by_port.pop(port, None)
            snap.devices.sort(key=lambda d: d.port)
            GLib.idle_add(_apply_snapshot, snap)
            time.sleep(4.0)

    initial = collect_status_snapshot(check_proc_listen=True)
    _apply_snapshot(initial)
    threading.Thread(target=_poll_loop, daemon=True).start()
    Gtk.main()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="textual-wgpu-tray")
    p.add_argument("--install-autostart", action="store_true", help="Install Ubuntu .desktop + autostart entry")
    args = p.parse_args(argv)
    if args.install_autostart:
        app_d, auto_d = install_desktop_entries(autostart=True)
        print(f"Installed: {app_d}")
        if auto_d:
            print(f"Autostart: {auto_d}")
        return 0
    return run_tray()


if __name__ == "__main__":
    raise SystemExit(main())
