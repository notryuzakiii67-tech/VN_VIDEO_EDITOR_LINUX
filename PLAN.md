# VN Studio for Linux — Implementation Plan

## 0. Environment findings (verified, not assumed)

| Item | Result |
|---|---|
| OS | Debian 12 container (target: Ubuntu 22.04/24.04 LTS desktop) |
| Python | 3.11 |
| PySide6 | 6.12.0 — installs cleanly from PyPI (no system packages needed) |
| FFmpeg | **not preinstalled**. Discovered via PATH → common paths → bundled → PyPI `imageio-ffmpeg` |
| Static FFmpeg | 7.0.2 johnvansickle build: libx264, libx265, libsvtav1, aac, libmp3lame, **libass**, librubberband, fontconfig |
| `ffprobe` | **absent** from the static build → native MP4/MKV/EBML metadata parser implemented in Python |
| `drawtext` | **not compiled in** → all text/titles go through **libass** (better: real fonts, outlines, shadows, `\move`/`\t` animation) |
| `scale` | output size is **static** — `eval=frame` does not resize per frame |
| `perspective` | has `eval=frame` but **no `t`/`n` variables** → unusable for animation |
| `zoompan` | ✔ `z`,`x`,`y` accept `on`/`time`/`iw`/`ih`/`zoom`; ✔ preserves RGBA alpha |
| `overlay` | ✔ `x`,`y` accept `t` (per-frame), full keyframe animation of position |
| `rotate` | ✔ accepts `t` expressions |
| `geq` | ✔ accepts `T` (animated alpha) — used only as a fallback (slow) |
| `sendcmd` | ✔ verified animating `colorchannelmixer=aa` → cheap animated opacity |
| `xfade` | ✔ 57 transitions; **requires a constant frame-rate input** and rejects `-r` on the input (`fps` filter must be last in the segment chain) |
| `ass`/`subtitles` | ✔ libass 0.17.3 + fontconfig → **Bebas Neue renders correctly** |

Rendering primitives are therefore chosen per-case, and the plan below reflects
what was *measured* to work on this exact toolchain.

## 1. Research: what VN Video Editor actually does

Consolidated from VN's own feature descriptions and independent reviews:

* **Multi-track timeline**, main video track + stacked PIP layers for video, photos,
  stickers, text; drag to reorder, swipe to delete, precision to 0.05 s, timeline
  zoom, keyframe markers drawn on the clip.
* **Transform**: pinch to scale, drag to move, rotate; crop; aspect ratio presets;
  keyframes on position/scale/rotation/opacity with easing; 19 animation presets.
* **Speed**: slider, **speed curve** (6 presets + custom points, Premiere-style time
  remapping), reverse, freeze frame (produces a ~1.5 s still).
* **Text**: font library, colour, size, stroke, shadow, background, alignment,
  animation templates; separate **subtitle** tool; auto captions.
* **Audio**: multi-track, volume keyframes, fades (J/L cuts), voice-over recording,
  music library, **beat markers** for music sync, audio effects.
* **Visual**: transitions between clips (duration editable, "apply to all"),
  filters, LUT import (.cube), chroma key (green/blue + intensity + spill),
  masks, blend modes, motion FX, mosaic, stabilisation, frame interpolation.
* **Workflow**: drafts saved automatically, unlimited undo/redo, non-destructive,
  templates/"VN codes", custom 4K60 export, no watermark.

## 2. Architecture

