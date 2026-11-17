"""Upload a CPU RGBA bitmap and draw it fullscreen via wgpu (optional CRT FX)."""

from __future__ import annotations

import struct
from typing import Any

_SHADER = """
struct VsOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
};

struct Fx {
  bloom: f32,
  scanlines: f32,
  _pad0: f32,
  _pad1: f32,
};

@group(0) @binding(0) var samp: sampler;
@group(0) @binding(1) var tex: texture_2d<f32>;
@group(0) @binding(2) var<uniform> fx: Fx;

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

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4f {
  let color = textureSample(tex, samp, in.uv);
  var rgb = color.rgb;
  let bloom = fx.bloom;
  if (bloom > 0.001) {
    let dims = vec2f(textureDimensions(tex));
    let texel = 1.0 / dims;
    var acc = rgb;
    acc += textureSample(tex, samp, in.uv + vec2f( texel.x, 0.0)).rgb;
    acc += textureSample(tex, samp, in.uv - vec2f( texel.x, 0.0)).rgb;
    acc += textureSample(tex, samp, in.uv + vec2f(0.0,  texel.y)).rgb;
    acc += textureSample(tex, samp, in.uv - vec2f(0.0,  texel.y)).rgb;
    acc += textureSample(tex, samp, in.uv + vec2f(2.0 * texel.x, 0.0)).rgb * 0.5;
    acc += textureSample(tex, samp, in.uv - vec2f(2.0 * texel.x, 0.0)).rgb * 0.5;
    acc += textureSample(tex, samp, in.uv + vec2f(0.0, 2.0 * texel.y)).rgb * 0.5;
    acc += textureSample(tex, samp, in.uv - vec2f(0.0, 2.0 * texel.y)).rgb * 0.5;
    let blurred = acc / 7.0;
    // Screen blend with bloom strength (cool-retro-term-ish glow).
    let glow = blurred * bloom;
    rgb = 1.0 - (1.0 - rgb) * (1.0 - glow);
  }
  let scan = fx.scanlines;
  if (scan > 0.001) {
    let py = in.uv.y * f32(textureDimensions(tex).y);
    if ((i32(floor(py)) & 1) == 1) {
      rgb = rgb * (1.0 - scan * 0.45);
    }
  }
  return vec4f(rgb, color.a);
}
"""


class FrameBlitter:
    def __init__(self, device: Any, texture_format: str) -> None:
        import wgpu

        self._wgpu = wgpu
        self.device = device
        self.texture_format = texture_format
        self._pipeline = None
        self._sampler = device.create_sampler(
            mag_filter="linear", min_filter="linear",
        )
        self._texture = None
        self._texture_view = None
        self._bind_group = None
        self._size = (0, 0)
        self._fx = (0.0, 0.0)
        self._fx_buf = device.create_buffer(
            size=16,
            usage=self._wgpu.BufferUsage.UNIFORM | self._wgpu.BufferUsage.COPY_DST,
        )
        self._write_fx(0.0, 0.0)
        shader = device.create_shader_module(code=_SHADER)
        self._pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": shader, "entry_point": "vs_main"},
            fragment={
                "module": shader,
                "entry_point": "fs_main",
                "targets": [{"format": texture_format}],
            },
            primitive={"topology": "triangle-list"},
        )

    def set_fx(self, *, bloom: float = 0.0, scanlines: float = 0.0) -> None:
        bloom = max(0.0, min(1.0, float(bloom)))
        scanlines = max(0.0, min(1.0, float(scanlines)))
        if (bloom, scanlines) == self._fx:
            return
        self._fx = (bloom, scanlines)
        self._write_fx(bloom, scanlines)

    def _write_fx(self, bloom: float, scanlines: float) -> None:
        self.device.queue.write_buffer(
            self._fx_buf, 0, struct.pack("ffff", bloom, scanlines, 0.0, 0.0),
        )

    def upload(self, rgba: memoryview | bytes, size: tuple[int, int]) -> None:
        w, h = size
        if w <= 0 or h <= 0:
            return
        if self._texture is None or self._size != (w, h):
            self._texture = self.device.create_texture(
                size=(w, h, 1),
                format="rgba8unorm",
                usage=(
                    self._wgpu.TextureUsage.COPY_DST
                    | self._wgpu.TextureUsage.TEXTURE_BINDING
                    | self._wgpu.TextureUsage.RENDER_ATTACHMENT
                ),
            )
            self._texture_view = self._texture.create_view()
            self._size = (w, h)
            self._bind_group = self.device.create_bind_group(
                layout=self._pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": 0, "resource": self._sampler},
                    {"binding": 1, "resource": self._texture_view},
                    {"binding": 2, "resource": {
                        "buffer": self._fx_buf,
                        "offset": 0,
                        "size": 16,
                    }},
                ],
            )
        self.device.queue.write_texture(
            {"texture": self._texture},
            rgba,
            {"bytes_per_row": w * 4, "rows_per_image": h},
            (w, h, 1),
        )

    def draw(self, target_view: Any) -> None:
        if self._bind_group is None or self._pipeline is None:
            return
        encoder = self.device.create_command_encoder()
        pass_ = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": target_view,
                    "clear_value": (0.0, 0.0, 0.0, 1.0),
                    "load_op": "clear",
                    "store_op": "store",
                }
            ]
        )
        pass_.set_pipeline(self._pipeline)
        pass_.set_bind_group(0, self._bind_group)
        pass_.draw(3)
        pass_.end()
        self.device.queue.submit([encoder.finish()])


