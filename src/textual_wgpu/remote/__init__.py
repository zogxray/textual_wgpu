"""Binary multi-plane remote runtime for ``textual-wgpu``."""

from __future__ import annotations

from .client import RemoteViewer
from .protocol import Frame, Op, Priority
from .recorder import DrawRecorder, RemoteSceneState, StyleInterner, apply_remote_frame, replay_ops
from .server_driver import RemoteServerDriver
from .transport import AsyncStreamTransport, FrameReassembler, PriorityMux

__all__ = [
    "AsyncStreamTransport",
    "DrawRecorder",
    "Frame",
    "FrameReassembler",
    "Op",
    "Priority",
    "PriorityMux",
    "RemoteSceneState",
    "RemoteServerDriver",
    "RemoteViewer",
    "StyleInterner",
    "apply_remote_frame",
    "replay_ops",
]
