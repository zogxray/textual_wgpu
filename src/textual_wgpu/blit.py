"""Upload a CPU RGBA bitmap and draw it fullscreen via wgpu."""

from __future__ import annotations

from typing import Any

import wgpu

_SHADER = """
struct VsOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
};

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> VsOut {
  // Fullscreen triangle.
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

@group(0) @binding(0) var samp: sampler;
@group(0) @binding(1) var tex: texture_2d<f32>;

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4f {
  return textureSample(tex, samp, in.uv);
}
"""


class FrameBlitter:
    def __init__(self, device: Any, texture_format: str) -> None:
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

    def upload(self, rgba: memoryview | bytes, size: tuple[int, int]) -> None:
        w, h = size
        if w <= 0 or h <= 0:
            return
        if self._texture is None or self._size != (w, h):
            self._texture = self.device.create_texture(
                size=(w, h, 1),
                format="rgba8unorm",
                usage=(
                    wgpu.TextureUsage.COPY_DST
                    | wgpu.TextureUsage.TEXTURE_BINDING
                    | wgpu.TextureUsage.RENDER_ATTACHMENT
                ),
            )
            self._texture_view = self._texture.create_view()
            self._size = (w, h)
            self._bind_group = self.device.create_bind_group(
                layout=self._pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": 0, "resource": self._sampler},
                    {"binding": 1, "resource": self._texture_view},
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
                    "clear_value": (0.08, 0.07, 0.09, 1.0),
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
