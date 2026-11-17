"""Binary remote protocol, PriorityMux preemption, chunk reassembly, and DrawOps."""

from __future__ import annotations

from textual_wgpu.remote.protocol import (
    HEADER_STRUCT,
    Frame,
    Op,
    Priority,
    decode_frame_commit,
    decode_hello,
    decode_input_pointer,
    decode_input_wheel,
    decode_layer_patch,
    decode_put_blob,
    decode_resize,
    decode_style_defs,
    decode_vid_nalu,
    encode_frame_commit,
    encode_hello,
    encode_input_pointer,
    encode_input_wheel,
    encode_layer_patch,
    encode_put_blob,
    encode_resize,
    encode_style_defs,
    encode_vid_nalu,
    pack_rgba,
)
from textual_wgpu.remote.transport import FrameReassembler, PriorityMux


def test_protocol_round_trips():
    h = decode_hello(encode_hello(width_scale=1.15, title="demo"))
    assert abs(h[0] - 1.15) < 1e-5
    assert h[2] == "demo"

    styles = [(1, 0xFFFFFFFF, 0, 0, 0, 13), (2, 0xD4AD72FF, 0x1E1923FF, 1, 1, 14)]
    assert decode_style_defs(encode_style_defs(styles)) == styles

    lid, rect, ops = decode_layer_patch(encode_layer_patch("panel:1", (10, 20, 300, 400), b"\x01\x02\x03"))
    assert lid == "panel:1"
    assert rect == (10, 20, 300, 400)
    assert ops == b"\x01\x02\x03"

    order, bg = decode_frame_commit(encode_frame_commit(["a", "b"], pack_rgba((10, 20, 30, 255))))
    assert order == ["a", "b"]
    assert bg == pack_rgba((10, 20, 30, 255))

    bh, bw, bh_px, bdata = decode_put_blob(encode_put_blob(b"1234567890abcdef", 160, 90, b"PNGDATA"))
    assert bh == b"1234567890abcdef" and bw == 160 and bh_px == 90 and bdata == b"PNGDATA"

    assert decode_input_pointer(encode_input_pointer(1, 12.5, 44.0, 1)) == (1, 12.5, 44.0, 1)
    assert decode_input_wheel(encode_input_wheel(10.0, 20.0, -1.0, 45.0)) == (10.0, 20.0, -1.0, 45.0)
    assert decode_resize(encode_resize(1280, 720)) == (1280, 720)

    mat = tuple(float(i) for i in range(16))
    pts, fl, rm, nalu = decode_vid_nalu(encode_vid_nalu(123456, 1, mat, b"\x00\x00\x01"))
    assert pts == 123456 and fl == 1 and rm == mat and nalu == b"\x00\x00\x01"


def test_priority_mux_preemption_and_chunk_reassembly():
    mux = PriorityMux(chunk_size=512, max_media_queued=2)
    big_blob = bytes((i % 251) for i in range(5000))
    mux.push(Frame(op=Op.PUT_BLOB, payload=big_blob, prio=Priority.P3_BULK), compress=False)
    mux.push(Frame(op=Op.LAYER_PATCH, payload=b"ui-delta", prio=Priority.P1_UI))
    mux.push(Frame(op=Op.INPUT_KEY, payload=b"tab", prio=Priority.P0_CONTROL))

    reasm = FrameReassembler()
    received: list[Frame] = []
    while (pkt := mux.pop_packet()) is not None:
        hdr = pkt[: HEADER_STRUCT.size]
        body = pkt[HEADER_STRUCT.size :]
        f = reasm.feed(hdr, body)
        if f is not None:
            received.append(f)

    # P0 control and P1 UI arrive BEFORE the chunked P3 blob finishes!
    assert [f.op for f in received] == [Op.INPUT_KEY, Op.LAYER_PATCH, Op.PUT_BLOB]
    assert received[2].payload == big_blob


def test_priority_mux_drops_stale_non_keyframe_media():
    mux = PriorityMux(chunk_size=256, max_media_queued=2)
    for i in range(5):
        mux.push(Frame(op=Op.VID_NALU, payload=bytes([i]) * 1000, prio=Priority.P2_MEDIA), keyframe=False)
    # Only the latest frame's chunks remain (stale non-keyframes dropped)
    reasm = FrameReassembler()
    frames = []
    while (pkt := mux.pop_packet()) is not None:
        f = reasm.feed(pkt[: HEADER_STRUCT.size], pkt[HEADER_STRUCT.size :])
        if f is not None:
            frames.append(f)
    assert len(frames) == 1
    assert frames[0].payload[0] == 4
