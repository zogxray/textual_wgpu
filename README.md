# textual-wgpu

Textual-inspired UI runtime on **wgpu**.

Not a Textual drop-in yet. Goal: same thin app model (screens, bindings,
compose) with a real GPU backend — panels, images, charts, and a 3D viewport
without terminal cell hacks.

Lives next to `make-dna-great-again` as a separate repo on purpose. MDGA should
keep using Textual until this API is useful enough to try one screen.

## Status

Milestone 1: rough app chrome on GPU —

`ModeBar` (F1–F10) · placeholder `body` · `KeyBar` · `status`

Painted with Pillow → uploaded as a wgpu texture (crude, fine for POC).

**Theme:** the library does not own brand colors. Pass ``Theme`` from the app
(``Theme.from_mapping(...)``) or use ``Theme.system()`` (host light/dark).
Same idea as Textual: app/host style → runtime paints.

## Install (library)

```bash
source ../venv/bin/activate
pip install -e /path/to/textual-wgpu
```

## Run

```bash
textual-wgpu-demo
# or
python -m textual_wgpu

# from make-dna-great-again (experimental branch)
python -m ydna --ui wgpu
```

- **F1–F10** — switch mode (body title only)  
- **Q** / Escape — quit  

## Layout

```text
src/textual_wgpu/
  app.py      # window, bindings, draw hook
  blit.py     # RGBA → fullscreen GPU blit
  shell.py    # ModeBar + body + KeyBar + status
  demo.py     # milestone-0 clear-screen
```

## Next (not done)

1. Clickable ModeBar  
2. One real mode body (e.g. F6 panels, still rough)  
3. `Viewport3D` inside body  

## Non-goals (for now)

- Full Textual CSS / widget compatibility  
- Shipping inside the MDGA package  
- React / DOM stacks  
