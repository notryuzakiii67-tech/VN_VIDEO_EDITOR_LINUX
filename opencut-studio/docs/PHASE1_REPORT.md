# Phase 1 report — Working MVP and complete basic workflow

**Status: COMPLETE** (acceptance test passing; suite green).

## 1. Features implemented

- Native Ubuntu desktop app (Python 3.11 + PySide6 6.9), original dark
  UI: top bar / media library (left) / preview+transport (centre) /
  context-sensitive Properties (right) / multitrack timeline (bottom).
- Project lifecycle: create (16:9, 9:16, 1:1, 4:5, 4:3, 21:9, custom),
  save/reopen in the versioned `.opencut` JSON format (envelope +
  engine-model payload, atomic writes, explicit migration refusal for
  newer formats), recent projects, dirty tracking with save/discard/
  cancel prompt.
- Media import (video/audio/image) driven by real stream inspection,
  with the documented unsupported-media policy and per-file reasons;
  thumbnails + audio waveform peaks generated on worker threads.
- Timeline: video/overlay/text/audio tracks, add tracks, hide/mute
  toggles, select (incl. Shift multi-select), drag-move with snapping,
  edge trims clamped by neighbours *and* source bounds, split (button,
  `S`, Ctrl+K, double-click), duplicate, delete, zoom (slider,
  Ctrl+wheel), playhead scrubbing, frame stepping.
- Clip editing: position/scale/rotation/opacity/fit + pan-focus,
  constant speed with source-span preservation (curves refused, with an
  explanation, until the Phase 2 curve editor), text clips (21 OFL
  fonts incl. Bebas Neue, size/colour/outline/shadow/align/bold/italic),
  audio volume/pan/fades/mute.
- Preview: frame-accurate scrub stills (same graph compiler as export),
  pipe-based playback paced by the audio clock; video-only fallback when
  no audio device exists.
- Export: MP4 (H.264+AAC) via the engine's real render pipeline, four
  presets, progress + cancellation, and post-render re-probing of the
  output file. Empty timelines and missing FFmpeg produce actionable
  messages, not silent failures.
- Undo/redo (snapshot history, 250 steps) for every edit; selection is
  restored across undo/redo where the clips still exist.

## 2. Files created

```
opencut-studio/
  LICENSE (GPL-3.0)  README.md  CHANGELOG.md  CONTRIBUTING.md
  CODE_OF_CONDUCT.md  SECURITY.md  pyproject.toml
  requirements.txt  requirements-dev.txt  .gitignore  main.py
  conftest.py
  opencut/            engine facade, project store, media importer,
                      timeline ops, exporter, UI (7 modules), app entry
  tests/              conftest (lavfi fixtures) + 6 test modules (31 tests)
  docs/               ARCHITECTURE, DEPENDENCIES, ROADMAP, this report,
                      screenshot-main.png
  scripts/            dev_setup.sh, run_tests.sh
  .github/workflows/  tests.yml (CI: Ubuntu, apt FFmpeg, offscreen Qt)
```

## 3. Architecture decisions

- **Reuse the repository's proven media engine** (`vn_editor.core`,
  GPL-3.0) behind one facade (`opencut/engine.py`) instead of rewriting
  a render pipeline: Phase 1 effort goes into the original UI, the
  `.opencut` format and workflows. Documented in ARCHITECTURE.md.
- **Own file format** (envelope + model payload, `format_version: 1`)
  so OpenCut can migrate independently; refusals are explicit.
- **Preview/export parity by construction** (one graph compiler);
  stills are rendered from a serialised project snapshot on a pool.
- **Snapshot undo**: simple, robust; O(project) per step is acceptable
  at Phase 1 scale (noted as a Phase 3 optimisation candidate).
- **Licence GPL-3.0-or-later** chosen over MIT (rationale in
  DEPENDENCIES.md, incl. the GPL static-FFmpeg note).

## 4–5. Tests executed and results

- `tests/` (pytest): **31/31 passing** — store/format (5), importer
  policy (5), timeline ops (10), exporter + real render (5), Phase 1
  acceptance (1), headless UI smoke (5).
- `main.py --self-test`: **OK** (window builds, caption added, frame
  rendered offscreen).
- Live playback smoke (offscreen): pipe playback runs, clock advances,
  stop is clean.
- Engine regression suites (shared with the editor this repo also
  contains): 38/38 render checks, 26/26 GUI checks — unchanged.
- Coverage of the brief's test list: project create/serialise ✅,
  media metadata ✅, timeline calculations ✅, trim/split ✅, speed ✅,
  keyframe evaluation — model-level only (Phase 2 exposes the editor;
  `SpeedCurve.speed_at` verified), audio timing ✅ (acceptance RMS at
  the SFX position), undo/redo ✅, render configuration ✅, error
  handling ✅ (format/importer/export errors), relinking ✅, export
  validation ✅.

## 6. Known bugs / rough edges

- Playback start deep into a long timeline pays a graph-build + seek
  cost (engine pipe behaviour; Phase 3 preview work).
- Undo snapshots grow with project size (see Architecture).
- Inspector spin boxes commit undo steps on `editingFinished`; a
  mid-typing snapshot can occur after 600 ms of text inactivity (by
  design, so text edits are undoable in sane chunks).
- No autosave/recovery wiring yet (engine support exists; Phase 3).

## 7. Unsupported in Phase 1 (by design, stated in the UI where relevant)

Transitions UI, keyframe editor, speed-curve editor, ripple/roll/slip/
slide, track locking, reverse, effects/colour UI, proxies, render queue,
plugins, AI tools. These are Phases 2–5; the underlying model fields
already exist and round-trip through `.opencut` files.

## 8. Install and launch

```bash
sudo apt install -y ffmpeg python3 python3-venv
cd VN_VIDEO_EDITOR_LINUX/opencut-studio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # or: main.py project.opencut
```

## 9. Acceptance test result

`tests/test_acceptance_phase1.py` — builds the brief's workflow
(landscape gameplay → 9:16, cover+zoom+reposition, 1.5× speed, large
Bebas Neue caption, SFX at exactly 2.0 s over a quieted bed), saves,
reopens, exports, then verifies **in the decoded file**: valid
video+audio streams, 304×540 vertical output, ≈4.0 s duration (6 s at
1.5×), caption-white pixels present at t=1.0 s and absent at t=3.9 s,
and SFX RMS > 3× the bed at the placed moment. **Passing.**

## 10. Remaining work

Phase 2 (see ROADMAP.md), starting with transitions UI + keyframe
evaluation tests, then ripple family, track locking, animated text,
audio keyframes/fade curves, and the speed-curve editor.
