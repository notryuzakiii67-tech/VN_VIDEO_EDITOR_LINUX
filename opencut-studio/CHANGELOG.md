# Changelog

All notable changes to OpenCut Studio. Format follows
[Keep a Changelog](https://keepachangelog.com); versions follow
[Semantic Versioning](https://semver.org).

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
