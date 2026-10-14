# Changelog

All notable changes to OpenCut Studio. Format follows
[Keep a Changelog](https://keepachangelog.com); versions follow
[Semantic Versioning](https://semver.org).

## [0.5.0] — 2026-10-14 — Phase 5: plugin architecture, AI hooks, open-source maturity

### Added
- Plugin architecture (`opencut/plugins.py`): discover, load, and manage
  plugins from `plugins/` (bundled) and user home directory. Five typed
  plugin interfaces: `EffectPlugin`, `TransitionPlugin`, `TitlePlugin`,
  `ImporterPlugin`, `ExporterPlugin`. Every failure mode (import error,
  register failure, on_load failure, missing requirements, bad return
  type, missing name, syntax error) is caught and logged, never crashes
  the app.
- Bundled example plugins: `example_warm.py` (effect), `example_title.py`
  (title preset), plus test fixtures for failure handling.
- Optional AI architecture documented: `AIHookPlugin` base class,
  design principles (opt-in, local-first, never required, no media
  upload without consent). Specific AI features (transcription, silence
  detection, scene detection, smart reframing) investigated and
  documented for future contributors.
- Project interchange investigation documented: import via FFmpeg +
  `ImporterPlugin`, export presets via `ExporterPlugin`, no current
  support for Premiere/DaVinci/Final Cut project files.
- Repository maturity: PR template, bug report and feature request
  issue templates, release checklist, updated SECURITY.md with plugin
  security guidance.
- 31 new automated tests (`tests/test_phase5.py`); 177 OpenCut tests
  total. All five development phases complete.

## [0.4.0] — 2026-10-14 — Phase 4: colour, compositing, effects, audio processing

### Added
- Colour grading sliders in the inspector: brightness, contrast,
  saturation, gamma (backed by the engine's "basic" effect) and
  temperature (separate effect). All parameters are undoable and
  evaluated per-clip via the engine's filter system.
- Compositing controls: blend mode selector (13 modes: normal, screen,
  multiply, overlay, soft/hard light, add, subtract, difference,
  lighten, darken, colour dodge/burn), mask shape picker (8 shapes:
  rect, ellipse, radial, linear, heart, star, split) with feather,
  width/height, and invert toggle; chroma key toggle with similarity
  and blend sliders.
- Effects panel: add/remove/enable/disable any of the engine's 38
  effects (across 6 categories: Basic, Colour, Blur & Sharp, Light,
  Stylise, Repair) with per-parameter live sliders. Effect catalogue
  dialog with category and effect pickers.
- Audio processing panel: 3-band EQ (low/mid/high), bass/treble boost,
  compressor, denoise, echo, reverb, highpass/lowpass cutoff, normalize
  toggle. All parameters undoable.
- Engine facade: `effect_categories()`, `effect_params()`,
  `effect_label()`, `effect_defaults()`, `BLEND_MODES`, `MASK_SHAPES`.
- 6 acceptance tests (`tests/test_acceptance_phase4.py`) verifying
  colour grading, masks, chroma key, audio processing, blend modes and
  round-trip fidelity. 22 unit tests (`tests/test_phase4.py`).
  146 OpenCut tests total.

## [0.3.0] — 2026-10-14 — Phase 3: performance & pro media workflows

### Added
- Background worker pool (`opencut/background.py`) for media analysis,
  thumbnail generation, waveform decoding, proxy transcoding, and
  background exports — all on `QThreadPool` so the UI never blocks.
- Proxy media: FFmpeg transcodes large videos to half-height H.264,
  cached in the project cache directory; skipped for small or audio-only
  assets. Context menu and File → Generate proxies.
- Preview quality selector (Draft/Low/Half/Full) in the preview transport
  bar, scaling scrub stills for faster iteration on large timelines.
- Autosave: a 20-second timer writes recovery snapshots when the project
  is dirty; startup recovery dialog offers to restore the latest snapshot.
- Media relinking: context menu on missing assets and File → Relink
  missing media, using the engine's tested `relink()` function.
- Render queue (`RenderQueue`): sequential background exports with
  progress reporting, cancellation, and an "Add to queue" button in the
  export dialog.
- VFR detection on import: non-standard fps and frame-count mismatches
  are flagged, with a user-visible warning suggesting re-encoding.
- Media panel enhancements: proxy indicator (◈) and missing indicator
  (⚠) on asset labels, proxy generation from context menu.
- 38 new automated tests (`tests/test_phase3.py`); 118 OpenCut tests
  total.

## [0.2.0] — 2026-10-14 — Phase 2: advanced editing & motion graphics

### Added
- Edit modes: ripple, roll, slip and slide edits plus ripple delete,
  with toolbar buttons and `V`/`B`/`N`/`Y`/`U` shortcuts.
- Keyframe animation for position X/Y, scale, rotation, opacity and
  audio volume: inspector panel evaluating values at the playhead,
  easing curves, keyframe diamonds on the timeline (click to seek),
  all undoable and identical in preview and export.
- Titles: six built-in animated title presets, "Add title" menu, and
  user presets (save the current text style, stored in `QSettings`).
- Transition picker with the engine's catalogue and adjustable,
  neighbour-clamped duration.
- Speed-curve presets, a custom speed-curve editor dialog (drag/add/
  delete points), and clip reverse.
- Audio fade curves (linear, smooth, …) alongside volume keyframes.
- Track locking (L) next to hide/mute; snapping toggle and playhead
  snapping; `Home`/`End`, `Ctrl+←/→` edit-point navigation, `Ctrl+A`.
- Export presets: Web 720 and Master join Original/Balanced/Small/Draft.
- Phase 2 acceptance test plus three engine regression tests
  (`tests/test_acceptance_phase2.py`, `tests/test_regressions_phase2.py`);
  80 OpenCut tests in total.

### Fixed (shared `vn_editor.core` engine)
- Speed-curve video rendering mis-timed slices; rewritten as a
  single-pass piecewise `setpts` (frame-accurate vs. the model).
- Static `transform.scale` was ignored by the movie render path, so
  picture-in-picture clips exported full-frame; Fit mode now folds the
  scale into the rendered size.
- Audio clips placed later on the timeline could be mixed from t=0
  (`adelay` timestamp placement vs. `amix`); placement now prepends
  real silence samples.
- Speed-curve audio was silent for clips with `src_in > 0` (double
  seek in the ramp slice windows); windows are clip-relative now.

## [0.1.0] — 2026-10-13 — Phase 1: working MVP

### Added
- Native Ubuntu desktop editor (Python + PySide6) with an original,
  simple dark workspace: top bar, media library, preview, properties,
  multitrack timeline.
- Versioned `.opencut` project format: create/save/reopen, format
  presets (16:9, 9:16, 1:1, 4:5, 4:3, 21:9, custom), recent projects,
  unsaved-changes protection, media relinking support.
- Media import for video/audio/images with stream-based metadata
  inspection and explicit rejection reasons; background thumbnails and
  audio waveforms.
- Timeline editing: multitrack (video/overlay/text/audio), move, trim
  (source-bounds aware), split, duplicate, delete, zoom, snapping,
  track hide/mute.
- Clip properties: transform (position/scale/rotation/opacity/fit/
  pan-focus), constant speed, text (21 OFL fonts incl. Bebas Neue,
  colour/outline/shadow), audio volume/pan/fades/mute.
- Frame-accurate scrub preview and audio-clock playback.
- MP4 export (H.264 + AAC) with presets, progress, cancellation and
  post-render validation.
- Full undo/redo; 31 automated tests including the Phase 1 acceptance
  workflow; headless self-test (`main.py --self-test`).
