use std::collections::HashMap;

use ab_glyph::{Font, FontVec, PxScale, ScaleFont};
use bytemuck::{Pod, Zeroable};
use tw_proto::{
    unpack_rgba, DrawOp, StyleDef, FONT_SANS, FONT_SANS_BOLD, RECT_FLAG_FILL, RECT_FLAG_OUTLINE,
};

pub const ATLAS_SIZE: u32 = 2048;

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable, Debug)]
pub struct QuadInstance {
    pub rect: [f32; 4],
    pub uv: [f32; 4],
    pub color: [f32; 4],
    pub mode: u32,
    pub _pad: [u32; 3],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BatchKind {
    Ui,
    Clear,
    Image([u8; 16]),
}

#[derive(Clone, Debug)]
pub struct DrawBatch {
    pub start: u32,
    pub count: u32,
    pub scissor: [u32; 4],
    pub kind: BatchKind,
}

#[derive(Clone, Debug)]
pub struct AtlasUpload {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
    pub data: Vec<u8>,
}

#[derive(Clone, Copy, Debug)]
pub struct GlyphEntry {
    pub u0: f32,
    pub v0: f32,
    pub u1: f32,
    pub v1: f32,
    pub ox: f32,
    pub oy: f32,
    pub w: f32,
    pub h: f32,
}

pub struct GlyphAtlas {
    pub size: u32,
    cursor_x: u32,
    cursor_y: u32,
    row_h: u32,
    cache: HashMap<(u8, u16, u8), GlyphEntry>,
    pub pending_uploads: Vec<AtlasUpload>,
}

impl Default for GlyphAtlas {
    fn default() -> Self {
        Self::new(ATLAS_SIZE)
    }
}

impl GlyphAtlas {
    pub fn new(size: u32) -> Self {
        Self {
            size,
            cursor_x: 1,
            cursor_y: 1,
            row_h: 0,
            cache: HashMap::new(),
            pending_uploads: Vec::new(),
        }
    }

    pub fn reset(&mut self) {
        self.cursor_x = 1;
        self.cursor_y = 1;
        self.row_h = 0;
        self.cache.clear();
        self.pending_uploads.clear();
    }

    pub fn get_or_rasterize(
        &mut self,
        font_tag: u8,
        font: &FontVec,
        glyph_id: ab_glyph::GlyphId,
        size_px: u8,
    ) -> Option<GlyphEntry> {
        let key = (font_tag, glyph_id.0, size_px);
        if let Some(e) = self.cache.get(&key) {
            return Some(*e);
        }
        let scale = PxScale::from((size_px as f32).max(8.0));
        let g = glyph_id.with_scale_and_position(scale, ab_glyph::point(0.0, 0.0));
        let Some(og) = font.outline_glyph(g) else {
            let empty = GlyphEntry { u0: 0.0, v0: 0.0, u1: 0.0, v1: 0.0, ox: 0.0, oy: 0.0, w: 0.0, h: 0.0 };
            self.cache.insert(key, empty);
            return Some(empty);
        };
        let bounds = og.px_bounds();
        let gw = bounds.width().ceil().max(1.0) as u32;
        let gh = bounds.height().ceil().max(1.0) as u32;
        if gw + 2 >= self.size || gh + 2 >= self.size {
            return None;
        }
        if self.cursor_x + gw + 1 >= self.size {
            self.cursor_x = 1;
            self.cursor_y += self.row_h + 1;
            self.row_h = 0;
        }
        if self.cursor_y + gh + 1 >= self.size {
            self.reset();
        }
        let ax = self.cursor_x;
        let ay = self.cursor_y;
        self.cursor_x += gw + 1;
        self.row_h = self.row_h.max(gh);

        let mut cov_buf = vec![0u8; (gw * gh) as usize];
        og.draw(|gx, gy, cov| {
            let ix = gx as u32;
            let iy = gy as u32;
            if ix < gw && iy < gh {
                cov_buf[(iy * gw + ix) as usize] = (cov * 255.0).round().clamp(0.0, 255.0) as u8;
            }
        });

        let inv = 1.0 / (self.size as f32);
        let entry = GlyphEntry {
            u0: ax as f32 * inv,
            v0: ay as f32 * inv,
            u1: (ax + gw) as f32 * inv,
            v1: (ay + gh) as f32 * inv,
            ox: bounds.min.x,
            oy: bounds.min.y,
            w: gw as f32,
            h: gh as f32,
        };
        self.cache.insert(key, entry);
        self.pending_uploads.push(AtlasUpload { x: ax, y: ay, w: gw, h: gh, data: cov_buf });
        Some(entry)
    }