_PLANE_SHADER = """
struct VsOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
};

struct CompositorUniforms {
  bloom: f32,
  scanlines: f32,
  has_plane: f32,
  _pad0: f32,
  plane_rect: vec4f,  // normalized (x0, y0, x1, y1) in [0, 1]
};

@group(0) @binding(0) var samp: sampler;
@group(0) @binding(1) var ui_tex: texture_2d<f32>;
@group(0) @binding(2) var plane_tex: texture_2d<f32>;
@group(0) @binding(3) var<uniform> uni: CompositorUniforms;

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

fn sample_composite(uv: vec2f) -> vec4f {
  let ui = textureSample(ui_tex, samp, uv);
  if (uni.has_plane > 0.5) {
    let r = uni.plane_rect;
    if (uv.x >= r.x && uv.x <= r.z && uv.y >= r.y && uv.y <= r.w) {
      let puv = vec2f((uv.x - r.x) / max(1e-5, r.z - r.x), (uv.y - r.y) / max(1e-5, r.w - r.y));
      let pl = textureSample(plane_tex, samp, puv);
      let rgb = mix(pl.rgb, ui.rgb, ui.a);
      return vec4f(rgb, max(pl.a, ui.a));
    }
  }
  return ui;
}

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4f {
  let color = sample_composite(in.uv);
  var rgb = color.rgb;
  let bloom = uni.bloom;
  if (bloom > 0.001) {
    let dims = vec2f(textureDimensions(ui_tex));
    let texel = 1.0 / dims;
    var acc = rgb;
    acc += sample_composite(in.uv + vec2f( texel.x, 0.0)).rgb;
    acc += sample_composite(in.uv - vec2f( texel.x, 0.0)).rgb;
    acc += sample_composite(in.uv + vec2f(0.0,  texel.y)).rgb;
    acc += sample_composite(in.uv - vec2f(0.0,  texel.y)).rgb;
    let blurred = acc / 5.0;
    rgb = 1.0 - (1.0 - rgb) * (1.0 - blurred * bloom);
  }
  let scan = uni.scanlines;
  if (scan > 0.001) {
    let py = in.uv.y * f32(textureDimensions(ui_tex).y);
    if ((i32(floor(py)) & 1) == 1) {
      rgb = rgb * (1.0 - scan * 0.45);
    }
  }
  return vec4f(rgb, color.a);
}
"""


