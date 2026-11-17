use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};

use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use thiserror::Error;

pub const PROTOCOL_VERSION: u8 = 1;
pub const HEADER_SIZE: usize = 16;

pub const FLAG_ZSTD: u8 = 0x01;
pub const FLAG_ZLIB: u8 = 0x02;
pub const FLAG_CHUNK: u8 = 0x04;
pub const FLAG_CHUNK_END: u8 = 0x08;

pub const RECT_FLAG_FILL: u8 = 0x01;
pub const RECT_FLAG_OUTLINE: u8 = 0x02;

pub const POINTER_DOWN: u8 = 1;
pub const POINTER_MOVE: u8 = 2;
pub const POINTER_UP: u8 = 3;

pub const PLANE_KIND_NONE: u8 = 0;
pub const PLANE_KIND_VIDEO: u8 = 1;
pub const PLANE_KIND_3D_SERVER: u8 = 2;
pub const PLANE_KIND_3D_CLIENT: u8 = 3;

pub const VID_FLAG_KEYFRAME: u8 = 0x01;
pub const VID_FLAG_RAW_RGBA: u8 = 0x02;
pub const VID_FLAG_HAS_DIMS: u8 = 0x04;
pub const VID_CODEC_RGBA: u8 = 0x00;
pub const VID_CODEC_H264: u8 = 0x10;
pub const VID_CODEC_HEVC: u8 = 0x20;
pub const VID_CODEC_AV1: u8 = 0x30;
pub const VID_CODEC_MASK: u8 = 0xF0;

pub const FONT_MONO: u8 = 0;
pub const FONT_MONO_BOLD: u8 = 1;
pub const FONT_MONO_ITALIC: u8 = 2;
pub const FONT_MONO_BOLD_ITALIC: u8 = 3;
pub const FONT_SANS: u8 = 4;
pub const FONT_SANS_BOLD: u8 = 5;
pub const FONT_BRAILLE: u8 = 6;
pub const FONT_BRAILLE_BOLD: u8 = 7;

