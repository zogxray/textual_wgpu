use std::collections::HashMap;
use std::io::Cursor;

pub mod gpu_ui;

use ab_glyph::{Font, FontVec, PxScale, ScaleFont};
use bytemuck::{Pod, Zeroable};
use gpu_ui::{
    compile_layers, BatchKind, GlyphAtlas, QuadInstance, ATLAS_SIZE, QUAD_SHADER,
};
use tw_proto::{
    decode_buf_write, decode_draw_ops, decode_frame_commit, decode_fx, decode_hello,
    decode_layer_patch, decode_plane_rect, decode_put_blob, decode_style_defs, decode_vid_nalu,
    unpack_rgba, DrawOp, Frame, Op, StyleDef, FONT_SANS, FONT_SANS_BOLD, PLANE_KIND_3D_CLIENT,
    PLANE_KIND_NONE, RECT_FLAG_FILL, RECT_FLAG_OUTLINE,
};

#[derive(Debug, Clone)]
pub struct RgbaImage {
    pub width: u32,
    pub height: u32,
    pub data: Vec<u8>,
}

impl RgbaImage {
    pub fn new_filled(width: u32, height: u32, rgba: [u8; 4]) -> Self {
        let w = width.max(1);
        let h = height.max(1);
        let mut data = vec![0u8; (w as usize) * (h as usize) * 4];
        for px in data.chunks_exact_mut(4) {
            px.copy_from_slice(&rgba);
        }
        Self { width: w, height: h, data }
    }

    #[inline]
    fn blend_pixel(&mut self, x: i32, y: i32, src: [u8; 4]) {
        if x < 0 || y < 0 || x >= self.width as i32 || y >= self.height as i32 {
            return;
        }
        let a = src[3] as u32;
        if a == 0 {
            return;
        }
        let idx = ((y as usize) * (self.width as usize) + (x as usize)) * 4;
        if a == 255 {
            self.data[idx..idx + 4].copy_from_slice(&src);
            return;
        }
        let inv = 255 - a;
        self.data[idx] = ((src[0] as u32 * a + self.data[idx] as u32 * inv) / 255) as u8;
        self.data[idx + 1] = ((src[1] as u32 * a + self.data[idx + 1] as u32 * inv) / 255) as u8;
        self.data[idx + 2] = ((src[2] as u32 * a + self.data[idx + 2] as u32 * inv) / 255) as u8;
        self.data[idx + 3] = (a + (self.data[idx + 3] as u32 * inv) / 255).min(255) as u8;
    }

    #[inline]
    fn set_pixel(&mut self, x: i32, y: i32, src: [u8; 4]) {
        if x < 0 || y < 0 || x >= self.width as i32 || y >= self.height as i32 {
            return;
        }
        let idx = ((y as usize) * (self.width as usize) + (x as usize)) * 4;
        self.data[idx..idx + 4].copy_from_slice(&src);
    }

    pub fn fill_rect(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, col: [u8; 4], clear: bool) {
        let rx0 = x0.max(0);
        let ry0 = y0.max(0);
        let rx1 = x1.min(self.width as i32 - 1);
        let ry1 = y1.min(self.height as i32 - 1);
        if rx1 < rx0 || ry1 < ry0 {
            return;
        }
        for y in ry0..=ry1 {
            for x in rx0..=rx1 {
                if clear {
                    self.set_pixel(x, y, col);
                } else {
                    self.blend_pixel(x, y, col);
                }
            }
        }
    }

    pub fn stroke_rect(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, col: [u8; 4], width: i32) {
        let w = width.max(1);
        for t in 0..w {
            let ax0 = x0 + t;
            let ay0 = y0 + t;
            let ax1 = x1 - t;
            let ay1 = y1 - t;
            if ax1 < ax0 || ay1 < ay0 {
                break;
            }
            for x in ax0..=ax1 {
                self.blend_pixel(x, ay0, col);
                self.blend_pixel(x, ay1, col);
            }
            for y in ay0..=ay1 {
                self.blend_pixel(ax0, y, col);
                self.blend_pixel(ax1, y, col);
            }
        }
    }

    pub fn draw_line(&mut self, mut x0: i32, mut y0: i32, x1: i32, y1: i32, col: [u8; 4]) {
        let dx = (x1 - x0).abs();
        let sx = if x0 < x1 { 1 } else { -1 };
        let dy = -(y1 - y0).abs();
        let sy = if y0 < y1 { 1 } else { -1 };
        let mut err = dx + dy;
        loop {
            self.blend_pixel(x0, y0, col);
            if x0 == x1 && y0 == y1 {
                break;
            }
            let e2 = 2 * err;
            if e2 >= dy {
                err += dy;
                x0 += sx;
            }
            if e2 <= dx {
                err += dx;
                y0 += sy;
            }
        }
    }

    pub fn blit_crop(&mut self, src: &RgbaImage, dx: i32, dy: i32, sx0: i32, sy0: i32, sx1: i32, sy1: i32) {
        let cx0 = sx0.max(0);
        let cy0 = sy0.max(0);
        let cx1 = sx1.min(src.width as i32);
        let cy1 = sy1.min(src.height as i32);
        for sy in cy0..cy1 {
            let ty = dy + (sy - cy0);
            if ty < 0 || ty >= self.height as i32 {
                continue;
            }
            for sx in cx0..cx1 {
                let tx = dx + (sx - cx0);
                if tx < 0 || tx >= self.width as i32 {
                    continue;
                }
                let si = ((sy as usize) * (src.width as usize) + (sx as usize)) * 4;
                let px = [src.data[si], src.data[si + 1], src.data[si + 2], src.data[si + 3]];
                self.blend_pixel(tx, ty, px);
            }
        }
    }

    pub fn composite_over(&mut self, layer: &RgbaImage, dx: i32, dy: i32) {
        self.blit_crop(layer, dx, dy, 0, 0, layer.width as i32, layer.height as i32);
    }
}

