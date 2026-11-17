"""CLI & persistent UNIX-socket session host for remote ``textual-wgpu``."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .client import RemoteViewer, spawn_ssh_transport, spawn_subprocess_transport, spawn_tcp_transport
from .server_driver import RemoteServerDriver
from .transport import AsyncStreamTransport


def _load_target(spec: str) -> Callable[[], Any]:
    """Resolve ``module:func`` (defaults to ``textual_wgpu.demo:build_demo_app``)."""
    mod_name, _, attr = spec.partition(":")
    if not attr:
        attr = "build_demo_app"
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, attr)
    return fn


async def run_stdio_server(app: Any, *, size: tuple[int, int] = (1100, 720)) -> None:
    """Serve ``app`` over stdin/stdout binary transport."""
    raw_out = sys.stdout.buffer
    raw_in = sys.stdin.buffer
    # Guard binary stdout from stray print() calls in user apps.
    sys.stdout = sys.stderr
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, raw_in)
    w_transport, w_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin,
        raw_out,
    )
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
    tx = AsyncStreamTransport(reader, writer)
    drv = RemoteServerDriver(app, tx, size=size)
    await drv.start()
    try:
        while drv.app._running:
            await asyncio.sleep(0.05)
    finally:
        await drv.stop()


def run_app_remote(app: Any, spec: str) -> None:
    """Entry point used by ``App.run()`` when ``TEXTUAL_WGPU_REMOTE`` is set."""
    from .directory import record_python_executable

    record_python_executable()
    s = (spec or "").strip()
    size = getattr(app, "SIZE", (1100, 720))
    if s in ("stdio", "1", "true"):
        asyncio.run(run_stdio_server(app, size=size))
        return
    if s == "lan":
        asyncio.run(serve_tcp_session(app, host="127.0.0.1", port=7420, size=size, advertise=True))
        return
    if s.startswith("unix:"):
        sock = Path(s[5:]).expanduser()
        asyncio.run(serve_unix_session(app, sock, size=size))
        return
    if s.startswith("tcp:"):
        hp = s[4:]
        host, _, port_s = hp.rpartition(":")
        host = host or "127.0.0.1"
        port = int(port_s) if port_s.isdigit() else 7420
        asyncio.run(serve_tcp_session(app, host=host, port=port, size=size))
        return
    raise ValueError(f"Unsupported TEXTUAL_WGPU_REMOTE={spec!r} (expected 'stdio', 'lan', 'unix:<path>', or 'tcp:<host>:<port>')")


async def _serve_stream_session(
    app: Any,
    drv_holder: list[RemoteServerDriver | None],
    active_lock: asyncio.Lock,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    size: tuple[int, int],
) -> None:
    prev_drv = drv_holder[0]
    if prev_drv is not None:
        # Preempt any stale/previous viewer so a newly connecting viewer attaches immediately.
        prev_drv.transport.close()
        if prev_drv._rx_task is not None and not prev_drv._rx_task.done():
            prev_drv._rx_task.cancel()

    async with active_lock:
        tx = AsyncStreamTransport(reader, writer)
        drv = drv_holder[0]
        if drv is None:
            drv = RemoteServerDriver(app, tx, size=size, detach_on_eof=True)
            drv_holder[0] = drv
            await drv.start()
            while drv.app._running and not tx._closed:
                await asyncio.sleep(0.02)
        else:
            if drv._rx_task is not None and not drv._rx_task.done():
                drv._rx_task.cancel()
                try:
                    await drv._rx_task
                except asyncio.CancelledError:
                    pass
            drv.transport.close()
            drv.transport = tx
            drv.recorder.reset_retained()
            await drv._send_hello()
            await drv.emit_frame()
            drv._rx_task = asyncio.create_task(drv._rx_loop())
            while drv.app._running and not tx._closed:
                await asyncio.sleep(0.02)


async def serve_unix_session(
    app: Any,
    socket_path: Path,
    *,
    size: tuple[int, int] = (1100, 720),
) -> None:
    """Keep ``app`` alive across client reconnects on a UNIX domain socket."""
    socket_path = socket_path.expanduser()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    drv_holder: list[RemoteServerDriver | None] = [None]
    active_lock = asyncio.Lock()

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _serve_stream_session(app, drv_holder, active_lock, reader, writer, size=size)

    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    try:
        await server.serve_forever()
    finally:
        if drv_holder[0] is not None:
            await drv_holder[0].stop()
        server.close()


async def serve_tcp_session(
    app: Any,
    host: str = "127.0.0.1",
    port: int = 7420,
    *,
    size: tuple[int, int] = (1100, 720),
    advertise: bool | None = None,
) -> None:
    """Keep ``app`` alive across client reconnects on a TCP port (default ``127.0.0.1:7420``)."""
    from .directory import LanPresenceBeacon, record_python_executable

    record_python_executable()
    drv_holder: list[RemoteServerDriver | None] = [None]
    active_lock = asyncio.Lock()

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _serve_stream_session(app, drv_holder, active_lock, reader, writer, size=size)

    server = await asyncio.start_server(_handle_client, host=host, port=int(port))
    should_adv = (not os.environ.get("PYTEST_CURRENT_TEST")) if advertise is None else bool(advertise)
    beacon: LanPresenceBeacon | None = None
    if should_adv:
        beacon = LanPresenceBeacon(
            app_title=str(getattr(app, "TITLE", "textual-wgpu") or "textual-wgpu"),
            tcp_port=int(port),
        )
        beacon.start()
    try:
        await server.serve_forever()
    finally:
        if beacon is not None:
            beacon.stop()
        if drv_holder[0] is not None:
            await drv_holder[0].stop()
        server.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="textual-wgpu", description="Remote GPU UI runtime")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_stdio = sub.add_parser("serve-stdio", help="Run app over stdin/stdout binary protocol")
    s_stdio.add_argument("--app", default="textual_wgpu.demo:build_demo_app")
    s_stdio.add_argument("--width", type=int, default=1100)
    s_stdio.add_argument("--height", type=int, default=720)

    s_sock = sub.add_parser("serve", help="Run persistent session daemon on a UNIX socket")
    s_sock.add_argument("--socket", type=Path, required=True)
    s_sock.add_argument("--app", default="textual_wgpu.demo:build_demo_app")
    s_sock.add_argument("--width", type=int, default=1100)
    s_sock.add_argument("--height", type=int, default=720)

    s_att = sub.add_parser("attach", help="Attach local GPU window over SSH")
    s_att.add_argument("host", help="SSH host (e.g. user@workstation)")
    s_att.add_argument("remote_cmd", nargs=argparse.REMAINDER)

    s_loc = sub.add_parser("attach-local", help="Run local windowed client connected to a stdio server subprocess")
    s_loc.add_argument("--app", default="textual_wgpu.demo:build_demo_app")
    s_loc.add_argument("--width", type=int, default=1100)
    s_loc.add_argument("--height", type=int, default=720)

    s_conn = sub.add_parser("connect-socket", help="Bridge stdin/stdout to a UNIX session socket")
    s_conn.add_argument("--socket", type=Path, required=True)

    s_tcp = sub.add_parser("serve-tcp", help="Run persistent session daemon on a TCP port")
    s_tcp.add_argument("--host", default="127.0.0.1")
    s_tcp.add_argument("--port", type=int, default=7420)
    s_tcp.add_argument("--app", default="textual_wgpu.demo:build_demo_app")
    s_tcp.add_argument("--width", type=int, default=1100)
    s_tcp.add_argument("--height", type=int, default=720)

    s_atcp = sub.add_parser("attach-tcp", help="Attach local GPU window to a TCP session")
    s_atcp.add_argument("host", nargs="?", default="127.0.0.1")
    s_atcp.add_argument("port", nargs="?", type=int, default=7420)

    sub.add_parser("nodes", help="List saved Network OS nodes (Workstation, Phone, Cloud)")

    s_node = sub.add_parser("attach-node", help="Connect to a saved node by ID from nodes.json")
    s_node.add_argument("node_id", help="Node ID (e.g. workstation, phone, cloud)")
    return p


async def bridge_stdio_socket(socket_path: Path) -> None:
    """Forward stdin/stdout bytes ↔ a UNIX domain socket (no socat required)."""
    socket_path = socket_path.expanduser()
    if not socket_path.exists():
        print(
            f"Session socket not found: {socket_path}\n"
            f"Start the session first with:\n"
            f"  textual-wgpu serve --socket {socket_path} --app ...",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)
    loop = asyncio.get_running_loop()
    s_reader, s_writer = await asyncio.open_unix_connection(str(socket_path))
    in_reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(in_reader), sys.stdin.buffer)
    w_tr, w_pr = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    out_writer = asyncio.StreamWriter(w_tr, w_pr, in_reader, loop)

    async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while chunk := await src.read(16384):
                dst.write(chunk)
                await dst.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                dst.close()
            except Exception:  # noqa: BLE001
                pass

    await asyncio.gather(_pump(in_reader, s_writer), _pump(s_reader, out_writer))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "serve-stdio":
        app = _load_target(args.app)()
        asyncio.run(run_stdio_server(app, size=(args.width, args.height)))
        return 0
    if args.cmd == "serve":
        app = _load_target(args.app)()
        asyncio.run(serve_unix_session(app, args.socket, size=(args.width, args.height)))
        return 0
    if args.cmd == "serve-tcp":
        app = _load_target(args.app)()
        asyncio.run(serve_tcp_session(app, host=args.host, port=args.port, size=(args.width, args.height)))
        return 0
    if args.cmd == "attach-tcp":  # pragma: no cover
        viewer = RemoteViewer(connector=lambda: spawn_tcp_transport(args.host, args.port))
        viewer.run_windowed()
        return 0
    if args.cmd == "connect-socket":
        asyncio.run(bridge_stdio_socket(args.socket))
        return 0
    if args.cmd == "attach-local":  # pragma: no cover
        cmd = [
            sys.executable, "-m", "textual_wgpu.remote.cli",
            "serve-stdio", "--app", args.app,
            "--width", str(args.width), "--height", str(args.height),
        ]
        viewer = RemoteViewer(
            connector=lambda: spawn_subprocess_transport(cmd),
            size=(args.width, args.height),
        )
        viewer.run_windowed()
        return 0
    if args.cmd == "attach":  # pragma: no cover
        remote_cmd = [c for c in (args.remote_cmd or []) if c != "--"]
        if not remote_cmd:
            remote_cmd = ["python3", "-m", "textual_wgpu.remote.cli", "serve-stdio"]

        viewer = RemoteViewer(connector=lambda: spawn_ssh_transport(args.host, remote_cmd))
        viewer.run_windowed()
        return 0
    if args.cmd == "nodes":
        from .directory import NodeRegistry

        reg = NodeRegistry()
        for n in reg.list_nodes():
            print(f"{n.id:<14} [{n.role:<11}] {n.name:<24} {n.endpoint_label()}")
        return 0
    if args.cmd == "attach-node":  # pragma: no cover
        from .directory import NodeRegistry

        reg = NodeRegistry()
        node = reg.get(args.node_id)
        if node is None:
            print(f"Unknown node {args.node_id!r}. Run 'textual-wgpu nodes' to list saved nodes.", file=sys.stderr)
            return 2
        if node.transport == "tcp":
            viewer = RemoteViewer(connector=lambda: spawn_tcp_transport(node.host, node.port))
        elif node.transport == "local":
            cmd = [sys.executable, "-m", "textual_wgpu.remote.cli", "serve-stdio"]
            viewer = RemoteViewer(connector=lambda: spawn_subprocess_transport(cmd))
        else:
            target = f"{node.user}@{node.host}" if node.user else node.host
            viewer = RemoteViewer(connector=lambda: spawn_ssh_transport(target, [node.remote_cmd]))
        viewer.run_windowed()
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