#[derive(Debug, Error)]
pub enum ProtoError {
    #[error("truncated frame or payload")]
    Truncated,
    #[error("invalid opcode {0:#x}")]
    InvalidOp(u16),
    #[error("compression error: {0}")]
    Compression(String),
    #[error("utf8 error: {0}")]
    Utf8(#[from] std::string::FromUtf8Error),
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Priority {
    P0Control = 0,
    P1Ui = 1,
    P2Media = 2,
    P3Bulk = 3,
}

impl Priority {
    pub fn from_u8(v: u8) -> Self {
        match v {
            0 => Self::P0Control,
            1 => Self::P1Ui,
            2 => Self::P2Media,
            _ => Self::P3Bulk,
        }
    }
}

#[repr(u16)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Op {
    Hello = 0x01,
    Caps = 0x02,
    Resize = 0x03,
    FsGet = 0x04,
    CamCtl = 0x05,
    MicCtl = 0x06,
    InputKey = 0x10,
    InputChar = 0x11,
    InputPointer = 0x12,
    InputWheel = 0x13,
    ClipSet = 0x14,
    ClipPaste = 0x15,
    OpenClientFile = 0x16,
    ClientFileData = 0x17,
    ListApps = 0x18,
    AppList = 0x19,
    LaunchApp = 0x1A,
    LaunchStatus = 0x1B,
    FsList = 0x1C,
    FsEntries = 0x1D,
    Ping = 0x1E,
    Pong = 0x1F,

    StyleDef = 0x20,
    LayerPatch = 0x21,
    LayerDrop = 0x22,
    FrameCommit = 0x23,
    FxSet = 0x24,
    TitleSet = 0x25,

    PutBlob = 0x30,
    BlobAck = 0x31,
    BufWrite = 0x40,
    VidNalu = 0x41,
    PlaneRect = 0x42,
    AudioPcm = 0x43,
    AudioStop = 0x44,
}

impl Op {
    pub fn from_u16(v: u16) -> Result<Self, ProtoError> {
        match v {
            0x01 => Ok(Self::Hello),
            0x02 => Ok(Self::Caps),
            0x03 => Ok(Self::Resize),
            0x04 => Ok(Self::FsGet),
            0x05 => Ok(Self::CamCtl),
            0x06 => Ok(Self::MicCtl),
            0x10 => Ok(Self::InputKey),
            0x11 => Ok(Self::InputChar),
            0x12 => Ok(Self::InputPointer),
            0x13 => Ok(Self::InputWheel),
            0x14 => Ok(Self::ClipSet),
            0x15 => Ok(Self::ClipPaste),
            0x16 => Ok(Self::OpenClientFile),
            0x17 => Ok(Self::ClientFileData),
            0x18 => Ok(Self::ListApps),
            0x19 => Ok(Self::AppList),
            0x1A => Ok(Self::LaunchApp),
            0x1B => Ok(Self::LaunchStatus),
            0x1C => Ok(Self::FsList),
            0x1D => Ok(Self::FsEntries),
            0x1E => Ok(Self::Ping),
            0x1F => Ok(Self::Pong),
            0x20 => Ok(Self::StyleDef),
            0x21 => Ok(Self::LayerPatch),
            0x22 => Ok(Self::LayerDrop),
            0x23 => Ok(Self::FrameCommit),
            0x24 => Ok(Self::FxSet),
            0x25 => Ok(Self::TitleSet),
            0x30 => Ok(Self::PutBlob),
            0x31 => Ok(Self::BlobAck),
            0x40 => Ok(Self::BufWrite),
            0x41 => Ok(Self::VidNalu),
            0x42 => Ok(Self::PlaneRect),
            0x43 => Ok(Self::AudioPcm),
            0x44 => Ok(Self::AudioStop),
            other => Err(ProtoError::InvalidOp(other)),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Frame {
    pub op: Op,
    pub payload: Vec<u8>,
    pub prio: Priority,
    pub surf_id: u32,
    pub seq: u32,
    pub flags: u8,
    pub version: u8,
}

impl Frame {
    pub fn new(op: Op, payload: Vec<u8>, prio: Priority) -> Self {
        Self {
            op,
            payload,
            prio,
            surf_id: 0,
            seq: 0,
            flags: 0,
            version: PROTOCOL_VERSION,
        }
    }

    pub fn encode(&self, compress: bool) -> Vec<u8> {
        let allow = compress && self.prio != Priority::P2Media && self.op != Op::PutBlob;
        let (body, cflag) = compress_payload(&self.payload, allow);
        let ver_flags = ((self.version & 0x0F) << 4) | ((self.flags | cflag) & 0x0F);
        let mut out = Vec::with_capacity(HEADER_SIZE + body.len());
        pack_header(&mut out, ver_flags, self.prio as u8, self.op as u16, self.surf_id, self.seq, body.len() as u32);
        out.extend_from_slice(&body);
        out
    }

    pub fn decode_parts(header: &[u8; HEADER_SIZE], body: &[u8]) -> Result<Self, ProtoError> {
        let (ver_flags, prio, op_raw, surf_id, seq, _plen) = unpack_header(header);
        let version = (ver_flags >> 4) & 0x0F;
        let flags = ver_flags & 0x0F;
        let op = Op::from_u16(op_raw)?;
        let raw = if (flags & FLAG_CHUNK) == 0 {
            decompress_payload(body, flags)?
        } else {
            body.to_vec()
        };
        Ok(Self {
            op,
            payload: raw,
            prio: Priority::from_u8(prio),
            surf_id,
            seq,
            flags: flags & !(FLAG_ZSTD | FLAG_ZLIB),
            version,
        })
    }
}

pub fn pack_header(
    out: &mut Vec<u8>,
    ver_flags: u8,
    prio: u8,
    op: u16,
    surf_id: u32,
    seq: u32,
    payload_len: u32,
) {
    out.push(ver_flags);
    out.push(prio);
    out.extend_from_slice(&op.to_le_bytes());
    out.extend_from_slice(&surf_id.to_le_bytes());
    out.extend_from_slice(&seq.to_le_bytes());
    out.extend_from_slice(&payload_len.to_le_bytes());
}

pub fn unpack_header(h: &[u8; HEADER_SIZE]) -> (u8, u8, u16, u32, u32, u32) {
    let ver_flags = h[0];
    let prio = h[1];
    let op = u16::from_le_bytes([h[2], h[3]]);
    let surf_id = u32::from_le_bytes([h[4], h[5], h[6], h[7]]);
    let seq = u32::from_le_bytes([h[8], h[9], h[10], h[11]]);
    let plen = u32::from_le_bytes([h[12], h[13], h[14], h[15]]);
    (ver_flags, prio, op, surf_id, seq, plen)
}

pub fn compress_payload(raw: &[u8], allow: bool) -> (Vec<u8>, u8) {
    if !allow || raw.len() < 64 {
        return (raw.to_vec(), 0);
    }
    // Use zlib (universally available in Python stdlib including Android Chaquopy).
    let mut enc = ZlibEncoder::new(Vec::new(), Compression::fast());
    if enc.write_all(raw).is_ok() {
        if let Ok(comp) = enc.finish() {
            if comp.len() < raw.len() {
                return (comp, FLAG_ZLIB);
            }
        }
    }
    (raw.to_vec(), 0)
}

pub fn decompress_payload(payload: &[u8], flags: u8) -> Result<Vec<u8>, ProtoError> {
    if (flags & FLAG_ZSTD) != 0 {
        return zstd::stream::decode_all(payload)
            .map_err(|e| ProtoError::Compression(e.to_string()));
    }
    if (flags & FLAG_ZLIB) != 0 {
        let mut dec = ZlibDecoder::new(payload);
        let mut out = Vec::new();
        dec.read_to_end(&mut out)
            .map_err(|e| ProtoError::Compression(e.to_string()))?;
        return Ok(out);
    }
    Ok(payload.to_vec())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StyleDef {
    pub style_id: u16,
    pub fg: u32,
    pub bg: u32,
    pub flags: u8,
    pub font_id: u8,
    pub size: u8,
}

pub fn unpack_rgba(val: u32) -> [u8; 4] {
    [
        ((val >> 24) & 0xFF) as u8,
        ((val >> 16) & 0xFF) as u8,
        ((val >> 8) & 0xFF) as u8,
        (val & 0xFF) as u8,
    ]
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DrawOp {
    Rect {
        flags: u8,
        x0: i16,
        y0: i16,
        x1: i16,
        y1: i16,
        fill: u32,
        outline: u32,
        width: u16,
    },
    Line {
        x0: i16,
        y0: i16,
        x1: i16,
        y1: i16,
        fill: u32,
        width: u16,
    },
    Text {
        x: i16,
        y: i16,
        style_id: u16,
        text: String,
    },
    ImgBlit {
        hash16: [u8; 16],
        dx: i16,
        dy: i16,
        sx0: i16,
        sy0: i16,
        sx1: i16,
        sy1: i16,
    },
    ClipPush {
        x0: i16,
        y0: i16,
        x1: i16,
        y1: i16,
    },
    ClipPop,
    ClearRect {
        x0: i16,
        y0: i16,
        x1: i16,
        y1: i16,
    },
}

#[inline]
fn ri16(b: &[u8], off: usize) -> i16 {
    i16::from_le_bytes([b[off], b[off + 1]])
}
#[inline]
fn ru16(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}
#[inline]
fn ru32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}
#[inline]
fn ru64(b: &[u8], off: usize) -> u64 {
    u64::from_le_bytes([
        b[off], b[off + 1], b[off + 2], b[off + 3],
        b[off + 4], b[off + 5], b[off + 6], b[off + 7],
    ])
}

pub fn decode_draw_ops(ops: &[u8]) -> Result<Vec<DrawOp>, ProtoError> {
    let mut out = Vec::new();
    let mut pos = 0usize;
    let n = ops.len();
    while pos < n {
        let code = ops[pos];
        match code {
            1 => {
                if pos + 20 > n {
                    return Err(ProtoError::Truncated);
                }
                out.push(DrawOp::Rect {
                    flags: ops[pos + 1],
                    x0: ri16(ops, pos + 2),
                    y0: ri16(ops, pos + 4),
                    x1: ri16(ops, pos + 6),
                    y1: ri16(ops, pos + 8),
                    fill: ru32(ops, pos + 10),
                    outline: ru32(ops, pos + 14),
                    width: ru16(ops, pos + 18),
                });
                pos += 20;
            }
            2 => {
                if pos + 15 > n {
                    return Err(ProtoError::Truncated);
                }
                out.push(DrawOp::Line {
                    x0: ri16(ops, pos + 1),
                    y0: ri16(ops, pos + 3),
                    x1: ri16(ops, pos + 5),
                    y1: ri16(ops, pos + 7),
                    fill: ru32(ops, pos + 9),
                    width: ru16(ops, pos + 13),
                });
                pos += 15;
            }
            3 => {
                if pos + 9 > n {
                    return Err(ProtoError::Truncated);
                }
                let x = ri16(ops, pos + 1);
                let y = ri16(ops, pos + 3);
                let style_id = ru16(ops, pos + 5);
                let tlen = ru16(ops, pos + 7) as usize;
                pos += 9;
                if pos + tlen > n {
                    return Err(ProtoError::Truncated);
                }
                let text = String::from_utf8_lossy(&ops[pos..pos + tlen]).into_owned();
                pos += tlen;
                out.push(DrawOp::Text { x, y, style_id, text });
            }
            4 => {
                if pos + 29 > n {
                    return Err(ProtoError::Truncated);
                }
                let mut hash16 = [0u8; 16];
                hash16.copy_from_slice(&ops[pos + 1..pos + 17]);
                out.push(DrawOp::ImgBlit {
                    hash16,
                    dx: ri16(ops, pos + 17),
                    dy: ri16(ops, pos + 19),
                    sx0: ri16(ops, pos + 21),
                    sy0: ri16(ops, pos + 23),
                    sx1: ri16(ops, pos + 25),
                    sy1: ri16(ops, pos + 27),
                });
                pos += 29;
            }
            5 => {
                if pos + 9 > n {
                    return Err(ProtoError::Truncated);
                }
                out.push(DrawOp::ClipPush {
                    x0: ri16(ops, pos + 1),
                    y0: ri16(ops, pos + 3),
                    x1: ri16(ops, pos + 5),
                    y1: ri16(ops, pos + 7),
                });
                pos += 9;
            }
            6 => {
                pos += 1;
                out.push(DrawOp::ClipPop);
            }
            7 => {
                if pos + 9 > n {
                    return Err(ProtoError::Truncated);
                }
                out.push(DrawOp::ClearRect {
                    x0: ri16(ops, pos + 1),
                    y0: ri16(ops, pos + 3),
                    x1: ri16(ops, pos + 5),
                    y1: ri16(ops, pos + 7),
                });
                pos += 9;
            }
            _ => break,
        }
    }
    Ok(out)
}

pub fn decode_hello(data: &[u8]) -> Result<(f32, [u8; 16], String), ProtoError> {
    if data.len() < 22 {
        return Err(ProtoError::Truncated);
    }
    let ws = f32::from_le_bytes([data[0], data[1], data[2], data[3]]);
    let mut fh = [0u8; 16];
    fh.copy_from_slice(&data[4..20]);
    let tlen = ru16(data, 20) as usize;
    if 22 + tlen > data.len() {
        return Err(ProtoError::Truncated);
    }
    let title = String::from_utf8_lossy(&data[22..22 + tlen]).into_owned();
    Ok((ws, fh, title))
}

pub fn decode_style_defs(data: &[u8]) -> Result<Vec<StyleDef>, ProtoError> {
    if data.len() < 2 {
        return Err(ProtoError::Truncated);
    }
    let count = ru16(data, 0) as usize;
    if 2 + count * 13 > data.len() {
        return Err(ProtoError::Truncated);
    }
    let mut out = Vec::with_capacity(count);
    let mut off = 2;
    for _ in 0..count {
        out.push(StyleDef {
            style_id: ru16(data, off),
            fg: ru32(data, off + 2),
            bg: ru32(data, off + 6),
            flags: data[off + 10],
            font_id: data[off + 11],
            size: data[off + 12],
        });
        off += 13;
    }
    Ok(out)
}

pub fn decode_layer_patch(data: &[u8]) -> Result<(String, [i16; 4], &[u8]), ProtoError> {
    if data.len() < 14 {
        return Err(ProtoError::Truncated);
    }
    let id_len = ru16(data, 0) as usize;
    let rect = [ri16(data, 2), ri16(data, 4), ri16(data, 6), ri16(data, 8)];
    let ops_len = ru32(data, 10) as usize;
    if 14 + id_len + ops_len > data.len() {
        return Err(ProtoError::Truncated);
    }
    let lid = String::from_utf8_lossy(&data[14..14 + id_len]).into_owned();
    let ops = &data[14 + id_len..14 + id_len + ops_len];
    Ok((lid, rect, ops))
}

pub fn decode_frame_commit(data: &[u8]) -> Result<(Vec<String>, u32), ProtoError> {
    if data.len() < 6 {
        return Err(ProtoError::Truncated);
    }
    let bg = ru32(data, 0);
    let count = ru16(data, 4) as usize;
    let mut off = 6usize;
    let mut ids = Vec::with_capacity(count);
    for _ in 0..count {
        if off + 2 > data.len() {
            return Err(ProtoError::Truncated);
        }
        let n = ru16(data, off) as usize;
        off += 2;
        if off + n > data.len() {
            return Err(ProtoError::Truncated);
        }
        ids.push(String::from_utf8_lossy(&data[off..off + n]).into_owned());
        off += n;
    }
    Ok((ids, bg))
}

pub fn decode_put_blob(data: &[u8]) -> Result<([u8; 16], u16, u16, &[u8]), ProtoError> {
    if data.len() < 20 {
        return Err(ProtoError::Truncated);
    }
    let mut h16 = [0u8; 16];
    h16.copy_from_slice(&data[0..16]);
    let w = ru16(data, 16);
    let h = ru16(data, 18);
    Ok((h16, w, h, &data[20..]))
}

pub fn decode_fx(data: &[u8]) -> Result<(f32, f32), ProtoError> {
    if data.len() < 8 {
        return Err(ProtoError::Truncated);
    }
    Ok((
        f32::from_le_bytes([data[0], data[1], data[2], data[3]]),
        f32::from_le_bytes([data[4], data[5], data[6], data[7]]),
    ))
}

pub fn decode_plane_rect(data: &[u8]) -> Result<([i16; 4], u8, u8), ProtoError> {
    if data.len() < 10 {
        return Err(ProtoError::Truncated);
    }
    Ok(([ri16(data, 0), ri16(data, 2), ri16(data, 4), ri16(data, 6)], data[8], data[9]))
}

pub fn decode_buf_write(data: &[u8]) -> Result<(u32, u32, &[u8]), ProtoError> {
    if data.len() < 8 {
        return Err(ProtoError::Truncated);
    }
    Ok((ru32(data, 0), ru32(data, 4), &data[8..]))
}

pub fn decode_vid_nalu(data: &[u8]) -> Result<(u64, u8, [f32; 16], &[u8]), ProtoError> {
    if data.len() < 73 {
        return Err(ProtoError::Truncated);
    }
    let pts = ru64(data, 0);
    let flags = data[8];
    let mut mat = [0.0f32; 16];
    for i in 0..16 {
        let off = 9 + i * 4;
        mat[i] = f32::from_le_bytes([data[off], data[off + 1], data[off + 2], data[off + 3]]);
    }
    Ok((pts, flags, mat, &data[73..]))
}

pub fn decode_audio_pcm(data: &[u8]) -> Result<(u32, u8, u8, &[u8]), ProtoError> {
    if data.len() < 6 {
        return Err(ProtoError::Truncated);
    }
    Ok((ru32(data, 0), data[4], data[5], &data[6..]))
}

pub fn encode_vid_nalu(pts_us: u64, flags: u8, nalu: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(73 + nalu.len());
    out.extend_from_slice(&pts_us.to_le_bytes());
    out.push(flags);
    out.extend_from_slice(&[0u8; 64]);
    out.extend_from_slice(nalu);
    out
}

pub fn encode_audio_pcm(sample_rate: u32, channels: u8, flags: u8, pcm: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(6 + pcm.len());
    out.extend_from_slice(&sample_rate.max(8000).to_le_bytes());
    out.push(channels.max(1));
    out.push(flags);
    out.extend_from_slice(pcm);
    out
}

pub fn encode_client_file(name: &str, data: &[u8]) -> Vec<u8> {
    let nb = name.as_bytes();
    let nlen = nb.len().min(4095);
    let mut out = Vec::with_capacity(2 + nlen + data.len());
    out.extend_from_slice(&(nlen as u16).to_le_bytes());
    out.extend_from_slice(&nb[..nlen]);
    out.extend_from_slice(data);
    out
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppEntry {
    pub id: String,
    pub name: String,
    pub description: String,
    pub kind: String,
}

fn push_str16(out: &mut Vec<u8>, s: &str, max_bytes: usize) {
    let b = s.as_bytes();
    let n = b.len().min(max_bytes).min(65535);
    out.extend_from_slice(&(n as u16).to_le_bytes());
    out.extend_from_slice(&b[..n]);
}

fn read_str16(data: &[u8], off: usize) -> Result<(String, usize), ProtoError> {
    if off + 2 > data.len() {
        return Err(ProtoError::Truncated);
    }
    let n = ru16(data, off) as usize;
    let start = off + 2;
    if start + n > data.len() {
        return Err(ProtoError::Truncated);
    }
    Ok((String::from_utf8_lossy(&data[start..start + n]).into_owned(), start + n))
}

pub fn encode_app_list(apps: &[AppEntry]) -> Vec<u8> {
    let mut out = Vec::with_capacity(2 + apps.len() * 32);
    out.extend_from_slice(&(apps.len().min(65535) as u16).to_le_bytes());
    for a in apps.iter().take(65535) {
        push_str16(&mut out, &a.id, 511);
        push_str16(&mut out, &a.name, 511);
        push_str16(&mut out, &a.description, 1023);
        push_str16(&mut out, &a.kind, 127);
    }
    out
}

pub fn decode_app_list(data: &[u8]) -> Result<Vec<AppEntry>, ProtoError> {
    if data.len() < 2 {
        return Err(ProtoError::Truncated);
    }
    let count = ru16(data, 0) as usize;
    let mut off = 2;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        let (id, o1) = read_str16(data, off)?;
        let (name, o2) = read_str16(data, o1)?;
        let (description, o3) = read_str16(data, o2)?;
        let (kind, o4) = read_str16(data, o3)?;
        off = o4;
        out.push(AppEntry { id, name, description, kind });
    }
    Ok(out)
}

pub fn encode_launch_app(app_id: &str, args: &str) -> Vec<u8> {
    let mut out = Vec::with_capacity(4 + app_id.len() + args.len());
    push_str16(&mut out, app_id, 511);
    push_str16(&mut out, args, 2047);
    out
}

pub fn decode_launch_app(data: &[u8]) -> Result<(String, String), ProtoError> {
    let (id, off) = read_str16(data, 0)?;
    let args = if off + 2 <= data.len() {
        read_str16(data, off)?.0
    } else {
        String::new()
    };
    Ok((id, args))
}

pub fn encode_launch_status(ok: bool, app_id: &str, msg: &str) -> Vec<u8> {
    let mut out = Vec::with_capacity(5 + app_id.len() + msg.len());
    out.push(if ok { 1 } else { 0 });
    push_str16(&mut out, app_id, 511);
    push_str16(&mut out, msg, 1023);
    out
}

pub fn decode_launch_status(data: &[u8]) -> Result<(bool, String, String), ProtoError> {
    if data.is_empty() {
        return Err(ProtoError::Truncated);
    }
    let ok = data[0] != 0;
    let (id, off) = read_str16(data, 1)?;
    let msg = if off + 2 <= data.len() {
        read_str16(data, off)?.0
    } else {
        String::new()
    };
    Ok((ok, id, msg))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FsEntry {
    pub is_dir: bool,
    pub name: String,
    pub path: String,
    pub size: u64,
}

pub fn encode_fs_entries(req_path: &str, entries: &[FsEntry]) -> Vec<u8> {
    let mut out = Vec::with_capacity(64 + entries.len() * 48);
    push_str16(&mut out, req_path, 1023);
    out.extend_from_slice(&(entries.len().min(65535) as u16).to_le_bytes());
    for e in entries.iter().take(65535) {
        out.push(if e.is_dir { 1 } else { 0 });
        push_str16(&mut out, &e.name, 511);
        push_str16(&mut out, &e.path, 1023);
        out.extend_from_slice(&e.size.to_le_bytes());
    }
    out
}

pub fn decode_fs_entries(data: &[u8]) -> Result<(String, Vec<FsEntry>), ProtoError> {
    let (req_path, mut off) = read_str16(data, 0)?;
    if off + 2 > data.len() {
        return Err(ProtoError::Truncated);
    }
    let count = ru16(data, off) as usize;
    off += 2;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        if off >= data.len() {
            return Err(ProtoError::Truncated);
        }
        let is_dir = data[off] != 0;
        off += 1;
        let (name, o1) = read_str16(data, off)?;
        let (path, o2) = read_str16(data, o1)?;
        if o2 + 8 > data.len() {
            return Err(ProtoError::Truncated);
        }
        let size = ru64(data, o2);
        off = o2 + 8;
        out.push(FsEntry { is_dir, name, path, size });
    }
    Ok((req_path, out))
}

pub fn encode_caps(has_gpu: bool, vram_mb: u32, codecs: &str) -> Vec<u8> {
    let cb = codecs.as_bytes();
    let clen = cb.len().min(65535);
    let mut out = Vec::with_capacity(7 + clen);
    out.push(if has_gpu { 1 } else { 0 });
    out.extend_from_slice(&vram_mb.to_le_bytes());
    out.extend_from_slice(&(clen as u16).to_le_bytes());
    out.extend_from_slice(&cb[..clen]);
    out
}

pub fn encode_resize(w: u32, h: u32) -> Vec<u8> {
    let mut out = Vec::with_capacity(8);
    out.extend_from_slice(&w.max(1).to_le_bytes());
    out.extend_from_slice(&h.max(1).to_le_bytes());
    out
}

pub fn encode_input_pointer(kind: u8, x: f32, y: f32, button: u8) -> Vec<u8> {
    let mut out = Vec::with_capacity(10);
    out.push(kind);
    out.extend_from_slice(&x.to_le_bytes());
    out.extend_from_slice(&y.to_le_bytes());
    out.push(button);
    out
}

pub fn encode_input_wheel(x: f32, y: f32, dx: f32, dy: f32) -> Vec<u8> {
    let mut out = Vec::with_capacity(16);
    out.extend_from_slice(&x.to_le_bytes());
    out.extend_from_slice(&y.to_le_bytes());
    out.extend_from_slice(&dx.to_le_bytes());
    out.extend_from_slice(&dy.to_le_bytes());
    out
}

pub struct PriorityMux {
    chunk_size: usize,
    max_media_queued: usize,
    lanes: [VecDeque<Vec<u8>>; 4],
    seq: u32,
}

impl PriorityMux {
    pub fn new(chunk_size: usize, max_media_queued: usize) -> Self {
        Self {
            chunk_size: chunk_size.max(256),
            max_media_queued: max_media_queued.max(1),
            lanes: [VecDeque::new(), VecDeque::new(), VecDeque::new(), VecDeque::new()],
            seq: 0,
        }
    }

    pub fn push(&mut self, mut frame: Frame, compress: bool, keyframe: bool) {
        let p = (frame.prio as usize).min(3);
        if frame.seq == 0 {
            self.seq = self.seq.wrapping_add(1);
            frame.seq = self.seq;
        }
        if p < 2 || frame.payload.len() <= self.chunk_size {
            self.lanes[p].push_back(frame.encode(compress));
            return;
        }
        let allow = compress && p != 2 && frame.op != Op::PutBlob;
        let (body, cflag) = compress_payload(&frame.payload, allow);
        if body.len() <= self.chunk_size {
            let vf = ((frame.version & 0x0F) << 4) | ((frame.flags | cflag) & 0x0F);
            let mut pkt = Vec::with_capacity(HEADER_SIZE + body.len());
            pack_header(&mut pkt, vf, p as u8, frame.op as u16, frame.surf_id, frame.seq, body.len() as u32);
            pkt.extend_from_slice(&body);
            self.lanes[p].push_back(pkt);
            return;
        }
        if p == 2 && !keyframe && self.lanes[p].len() >= self.max_media_queued {
            self.lanes[p].clear();
        }
        let mut off = 0usize;
        while off < body.len() {
            let end = (off + self.chunk_size).min(body.len());
            let last = end >= body.len();
            let flags = cflag | FLAG_CHUNK | if last { FLAG_CHUNK_END } else { 0 };
            let vf = ((frame.version & 0x0F) << 4) | (flags & 0x0F);
            let mut pkt = Vec::with_capacity(HEADER_SIZE + (end - off));
            pack_header(&mut pkt, vf, p as u8, frame.op as u16, frame.surf_id, frame.seq, (end - off) as u32);
            pkt.extend_from_slice(&body[off..end]);
            self.lanes[p].push_back(pkt);
            off = end;
        }
    }

    pub fn pop_packet(&mut self) -> Option<Vec<u8>> {
        for lane in &mut self.lanes {
            if let Some(pkt) = lane.pop_front() {
                return Some(pkt);
            }
        }
        None
    }
}

#[derive(Default)]
pub struct FrameReassembler {
    partials: HashMap<(u8, u16, u32, u32), (u8, Vec<u8>)>,
}

impl FrameReassembler {
    pub fn feed(&mut self, header: &[u8; HEADER_SIZE], body: &[u8]) -> Result<Option<Frame>, ProtoError> {
        let (vf, prio, op_raw, surf_id, seq, _plen) = unpack_header(header);
        let version = (vf >> 4) & 0x0F;
        let flags = vf & 0x0F;
        let op = Op::from_u16(op_raw)?;
        if (flags & FLAG_CHUNK) == 0 {
            let raw = decompress_payload(body, flags)?;
            return Ok(Some(Frame {
                op,
                payload: raw,
                prio: Priority::from_u8(prio),
                surf_id,
                seq,
                flags: flags & !(FLAG_ZSTD | FLAG_ZLIB),
                version,
            }));
        }
        let key = (prio, op_raw, surf_id, seq);
        let comp_flags = flags & (FLAG_ZSTD | FLAG_ZLIB);
        let entry = self.partials.entry(key).or_insert_with(|| (comp_flags, Vec::new()));
        if entry.0 == 0 {
            entry.0 = comp_flags;
        }
        entry.1.extend_from_slice(body);
        if (flags & FLAG_CHUNK_END) != 0 {
            let (cf, buf) = self.partials.remove(&key).unwrap();
            let raw = decompress_payload(&buf, cf)?;
            return Ok(Some(Frame {
                op,
                payload: raw,
                prio: Priority::from_u8(prio),
                surf_id,
                seq,
                flags: 0,
                version,
            }));
        }
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_roundtrip_and_priority_preemption() {
        let mut mux = PriorityMux::new(256, 2);
        let big: Vec<u8> = (0..1200).map(|i| (i % 251) as u8).collect();
        mux.push(Frame::new(Op::PutBlob, big.clone(), Priority::P3Bulk), false, false);
        mux.push(Frame::new(Op::InputKey, b"tab".to_vec(), Priority::P0Control), true, false);

        let mut reasm = FrameReassembler::default();
        let mut ops = Vec::new();
        while let Some(pkt) = mux.pop_packet() {
            let hdr: &[u8; HEADER_SIZE] = pkt[..HEADER_SIZE].try_into().unwrap();
            if let Some(f) = reasm.feed(hdr, &pkt[HEADER_SIZE..]).unwrap() {
                ops.push((f.op, f.payload));
            }
        }
        assert_eq!(ops[0].0, Op::InputKey);
        assert_eq!(ops[0].1, b"tab");
        assert_eq!(ops[1].0, Op::PutBlob);
        assert_eq!(ops[1].1, big);
    }

    #[test]
    fn decode_draw_ops_roundtrip() {
        let mut raw = Vec::new();
        // OP_RECT (20B)
        raw.push(1);
        raw.push(RECT_FLAG_FILL);
        raw.extend_from_slice(&10i16.to_le_bytes());
        raw.extend_from_slice(&20i16.to_le_bytes());
        raw.extend_from_slice(&100i16.to_le_bytes());
        raw.extend_from_slice(&80i16.to_le_bytes());
        raw.extend_from_slice(&0x112233FFu32.to_le_bytes());
        raw.extend_from_slice(&0u32.to_le_bytes());
        raw.extend_from_slice(&1u16.to_le_bytes());
        // OP_CLEAR_RECT (9B)
        raw.push(7);
        raw.extend_from_slice(&12i16.to_le_bytes());
        raw.extend_from_slice(&22i16.to_le_bytes());
        raw.extend_from_slice(&98i16.to_le_bytes());
        raw.extend_from_slice(&78i16.to_le_bytes());
        // OP_TEXT (9B + 2B)
        raw.push(3);
        raw.extend_from_slice(&15i16.to_le_bytes());
        raw.extend_from_slice(&25i16.to_le_bytes());
        raw.extend_from_slice(&5u16.to_le_bytes());
        raw.extend_from_slice(&2u16.to_le_bytes());
        raw.extend_from_slice(b"hi");

        let decoded = decode_draw_ops(&raw).unwrap();
        assert_eq!(decoded.len(), 3);
        assert!(matches!(decoded[0], DrawOp::Rect { x0: 10, fill: 0x112233FF, .. }));
        assert!(matches!(decoded[1], DrawOp::ClearRect { x0: 12, y1: 78, .. }));
        assert!(matches!(&decoded[2], DrawOp::Text { style_id: 5, text, .. } if text == "hi"));
    }

    #[test]
    fn app_directory_roundtrip() {
        let apps = vec![
            AppEntry {
                id: "demo".into(),
                name: "Demo".into(),
                description: "All modes".into(),
                kind: "python".into(),
            },
            AppEntry {
                id: "demo:media".into(),
                name: "Media".into(),
                description: "H.265/H.264".into(),
                kind: "mode".into(),
            },
        ];
        let enc = encode_app_list(&apps);
        let dec = decode_app_list(&enc).unwrap();
        assert_eq!(dec, apps);

        let la = encode_launch_app("demo:media", "--hd");
        assert_eq!(decode_launch_app(&la).unwrap(), ("demo:media".into(), "--hd".into()));

        let ls = encode_launch_status(true, "demo:media", "Switched");
        assert_eq!(decode_launch_status(&ls).unwrap(), (true, "demo:media".into(), "Switched".into()));
    }
}