#[derive(Debug, Clone)]
pub struct YuvPlaneData {
    pub width: u32,
    pub height: u32,
    pub y: Vec<u8>,
    pub u: Vec<u8>,
    pub v: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct PlaneData {
    pub rect: [i16; 4],
    pub kind: u8,
    pub flags: u8,
    pub rgba: Vec<u8>,
    pub yuv: Option<YuvPlaneData>,
    pub size: (u32, u32),
}

pub struct SceneState {
    pub styles: HashMap<u16, StyleDef>,
    pub blobs: HashMap<[u8; 16], RgbaImage>,
    pub font_mono: Option<FontVec>,
    pub font_sans: Option<FontVec>,
    pub mono_hash: [u8; 16],
    pub layers: HashMap<String, ([i16; 4], Vec<DrawOp>)>,
    pub order: Vec<String>,
    pub bg_rgba: [u8; 4],
    pub title: String,
    pub width_scale: f32,
    pub fx: (f32, f32),
    pub clipboard: String,
    pub planes: HashMap<u32, PlaneData>,
    pub buffers: HashMap<(u32, u32), Vec<u8>>,
    pub last_audio: Option<(u32, u8, Vec<u8>)>,
}

impl Default for SceneState {
    fn default() -> Self {
        let mut s = Self {
            styles: HashMap::new(),
            blobs: HashMap::new(),
            font_mono: None,
            font_sans: None,
            mono_hash: [0; 16],
            layers: HashMap::new(),
            order: Vec::new(),
            bg_rgba: [20, 20, 24, 255],
            title: "textual-wgpu".to_string(),
            width_scale: 1.0,
            fx: (0.0, 0.0),
            clipboard: String::new(),
            planes: HashMap::new(),
            buffers: HashMap::new(),
            last_audio: None,
        };
        s.load_fallback_fonts();
        s
    }
}

impl SceneState {
    fn load_fallback_fonts(&mut self) {
        for p in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/system/fonts/DroidSansMono.ttf",
        ] {
            if let Ok(bytes) = std::fs::read(p) {
                if let Ok(f) = FontVec::try_from_vec(bytes) {
                    self.font_mono = Some(f);
                    break;
                }
            }
        }
        for p in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/system/fonts/Roboto-Regular.ttf",
        ] {
            if let Ok(bytes) = std::fs::read(p) {
                if let Ok(f) = FontVec::try_from_vec(bytes) {
                    self.font_sans = Some(f);
                    break;
                }
            }
        }
    }

    pub fn apply_frame(&mut self, frame: &Frame) -> bool {
        match frame.op {
            Op::Hello => {
                if let Ok((ws, fh, title)) = decode_hello(&frame.payload) {
                    self.width_scale = ws;
                    self.mono_hash = fh;
                    if !title.is_empty() {
                        self.title = title;
                    }
                }
                false
            }
            Op::TitleSet => {
                self.title = String::from_utf8_lossy(&frame.payload).into_owned();
                false
            }
            Op::FxSet => {
                if let Ok(fx) = decode_fx(&frame.payload) {
                    self.fx = fx;
                }
                false
            }
            Op::StyleDef => {
                if let Ok(defs) = decode_style_defs(&frame.payload) {
                    for d in defs {
                        self.styles.insert(d.style_id, d);
                    }
                }
                false
            }
            Op::PutBlob => {
                if let Ok((h16, bw, bh, data)) = decode_put_blob(&frame.payload) {
                    if bw == 0 && bh == 0 {
                        if let Ok(f) = FontVec::try_from_vec(data.to_vec()) {
                            if h16 == self.mono_hash || self.font_mono.is_none() {
                                self.font_mono = Some(f);
                            } else {
                                self.font_sans = Some(f);
                            }
                        }
                    } else if let Some(im) = decode_png_rgba(data) {
                        self.blobs.insert(h16, im);
                    }
                }
                false
            }
            Op::LayerPatch => {
                if let Ok((lid, rect, ops_bytes)) = decode_layer_patch(&frame.payload) {
                    if let Ok(ops) = decode_draw_ops(ops_bytes) {
                        self.layers.insert(lid, (rect, ops));
                    }
                }
                false
            }
            Op::LayerDrop => {
                let lid = String::from_utf8_lossy(&frame.payload).into_owned();
                self.layers.remove(&lid);
                false
            }
            Op::FrameCommit => {
                if let Ok((order, bg)) = decode_frame_commit(&frame.payload) {
                    self.order = order;
                    self.bg_rgba = unpack_rgba(bg);
                    return true;
                }
                false
            }
            Op::ClipSet => {
                self.clipboard = String::from_utf8_lossy(&frame.payload).into_owned();
                false
            }
            Op::AudioPcm => {
                if let Ok((sr, ch, _fl, pcm)) = tw_proto::decode_audio_pcm(&frame.payload) {
                    self.last_audio = Some((sr, ch, pcm.to_vec()));
                }
                false
            }
            Op::AudioStop => {
                self.last_audio = None;
                false
            }
            Op::PlaneRect => {
                if let Ok((rect, kind, flags)) = decode_plane_rect(&frame.payload) {
                    if kind == PLANE_KIND_NONE || rect[2] <= rect[0] || rect[3] <= rect[1] {
                        self.planes.remove(&frame.surf_id);
                    } else {
                        let e = self.planes.entry(frame.surf_id).or_insert(PlaneData {
                            rect,
                            kind,
                            flags,
                            rgba: Vec::new(),
                            yuv: None,
                            size: (0, 0),
                        });
                        e.rect = rect;
                        e.kind = kind;
                        e.flags = flags;
                    }
                }
                false
            }
            Op::BufWrite => {
                if let Ok((buf_id, offset, data)) = decode_buf_write(&frame.payload) {
                    let buf = self.buffers.entry((frame.surf_id, buf_id)).or_default();
                    let end = offset as usize + data.len();
                    if buf.len() < end {
                        buf.resize(end, 0);
                    }
                    buf[offset as usize..end].copy_from_slice(data);
                    if let Some(pl) = self.planes.get_mut(&frame.surf_id) {
                        if pl.kind == PLANE_KIND_3D_CLIENT && buf.len() >= 64 {
                            let pw = (pl.rect[2] - pl.rect[0]).max(8) as u32;
                            let ph = (pl.rect[3] - pl.rect[1]).max(8) as u32;
                            let mut m = [0.0f32; 16];
                            for (i, chunk) in buf[..64].chunks_exact(4).enumerate() {
                                m[i] = f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
                            }
                            let yaw = if m[0] != 0.0 || m[1] != 0.0 { m[1].atan2(m[0]) } else { 0.55 };
                            let pitch = if m[5] != 0.0 || m[6] != 0.0 { (-m[6]).atan2(m[5]) } else { 0.35 };
                            let zoom = m[14].clamp(0.3, 3.0);
                            pl.rgba = render_mesh_rgba(pw, ph, yaw, pitch, zoom, [30, 30, 36, 255], [108, 178, 235]);
                            pl.size = (pw, ph);
                            return true;
                        }
                    }
                }
                false
            }
            Op::VidNalu => {
                if let Ok((_pts, flags, _mat, raw)) = decode_vid_nalu(&frame.payload) {
                    let e = self.planes.entry(frame.surf_id).or_insert(PlaneData {
                        rect: [0, 0, 0, 0],
                        kind: 0,
                        flags,
                        rgba: Vec::new(),
                        yuv: None,
                        size: (0, 0),
                    });
                    if (flags & 0x04) != 0 && raw.len() >= 4 {
                        let fw = u16::from_le_bytes([raw[0], raw[1]]) as u32;
                        let fh = u16::from_le_bytes([raw[2], raw[3]]) as u32;
                        let px = &raw[4..];
                        if fw > 0 && fh > 0 && px.len() == (fw * fh * 4) as usize {
                            e.rgba = px.to_vec();
                            e.yuv = None;
                            e.size = (fw, fh);
                        }
                    } else if (flags & 0x02) != 0 {
                        let pw = (e.rect[2] - e.rect[0]).max(1) as u32;
                        let ph = (e.rect[3] - e.rect[1]).max(1) as u32;
                        if raw.len() == (pw * ph * 4) as usize {
                            e.rgba = raw.to_vec();
                            e.yuv = None;
                            e.size = (pw, ph);
                        }
                    }
                    return true;
                }
                false
            }
            _ => false,
        }
    }

    pub fn push_decoded_plane(&mut self, surf_id: u32, w: u32, h: u32, rgba: Vec<u8>) {
        if w == 0 || h == 0 || rgba.len() != (w as usize * h as usize * 4) {
            return;
        }
        let e = self.planes.entry(surf_id).or_insert(PlaneData {
            rect: [0, 0, w as i16, h as i16],
            kind: 1,
            flags: 0,
            rgba: Vec::new(),
            yuv: None,
            size: (0, 0),
        });
        e.rgba = rgba;
        e.yuv = None;
        e.size = (w, h);
    }

    #[allow(clippy::too_many_arguments)]
    pub fn push_decoded_yuv420(
        &mut self,
        surf_id: u32,
        w: u32,
        h: u32,
        y: &[u8],
        u: &[u8],
        v: &[u8],
        y_stride: usize,
        u_stride: usize,
        v_stride: usize,
        uv_pix_stride: usize,
    ) {
        if w < 2 || h < 2 {
            return;
        }
        let wu = w as usize;
        let hu = h as usize;
        let cw = (wu / 2).max(1);
        let ch = (hu / 2).max(1);

        let mut y_out = vec![16u8; wu * hu];
        if y_stride == wu && y.len() >= wu * hu {
            y_out.copy_from_slice(&y[..wu * hu]);
        } else {
            for row in 0..hu {
                let src_off = row * y_stride;
                if src_off < y.len() {
                    let avail = (y.len() - src_off).min(wu);
                    y_out[row * wu..row * wu + avail].copy_from_slice(&y[src_off..src_off + avail]);
                }
            }
        }

        let mut u_out = vec![128u8; cw * ch];
        let mut v_out = vec![128u8; cw * ch];
        if uv_pix_stride == 1 && u_stride == cw && v_stride == cw && u.len() >= cw * ch && v.len() >= cw * ch {
            u_out.copy_from_slice(&u[..cw * ch]);
            v_out.copy_from_slice(&v[..cw * ch]);
        } else {
            for row in 0..ch {
                let ub = row * u_stride;
                let vb = row * v_stride;
                let dst = row * cw;
                for col in 0..cw {
                    let off = col * uv_pix_stride;
                    if let Some(&val) = u.get(ub + off) {
                        u_out[dst + col] = val;
                    }
                    if let Some(&val) = v.get(vb + off) {
                        v_out[dst + col] = val;
                    }
                }
            }
        }

        let e = self.planes.entry(surf_id).or_insert(PlaneData {
            rect: [0, 0, w as i16, h as i16],
            kind: 1,
            flags: 0,
            rgba: Vec::new(),
            yuv: None,
            size: (0, 0),
        });
        e.yuv = Some(YuvPlaneData {
            width: w,
            height: h,
            y: y_out,
            u: u_out,
            v: v_out,
        });
        e.size = (w, h);
    }

    pub fn set_plane_rotation(&mut self, surf_id: u32, rot_idx: u8) {
        if let Some(e) = self.planes.get_mut(&surf_id) {
            e.flags = (e.flags & !0x0C) | ((rot_idx & 0x03) << 2);
        }
    }

    pub fn render_ui(&self, width: u32, height: u32) -> RgbaImage {
        let mut ui = RgbaImage::new_filled(width, height, self.bg_rgba);
        for lid in &self.order {
            if let Some((_rect, ops)) = self.layers.get(lid) {
                self.replay_ops(&mut ui, ops);
            }
        }
        ui
    }

    fn draw_text_run(&self, img: &mut RgbaImage, x: i32, y: i32, style_id: u16, text: &str, ox: i32, oy: i32) {
        let st = self.styles.get(&style_id).copied().unwrap_or(StyleDef {
            style_id,
            fg: 0xF0E6D8FF,
            bg: 0,
            flags: 0,
            font_id: 0,
            size: 13,
        });
        let fg = unpack_rgba(st.fg);
        let is_sans = st.font_id == FONT_SANS || st.font_id == FONT_SANS_BOLD;
        let font = if is_sans {
            self.font_sans.as_ref().or(self.font_mono.as_ref())
        } else {
            self.font_mono.as_ref().or(self.font_sans.as_ref())
        };
        let Some(font) = font else { return };
        let scale = PxScale::from((st.size as f32).max(8.0));
        let scaled = font.as_scaled(scale);
        let mut pen_x = (x - ox) as f32;
        let baseline_y = (y - oy) as f32 + scaled.ascent();

        for ch in text.chars() {
            let gid = scaled.glyph_id(ch);
            let adv = scaled.h_advance(gid);
            let g = gid.with_scale_and_position(scale, ab_glyph::point(pen_x, baseline_y));
            if let Some(og) = font.outline_glyph(g) {
                let bounds = og.px_bounds();
                og.draw(|gx, gy, cov| {
                    let a = ((cov * fg[3] as f32).round() as u32).min(255) as u8;
                    if a > 0 {
                        img.blend_pixel(bounds.min.x as i32 + gx as i32, bounds.min.y as i32 + gy as i32, [fg[0], fg[1], fg[2], a]);
                    }
                });
            }
            pen_x += adv;
        }
    }

    fn replay_ops(&self, root_img: &mut RgbaImage, ops: &[DrawOp]) {
        struct ClipFrame {
            cx0: i32,
            cy0: i32,
            prev_ox: i32,
            prev_oy: i32,
            prev_img: RgbaImage,
        }
        let mut stack: Vec<ClipFrame> = Vec::new();
        let mut cur = std::mem::replace(root_img, RgbaImage::new_filled(1, 1, [0, 0, 0, 0]));
        let mut ox = 0i32;
        let mut oy = 0i32;

        for op in ops {
            match op {
                DrawOp::Rect { flags, x0, y0, x1, y1, fill, outline, width } => {
                    if (flags & RECT_FLAG_FILL) != 0 {
                        cur.fill_rect(*x0 as i32 - ox, *y0 as i32 - oy, *x1 as i32 - ox, *y1 as i32 - oy, unpack_rgba(*fill), false);
                    }
                    if (flags & RECT_FLAG_OUTLINE) != 0 {
                        cur.stroke_rect(*x0 as i32 - ox, *y0 as i32 - oy, *x1 as i32 - ox, *y1 as i32 - oy, unpack_rgba(*outline), *width as i32);
                    }
                }
                DrawOp::Line { x0, y0, x1, y1, fill, .. } => {
                    cur.draw_line(*x0 as i32 - ox, *y0 as i32 - oy, *x1 as i32 - ox, *y1 as i32 - oy, unpack_rgba(*fill));
                }
                DrawOp::Text { x, y, style_id, text } => {
                    self.draw_text_run(&mut cur, *x as i32, *y as i32, *style_id, text, ox, oy);
                }
                DrawOp::ImgBlit { hash16, dx, dy, sx0, sy0, sx1, sy1 } => {
                    if let Some(blob) = self.blobs.get(hash16) {
                        cur.blit_crop(blob, *dx as i32 - ox, *dy as i32 - oy, *sx0 as i32, *sy0 as i32, *sx1 as i32, *sy1 as i32);
                    }
                }
                DrawOp::ClipPush { x0, y0, x1, y1 } => {
                    let cw = (*x1 as i32 - *x0 as i32).max(1) as u32;
                    let ch = (*y1 as i32 - *y0 as i32).max(1) as u32;
                    let layer = RgbaImage::new_filled(cw, ch, [0, 0, 0, 0]);
                    let prev_img = std::mem::replace(&mut cur, layer);
                    stack.push(ClipFrame { cx0: *x0 as i32, cy0: *y0 as i32, prev_ox: ox, prev_oy: oy, prev_img });
                    ox = *x0 as i32;
                    oy = *y0 as i32;
                }
                DrawOp::ClipPop => {
                    if let Some(cf) = stack.pop() {
                        let child = std::mem::replace(&mut cur, cf.prev_img);
                        cur.composite_over(&child, cf.cx0 - cf.prev_ox, cf.cy0 - cf.prev_oy);
                        ox = cf.prev_ox;
                        oy = cf.prev_oy;
                    }
                }
                DrawOp::ClearRect { x0, y0, x1, y1 } => {
                    cur.fill_rect(*x0 as i32 - ox, *y0 as i32 - oy, *x1 as i32 - ox, *y1 as i32 - oy, [0, 0, 0, 0], true);
                }
            }
        }
        while let Some(cf) = stack.pop() {
            let child = std::mem::replace(&mut cur, cf.prev_img);
            cur.composite_over(&child, cf.cx0 - cf.prev_ox, cf.cy0 - cf.prev_oy);
        }
        *root_img = cur;
    }
}

