# Changelog

All notable changes to OpenCut Studio. Format follows
[Keep a Changelog](https://keepachangelog.com); versions follow
[Semantic Versioning](https://semver.org).

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