    pub fn drain_uploads(&mut self) -> Vec<AtlasUpload> {
        std::mem::take(&mut self.pending_uploads)
    }
}

#[inline]
fn rgba_norm(c: [u8; 4]) -> [f32; 4] {
    [
        c[0] as f32 / 255.0,
        c[1] as f32 / 255.0,
        c[2] as f32 / 255.0,
        c[3] as f32 / 255.0,
    ]
}

fn intersect_scissor(a: [u32; 4], x0: i32, y0: i32, x1: i32, y1: i32) -> [u32; 4] {
    let ax1 = (a[0] + a[2]) as i32;
    let ay1 = (a[1] + a[3]) as i32;
    let nx0 = (a[0] as i32).max(x0);
    let ny0 = (a[1] as i32).max(y0);
    let nx1 = ax1.min(x1);
    let ny1 = ay1.min(y1);
    if nx1 <= nx0 || ny1 <= ny0 {
        [nx0.max(0) as u32, ny0.max(0) as u32, 0, 0]
    } else {
        [nx0 as u32, ny0 as u32, (nx1 - nx0) as u32, (ny1 - ny0) as u32]
    }
}

pub struct CompiledUi {
    pub instances: Vec<QuadInstance>,
    pub batches: Vec<DrawBatch>,
}

fn push_instance(
    instances: &mut Vec<QuadInstance>,
    batches: &mut Vec<DrawBatch>,
    scissor: [u32; 4],
    kind: BatchKind,
    inst: QuadInstance,
) {
    if scissor[2] == 0 || scissor[3] == 0 {
        return;
    }
    let idx = instances.len() as u32;
    instances.push(inst);
    if let Some(last) = batches.last_mut() {
        if last.scissor == scissor && last.kind == kind && last.start + last.count == idx {
            last.count += 1;
            return;
        }
    }
    batches.push(DrawBatch {
        start: idx,
        count: 1,
        scissor,
        kind,
    });
}

#[allow(clippy::too_many_arguments)]
pub fn compile_layers<'a, I>(
    width: u32,
    height: u32,
    styles: &HashMap<u16, StyleDef>,
    blobs_dims: &HashMap<[u8; 16], (u32, u32)>,
    font_mono: Option<&FontVec>,
    font_sans: Option<&FontVec>,
    atlas: &mut GlyphAtlas,
    layers: I,
) -> CompiledUi
where
    I: IntoIterator<Item = &'a [DrawOp]>,
{
    let mut instances = Vec::with_capacity(1024);
    let mut batches = Vec::with_capacity(64);
    let root_scissor = [0u32, 0u32, width.max(1), height.max(1)];

    for ops in layers {
        let mut stack = vec![root_scissor];
        for op in ops {
            let cur_scissor = *stack.last().unwrap_or(&root_scissor);
            match op {
                DrawOp::Rect { flags, x0, y0, x1, y1, fill, outline, width: bw } => {
                    let fx0 = *x0 as f32;
                    let fy0 = *y0 as f32;
                    let fx1 = (*x1 as f32) + 1.0;
                    let fy1 = (*y1 as f32) + 1.0;
                    if (flags & RECT_FLAG_FILL) != 0 && fx1 > fx0 && fy1 > fy0 {
                        push_instance(
                            &mut instances,
                            &mut batches,
                            cur_scissor,
                            BatchKind::Ui,
                            QuadInstance {
                                rect: [fx0, fy0, fx1, fy1],
                                uv: [0.0; 4],
                                color: rgba_norm(unpack_rgba(*fill)),
                                mode: 0,
                                _pad: [0; 3],
                            },
                        );
                    }
                    if (flags & RECT_FLAG_OUTLINE) != 0 && fx1 > fx0 && fy1 > fy0 {
                        let t = (*bw).max(1) as f32;
                        let col = rgba_norm(unpack_rgba(*outline));
                        let top_y1 = (fy0 + t).min(fy1);
                        let bot_y0 = (fy1 - t).max(fy0);
                        push_instance(
                            &mut instances,
                            &mut batches,
                            cur_scissor,
                            BatchKind::Ui,
                            QuadInstance { rect: [fx0, fy0, fx1, top_y1], uv: [0.0; 4], color: col, mode: 0, _pad: [0; 3] },
                        );
                        if bot_y0 > top_y1 {
                            push_instance(
                                &mut instances,
                                &mut batches,
                                cur_scissor,
                                BatchKind::Ui,
                                QuadInstance { rect: [fx0, bot_y0, fx1, fy1], uv: [0.0; 4], color: col, mode: 0, _pad: [0; 3] },
                            );
                        }
                        if bot_y0 > top_y1 {
                            let left_x1 = (fx0 + t).min(fx1);
                            let right_x0 = (fx1 - t).max(fx0);
                            push_instance(
                                &mut instances,
                                &mut batches,
                                cur_scissor,
                                BatchKind::Ui,
                                QuadInstance { rect: [fx0, top_y1, left_x1, bot_y0], uv: [0.0; 4], color: col, mode: 0, _pad: [0; 3] },
                            );
                            if right_x0 > left_x1 {
                                push_instance(
                                    &mut instances,
                                    &mut batches,
                                    cur_scissor,
                                    BatchKind::Ui,
                                    QuadInstance { rect: [right_x0, top_y1, fx1, bot_y0], uv: [0.0; 4], color: col, mode: 0, _pad: [0; 3] },
                                );
                            }
                        }
                    }
                }
                DrawOp::Line { x0, y0, x1, y1, fill, width: lw } => {
                    let col = rgba_norm(unpack_rgba(*fill));
                    let hw = ((*lw).max(1) as f32) * 0.5;
                    push_instance(
                        &mut instances,
                        &mut batches,
                        cur_scissor,
                        BatchKind::Ui,
                        QuadInstance {
                            rect: [*x0 as f32 + 0.5, *y0 as f32 + 0.5, *x1 as f32 + 0.5, *y1 as f32 + 0.5],
                            uv: [hw, 0.0, 0.0, 0.0],
                            color: col,
                            mode: 3,
                            _pad: [0; 3],
                        },
                    );
                }
                DrawOp::Text { x, y, style_id, text } => {
                    let st = styles.get(style_id).copied().unwrap_or(StyleDef {
                        style_id: *style_id,
                        fg: 0xF0E6D8FF,
                        bg: 0,
                        flags: 0,
                        font_id: 0,
                        size: 13,
                    });
                    let is_sans = st.font_id == FONT_SANS || st.font_id == FONT_SANS_BOLD;
                    let (font_tag, font_opt) = if is_sans {
                        (1u8, font_sans.or(font_mono))
                    } else {
                        (0u8, font_mono.or(font_sans))
                    };
                    let Some(font) = font_opt else { continue };
                    let size_px = st.size.max(8);
                    let scale = PxScale::from(size_px as f32);
                    let scaled = font.as_scaled(scale);
                    let mut pen_x = *x as f32;
                    let baseline_y = *y as f32 + scaled.ascent();
                    let col = rgba_norm(unpack_rgba(st.fg));

                    for ch in text.chars() {
                        let gid = scaled.glyph_id(ch);
                        let adv = scaled.h_advance(gid);
                        if let Some(ge) = atlas.get_or_rasterize(font_tag, font, gid, size_px) {
                            if ge.w > 0.0 && ge.h > 0.0 {
                                let gx0 = pen_x.round() + ge.ox;
                                let gy0 = baseline_y.round() + ge.oy;
                                push_instance(
                                    &mut instances,
                                    &mut batches,
                                    cur_scissor,
                                    BatchKind::Ui,
                                    QuadInstance {
                                        rect: [gx0, gy0, gx0 + ge.w, gy0 + ge.h],
                                        uv: [ge.u0, ge.v0, ge.u1, ge.v1],
                                        color: col,
                                        mode: 1,
                                        _pad: [0; 3],
                                    },
                                );
                            }
                        }
                        pen_x += adv;
                    }
                }
                DrawOp::ImgBlit { hash16, dx, dy, sx0, sy0, sx1, sy1 } => {
                    if let Some(&(bw, bh)) = blobs_dims.get(hash16) {
                        let bwf = bw.max(1) as f32;
                        let bhf = bh.max(1) as f32;
                        let dw = (*sx1 - *sx0).max(0) as f32;
                        let dh = (*sy1 - *sy0).max(0) as f32;
                        if dw > 0.0 && dh > 0.0 {
                            push_instance(
                                &mut instances,
                                &mut batches,
                                cur_scissor,
                                BatchKind::Image(*hash16),
                                QuadInstance {
                                    rect: [*dx as f32, *dy as f32, *dx as f32 + dw, *dy as f32 + dh],
                                    uv: [*sx0 as f32 / bwf, *sy0 as f32 / bhf, *sx1 as f32 / bwf, *sy1 as f32 / bhf],
                                    color: [1.0, 1.0, 1.0, 1.0],
                                    mode: 2,
                                    _pad: [0; 3],
                                },
                            );
                        }
                    }
                }
                DrawOp::ClipPush { x0, y0, x1, y1 } => {
                    let next = intersect_scissor(cur_scissor, *x0 as i32, *y0 as i32, *x1 as i32, *y1 as i32);
                    stack.push(next);
                }
                DrawOp::ClipPop => {
                    if stack.len() > 1 {
                        stack.pop();
                    }
                }
                DrawOp::ClearRect { x0, y0, x1, y1 } => {
                    let fx0 = *x0 as f32;
                    let fy0 = *y0 as f32;
                    let fx1 = (*x1 as f32) + 1.0;
                    let fy1 = (*y1 as f32) + 1.0;
                    if fx1 > fx0 && fy1 > fy0 {
                        push_instance(
                            &mut instances,
                            &mut batches,
                            cur_scissor,
                            BatchKind::Clear,
                            QuadInstance {
                                rect: [fx0, fy0, fx1, fy1],
                                uv: [0.0; 4],
                                color: [0.0, 0.0, 0.0, 0.0],
                                mode: 4,
                                _pad: [0; 3],
                            },
                        );
                    }
                }
            }
        }
    }

    CompiledUi { instances, batches }
}

