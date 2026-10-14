# Phase 2 report — Advanced editing and motion graphics

**Status: COMPLETE (v0.2.0).** The Phase 2 acceptance test passes, and so
do the full OpenCut suite (80 tests), the VN engine suite (38/38) and the
VN GUI smoke test (26/26).

## 1. Features implemented

**Timeline editing**
- Five edit modes in the timeline toolbar (and on `V`/`B`/`N`/`Y`/`U`):
  Select, Ripple, Roll, Slip and Slide, plus ripple delete. Roll edits a
  shared boundary, slip moves a clip's source window under a fixed
  timeline span, slide moves a clip and its neighbours together;
  all are clamped to source bounds and neighbour positions.
- Improved snapping: a Snap toggle in the toolbar, snapping of the
  dragged edge/body to the playhead and to clip edges on every track,
  and playhead snapping while scrubbing the ruler.
- Track header buttons are now **L**ock / **H**ide / **M**ute. Locked
  tracks refuse clip drags and splits. Additional navigation: `Home` /
  `End`, `Ctrl+Left`/`Ctrl+Right` jump between edit points, `Ctrl+A`
  selects all clips.

**Keyframes and motion**
- Keyframe animation for Position X/Y, Scale, Rotation, Opacity and
  audio Volume, with easing (linear, ease-in, ease-out, ease-in-out, and
  the engine's other curves). The inspector's Animation panel shows the
  property's value *at the playhead* (evaluated, e.g. 119.2 % mid-ramp),
  adds/updates/deletes the keyframe at the playhead, lists all keyframes
  with click-to-seek, and clears a property. Everything is undoable.
- Keyframes render as diamonds along the bottom of timeline clips;
  clicking a diamond selects the clip and seeks the playhead to it.
- Keyframes are evaluated by the same code in scrub preview and in
  export (see §9 — parity is asserted, not assumed).

**Titles and animated text**
- `opencut/timeline/titles.py`: six built-in title presets (starting
  with *Bold Impact*), each bundling font styling with a text animation
  (e.g. pop-in) expressed through the keyframe system.
- "Add title" menu in the top bar; users can save the selected text
  clip's style as their own preset (persisted via `QSettings`) and
  re-apply it later.

**Transitions, speed, audio**
- Transition picker in the inspector with the engine's transition
  catalogue (grouped by category) and an adjustable duration, clamped
  against the neighbouring clips; the timeline shows the active
  transition on the clip.
- Speed: the engine's speed-curve presets (Slow Start, Slow Fast Slow,
  Bullet Time, …), a custom speed-curve editor dialog (drag points,
  double-click to add, right-click to delete, preset starting points),
  custom constant speed, and clip reverse. Speed-curve and reversed
  clips are badged on the timeline (`curve` / `rev`).
- Audio: volume keyframes, fade-in/out with selectable fade curves
  (linear, smooth, …), on top of Phase 1 volume/pan/fades/mute.
- Export presets grew to six: Original, Balanced, Small, Draft,
  Web 720 and Master.

**Undo/redo**: every new operation (ripple/roll/slip/slide, keyframes,
transitions, curves, reverse, title insertion, track flags) runs inside
named snapshot transactions, so one user gesture is one undo step,
including during continuous mouse drags.

## 2. Files created or changed

- `opencut/timeline/titles.py` (new) — title presets + application.
- `opencut/timeline/ops.py` — ripple/roll/slip/slide/ripple-delete,
  keyframe ops, transition ops, speed/reverse/fade-curve ops.
- `opencut/ui/timeline.py` — edit modes, snap toggle, lock button,
  keyframe diamonds, extended keyboard control.
- `opencut/ui/inspector.py` — keyframes panel, transition picker,
  speed-curve editor dialog, fade-curve controls.
- `opencut/ui/main_window.py` — Add-title menu, user title presets.
- `vn_editor/core/speedcurve.py` — video + audio speed-curve fixes (§3).
- `vn_editor/core/audio.py` — timeline placement fix (§3).
- `vn_editor/core/render.py` — static-scale fix (§3).
- `tests/test_phase2_ops.py` (26), `tests/test_titles.py` (6),
  `tests/test_ui_phase2.py` (13), `tests/test_acceptance_phase2.py` (1),
  `tests/test_regressions_phase2.py` (3), `tests/conftest.py`
  (solid-colour fixtures). `.opencut` format stayed at v1 — new fields
  (keyframes, curves, presets metadata) load with defaults, no
  migration was needed.

## 3. Engine bugs found by Phase 2 testing (and fixed)

Phase 2's acceptance criteria exposed four real bugs in the shared
`vn_editor.core` engine that every pre-existing suite passed, because
no test had ever asserted *audio placement* or *overlay geometry*:

1. **Speed-curve video**: the split/tempo/concat slice renderer lost
   frames and mis-timed slices (timebase quantisation plus concat
   segment accounting). Replaced by a single-pass piecewise
   `setpts` in integer microseconds; verified frame-accurate against
   the model (90/90 frames, source identity within 0.046 s).
2. **Static scale ignored (movie path)**: a clip with
   `transform.scale = 0.35` and no scale keyframes rendered
   full-frame in exports while the scrub still showed it shrunk.
   `build_transform` now folds the static scale into the fitted size
   in Fit mode. (Cover mode still fills the frame, by design.)