```
vn_editor/
  core/                     no Qt import -> fully unit-testable headless
    ffmpeg.py      binary discovery (PATH / common dirs / bundled / pypi), capability probe
    probe.py       native MP4/MOV + Matroska metadata parser; ffprobe + stderr fallback
    model.py       Project, Track, Clip, Transform, Keyframe, effects, transitions
    project.py     .vnp save/load (zip+json), autosave, crash recovery, recent list
    undo.py        command stack (mergeable commands + snapshot transactions)
    expr.py        keyframe -> FFmpeg expression compiler (with easing)
    ass.py         Text/Sticker/Caption -> ASS script (libass), animations
    effects.py     filter catalogue + parameter schema -> filtergraph fragments
    transitions.py 57 xfade transitions + custom GPU-free ones (zoom, blur, whip)
    speedcurve.py  piecewise speed remap -> setpts + atempo chains
    render.py      filtergraph compiler (the heart), export + preview + single frame
    audio.py       volume/fade/EQ/compressor graph, voiceover, mixdown
    sfx.py         procedural sound-effect synthesis (fahhh / thud / get out / ...)
    library.py     built-in music + SFX library, offline generation
    beat.py        numpy beat detection (onset/energy) -> beat markers
    captions.py    SRT/VTT import-export, smart timing, optional ASR hook
    presets.py     templates incl. "Minecraft Vertical POV"
    keyframes.py   interpolation + evaluation
  ui/                       PySide6
    theme.py       VN-like dark palette + QSS
    icons.py       procedural vector icons (no third-party assets)
    widgets/       timeline, ruler, clip items, preview monitor, media bin,
                   inspector stack, tool panels, export dialog, dialogs
  resources/fonts/          21 OFL fonts incl. Bebas Neue (+ OFL license)
```

### Rendering strategy (derived from the measurements above)

Every visual clip is compiled to an **RGBA layer** and composited onto a
`yuva420p` canvas with `overlay`, so clipping is free and layer order is exact.

| Transform case | Technique |
|---|---|
| static position + static scale | static `scale` → `overlay=x:y` |
| animated position only | static `scale` → `overlay=x='expr(t)'` |
| animated scale (zoom in/out) | `zoompan(z=expr(on/time), x=, y=)` on a canvas-size stage → `overlay=0:0` |
| rotation (static or animated) | `rotate` with expanded output size, applied before placement |
| opacity | `colorchannelmixer=aa` (static) / `sendcmd`+`colorchannelmixer` (animated) |

`zoompan`'s crop window must stay inside its input, so for animated scale the
stage is padded by `p = ceil(1/min_scale)` (capped at 4). Scale ≥ 1 is unbounded;
scale below 1/4 is clamped and surfaced in the UI — documented, not hidden.

Speed changes are compiled to `setpts` + an `atempo`/`rubberband` chain, so audio
stays pitch-corrected. Speed curves become piecewise-constant speed segments.

### Audio strategy
All audio clips → one `amix` graph (`adelay` for placement, `afade`, `volume`,
EQ/compressor per clip) exported as a WAV stem; the video graph consumes the stem
as one extra input. This keeps the graph small and guarantees A/V sync.

### Preview strategy
Segment cache: the timeline is rendered in ~4 s chunks at preview resolution into
`~/.cache/vn-editor/<project-hash>/`, rendered by a background thread with the
playhead's chunk first. Playback streams video and audio out of the cache with one
ffmpeg process (two pipes) so playback is genuinely synchronised. The UI shows a
"buffering" state instead of pretending to play.

## 3. Build order
1. core: ffmpeg/probe/model/project/undo
2. expr + ass + effects + transitions + speedcurve
3. render (export first, then preview) — validated by rendering real files
4. audio + sfx + library + beat
5. UI shell + theme + icons
6. timeline widget (the biggest UI piece)
7. preview monitor, media bin, inspector, tool panels
8. captions, presets, templates, export dialog, shortcuts
9. headless test suite + real end-to-end export verification
10. docs

## 4. Legal / originality
* Clean-room implementation; no VN code, art or audio is used or redistributed.
* Fonts: 21 SIL Open Font License families, licence text included.
* Sound effects are **synthesised procedurally at runtime** from noise/oscillator
  models, so no third-party sample licensing applies.
* Music: generated chord/beat loops, original, no royalties.
* Visual design imitates VN's *layout conventions* (dark workspace, preview top,
  timeline bottom, tool bar between) — layout ideas, not copied assets.

## 5. Explicit non-goals / known limits
* No GPU or cloud dependency; CPU-only (libx264). Older machines use the preview
  scale + `ultrafast`/`superfast` presets.
* Auto captions use a pluggable ASR backend (Vosk if installed); without it the app
  imports SRT/VTT, does speech-region detection for timing, and provides a manual
  caption editor. The UI states which mode is active.
* Effects that need heavy per-pixel maths beyond FFmpeg's filters (true optical
  flow retiming) fall back to `minterpolate` where available.