fn decode_png_rgba(bytes: &[u8]) -> Option<RgbaImage> {
    let mut dec = png::Decoder::new(Cursor::new(bytes));
    dec.set_transformations(png::Transformations::EXPAND | png::Transformations::ALPHA);
    let mut reader = dec.read_info().ok()?;
    let mut buf = vec![0u8; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf).ok()?;
    let slice = &buf[..info.buffer_size()];
    let rgba = match info.color_type {
        png::ColorType::Rgba => slice.to_vec(),
        png::ColorType::Rgb => {
            let mut out = Vec::with_capacity((info.width * info.height * 4) as usize);
            for c in slice.chunks_exact(3) {
                out.extend_from_slice(&[c[0], c[1], c[2], 255]);
            }
            out
        }
        _ => return None,
    };
    Some(RgbaImage { width: info.width, height: info.height, data: rgba })
}

const ICO_VERTS: [[f32; 3]; 12] = {
    // Unnormalized icosahedron vertices (normalized at runtime)
    let p = 1.61803398875;
    [
        [-1.0, p, 0.0], [1.0, p, 0.0], [-1.0, -p, 0.0], [1.0, -p, 0.0],
        [0.0, -1.0, p], [0.0, 1.0, p], [0.0, -1.0, -p], [0.0, 1.0, -p],
        [p, 0.0, -1.0], [p, 0.0, 1.0], [-p, 0.0, -1.0], [-p, 0.0, 1.0],
    ]
};

