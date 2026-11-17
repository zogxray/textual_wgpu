"""Multi-session App Broker & SSH-tunneled session attachment for Meta-OS window mobility."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    app_id: str
    name: str
    port: int
    pid: int = 0
    started_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pick_free_port(start: int = 7430, end: int = 7499) -> int:
    for p in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = int(s.getsockname()[1])
    s.close()
    return p


def _tcp_ready(host: str, port: int, timeout: float = 0.08) -> bool:
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


class SessionBroker:
    """Manages independent app sessions per device so apps never preempt each other."""

    def __init__(self, *, spawner: Callable[[str, str, int], Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[SessionInfo, Any]] = {}
        self._spawner = spawner

    def spawn_session(self, app_id: str = "demo", args: str = "", *, width: int = 1100, height: int = 720) -> SessionInfo:
        from .directory import resolve_app_entry

        entry = resolve_app_entry(app_id)
        if entry is None:
            raise ValueError(f"Unknown app: {app_id}")
        target = "textual_wgpu.demo:build_demo_app" if entry.kind == "mode" else entry.target
        port = _pick_free_port()
        sid = uuid.uuid4().hex[:8]

        if self._spawner is not None:
            handle = self._spawner(target, args, port)
            pid = getattr(handle, "pid", 0) or os.getpid()
            info = SessionInfo(
                session_id=sid,
                app_id=entry.app_id,
                name=entry.name,
                port=port,
                pid=int(pid),
                started_at=time.time(),
            )
            with self._lock:
                self._sessions[sid] = (info, handle)
            return info

        cmd = [
            sys.executable,
            "-m",
            "textual_wgpu.remote.cli",
            "serve-tcp",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--app",
            target,
            "--width",
            str(int(width)),
            "--height",
            str(int(height)),
            "--no-advertise",
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Session exited early (rc={proc.returncode})")
            if _tcp_ready("127.0.0.1", port):
                break
            time.sleep(0.05)
        info = SessionInfo(
            session_id=sid,
            app_id=entry.app_id,
            name=entry.name,
            port=port,
            pid=int(proc.pid),
            started_at=time.time(),
        )
        with self._lock:
            self._sessions[sid] = (info, proc)
        return info

    def list_sessions(self) -> list[SessionInfo]:
        alive: list[SessionInfo] = []
        with self._lock:
            dead_ids: list[str] = []
            for sid, (info, handle) in self._sessions.items():
                if hasattr(handle, "poll") and handle.poll() is not None:
                    dead_ids.append(sid)
                    continue
                if hasattr(handle, "is_alive") and not handle.is_alive():
                    dead_ids.append(sid)
                    continue
                alive.append(info)
            for sid in dead_ids:
                self._sessions.pop(sid, None)
        return alive

    def stop_session(self, session_id: str) -> bool:
        with self._lock:
            item = self._sessions.pop(session_id, None)
        if item is None:
            return False
        _info, handle = item
        for m in ("terminate", "close", "stop"):
            fn = getattr(handle, m, None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
                break
        return True

    def close_all(self) -> None:
        for s in list(self.list_sessions()):
            self.stop_session(s.session_id)


def start_session_tcp_relay(res_host: str, res_port: int, target_port: int) -> tuple[int, Callable[[], None]]:
    """Open a local 127.0.0.1 TCP port that tunnels through ``res_host:res_port`` to ``target_port``."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.25)
    local_port = int(srv.getsockname()[1])
    stop_ev = threading.Event()

    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while not stop_ev.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _handle(client: socket.socket) -> None:
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remote.settimeout(4.0)
            remote.connect((res_host, int(res_port)))
            remote.sendall((json.dumps({"op": "tunnel_tcp", "port": int(target_port)}) + "\n").encode("utf-8"))
            buf = bytearray()
            while len(buf) < 4096:
                b = remote.recv(1)
                if not b or b == b"\n":
                    break
                buf.extend(b)
            meta = json.loads(buf.decode("utf-8", errors="replace") or "{}")
            if not meta.get("ok"):
                client.close()
                remote.close()
                return
            remote.settimeout(None)
            client.settimeout(None)
            t1 = threading.Thread(target=_pump, args=(client, remote), daemon=True)
            t2 = threading.Thread(target=_pump, args=(remote, client), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass
            try:
                remote.close()
            except OSError:
                pass

    def _accept() -> None:
        while not stop_ev.is_set():
            try:
                c, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=_handle, args=(c,), daemon=True).start()

    threading.Thread(target=_accept, daemon=True).start()

    def _close() -> None:
        stop_ev.set()
        try:
            srv.close()
        except OSError:
            pass

    return local_port, _close