class PlaneCompositor:
    """Multi-plane GPU compositor: blends Video/3D texture plane + UI overlay + CRT FX."""

    def __init__(self, device: Any, texture_format: str) -> None:
        import wgpu

        self._wgpu = wgpu
        self.device = device
        self.texture_format = texture_format
        self._sampler = device.create_sampler(mag_filter="linear", min_filter="linear")
        self._ui_tex = None
        self._ui_view = None
        self._ui_size = (0, 0)
        self._plane_tex = self._make_rgba_tex((1, 1))
        self._plane_view = self._plane_tex.create_view()
        self._plane_size = (1, 1)
        self._has_plane = 0.0
        self._plane_rect_px = (0, 0, 1, 1)
        self._fx = (0.0, 0.0)
        self._uni_buf = device.create_buffer(
            size=32,
            usage=self._wgpu.BufferUsage.UNIFORM | self._wgpu.BufferUsage.COPY_DST,
        )
        shader = device.create_shader_module(code=_PLANE_SHADER)
        self._pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": shader, "entry_point": "vs_main"},
            fragment={
                "module": shader,
                "entry_point": "fs_main",
                "targets": [{"format": texture_format}],
            },
            primitive={"topology": "triangle-list"},
        )
        self._bind_group = None

    def _make_rgba_tex(self, size: tuple[int, int]):
        return self.device.create_texture(
            size=(max(1, size[0]), max(1, size[1]), 1),
            format="rgba8unorm",
            usage=(
                self._wgpu.TextureUsage.COPY_DST
                | self._wgpu.TextureUsage.TEXTURE_BINDING
                | self._wgpu.TextureUsage.RENDER_ATTACHMENT
            ),
        )

    def _rebuild_bind_group(self) -> None:
        if self._ui_view is None:
            return
        self._bind_group = self.device.create_bind_group(
            layout=self._pipeline.get_bind_group_layout(0),
            entries=[
                {"binding": 0, "resource": self._sampler},
                {"binding": 1, "resource": self._ui_view},
                {"binding": 2, "resource": self._plane_view},
                {"binding": 3, "resource": {"buffer": self._uni_buf, "offset": 0, "size": 32}},
            ],
        )

    def _write_uniforms(self) -> None:
        uw = max(1, self._ui_size[0])
        uh = max(1, self._ui_size[1])
        x0, y0, x1, y1 = self._plane_rect_px
        self.device.queue.write_buffer(
            self._uni_buf,
            0,
            struct.pack(
                "ffffffff",
                self._fx[0],
                self._fx[1],
                self._has_plane,
                0.0,
                x0 / uw,
                y0 / uh,
                x1 / uw,
                y1 / uh,
            ),
        )

    def set_fx(self, *, bloom: float = 0.0, scanlines: float = 0.0) -> None:
        self._fx = (max(0.0, min(1.0, float(bloom))), max(0.0, min(1.0, float(scanlines))))
        self._write_uniforms()

    def upload_ui(self, rgba: memoryview | bytes, size: tuple[int, int]) -> None:
        w, h = size
        if w <= 0 or h <= 0:
            return
        if self._ui_tex is None or self._ui_size != (w, h):
            self._ui_tex = self._make_rgba_tex((w, h))
            self._ui_view = self._ui_tex.create_view()
            self._ui_size = (w, h)
            self._rebuild_bind_group()
            self._write_uniforms()
        self.device.queue.write_texture(
            {"texture": self._ui_tex},
            rgba,
            {"bytes_per_row": w * 4, "rows_per_image": h},
            (w, h, 1),
        )

    def upload_plane(
        self,
        rgba: memoryview | bytes,
        tex_size: tuple[int, int],
        dest_rect: tuple[int, int, int, int],
    ) -> None:
        pw, ph = tex_size
        if pw <= 0 or ph <= 0:
            return
        if self._plane_size != (pw, ph):
            self._plane_tex = self._make_rgba_tex((pw, ph))
            self._plane_view = self._plane_tex.create_view()
            self._plane_size = (pw, ph)
            self._rebuild_bind_group()
        self._has_plane = 1.0
        self._plane_rect_px = dest_rect
        self._write_uniforms()
        self.device.queue.write_texture(
            {"texture": self._plane_tex},
            rgba,
            {"bytes_per_row": pw * 4, "rows_per_image": ph},
            (pw, ph, 1),
        )

    def clear_plane(self) -> None:
        self._has_plane = 0.0
        self._write_uniforms()

    def draw(self, target_view: Any) -> None:
        if self._bind_group is None:
            return
        encoder = self.device.create_command_encoder()
        pass_ = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": target_view,
                    "clear_value": (0.0, 0.0, 0.0, 1.0),
                    "load_op": "clear",
                    "store_op": "store",
                }
            ]
        )
        pass_.set_pipeline(self._pipeline)
        pass_.set_bind_group(0, self._bind_group)
        pass_.draw(3)
        pass_.end()
        self.device.queue.submit([encoder.finish()])