const ICO_FACES: [[usize; 3]; 20] = [
    [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
    [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
    [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
    [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
];

pub fn render_mesh_rgba(
    width: u32,
    height: u32,
    yaw: f32,
    pitch: f32,
    zoom: f32,
    bg: [u8; 4],
    accent: [u8; 3],
) -> Vec<u8> {
    let mut img = RgbaImage::new_filled(width, height, bg);
    let w = width as f32;
    let h = height as f32;
    let (cy, sy) = (yaw.cos(), yaw.sin());
    let (cp, sp) = (pitch.cos(), pitch.sin());
    let scale = w.min(h) * 0.36 * zoom.clamp(0.3, 3.0);
    let (cx, cy_px) = (w * 0.5, h * 0.5);

    let mut tr = [[0.0f32; 3]; 12];
    for (i, v) in ICO_VERTS.iter().enumerate() {
        let len = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        let (vx, vy, vz) = (v[0] / len, v[1] / len, v[2] / len);
        let x1 = vx * cy + vz * sy;
        let z1 = -vx * sy + vz * cy;
        let y2 = vy * cp - z1 * sp;
        let z2 = vy * sp + z1 * cp;
        tr[i] = [x1, y2, z2];
    }

    let mut tris: Vec<(f32, [[f32; 2]; 3], [u8; 4])> = Vec::new();
    let (lx, ly, lz) = (0.45f32, -0.55f32, 0.70f32);
    for [i0, i1, i2] in ICO_FACES {
        let (p0, p1, p2) = (tr[i0], tr[i1], tr[i2]);
        let (ux, uy, uz) = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]);
        let (wx, wy, wz) = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]);
        let (nx, ny, nz) = (uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx);
        if nz <= 0.0 {
            continue;
        }
        let nlen = (nx * nx + ny * ny + nz * nz).sqrt().max(1e-5);
        let ndotl = (0.25 + 0.75 * ((nx * lx + ny * ly + nz * lz) / nlen).max(0.0)).clamp(0.15, 1.0);
        let col = [
            (accent[0] as f32 * ndotl) as u8,
            (accent[1] as f32 * ndotl) as u8,
            (accent[2] as f32 * ndotl) as u8,
            255,
        ];
        let mut pts = [[0.0f32; 2]; 3];
        for (k, p) in [p0, p1, p2].iter().enumerate() {
            let persp = 3.2 / (3.2 - p[2]).max(0.8);
            pts[k] = [cx + p[0] * scale * persp, cy_px - p[1] * scale * persp];
        }
        tris.push(((p0[2] + p1[2] + p2[2]) / 3.0, pts, col));
    }
    tris.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    for (_z, pts, col) in tris {
        raster_triangle(&mut img, pts, col);
        img.draw_line(pts[0][0] as i32, pts[0][1] as i32, pts[1][0] as i32, pts[1][1] as i32, [255, 255, 255, 65]);
        img.draw_line(pts[1][0] as i32, pts[1][1] as i32, pts[2][0] as i32, pts[2][1] as i32, [255, 255, 255, 65]);
        img.draw_line(pts[2][0] as i32, pts[2][1] as i32, pts[0][0] as i32, pts[0][1] as i32, [255, 255, 255, 65]);
    }
    img.data
}

fn raster_triangle(img: &mut RgbaImage, pts: [[f32; 2]; 3], col: [u8; 4]) {
    let min_x = pts[0][0].min(pts[1][0]).min(pts[2][0]).floor().max(0.0) as i32;
    let max_x = pts[0][0].max(pts[1][0]).max(pts[2][0]).ceil().min((img.width as i32 - 1) as f32) as i32;
    let min_y = pts[0][1].min(pts[1][1]).min(pts[2][1]).floor().max(0.0) as i32;
    let max_y = pts[0][1].max(pts[1][1]).max(pts[2][1]).ceil().min((img.height as i32 - 1) as f32) as i32;
    let edge = |a: [f32; 2], b: [f32; 2], c: [f32; 2]| -> f32 {
        (c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])
    };
    let area = edge(pts[0], pts[1], pts[2]);
    if area.abs() < 1e-4 {
        return;
    }
    let sign = if area > 0.0 { 1.0 } else { -1.0 };
    for y in min_y..=max_y {
        for x in min_x..=max_x {
            let p = [x as f32 + 0.5, y as f32 + 0.5];
            if edge(pts[0], pts[1], p) * sign >= 0.0
                && edge(pts[1], pts[2], p) * sign >= 0.0
                && edge(pts[2], pts[0], p) * sign >= 0.0
            {
                img.blend_pixel(x, y, col);
            }
        }
    }
}

const PLANE_SHADER: &str = r#"
struct VsOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
};

struct CompositorUniforms {
  bloom: f32,
  scanlines: f32,
  has_plane: f32,
  plane_rot: f32,
  plane_rect: vec4f,
  view_rect: vec4f,   // (u0, v0, u1, v1) visible virtual-canvas window into texture
};

@group(0) @binding(0) var samp: sampler;
@group(0) @binding(1) var ui_tex: texture_2d<f32>;
@group(0) @binding(2) var plane_tex: texture_2d<f32>;
@group(0) @binding(3) var<uniform> uni: CompositorUniforms;
@group(0) @binding(4) var y_tex: texture_2d<f32>;
@group(0) @binding(5) var u_tex: texture_2d<f32>;
@group(0) @binding(6) var v_tex: texture_2d<f32>;

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> VsOut {
  var p = array<vec2f, 3>(
    vec2f(-1.0, -1.0),
    vec2f( 3.0, -1.0),
    vec2f(-1.0,  3.0),
  );
  var uv = array<vec2f, 3>(
    vec2f(0.0, 1.0),
    vec2f(2.0, 1.0),
    vec2f(0.0, -1.0),
  );
  var out: VsOut;
  out.pos = vec4f(p[i], 0.0, 1.0);
  out.uv = uv[i];
  return out;
}

fn map_view_uv(screen_uv: vec2f) -> vec2f {
  let vr = uni.view_rect;
  return vec2f(mix(vr.x, vr.z, screen_uv.x), mix(vr.y, vr.w, screen_uv.y));
}

