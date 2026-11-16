# textual-wgpu

Textual-compatible UI runtime on **wgpu**.

GPU-rendered runtime (`0.19.0`) implementing Textual's app model (screens, modes,
`with Container():` compose DSL, bindings, reactive state, CSS, events, workers,
and headless `Pilot` testing) backed by a Pillow → wgpu blit pipeline with optional
CRT post-FX (bloom / scanlines).

Lives next to `make-dna-great-again` as a standalone package.

## API Coverage (vs `textual 8.2.8`)

- **71 / 71** top-level modules (`app`, `screen`, `widget`, `widgets`, `containers`,
  `events`, `reactive`, `on`, `message`, `binding`, `actions`, `css`, `dom`,
  `driver`, `pilot`, `worker`, `validation`, `suggester`, `fuzzy`, `command`,
  `timer`, `notifications`, `color`, `document`, `canvas`, …)
- **42 / 42** widgets (`Button`, `Checkbox`, `Collapsible`, `ContentSwitcher`,
  `DataTable`, `Digits`, `DirectoryTree`, `Footer`, `Header`, `HelpPanel`, `Input`,
  `KeyPanel`, `Label`, `Link`, `ListItem`, `ListView`, `LoadingIndicator`, `Log`,
  `Markdown`, `MarkdownViewer`, `MaskedInput`, `OptionList`, `Placeholder`, `Pretty`,
  `ProgressBar`, `RadioButton`, `RadioSet`, `RichLog`, `Rule`, `Select`,
  `SelectionList`, `Sparkline`, `Static`, `Switch`, `Tab`, `TabPane`,
  `TabbedContent`, `Tabs`, `TextArea`, `Tooltip`, `Tree`, `Welcome`)
- **15 / 15** `textual.containers` exports (`Container`, `Vertical`, `VerticalGroup`,
  `Horizontal`, `HorizontalGroup`, `Grid`, `ItemGrid`, `Center`, `Middle`,
  `CenterMiddle`, `Right`, `ScrollableContainer`, `VerticalScroll`, `HorizontalScroll`,
  `reactive`)
- **42 / 42** `textual.events` `Event` classes (and **56 / 56** module symbols)

**Theme:** pass `Theme.from_mapping(...)`, `Theme(...)`, or `Theme.system()` — app-owned.

## Install

```bash
source ../venv/bin/activate
pip install -e ".[dev]"
# optional Textual parity comparison suite:
pip install -e ".[dev,parity]"
```

## Run

```bash
textual-wgpu-demo
# or
python -m textual_wgpu

# from make-dna-great-again:
python -m ydna --ui wgpu          # ModeApp CRT shell
python -m ydna --ui textual-wgpu  # real YDnaApp + workspaces via shim
```

- **F1–F10** — switch mode
- **Q** / **Escape** — quit

## Test (headless Pilot)

```python
async with app.run_test() as pilot:
    await pilot.press("ctrl+p")
    await pilot.action("quit")
```

```bash
pytest -q
```
