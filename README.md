# textual-wgpu

Textual-inspired UI runtime on **wgpu**.

Not a Textual drop-in yet. Goal: same thin app model (screens, bindings,
compose) with a real GPU backend — panels, images, charts, and a 3D viewport
without terminal cell hacks.

Lives next to `make-dna-great-again` as a separate repo on purpose. MDGA should
keep using Textual until this API is useful enough to try one screen.

## Status

Milestone 0: GPU window that clears a color and quits on **Q**.

## Install

```bash
# from DNA/textual-wgpu
python -m venv .venv && source .venv/bin/activate
pip install -e .

# or into the shared DNA venv
source ../venv/bin/activate
pip install -e .
```

## Demo

```bash
textual-wgpu-demo
# or
python -m textual_wgpu
```

- **Q** / Escape — quit  
- green clear color = GPU path is alive  

## Layout

```text
src/textual_wgpu/
  app.py      # tiny App shell (bindings + draw hook)
  demo.py     # milestone-0 clear-screen demo
```

## Next (not done)

1. `compose` + one text/static widget  
2. `Viewport3D` (rotating cube)  
3. Grow API toward what MDGA actually imports from Textual  

## Non-goals (for now)

- Full Textual CSS / widget compatibility  
- Shipping inside the MDGA package  
- React / DOM stacks  