fn sample_composite(uv: vec2f) -> vec4f {
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
    return vec4f(0.06, 0.06, 0.08, 1.0);
  }
  let ui = textureSample(ui_tex, samp, uv);
  if (uni.has_plane > 0.5) {
    let r = uni.plane_rect;
    if (uv.x >= r.x && uv.x <= r.z && uv.y >= r.y && uv.y <= r.w) {
      let puv = vec2f((uv.x - r.x) / max(1e-5, r.z - r.x), (uv.y - r.y) / max(1e-5, r.w - r.y));
      var ruv = puv;
      let rot = i32(round(uni.plane_rot));
      if (rot == 1) {
        ruv = vec2f(puv.y, 1.0 - puv.x);
      } else if (rot == 2) {
        ruv = vec2f(1.0 - puv.x, 1.0 - puv.y);
      } else if (rot == 3) {
        ruv = vec2f(1.0 - puv.y, puv.x);
      }
      var pl: vec4f;
      if (uni.has_plane > 1.5) {
        let yv = max(0.0, textureSample(y_tex, samp, ruv).r - 0.062745);
        let uu = textureSample(u_tex, samp, ruv).r - 0.5;
        let vv = textureSample(v_tex, samp, ruv).r - 0.5;
        pl = vec4f(
          clamp(1.164 * yv + 1.596 * vv, 0.0, 1.0),
          clamp(1.164 * yv - 0.391 * uu - 0.813 * vv, 0.0, 1.0),
          clamp(1.164 * yv + 2.018 * uu, 0.0, 1.0),
          1.0,
        );
      } else {
        pl = textureSample(plane_tex, samp, ruv);
      }
      let rgb = mix(pl.rgb, ui.rgb, ui.a);
      return vec4f(rgb, max(pl.a, ui.a));
    }
  }
  return ui;
}

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4f {
  let uv = map_view_uv(in.uv);
  let color = sample_composite(uv);
  var rgb = color.rgb;
  let bloom = uni.bloom;
  if (bloom > 0.001) {
    let dims = vec2f(textureDimensions(ui_tex));
    let texel = 1.0 / dims;
    var acc = rgb;
    acc += sample_composite(uv + vec2f( texel.x, 0.0)).rgb;
    acc += sample_composite(uv - vec2f( texel.x, 0.0)).rgb;
    acc += sample_composite(uv + vec2f(0.0,  texel.y)).rgb;
    acc += sample_composite(uv - vec2f(0.0,  texel.y)).rgb;
    let blurred = acc / 5.0;
    rgb = 1.0 - (1.0 - rgb) * (1.0 - blurred * bloom);
  }
  let scan = uni.scanlines;
  if (scan > 0.001) {
    let py = uv.y * f32(textureDimensions(ui_tex).y);
    if ((i32(floor(py)) & 1) == 1) {
      rgb = rgb * (1.0 - scan * 0.45);
    }
  }
  return vec4f(rgb, color.a);
}
"#;

#[derive(Debug, Clone, Copy)]
pub struct ViewportTransform {
    pub virtual_size: Option<(u32, u32)>,
    pub zoom: f32,
    pub pan_u: f32,
    pub pan_v: f32,
}

impl Default for ViewportTransform {
    fn default() -> Self {
        Self {
            virtual_size: None,
            zoom: 1.0,
            pan_u: 0.0,
            pan_v: 0.0,
        }
    }
}

impl ViewportTransform {
    pub fn view_rect(&self, surface_size: (u32, u32), server_size: (u32, u32)) -> [f32; 4] {
        let sw = surface_size.0.max(1) as f32;
        let sh = surface_size.1.max(1) as f32;
        let vw = server_size.0.max(1) as f32;
        let vh = server_size.1.max(1) as f32;
        let z = self.zoom.clamp(0.5, 6.0);

        // Aspect-preserving span of server texture visible on screen at zoom z
        let surface_aspect = sw / sh;
        let server_aspect = vw / vh;
        let (mut span_u, mut span_v) = if surface_aspect > server_aspect {
            (surface_aspect / server_aspect / z, 1.0 / z)
        } else {
            (1.0 / z, server_aspect / surface_aspect / z)
        };
        if self.virtual_size.is_none() && (z - 1.0).abs() < 1e-4 {
            span_u = 1.0;
            span_v = 1.0;
        }
        let cx = (0.5 + self.pan_u).clamp(0.0, 1.0);
        let cy = (0.5 + self.pan_v).clamp(0.0, 1.0);
        let u0 = cx - span_u * 0.5;
        let v0 = cy - span_v * 0.5;
        [u0, v0, u0 + span_u, v0 + span_v]
    }

    pub fn screen_to_server(
        &self,
        px: f32,
        py: f32,
        surface_size: (u32, u32),
        server_size: (u32, u32),
    ) -> (f32, f32) {
        let vr = self.view_rect(surface_size, server_size);
        let su = px / surface_size.0.max(1) as f32;
        let sv = py / surface_size.1.max(1) as f32;
        let u = vr[0] + (vr[2] - vr[0]) * su;
        let v = vr[1] + (vr[3] - vr[1]) * sv;
        (
            (u * server_size.0.max(1) as f32).clamp(0.0, (server_size.0.saturating_sub(1)) as f32),
            (v * server_size.1.max(1) as f32).clamp(0.0, (server_size.1.saturating_sub(1)) as f32),
        )
    }

    pub fn apply_pan_zoom(&mut self, zoom_factor: f32, dpx: f32, dpy: f32, surface_size: (u32, u32)) {
        self.zoom = (self.zoom * zoom_factor).clamp(0.5, 6.0);
        let inv_w = 1.0 / (surface_size.0.max(1) as f32 * self.zoom);
        let inv_h = 1.0 / (surface_size.1.max(1) as f32 * self.zoom);
        self.pan_u = (self.pan_u - dpx * inv_w).clamp(-0.5, 0.5);
        self.pan_v = (self.pan_v - dpy * inv_h).clamp(-0.5, 0.5);
    }

    pub fn reset(&mut self) {
        self.zoom = 1.0;
        self.pan_u = 0.0;
        self.pan_v = 0.0;
    }
}

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct UniformData {
    bloom: f32,
    scanlines: f32,
    has_plane: f32,
    plane_rot: f32,
    plane_rect: [f32; 4],
    view_rect: [f32; 4],
}

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct UiUniformData {
    viewport: [f32; 2],
    _pad: [f32; 2],
}

pub struct GpuCompositor {
    sampler: wgpu::Sampler,
    pipeline: wgpu::RenderPipeline,
    uni_buf: wgpu::Buffer,
    ui_tex: wgpu::Texture,
    ui_view: wgpu::TextureView,
    ui_size: (u32, u32),
    plane_tex: wgpu::Texture,
    plane_view: wgpu::TextureView,
    plane_size: (u32, u32),
    y_tex: wgpu::Texture,
    y_view: wgpu::TextureView,
    u_tex: wgpu::Texture,
    u_view: wgpu::TextureView,
    v_tex: wgpu::Texture,
    v_view: wgpu::TextureView,
    yuv_size: (u32, u32),
    bind_group: wgpu::BindGroup,
    fx: (f32, f32),
    has_plane: f32,
    plane_rect_px: [i16; 4],

    // Pure-GPU DrawOp instanced renderer state
    ui_pipeline: wgpu::RenderPipeline,
    clear_pipeline: wgpu::RenderPipeline,
    ui_uni_buf: wgpu::Buffer,
    glyph_tex: wgpu::Texture,
    glyph_view: wgpu::TextureView,
    _dummy_blob_tex: wgpu::Texture,
    ui_bind_group: wgpu::BindGroup,
    blob_cache: HashMap<[u8; 16], (wgpu::Texture, wgpu::BindGroup)>,
    instance_buf: wgpu::Buffer,
    instance_cap: usize,
    atlas: GlyphAtlas,
}