pub const QUAD_SHADER: &str = r#"
struct UiUniforms {
  viewport: vec2f,
  _pad: vec2f,
};

struct VsIn {
  @location(0) rect: vec4f,
  @location(1) uv: vec4f,
  @location(2) color: vec4f,
  @location(3) mode: u32,
};

struct VsOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
  @location(1) color: vec4f,
  @location(2) @interpolate(flat) mode: u32,
};

@group(0) @binding(0) var samp: sampler;
@group(0) @binding(1) var glyph_tex: texture_2d<f32>;
@group(0) @binding(2) var blob_tex: texture_2d<f32>;
@group(0) @binding(3) var<uniform> uni: UiUniforms;

@vertex
fn vs_main(@builtin(vertex_index) vid: u32, inst: VsIn) -> VsOut {
  // 2 triangles forming a unit quad: (0,0), (1,0), (0,1), (0,1), (1,0), (1,1)
  var corners = array<vec2f, 6>(
    vec2f(0.0, 0.0),
    vec2f(1.0, 0.0),
    vec2f(0.0, 1.0),
    vec2f(0.0, 1.0),
    vec2f(1.0, 0.0),
    vec2f(1.0, 1.0),
  );
  let c = corners[vid];
  var px: vec2f;
  if (inst.mode == 3u) {
    let p0 = inst.rect.xy;
    let p1 = inst.rect.zw;
    let d = p1 - p0;
    let len = max(length(d), 1e-4);
    let n = vec2f(-d.y, d.x) / len;
    let hw = max(inst.uv.x, 0.5);
    px = mix(p0, p1, c.x) + n * ((c.y - 0.5) * 2.0 * hw);
  } else {
    px = mix(inst.rect.xy, inst.rect.zw, c);
  }
  let ndc = vec2f(
    (px.x / max(uni.viewport.x, 1.0)) * 2.0 - 1.0,
    1.0 - (px.y / max(uni.viewport.y, 1.0)) * 2.0,
  );
  var out: VsOut;
  out.pos = vec4f(ndc, 0.0, 1.0);
  out.uv = mix(inst.uv.xy, inst.uv.zw, c);
  out.color = inst.color;
  out.mode = inst.mode;
  return out;
}

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4f {
  if (in.mode == 4u) {
    return vec4f(0.0, 0.0, 0.0, 0.0);
  }
  if (in.mode == 1u) {
    let cov = textureSample(glyph_tex, samp, in.uv).r;
    return vec4f(in.color.rgb, in.color.a * cov);
  }
  if (in.mode == 2u) {
    return textureSample(blob_tex, samp, in.uv) * in.color;
  }
  return in.color;
}
"#;
