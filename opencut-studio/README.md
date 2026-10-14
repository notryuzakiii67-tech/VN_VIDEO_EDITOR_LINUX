# OpenCut Studio

**A simple, professional open-source desktop video editor for Ubuntu Linux.**

*Professional power underneath. Simplicity on the surface.*

> The name "OpenCut Studio" is **provisional**. No trademark search has been
> performed and no availability is claimed.

![OpenCut Studio editing a vertical POV video](docs/screenshot-main.png)

OpenCut Studio takes inspiration from the *workflows* of editors like
Premiere Pro, DaVinci Resolve, CapCut and VN — never their code, branding or
assets. The interface is original, and so is every line of this project.

## What works today (Phase 1)

- Native Ubuntu desktop app (Python + PySide6/Qt), dark, uncluttered UI:
  top bar, media library, preview, properties panel, multitrack timeline.
- **Projects**: create (16:9, 9:16, 1:1, 4:5, 4:3, 21:9 or custom size),
  save and reopen in the versioned `.opencut` JSON format, recent-projects
  list, unsaved-changes protection.
- **Media**: import video / audio / images with real metadata inspection
  (streams, resolution, duration, codecs). Unusable files are rejected with
  a specific reason — nothing is ever silently dropped. Thumbnails and audio
  waveforms are generated in the background.
- **Timeline**: video, overlay, text and audio tracks; select, drag to move,
  drag edges to trim, split (double-click, `S`, or the button), duplicate,
  delete, zoom, snapping to the playhead and clip edges, track hide/mute.
- **Editing**: position / scale / rotation / opacity / fit (fit, cover,
  stretch) with pan-focus for reframing; constant playback speed
  (0.1×–8× class range, UI presets 0.25×–4×); text clips with font
  (21 bundled families incl. Bebas Neue), size, colour, outline, shadow,
  alignment and position; per-clip volume, pan, fade in/out and mute.
- **Preview**: frame-accurate scrub stills rendered with the same graph the
  exporter uses, plus real-time playback synchronised to the audio clock.
- **Export**: real MP4 (H.264 + AAC) rendering with presets (Original /
  Balanced / Small / Draft), progress bar, cancellation, and a post-render
  check that re-probes the file. Export errors are reported, never hidden.
- **Undo/redo** for every edit (250-step snapshot history).

### New in Phase 2

- **Edit modes**: ripple, roll, slip and slide edits and ripple delete
  (toolbar or `V`/`B`/`N`/`Y`/`U`), snapping toggle with playhead
  snapping, and lockable tracks (**L**/**H**/**M** header buttons).
- **Keyframes**: position, scale, rotation, opacity and volume can be
  animated with easing curves. The inspector evaluates the value at the
  playhead; diamonds on the timeline mark keyframes (click one to jump
  to it). Preview and export evaluate identically — the acceptance test
  asserts pixel agreement between them.
- **Titles**: six built-in animated title presets (big Bebas Impact and
  friends), plus your own saved presets in the *Add title* menu.
- **Transitions** with adjustable duration, **speed curves** (presets
  or a custom point editor), **clip reverse**, and audio **fade curves**
  on top of volume keyframes.
- **Export presets**: Original / Balanced / Small / Draft / Web 720 /
  Master, all validated after rendering.
- 80 automated tests, including a Phase 2 acceptance test (multitrack
  edit with animated title, transitions, speed curve, reversed PiP and
  audio keyframes → save → reopen → export → verify) and regression
  tests for four engine bugs that test found. See
  [docs/PHASE2_REPORT.md](docs/PHASE2_REPORT.md).

The five-phase roadmap (performance & proxies → colour/audio/compositing
→ AI & plugins) lives in [docs/ROADMAP.md](docs/ROADMAP.md). What is
*not* implemented yet is listed there honestly rather than implied.

## Install on Ubuntu

```bash
sudo apt update
sudo apt install -y ffmpeg python3 python3-venv python3-pip

git clone <this-repository>
cd VN_VIDEO_EDITOR_LINUX/opencut-studio

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

> **Repository layout note.** OpenCut Studio currently lives inside the
> VN_VIDEO_EDITOR_LINUX repository and uses that repository's media engine
> (`vn_editor.core`, GPL-3.0) through a thin adapter (`opencut/engine.py`).
> Run it from this checkout as shown above; if the project is later split
> into its own repository, the engine package is vendored alongside.
> See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

No FFmpeg? Install it with apt (above) — without it the editor still opens
and edits projects, but cannot preview frames or export. Alternatively
`pip install imageio-ffmpeg` provides a bundled static FFmpeg the engine
uses as a fallback.

## The 60-second POV workflow (the one this editor is tuned for)

1. **File → New project**, choose **9:16 Vertical**.
2. **Import** your landscape gameplay and your meme sounds.
3. Double-click the gameplay to add it. In **Properties**: Fit = *cover*,
   Scale ≈ 135%, adjust Pan focus to frame the action.
4. Set **Speed** to 1.5× (or your taste).
5. **Add text**, write `POV: …`, font **Bebas Neue**, drag it where it
   belongs on the timeline; size/position from Properties.
6. Double-click each sound effect and drag it to the exact frame; set
   volume/fades in Properties.
7. Press play, scrub to check, **Ctrl+S** to save.
8. **Export MP4** → *Balanced* → done. The file is verified after rendering.

## Testing

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest        # 31 tests, incl. the Phase 1 acceptance
.venv/bin/python main.py --self-test
```

Tests generate their own synthetic media with FFmpeg (no downloaded
fixtures) and include an end-to-end acceptance test that builds the POV
edit above, saves/reopens it, exports it, and verifies the caption pixels
and the sound effect's timing *in the decoded output*.

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE). The licence choice (and why not
MIT) and the licence of every dependency, font and bundled binary are
recorded in [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Project documents

- [Architecture](docs/ARCHITECTURE.md) · [Dependencies & licences](docs/DEPENDENCIES.md)
- [Roadmap](docs/ROADMAP.md) · [Phase 1 report](docs/PHASE1_REPORT.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)
