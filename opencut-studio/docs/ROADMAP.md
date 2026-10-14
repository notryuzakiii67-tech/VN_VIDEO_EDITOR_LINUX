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

## Phase 4 — Professional colour, audio, compositing, effects (next)

Colour: brightness/contrast/saturation/temperature, curves, wheels, LUT
import, before/after, scopes. Audio: EQ, compression, limiting, noise
reduction, automation, buses, metering/loudness. Compositing: alpha,
blend modes, masks, chroma key (model fields already exist), effect
stacking/reordering with enable toggles and presets. (The engine's
effects library is large; Phase 4 exposes it carefully, with per-effect
render verification.)

## Phase 5 — AI tools, extensibility, open-source maturity

Documented plugin architecture (effects, transitions, titles, importers/
exporters, optional AI hooks), contributor docs, project interchange
investigation, optional local AI (transcription/subtitles, silence and
scene detection, smart reframing) — always opt-in, local-first, never
required for the core editor. Repository infrastructure (this repo
already has: CONTRIBUTING, code of conduct, security policy, CI workflow,
changelog) is completed and exercised by real contributions.

## Explicitly not claimed until validated

HDR/colour-managed pipelines, motion/face tracking, rotoscoping, AI
cutout, real-time playback of arbitrary 4K media on modest hardware, and
the "OpenCut Studio" name/trademark (provisional).