impl GpuCompositor {
    pub fn new(device: &wgpu::Device, format: wgpu::TextureFormat) -> Self {
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let uni_buf = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("tw-uni"),
            size: std::mem::size_of::<UniformData>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("tw-plane-shader"),
            source: wgpu::ShaderSource::Wgsl(PLANE_SHADER.into()),
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("tw-plane-pipeline"),
            layout: None,
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_main"),
                compilation_options: Default::default(),
                buffers: &[],
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_main"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: None,
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
            cache: None,
        });
        let ui_tex = make_ui_tex(device, 1, 1);
        let ui_view = ui_tex.create_view(&Default::default());
        let plane_tex = make_tex(device, 1, 1);
        let plane_view = plane_tex.create_view(&Default::default());
        let y_tex = make_r8_tex(device, 1, 1);
        let y_view = y_tex.create_view(&Default::default());
        let u_tex = make_r8_tex(device, 1, 1);
        let u_view = u_tex.create_view(&Default::default());
        let v_tex = make_r8_tex(device, 1, 1);
        let v_view = v_tex.create_view(&Default::default());
        let bind_group = make_bg(
            device,
            &pipeline,
            &sampler,
            &ui_view,
            &plane_view,
            &uni_buf,
            &y_view,
            &u_view,
            &v_view,
        );

        // Pure-GPU DrawOp instanced pipelines
        let quad_shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("tw-quad-shader"),
            source: wgpu::ShaderSource::Wgsl(QUAD_SHADER.into()),
        });
        let ui_bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("tw-ui-bgl"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 3,
                    visibility: wgpu::ShaderStages::VERTEX,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
            ],
        });
        let ui_pl_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("tw-ui-pl-layout"),
            bind_group_layouts: &[&ui_bgl],
            push_constant_ranges: &[],
        });
        let inst_attrs = [
            wgpu::VertexAttribute { format: wgpu::VertexFormat::Float32x4, offset: 0, shader_location: 0 },
            wgpu::VertexAttribute { format: wgpu::VertexFormat::Float32x4, offset: 16, shader_location: 1 },
            wgpu::VertexAttribute { format: wgpu::VertexFormat::Float32x4, offset: 32, shader_location: 2 },
            wgpu::VertexAttribute { format: wgpu::VertexFormat::Uint32, offset: 48, shader_location: 3 },
        ];
        let inst_layout = wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<QuadInstance>() as u64,
            step_mode: wgpu::VertexStepMode::Instance,
            attributes: &inst_attrs,
        };
        let ui_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("tw-ui-pipeline"),
            layout: Some(&ui_pl_layout),
            vertex: wgpu::VertexState {
                module: &quad_shader,
                entry_point: Some("vs_main"),
                compilation_options: Default::default(),
                buffers: std::slice::from_ref(&inst_layout),
            },
            fragment: Some(wgpu::FragmentState {
                module: &quad_shader,
                entry_point: Some("fs_main"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format: wgpu::TextureFormat::Rgba8Unorm,
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
            cache: None,
        });
        let clear_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("tw-clear-pipeline"),
            layout: Some(&ui_pl_layout),
            vertex: wgpu::VertexState {
                module: &quad_shader,
                entry_point: Some("vs_main"),
                compilation_options: Default::default(),
                buffers: std::slice::from_ref(&inst_layout),
            },
            fragment: Some(wgpu::FragmentState {
                module: &quad_shader,
                entry_point: Some("fs_main"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format: wgpu::TextureFormat::Rgba8Unorm,
                    blend: Some(wgpu::BlendState::REPLACE),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
            cache: None,
        });
        let ui_uni_buf = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("tw-ui-uni"),
            size: std::mem::size_of::<UiUniformData>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let glyph_tex = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("tw-glyph-atlas"),
            size: wgpu::Extent3d { width: ATLAS_SIZE, height: ATLAS_SIZE, depth_or_array_layers: 1 },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::R8Unorm,
            usage: wgpu::TextureUsages::COPY_DST | wgpu::TextureUsages::TEXTURE_BINDING,
            view_formats: &[],
        });
        let glyph_view = glyph_tex.create_view(&Default::default());
        let dummy_blob_tex = make_tex(device, 1, 1);
        let dummy_blob_view = dummy_blob_tex.create_view(&Default::default());
        let ui_bind_group = make_ui_bg(device, &ui_pipeline, &sampler, &glyph_view, &dummy_blob_view, &ui_uni_buf);
        let instance_cap = 2048;
        let instance_buf = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("tw-ui-instances"),
            size: (instance_cap * std::mem::size_of::<QuadInstance>()) as u64,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        Self {
            sampler,
            pipeline,
            uni_buf,
            ui_tex,
            ui_view,
            ui_size: (1, 1),
            plane_tex,
            plane_view,
            plane_size: (1, 1),
            y_tex,
            y_view,
            u_tex,
            u_view,
            v_tex,
            v_view,
            yuv_size: (1, 1),
            bind_group,
            fx: (0.0, 0.0),
            has_plane: 0.0,
            plane_rect_px: [0, 0, 1, 1],
            ui_pipeline,
            clear_pipeline,
            ui_uni_buf,
            glyph_tex,
            glyph_view,
            _dummy_blob_tex: dummy_blob_tex,
            ui_bind_group,
            blob_cache: HashMap::new(),
            instance_buf,
            instance_cap,
            atlas: GlyphAtlas::default(),
        }
    }

    pub fn render_state(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        view: &wgpu::TextureView,
        state: &SceneState,
        server_size: (u32, u32),
    ) {
        self.render_state_with_transform(device, queue, view, state, server_size, server_size, &ViewportTransform::default());
    }

    pub fn render_state_with_transform(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        view: &wgpu::TextureView,
        state: &SceneState,
        server_size: (u32, u32),
        surface_size: (u32, u32),
        transform: &ViewportTransform,
    ) {
        let (w, h) = (server_size.0.max(1), server_size.1.max(1));
        if self.ui_size != (w, h) {
            self.ui_tex = make_ui_tex(device, w, h);
            self.ui_view = self.ui_tex.create_view(&Default::default());
            self.ui_size = (w, h);
            self.bind_group = make_bg(
                device,
                &self.pipeline,
                &self.sampler,
                &self.ui_view,
                &self.plane_view,
                &self.uni_buf,
                &self.y_view,
                &self.u_view,
                &self.v_view,
            );
        }

        // Upload any newly seen @image blobs to GPU textures once (cached by hash16).
        let mut blobs_dims: HashMap<[u8; 16], (u32, u32)> = HashMap::with_capacity(state.blobs.len());
        for (h16, img) in &state.blobs {
            blobs_dims.insert(*h16, (img.width, img.height));
            if !self.blob_cache.contains_key(h16) {
                let tex = make_tex(device, img.width, img.height);
                queue.write_texture(
                    tex.as_image_copy(),
                    &img.data,
                    wgpu::TexelCopyBufferLayout {
                        offset: 0,
                        bytes_per_row: Some(img.width * 4),
                        rows_per_image: Some(img.height),
                    },
                    wgpu::Extent3d { width: img.width, height: img.height, depth_or_array_layers: 1 },
                );
                let tv = tex.create_view(&Default::default());
                let bg = make_ui_bg(device, &self.ui_pipeline, &self.sampler, &self.glyph_view, &tv, &self.ui_uni_buf);
                self.blob_cache.insert(*h16, (tex, bg));
            }
        }

        // Compile retained DrawOps into GPU instanced quads + incremental glyph atlas uploads.
        let compiled = compile_layers(
            w,
            h,
            &state.styles,
            &blobs_dims,
            state.font_mono.as_ref(),
            state.font_sans.as_ref(),
            &mut self.atlas,
            state.order.iter().filter_map(|lid| state.layers.get(lid).map(|(_, ops)| ops.as_slice())),
        );
        for up in self.atlas.drain_uploads() {
            queue.write_texture(
                wgpu::TexelCopyTextureInfo {
                    texture: &self.glyph_tex,
                    mip_level: 0,
                    origin: wgpu::Origin3d { x: up.x, y: up.y, z: 0 },
                    aspect: wgpu::TextureAspect::All,
                },
                &up.data,
                wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(up.w),
                    rows_per_image: Some(up.h),
                },
                wgpu::Extent3d { width: up.w, height: up.h, depth_or_array_layers: 1 },
            );
        }

        let ui_uni = UiUniformData { viewport: [w as f32, h as f32], _pad: [0.0; 2] };
        queue.write_buffer(&self.ui_uni_buf, 0, bytemuck::bytes_of(&ui_uni));

        if !compiled.instances.is_empty() {
            if compiled.instances.len() > self.instance_cap {
                self.instance_cap = (compiled.instances.len() * 2).max(2048);
                self.instance_buf = device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some("tw-ui-instances"),
                    size: (self.instance_cap * std::mem::size_of::<QuadInstance>()) as u64,
                    usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
                    mapped_at_creation: false,
                });
            }
            queue.write_buffer(&self.instance_buf, 0, bytemuck::cast_slice(&compiled.instances));
        }

        let mut uploaded_plane = false;
        let mut plane_rot = 0.0f32;
        for pl in state.planes.values() {
            if let Some(yuv) = &pl.yuv {
                let (pw, ph) = (yuv.width, yuv.height);
                if pw >= 2 && ph >= 2 {
                    let cw = (pw / 2).max(1);
                    let ch = (ph / 2).max(1);
                    if self.yuv_size != (pw, ph) {
                        self.y_tex = make_r8_tex(device, pw, ph);
                        self.y_view = self.y_tex.create_view(&Default::default());
                        self.u_tex = make_r8_tex(device, cw, ch);
                        self.u_view = self.u_tex.create_view(&Default::default());
                        self.v_tex = make_r8_tex(device, cw, ch);
                        self.v_view = self.v_tex.create_view(&Default::default());
                        self.yuv_size = (pw, ph);
                        self.bind_group = make_bg(
                            device,
                            &self.pipeline,
                            &self.sampler,
                            &self.ui_view,
                            &self.plane_view,
                            &self.uni_buf,
                            &self.y_view,
                            &self.u_view,
                            &self.v_view,
                        );
                    }
                    queue.write_texture(
                        self.y_tex.as_image_copy(),
                        &yuv.y,
                        wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(pw), rows_per_image: Some(ph) },
                        wgpu::Extent3d { width: pw, height: ph, depth_or_array_layers: 1 },
                    );
                    queue.write_texture(
                        self.u_tex.as_image_copy(),
                        &yuv.u,
                        wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(cw), rows_per_image: Some(ch) },
                        wgpu::Extent3d { width: cw, height: ch, depth_or_array_layers: 1 },
                    );
                    queue.write_texture(
                        self.v_tex.as_image_copy(),
                        &yuv.v,
                        wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(cw), rows_per_image: Some(ch) },
                        wgpu::Extent3d { width: cw, height: ch, depth_or_array_layers: 1 },
                    );
                    self.has_plane = 2.0;
                    self.plane_rect_px = pl.rect;
                    plane_rot = ((pl.flags >> 2) & 0x03) as f32;
                    uploaded_plane = true;
                    break;
                }
            } else if !pl.rgba.is_empty() && pl.size.0 > 0 && pl.size.1 > 0 {
                let (pw, ph) = pl.size;
                if self.plane_size != (pw, ph) {
                    self.plane_tex = make_tex(device, pw, ph);
                    self.plane_view = self.plane_tex.create_view(&Default::default());
                    self.plane_size = (pw, ph);
                    self.bind_group = make_bg(
                        device,
                        &self.pipeline,
                        &self.sampler,
                        &self.ui_view,
                        &self.plane_view,
                        &self.uni_buf,
                        &self.y_view,
                        &self.u_view,
                        &self.v_view,
                    );
                }
                queue.write_texture(
                    self.plane_tex.as_image_copy(),
                    &pl.rgba,
                    wgpu::TexelCopyBufferLayout {
                        offset: 0,
                        bytes_per_row: Some(pw * 4),
                        rows_per_image: Some(ph),
                    },
                    wgpu::Extent3d { width: pw, height: ph, depth_or_array_layers: 1 },
                );
                self.has_plane = 1.0;
                self.plane_rect_px = pl.rect;
                plane_rot = ((pl.flags >> 2) & 0x03) as f32;
                uploaded_plane = true;
                break;
            }
        }
        if !uploaded_plane {
            self.has_plane = 0.0;
        }
        self.fx = state.fx;
        let uni = UniformData {
            bloom: self.fx.0,
            scanlines: self.fx.1,
            has_plane: self.has_plane,
            plane_rot,
            plane_rect: [
                self.plane_rect_px[0] as f32 / w as f32,
                self.plane_rect_px[1] as f32 / h as f32,
                self.plane_rect_px[2] as f32 / w as f32,
                self.plane_rect_px[3] as f32 / h as f32,
            ],
            view_rect: transform.view_rect(surface_size, (w, h)),
        };
        queue.write_buffer(&self.uni_buf, 0, bytemuck::bytes_of(&uni));

        let bg = state.bg_rgba;
        let mut encoder = device.create_command_encoder(&Default::default());
        // Pass 1: Pure-GPU instanced quads + glyph atlas + scissor clips into ui_view
        {
            let mut ui_pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("tw-gpu-ui-pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &self.ui_view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: bg[0] as f64 / 255.0,
                            g: bg[1] as f64 / 255.0,
                            b: bg[2] as f64 / 255.0,
                            a: bg[3] as f64 / 255.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            if !compiled.instances.is_empty() {
                ui_pass.set_vertex_buffer(0, self.instance_buf.slice(..));
                for batch in &compiled.batches {
                    let sx = batch.scissor[0].min(w.saturating_sub(1));
                    let sy = batch.scissor[1].min(h.saturating_sub(1));
                    let sw = batch.scissor[2].min(w.saturating_sub(sx));
                    let sh = batch.scissor[3].min(h.saturating_sub(sy));
                    if sw == 0 || sh == 0 || batch.count == 0 {
                        continue;
                    }
                    ui_pass.set_scissor_rect(sx, sy, sw, sh);
                    match &batch.kind {
                        BatchKind::Ui => {
                            ui_pass.set_pipeline(&self.ui_pipeline);
                            ui_pass.set_bind_group(0, &self.ui_bind_group, &[]);
                        }
                        BatchKind::Clear => {
                            ui_pass.set_pipeline(&self.clear_pipeline);
                            ui_pass.set_bind_group(0, &self.ui_bind_group, &[]);
                        }
                        BatchKind::Image(h16) => {
                            let Some((_, blob_bg)) = self.blob_cache.get(h16) else { continue };
                            ui_pass.set_pipeline(&self.ui_pipeline);
                            ui_pass.set_bind_group(0, blob_bg, &[]);
                        }
                    }
                    ui_pass.draw(0..6, batch.start..batch.start + batch.count);
                }
            }
        }
        // Pass 2: Plane + UI composite + viewport transform + CRT post-FX onto swapchain
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("tw-comp-pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &self.bind_group, &[]);
            pass.draw(0..3, 0..1);
        }
        queue.submit(Some(encoder.finish()));
    }
}

