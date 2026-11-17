"""Priority-scheduled binary multiplexer & asyncio stream transport."""

from __future__ import annotations

import asyncio
from collections import deque

from .protocol import (
    FLAG_CHUNK,
    FLAG_CHUNK_END,
    FLAG_ZLIB,
    FLAG_ZSTD,
    HEADER_STRUCT,
    PROTOCOL_VERSION,
    Frame,
    Priority,
    compress_payload,
    decompress_payload,
)


class PriorityMux:
    """Four-lane priority scheduler with chunking on low-priority bulk/media lanes.

    - ``P0_CONTROL`` and ``P1_UI`` always preempt ``P2_MEDIA`` and ``P3_BULK``.
    - Large ``P2`` / ``P3`` frames are split into ``chunk_size`` pieces so a
      ``P0`` keystroke or ``P1`` Chat update never stalls behind a large blob.
    - ``P2_MEDIA`` drops stale queued packets when ``len(q2) > max_media_queued``.
    """

    def __init__(self, *, chunk_size: int = 4096, max_media_queued: int = 2) -> None:
        self.chunk_size = max(256, int(chunk_size))
        self.max_media_queued = max(1, int(max_media_queued))
        self._lanes: list[deque[bytes]] = [deque(), deque(), deque(), deque()]
        self._media_frames: deque[int] = deque()
        self._seq = 0

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    def push(self, frame: Frame, *, compress: bool = True, keyframe: bool = False) -> None:
        prio = max(0, min(3, int(frame.prio)))
        if frame.seq == 0:
            frame.seq = self.next_seq()

        if prio < Priority.P2_MEDIA or len(frame.payload) <= self.chunk_size:
            self._lanes[prio].append(frame.encode(compress=compress))
            return

        # Pre-compress once before chunking if applicable.
        allow = compress and prio != Priority.P2_MEDIA and frame.op != 0x30
        body, cflag = compress_payload(frame.payload, allow_compress=allow)
        total = len(body)
        if total <= self.chunk_size:
            ver_flags = ((frame.version & 0x0F) << 4) | ((frame.flags | cflag) & 0x0F)
            hdr = HEADER_STRUCT.pack(
                ver_flags, prio, frame.op & 0xFFFF, frame.surf_id & 0xFFFFFFFF, frame.seq, total,
            )
            self._lanes[prio].append(hdr + body)
            return

        if prio == Priority.P2_MEDIA and not keyframe and len(self._lanes[prio]) >= self.max_media_queued:
            # Drop stale media chunks so latency never accumulates.
            self._lanes[prio].clear()

        offset = 0
        cs = self.chunk_size
        while offset < total:
            end = min(total, offset + cs)
            is_last = end >= total
            flags = cflag | FLAG_CHUNK | (FLAG_CHUNK_END if is_last else 0)
            ver_flags = ((frame.version & 0x0F) << 4) | (flags & 0x0F)
            chunk = body[offset:end]
            hdr = HEADER_STRUCT.pack(
                ver_flags, prio, frame.op & 0xFFFF, frame.surf_id & 0xFFFFFFFF, frame.seq, len(chunk),
            )
            self._lanes[prio].append(hdr + chunk)
            offset = end

    def pop_packet(self) -> bytes | None:
        for lane in self._lanes:
            if lane:
                return lane.popleft()
        return None

    def pending_count(self) -> int:
        return sum(len(lane) for lane in self._lanes)


class FrameReassembler:
    """Reassemble possibly chunked frames arriving from ``PriorityMux``."""

    def __init__(self) -> None:
        self._partials: dict[tuple[int, int, int, int], tuple[int, bytearray]] = {}

    def feed(self, header_bytes: bytes, body: bytes) -> Frame | None:
        ver_flags, prio, op, surf_id, seq, _plen = HEADER_STRUCT.unpack(header_bytes)
        version = (ver_flags >> 4) & 0x0F
        flags = ver_flags & 0x0F
        if not (flags & FLAG_CHUNK):
            raw = decompress_payload(body, flags)
            return Frame(
                op=op,
                payload=raw,
                prio=prio,
                surf_id=surf_id,
                seq=seq,
                flags=flags & ~(FLAG_ZSTD | FLAG_ZLIB),
                version=version,
            )
        key = (prio, op, surf_id, seq)
        comp_flags = flags & (FLAG_ZSTD | FLAG_ZLIB)
        entry = self._partials.get(key)
        if entry is None:
            buf = bytearray(body)
            self._partials[key] = (comp_flags, buf)
        else:
            comp_flags = entry[0] or comp_flags
            entry[1].extend(body)
            buf = entry[1]
        if flags & FLAG_CHUNK_END:
            self._partials.pop(key, None)
            raw = decompress_payload(bytes(buf), comp_flags)
            return Frame(
                op=op,
                payload=raw,
                prio=prio,
                surf_id=surf_id,
                seq=seq,
                flags=0,
                version=version or PROTOCOL_VERSION,
            )
        return None


class AsyncStreamTransport:
    """Asyncio StreamReader/StreamWriter wrapper backed by ``PriorityMux``."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        chunk_size: int = 4096,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.mux = PriorityMux(chunk_size=chunk_size)
        self.reassembler = FrameReassembler()
        self._closed = False

    def send(self, frame: Frame, *, compress: bool = True, keyframe: bool = False) -> None:
        if self._closed:
            return
        self.mux.push(frame, compress=compress, keyframe=keyframe)

    async def flush(self) -> None:
        if self._closed:
            return
        wrote = False
        try:
            while (pkt := self.mux.pop_packet()) is not None:
                self.writer.write(pkt)
                wrote = True
            if wrote:
                await self.writer.drain()
        except (ConnectionError, OSError, EOFError):
            self._closed = True

    async def send_now(self, frame: Frame, *, compress: bool = True, keyframe: bool = False) -> None:
        self.send(frame, compress=compress, keyframe=keyframe)
        await self.flush()

    async def recv(self) -> Frame | None:
        while True:
            try:
                hdr = await self.reader.readexactly(HEADER_STRUCT.size)
            except (asyncio.IncompleteReadError, ConnectionError, OSError, EOFError):
                self._closed = True
                return None
            _vf, _p, _op, _sid, _seq, plen = HEADER_STRUCT.unpack(hdr)
            try:
                body = await self.reader.readexactly(plen) if plen else b""
            except (asyncio.IncompleteReadError, ConnectionError, OSError, EOFError):
                self._closed = True
                return None
            frame = self.reassembler.feed(hdr, body)
            if frame is not None:
                return frame

    def close(self) -> None:
        self._closed = True
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001
            pass
