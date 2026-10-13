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

## Phase 2 — Advanced editing and motion graphics (next)

Ripple/roll/slip/slide edits, ripple delete, improved snapping, track
locking (the model's `locked` flags already exist), keyframe editing and
evaluation for position/scale/rotation/opacity, animated text, title
presets, transitions palette with adjustable duration (engine supports 65
transition kinds — needs its UI), audio volume keyframes and fade curves,
custom speed curves editor (the `SpeedCurve` data model exists; constant
speed deliberately refuses curve clips today), reverse, flexible aspect
presets, better export presets, preview/render parity checks extended to
every exposed feature.

## Phase 3 — Performance and professional media workflows

Proxy media, configurable preview quality, thumbnail/waveform caching to
disk, background media analysis, render queue, autosave + crash recovery
(the engine already stores autosaves/recovery slots — needs wiring),
media relinking UI (engine `relink` exists and is tested), project/media
search improvements, VFR media testing, performance measurements on
long/high-resolution projects.

## Phase 4 — Professional colour, audio, compositing, effects

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
