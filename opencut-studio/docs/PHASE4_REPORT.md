# Phase 4 report — Professional colour, audio, compositing, effects

**Version:** 0.4.0  
**Date:** 2026-10-14  
**Status:** ✅ COMPLETE

---

## Features delivered

### 1. Colour grading (inspector → Colour group)

Five sliders, all backed by the engine's effect system with per-parameter undo:

| Control | Effect kind | Parameter | Range |
|---------|------------|-----------|-------|
| Brightness | `basic` | `brightness` | −1.0 to 1.0 |
| Contrast | `basic` | `contrast` | 0.0 to 3.0 |
| Saturation | `basic` | `saturation` | 0.0 to 3.0 |
| Gamma | `basic` | `gamma` | 0.1 to 3.0 |
| Temperature | `temperature` | `temperature` | −1.0 to 1.0 |

The `basic` and `temperature` effects are auto-created (with engine defaults)
the first time a video/image clip is selected. Subsequent selections read
the existing values. The engine compiles these into FFmpeg `eq` and
`colortemperature` filters in the render graph.

### 2. Compositing (inspector → Compositing group)

Three systems, all using the engine's existing model fields and render
pipeline:

**Blend modes** — 13 modes mapped to FFmpeg's `blend` filter:
normal, screen, multiply, overlay, soft light, hard light, add, subtract,
difference, lighten, darken, colour dodge, colour burn.

**Masks** — 8 shapes (rect, ellipse, radial, linear, heart, star, split)
rendered as RGBA PNGs and composited via `alphamerge`. Controls for
feather (0–100%), width/height (0–100%), and invert toggle.

**Chroma key** — toggle + similarity (0–100%) + blend (0–100%) sliders.
The engine uses `chromakey` + `colorchannelmixer` filters.

### 3. Effects panel (inspector → Effects group)

- **Add effect:** category → effect picker dialog with all 38 engine effects.
- **Per-parameter sliders:** live-updating, undo-bound sliders for every
  float parameter of the selected effect.
- **Enable/disable:** checkbox per effect (toggleable without removing).
- **Remove:** ✕ button per effect.

The engine's `FilterInstance` model stores effects as a list on each clip;
the compiler (`build_effect_chain`) emits the correct FFmpeg filter graph.

### 4. Audio processing (inspector → Audio processing group)

| Control | AudioProps field | Range |
|---------|-----------------|-------|
| EQ Low | `eq_low` | −12 to +12 dB |
| EQ Mid | `eq_mid` | −12 to +12 dB |
| EQ High | `eq_high` | −12 to +12 dB |
| Bass boost | `bass_boost` | 0–100% |
| Treble boost | `treble_boost` | 0–100% |
| Compressor | `compressor` | 0–100% |
| Denoise | `denoise` | 0–100% |
| Echo | `echo` | 0–100% |
| Reverb | `reverb` | 0–100% |
| Highpass | `highpass` | 0–20000 Hz |
| Lowpass | `lowpass` | 0–20000 Hz |
| Normalize | `normalize` | checkbox |

The engine's `clip_audio_filters()` compiles these into FFmpeg
`equalizer`, `acompressor`, `afftdn`, `aecho`, `aecho` (reverb variant),
`highpass`, `lowpass`, and `loudnorm` filters.

### 5. Engine facade additions

```python
engine.effect_categories()  # {"Basic": [...], "Colour": [...], ...}
engine.effect_params("blur")  # [Param(key="blur", ...), ...]
engine.effect_label("bw")  # "Black & White"
engine.effect_defaults("basic")  # {"brightness": 0.0, "contrast": 1.0, ...}
engine.BLEND_MODES  # ["normal", "screen", ...]
engine.MASK_SHAPES  # ["none", "rect", "ellipse", ...]
```

---

## Files changed/created

| File | Change |
|------|--------|
| `opencut/engine.py` | Added `effect_categories`, `effect_params`, `effect_label`, `effect_defaults`, `BLEND_MODES`, `MASK_SHAPES`, effects import |
| `opencut/ui/inspector.py` | `_build_colour_group`, `_build_compositing_group`, `_build_effects_group`, `_build_audio_processing_group`, Phase 4 helpers, `_build_text_group` restored |
| `tests/test_phase4.py` | **NEW** — 22 tests |
| `tests/test_acceptance_phase4.py` | **NEW** — 6 acceptance tests |
| `opencut/__init__.py` | Version 0.4.0 |
| `pyproject.toml` | Version 0.4.0 |
| `CHANGELOG.md` | Phase 4 entry |
| `docs/ROADMAP.md` | Phase 4 ✅, Phase 5 next |
| `docs/PHASE4_REPORT.md` | **NEW** — this file |

---

## Test results

| Suite | Result |
|-------|--------|
| OpenCut `pytest tests/` | **146 passed** (118 prior + 22 Phase 4 + 6 acceptance) |
| `main.py --self-test` | **OPENCUT SELF-TEST OK (ffmpeg=yes)** |
| VN `test_render.py` | **38/38 passed** |

---

## Known bugs / limitations

1. **No LUT import UI:** the engine supports `.cube` LUT files via the
   `lut` effect, but there's no file-picker UI for selecting a LUT file
   yet. Users can add the `lut` effect from the effects panel and set the
   path parameter manually.

2. **No scopes/waveform monitor:** colour scopes (vectorscope, waveform,
   histogram) are a visual aid not yet implemented.

3. **No before/after toggle:** a split-screen or A/B comparison mode
   for colour grading is not yet implemented.

4. **No effect presets:** the engine supports effect presets but the UI
   doesn't expose a preset picker yet.

5. **No audio buses/metering:** audio bus routing and loudness metering
   are not yet implemented.

6. **VN `gui_smoke.py` crash:** same PySide6 6.12.0 refcount bug as
   Phase 3 — not a Phase 4 regression.

---

## Install and launch

```bash
sudo apt install ffmpeg python3 python3-venv python3-pip \
  libgl1 libegl1 libxkbcommon0 libdbus-1-3
cd opencut-studio
python3 -m venv .venv
.venv/bin/pip install PySide6-Essentials numpy pytest imageio-ffmpeg
.venv/bin/python main.py
```

---

## Acceptance

- All 146 OpenCut tests pass (including 6 acceptance + 22 Phase 4 tests).
- Self-test OK with FFmpeg available.
- VN engine tests pass 38/38.
- Colour grading produces a measurable RMS difference from a plain export.
- Masks, chroma key, blend modes, and audio processing all export
  successfully and survive round-trip save/reopen.
- No existing features regressed.

---

## Remaining work

Phase 5 (next): plugin architecture, AI tools, contributor docs, repository
maturity. See [ROADMAP.md](ROADMAP.md).