fn make_ui_tex(device: &wgpu::Device, w: u32, h: u32) -> wgpu::Texture {
    device.create_texture(&wgpu::TextureDescriptor {
        label: Some("tw-ui-tex"),
        size: wgpu::Extent3d { width: w.max(1), height: h.max(1), depth_or_array_layers: 1 },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: wgpu::TextureFormat::Rgba8Unorm,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING,
        view_formats: &[],
    })
}

fn make_ui_bg(
    device: &wgpu::Device,
    pipeline: &wgpu::RenderPipeline,
    sampler: &wgpu::Sampler,
    glyph_view: &wgpu::TextureView,
    blob_view: &wgpu::TextureView,
    uni_buf: &wgpu::Buffer,
) -> wgpu::BindGroup {
    device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("tw-ui-bg"),
        layout: &pipeline.get_bind_group_layout(0),
        entries: &[
            wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::Sampler(sampler) },
            wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::TextureView(glyph_view) },
            wgpu::BindGroupEntry { binding: 2, resource: wgpu::BindingResource::TextureView(blob_view) },
            wgpu::BindGroupEntry { binding: 3, resource: uni_buf.as_entire_binding() },
        ],
    })
}

fn make_tex(device: &wgpu::Device, w: u32, h: u32) -> wgpu::Texture {
    device.create_texture(&wgpu::TextureDescriptor {
        label: Some("tw-tex"),
        size: wgpu::Extent3d { width: w.max(1), height: h.max(1), depth_or_array_layers: 1 },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: wgpu::TextureFormat::Rgba8Unorm,
        usage: wgpu::TextureUsages::COPY_DST | wgpu::TextureUsages::TEXTURE_BINDING,
        view_formats: &[],
    })
}

