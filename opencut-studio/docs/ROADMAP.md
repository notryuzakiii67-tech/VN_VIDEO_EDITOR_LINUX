# OpenCut Studio roadmap

Development proceeds in five phases, **in order**. A phase is only marked
complete when its acceptance test passes. Working features are never
replaced with placeholders while later phases are built.

## Phase 1 — Working MVP and complete basic workflow ✅ (v0.1.0)

Application launch, clean interface, versioned project create/save/reopen,
video/audio/image import with metadata, media library, preview + playback +
seeking, multitrack timeline, select/move/trim/split/delete, timeline zoom,
transform (position/scale/rotation), landscape & vertical formats + custom
sizes, clip speed, text overlays (size/colour/outline/shadow/position),
audio tracks with trim/volume/fades, undo/redo, MP4 export with progress
and cancellation, actionable errors, automated tests incl. the Phase 1
acceptance workflow. See [PHASE1_REPORT.md](PHASE1_REPORT.md).

## Phase 2 — Advanced editing and motion graphics ✅ (v0.2.0)

Ripple/roll/slip/slide edits, ripple delete, improved snapping with a
toggle and playhead snapping, track locking, keyframe editing and
evaluation for position/scale/rotation/opacity plus audio volume
(diamonds on the timeline, inspector panel evaluating at the playhead),
animated text and title presets (built-in + user), transitions palette
with adjustable duration, audio volume keyframes and fade curves,
custom speed-curve editor, reverse, more export presets (Web 720,
Master), preview/render parity asserted in the acceptance test. Four
engine bugs found by that test were fixed in `vn_editor.core` (speed
curves, static scale, audio placement). See
[PHASE2_REPORT.md](PHASE2_REPORT.md).

## Phase 3 — Performance and professional media workflows ✅ (v0.3.0)

Background worker pool (QThreadPool-based) for thumbnails, waveforms,
proxy generation, media analysis, and exports. Proxy media generation
(FFmpeg transcode to half-height H.264, cached in the project cache
directory, skipped for already-small or audio-only assets). Configurable
preview quality selector (Draft/Low/Half/Full) that scales scrub stills
before rendering. Autosave timer with 20-second tick writing recovery
snapshots; startup recovery dialog offers to restore the latest snapshot.
Media relinking UI (context menu and File menu) for missing files using
the engine's `relink()`. Render queue for background exports with
sequential job execution, progress, and cancellation. VFR detection on
import (flags non-standard fps and frame-count mismatches, warns the
user). Media panel enhancements: proxy/missing indicators on asset labels,
proxy generation from context menu, relink from context menu. Export
dialog gains an "Add to queue" button alongside "Export now". See
[PHASE3_REPORT.md](PHASE3_REPORT.md).

## Phase 4 — Professional colour, audio, compositing, effects ✅ (v0.4.0)

Colour grading sliders (brightness, contrast, saturation, gamma,
temperature) backed by the engine's effect system with per-parameter
undo. Compositing: 13 blend modes (normal/screen/multiply/overlay/…),
8 mask shapes (rect/ellipse/radial/linear/heart/star/split) with
feather/size/invert, and chroma key (colour/similarity/blend). Effects
panel: add/remove/enable/disable any of the engine's 38 effects across
6 categories (Basic, Colour, Blur & Sharp, Light, Stylise, Repair)
with live parameter sliders. Audio processing: 3-band EQ, bass/treble
boost, compressor, denoise, echo, reverb, highpass/lowpass, normalize.
All effects, compositing settings and audio processing survive
save/reopen. Acceptance: colour-graded + masked + chroma-keyed +
audio-processed project exported and verified (RMS difference between
plain and graded exports proves effects are applied in the render).
See [PHASE4_REPORT.md](PHASE4_REPORT.md).

## Phase 5 — AI tools, extensibility, open-source maturity ✅ (v0.5.0)

Plugin architecture (`opencut/plugins.py`) with discovery, loading,
error handling, and five typed plugin interfaces (Effect, Transition,
Title, Importer, Exporter). Plugins are discovered from `plugins/`
(bundled) and `~/.local/share/OpenCutStudio/plugins/` (user-installed).
Every failure mode (import error, register failure, on_load failure,
missing requirements, bad return type, missing name) is caught and
logged, never crashes the app. Two bundled example plugins (warm effect,
gaming title) plus test fixtures for failure modes. 31 plugin tests
covering all loading and failure paths. Optional AI architecture
documented (opt-in, local-first, never required, no media upload
without consent). Repository maturity: PR template, issue templates
(bug report, feature request), release checklist, updated SECURITY.md
with plugin security guidance. All five phases complete. See
[PHASE5_REPORT.md](PHASE5_REPORT.md).

## Explicitly not claimed until validated

HDR/colour-managed pipelines, motion/face tracking, rotoscoping, AI
cutout, real-time playback of arbitrary 4K media on modest hardware, and
the "OpenCut Studio" name/trademark (provisional).
