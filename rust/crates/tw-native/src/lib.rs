use std::collections::HashMap;

use ab_glyph::{Font, FontVec, PxScale, ScaleFont};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use tw_proto::{decode_draw_ops, DrawOp, StyleDef, FONT_SANS, FONT_SANS_BOLD};
use tw_render::gpu_ui::{compile_layers, BatchKind, GlyphAtlas, ATLAS_SIZE, QUAD_SHADER};
use tw_render::render_mesh_rgba as rust_render_mesh_rgba;

#[pyclass]
pub struct GpuUiCompiler {
    styles: HashMap<u16, StyleDef>,
    blobs_dims: HashMap<[u8; 16], (u32, u32)>,
    font_mono: Option<FontVec>,
    font_sans: Option<FontVec>,
    atlas: GlyphAtlas,
}

#[pymethods]
impl GpuUiCompiler {
    #[new]
    fn new() -> Self {
        let mut s = Self {
            styles: HashMap::new(),
            blobs_dims: HashMap::new(),
            font_mono: None,
            font_sans: None,
            atlas: GlyphAtlas::default(),
        };
        for p in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ] {
            if let Ok(b) = std::fs::read(p) {
                if let Ok(f) = FontVec::try_from_vec(b) {
                    s.font_mono = Some(f);
                    break;
                }
            }
        }
        for p in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ] {
            if let Ok(b) = std::fs::read(p) {
                if let Ok(f) = FontVec::try_from_vec(b) {
                    s.font_sans = Some(f);
                    break;
                }
            }
        }
        s
    }

    fn load_font_mono(&mut self, data: &[u8]) -> PyResult<()> {
        let f = FontVec::try_from_vec(data.to_vec())
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("bad mono font: {e:?}")))?;
        self.font_mono = Some(f);
        self.atlas.reset();
        Ok(())
    }

    fn load_font_sans(&mut self, data: &[u8]) -> PyResult<()> {
        let f = FontVec::try_from_vec(data.to_vec())
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("bad sans font: {e:?}")))?;
        self.font_sans = Some(f);
        self.atlas.reset();
        Ok(())
    }

    fn set_styles(&mut self, styles: Vec<(u16, u32, u32, u8, u8, u8)>) {
        for (style_id, fg, bg, flags, font_id, size) in styles {
            self.styles.insert(
                style_id,
                StyleDef { style_id, fg, bg, flags, font_id, size },
            );
        }
    }

    fn set_blob_dims(&mut self, hash16: &[u8], w: u32, h: u32) -> PyResult<()> {
        if hash16.len() != 16 {
            return Err(pyo3::exceptions::PyValueError::new_err("hash16 must be 16 bytes"));
        }
        let mut h_arr = [0u8; 16];
        h_arr.copy_from_slice(hash16);
        self.blobs_dims.insert(h_arr, (w, h));
        Ok(())
    }

    fn measure_text(&self, font_id: u8, size_px: u8, text: &str) -> (i32, i32) {
        let is_sans = font_id == FONT_SANS || font_id == FONT_SANS_BOLD;
        let font_opt = if is_sans {
            self.font_sans.as_ref().or(self.font_mono.as_ref())
        } else {
            self.font_mono.as_ref().or(self.font_sans.as_ref())
        };
        let Some(font) = font_opt else {
            return ((text.chars().count() as i32) * (size_px.max(8) as i32 * 6 / 10), size_px.max(8) as i32);
        };
        let scale = PxScale::from((size_px as f32).max(8.0));
        let scaled = font.as_scaled(scale);
        let mut w = 0.0f32;
        for ch in text.chars() {
            w += scaled.h_advance(scaled.glyph_id(ch));
        }
        let h = (scaled.ascent() - scaled.descent()).ceil().max(1.0) as i32;
        (w.ceil() as i32, h)
    }

    #[allow(clippy::type_complexity)]
    fn compile_layers<'py>(
        &mut self,
        py: Python<'py>,
        width: u32,
        height: u32,
        layers_ops: Vec<Vec<u8>>,
    ) -> PyResult<(
        Bound<'py, PyBytes>,
        Vec<(u32, u32, u32, u32, u32, u32, u8, Option<Bound<'py, PyBytes>>)>,
        Vec<(u32, u32, u32, u32, Bound<'py, PyBytes>)>,
    )> {
        let mut decoded_layers: Vec<Vec<DrawOp>> = Vec::with_capacity(layers_ops.len());
        for raw in &layers_ops {
            let ops = decode_draw_ops(raw)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("decode_draw_ops: {e}")))?;
            decoded_layers.push(ops);
        }
        let compiled = compile_layers(
            width,
            height,
            &self.styles,
            &self.blobs_dims,
            self.font_mono.as_ref(),
            self.font_sans.as_ref(),
            &mut self.atlas,
            decoded_layers.iter().map(|v| v.as_slice()),
        );
        let inst_bytes = PyBytes::new(py, bytemuck::cast_slice(&compiled.instances));
        let batches = compiled
            .batches
            .into_iter()
            .map(|b| {
                let (kind_id, h_opt) = match b.kind {
                    BatchKind::Ui => (0u8, None),
                    BatchKind::Clear => (1u8, None),
                    BatchKind::Image(h16) => (2u8, Some(PyBytes::new(py, &h16))),
                };
                (
                    b.start,
                    b.count,
                    b.scissor[0],
                    b.scissor[1],
                    b.scissor[2],
                    b.scissor[3],
                    kind_id,
                    h_opt,
                )
            })
            .collect();
        let uploads = self
            .atlas
            .drain_uploads()
            .into_iter()
            .map(|u| (u.x, u.y, u.w, u.h, PyBytes::new(py, &u.data)))
            .collect();
        Ok((inst_bytes, batches, uploads))
    }
}

#[pyfunction]
fn render_mesh_rgba<'py>(
    py: Python<'py>,
    width: u32,
    height: u32,
    yaw: f32,
    pitch: f32,
    zoom: f32,
    bg: (u8, u8, u8, u8),
    accent: (u8, u8, u8),
) -> Bound<'py, PyBytes> {
    let data = rust_render_mesh_rgba(
        width,
        height,
        yaw,
        pitch,
        zoom,
        [bg.0, bg.1, bg.2, bg.3],
        [accent.0, accent.1, accent.2],
    );
    PyBytes::new(py, &data)
}

#[pymodule]
fn tw_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("QUAD_SHADER", QUAD_SHADER)?;
    m.add("ATLAS_SIZE", ATLAS_SIZE)?;
    m.add_class::<GpuUiCompiler>()?;
    m.add_function(wrap_pyfunction!(render_mesh_rgba, m)?)?;
    Ok(())
}