fn make_r8_tex(device: &wgpu::Device, w: u32, h: u32) -> wgpu::Texture {
    device.create_texture(&wgpu::TextureDescriptor {
        label: Some("tw-r8-tex"),
        size: wgpu::Extent3d { width: w.max(1), height: h.max(1), depth_or_array_layers: 1 },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: wgpu::TextureFormat::R8Unorm,
        usage: wgpu::TextureUsages::COPY_DST | wgpu::TextureUsages::TEXTURE_BINDING,
        view_formats: &[],
    })
}

#[allow(clippy::too_many_arguments)]
fn make_bg(
    device: &wgpu::Device,
    pipeline: &wgpu::RenderPipeline,
    sampler: &wgpu::Sampler,
    ui_view: &wgpu::TextureView,
    plane_view: &wgpu::TextureView,
    uni_buf: &wgpu::Buffer,
    y_view: &wgpu::TextureView,
    u_view: &wgpu::TextureView,
    v_view: &wgpu::TextureView,
) -> wgpu::BindGroup {
    device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("tw-bg"),
        layout: &pipeline.get_bind_group_layout(0),
        entries: &[
            wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::Sampler(sampler) },
            wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::TextureView(ui_view) },
            wgpu::BindGroupEntry { binding: 2, resource: wgpu::BindingResource::TextureView(plane_view) },
            wgpu::BindGroupEntry { binding: 3, resource: uni_buf.as_entire_binding() },
            wgpu::BindGroupEntry { binding: 4, resource: wgpu::BindingResource::TextureView(y_view) },
            wgpu::BindGroupEntry { binding: 5, resource: wgpu::BindingResource::TextureView(u_view) },
            wgpu::BindGroupEntry { binding: 6, resource: wgpu::BindingResource::TextureView(v_view) },
        ],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn viewport_transform_maps_center_and_zoom() {
        let mut vt = ViewportTransform {
            virtual_size: Some((1100, 720)),
            zoom: 1.0,
            pan_u: 0.0,
            pan_v: 0.0,
        };
        let (sx, sy) = vt.screen_to_server(540.0, 1200.0, (1080, 2400), (1100, 720));
        assert!((sx - 550.0).abs() < 2.0);
        assert!((sy - 360.0).abs() < 2.0);

        vt.apply_pan_zoom(2.0, 0.0, 0.0, (1080, 2400));
        assert!((vt.zoom - 2.0).abs() < 1e-4);
        let vr = vt.view_rect((1080, 2400), (1100, 720));
        assert!((vr[2] - vr[0]) < 0.6);
    }

    #[test]
    fn compile_layers_batches_quads_glyphs_and_clips() {
        let mut st = SceneState::default();
        st.styles.insert(
            1,
            StyleDef {
                style_id: 1,
                fg: 0xFFFFFFFF,
                bg: 0,
                flags: 0,
                font_id: 0,
                size: 14,
            },
        );
        let ops = vec![
            DrawOp::Rect {
                flags: RECT_FLAG_FILL | RECT_FLAG_OUTLINE,
                x0: 10,
                y0: 10,
                x1: 200,
                y1: 100,
                fill: 0x203040FF,
                outline: 0xD4AD72FF,
                width: 2,
            },
            DrawOp::ClipPush { x0: 20, y0: 20, x1: 120, y1: 80 },
            DrawOp::Text { x: 24, y: 24, style_id: 1, text: "GPU Hello!".into() },
            DrawOp::Line { x0: 20, y0: 60, x1: 120, y1: 60, fill: 0x6CB2EBFF, width: 2 },
            DrawOp::ClipPop,
            DrawOp::ClearRect { x0: 210, y0: 10, x1: 300, y1: 100 },
        ];
        let mut atlas = GlyphAtlas::default();
        let compiled = compile_layers(
            640,
            360,
            &st.styles,
            &HashMap::new(),
            st.font_mono.as_ref(),
            st.font_sans.as_ref(),
            &mut atlas,
            std::iter::once(ops.as_slice()),
        );
        assert!(compiled.instances.len() >= 7);
        assert!(compiled.batches.iter().any(|b| b.scissor == [20, 20, 100, 60] && b.kind == BatchKind::Ui));
        assert!(compiled.batches.iter().any(|b| b.kind == BatchKind::Clear));
        if st.font_mono.is_some() {
            let uploads = atlas.drain_uploads();
            assert!(!uploads.is_empty());
            // Second frame with identical text emits zero new glyph uploads
            let _ = compile_layers(
                640,
                360,
                &st.styles,
                &HashMap::new(),
                st.font_mono.as_ref(),
                st.font_sans.as_ref(),
                &mut atlas,
                std::iter::once(ops.as_slice()),
            );
            assert!(atlas.drain_uploads().is_empty());
        }
    }

    #[test]
    fn gpu_compositor_renders_on_headless_adapter_if_present() {
        let instance = wgpu::Instance::default();
        let Some(adapter) = pollster_block_on(instance.request_adapter(&wgpu::RequestAdapterOptions::default())) else {
            return;
        };
        let Ok((device, queue)) = pollster_block_on(adapter.request_device(&Default::default(), None)) else {
            return;
        };
        let mut comp = GpuCompositor::new(&device, wgpu::TextureFormat::Rgba8Unorm);
        let mut st = SceneState::default();
        st.styles.insert(
            1,
            StyleDef { style_id: 1, fg: 0xFFFFFFFF, bg: 0, flags: 0, font_id: 0, size: 14 },
        );
        st.layers.insert(
            "root".into(),
            (
                [0, 0, 320, 180],
                vec![
                    DrawOp::Rect {
                        flags: RECT_FLAG_FILL | RECT_FLAG_OUTLINE,
                        x0: 8, y0: 8, x1: 310, y1: 170,
                        fill: 0x203040FF, outline: 0xD4AD72FF, width: 2,
                    },
                    DrawOp::Text { x: 16, y: 16, style_id: 1, text: "Pure GPU!".into() },
                    DrawOp::ClearRect { x0: 20, y0: 40, x1: 140, y1: 120 },
                ],
            ),
        );
        st.order = vec!["root".into()];
        let target = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("test-target"),
            size: wgpu::Extent3d { width: 320, height: 180, depth_or_array_layers: 1 },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        });
        let view = target.create_view(&Default::default());
        comp.render_state(&device, &queue, &view, &st, (320, 180));
        // Also verify GPU-side YUV420 shader plane rendering
        st.push_decoded_yuv420(
            1,
            64,
            32,
            &vec![180u8; 64 * 32],
            &vec![128u8; 32 * 16],
            &vec![128u8; 32 * 16],
            64,
            32,
            32,
            1,
        );
        comp.render_state(&device, &queue, &view, &st, (320, 180));
        device.poll(wgpu::Maintain::Wait);
    }

    fn pollster_block_on<F: std::future::Future>(fut: F) -> F::Output {
        use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};
        fn noop(_: *const ()) {}
        fn clone(p: *const ()) -> RawWaker {
            RawWaker::new(p, &VTABLE)
        }
        static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, noop, noop, noop);
        let waker = unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VTABLE)) };
        let mut cx = Context::from_waker(&waker);
        let mut pinned = Box::pin(fut);
        loop {
            match pinned.as_mut().poll(&mut cx) {
                Poll::Ready(val) => return val,
                Poll::Pending => std::thread::yield_now(),
            }
        }
    }
}