3. **Audio placement**: clip chains were positioned with `adelay`,
   which shifts frame timestamps; with the engine's pre-fix chain the
   final `amix` mix could align a late clip by sample count instead,
   so a clip placed at 4 s was measured mixed from 0 s, overlapping
   the earlier clip (~√2× loudness) with silence at its real position.
   Placement is now prepended as real silence samples
   (`anullsrc` + `concat`), which is correct by construction
   regardless of mixer timestamp behaviour. Honest caveat: the
   exported acceptance-test mix happened to produce identical RMS
   before/after this fix — the displacement is real but depends on
   the chain arrangement, so the regression test asserts the
   user-visible contract (silent before, loud at/after the clip)
   rather than a specific failure value.
4. **Speed-curve audio silent for `src_in > 0`**: the ramp branch
   windowed slices with absolute `atrim` on a stream already
   input-seeked to `src_in` — a double seek. A ramped clip trimmed
   3 s into its source exported as total silence (measured RMS 0.0).
   Slice windows are now clip-relative, and slice placement inside
   the fragment uses silence-prepends like (3).

The acceptance test itself also had a scenario flaw (the reversed
PiP's own 440 Hz audio was never muted, confounding the music-bed
envelope measurement); the scenario now mutes it, as its comments
always intended. The assertions were not weakened.

## 4–5. Tests executed and results

All runs on 2026-10-14, FFmpeg 7.0.2 (imageio-ffmpeg static build),
`QT_QPA_PLATFORM=offscreen`:

| Suite | Result |
|---|---|
| OpenCut `pytest tests/` (80 tests) | **80 passed** |
| — `test_phase2_ops.py` | 26 passed |
| — `test_titles.py` | 6 passed |
| — `test_ui_phase2.py` (headless Qt) | 13 passed |
| — `test_acceptance_phase1.py` + `test_acceptance_phase2.py` | 2 passed |
| — `test_regressions_phase2.py` | 3 passed |
| — `test_store.py` + `test_importer.py` + `test_exporter.py` + `test_ops.py` + `test_ui_smoke.py` | 25 passed |
| VN engine `tests/test_render.py` | **38/38 passed** |
| VN GUI `tools/gui_smoke.py` | **26/26 passed** |
| `main.py --self-test` | OK |

**Falsification check**: each regression test was also run against a
temporarily reverted fix. The speed-curve test fails on the old code
with RMS exactly 0.0 at the clip's position; the PiP test fails with a
pure-red corner pixel (252, 0, 0) where the blue base should show.
The plain-placement test passes on the shipped code and pins the
placement contract (see the caveat in §3.3).

## 6. Known bugs / rough edges

- **Cover mode** clips fill the frame by design; `transform.scale`
  composes in Fit mode (and through keyframes). Use Fit for
  picture-in-picture overlays.
- The **Draft** export preset downscales to at most 960×540 — expected,
  but it surprises users who pick it for final renders; the export
  dialog labels it as a preview-quality preset.
- Scrub stills for heavy multitrack projects still render on demand
  per playhead position (no thumbnail/still cache yet — Phase 3).
- This development sandbox lacks system `libGL`/`libEGL`/`libxkbcommon`/
  `libdbus-1`; the UI suites were run headlessly against locally built
  stub libraries so Qt could load. That workaround is sandbox-only and
  is **not** shipped; on a normal Ubuntu install the packages below
  provide the real libraries.

## 7. Unsupported in Phase 2 (by design)

Colour grading, EQ/compression buses, masks/chroma, effect stacks
(Phase 4); proxies, render queue, autosave/crash-recovery UI, relink UI
(Phase 3); plugins and AI tools (Phase 5). Transitions are limited to
one transition *into* a clip (no separate out-transition), matching the
engine model. No HDR, tracking or rotoscoping (see ROADMAP).

## 8. Install and launch

```bash
sudo apt update
sudo apt install -y ffmpeg python3 python3-venv python3-pip \
    libgl1 libegl1 libxkbcommon0 libdbus-1-3
git clone <this-repository>
cd VN_VIDEO_EDITOR_LINUX/opencut-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # or: main.py project.opencut
scripts/run_tests.sh                # full suite + headless self-test
```

## 9. Acceptance test result

`tests/test_acceptance_phase2.py` **passes**. It builds a multitrack
edit — two gameplay clips joined by a 0.6 s fade, scale keyframes
1.0→1.6, a "Slow Start" speed curve, an animated *Bold Impact* title,
a music bed with volume keyframes (0→0.9→0) and a smooth fade curve,
and a reversed 0.35-scale PiP — then:

1. saves and reopens the project, asserting every edit survived
   (transition kind/duration, keyframe values, curve preset, title
   animation, fade type, reverse flag, scale, total duration);
2. exports (Draft) and validates the MP4 (streams, ~6 s duration);
3. asserts preview/export pixel agreement (mean absolute difference
   below the 8.0 threshold) at a transition midpoint (t=3.0), during
   the title animation (t=1.2) and over the curve + reversed PiP
   (t=5.2), and that the title's glyphs are visibly on screen;
4. measures the exported audio envelope: the mid-bed segment is more
   than 2× the intro and more than 3× the outro, proving the volume
   keyframes and fade curve reached the render.

## 10. Remaining work

Phase 3 (performance & professional workflows): proxy media, preview
quality settings, thumbnail/waveform caches, background analysis,
render queue with cancellation, autosave + crash recovery, media
relinking UI, project/media search, VFR testing — ending, as always,
with its own acceptance test and report. Phases 4 (colour/audio/
compositing/effects) and 5 (plugins, optional local AI, repo maturity)
follow in order.
