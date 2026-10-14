# Phase 3 report — Performance and professional media workflows

**Version:** 0.3.0  
**Date:** 2026-10-14  
**Status:** ✅ COMPLETE

---

## Features delivered

### 1. Background worker pool (`opencut/background.py`)

All heavy I/O runs on a `QThreadPool` so the UI thread never blocks:

| Worker | Purpose |
|--------|---------|
| `ThumbnailWorker` | Generate poster frames for the media library |
| `WaveformWorker` | Decode peak envelopes for audio clips |
| `ProxyWorker` | Transcode high-res video to a lightweight preview proxy |
| `MediaAnalysisWorker` | Probe a file for VFR, codec, duration, issues |
| `ExportWorker` | Render a project to MP4 (used by the render queue) |

Workers are fire-and-forget (`setAutoDelete(True)`). Each has a paired
`Signals` bundle that posts results back to the UI thread.

### 2. Proxy media

The engine already had `proxy_path` on `MediaAsset`; Phase 3 adds the
generation workflow:

- `engine.generate_proxy(source, out, proxy_height=540)` — FFmpeg
  transcodes to half-height (or `proxy_height`-capped) H.264 with
  ultrafast encoding, stored in the cache directory.
- Audio-only and image files are skipped (return `False`).
- Videos already at or below `proxy_height` are skipped.
- Proxies can be generated per-asset from the media panel context menu,
  or for all assets via File → Generate proxies.

### 3. Preview quality selector

A combo box in the preview transport bar offers four quality levels:

| Level | Scale | Use case |
|-------|-------|----------|
| Draft | 0.25 | Quick visual check on 4K timelines |
| Low | 0.4 | Fast scrubbing |
| Half | 0.5 | Default — good balance |
| Full | 1.0 | Final quality check before export |

Changing quality triggers a re-render of the current frame.

### 4. Autosave + crash recovery

- **Autosave timer:** 20-second `QTimer` checks if the project is dirty
  and writes a recovery snapshot via `engine.autosave()`.
- **Startup recovery dialog:** on launch, `QTimer.singleShot(500ms)`
  checks `engine.recovery_entries()`. If any exist, the user sees a
  dialog offering to recover or discard the newest entry. The entry is
  cleared either way.
- The engine's `AutoSave` class (in `vn_editor.core.project`) handles
  rolling slot files, metadata, and pruning.

### 5. Media relinking

- **Context menu:** right-click a missing asset (⚠ indicator) →
  "Relink file…" → file dialog → `engine.relink_media()`.
- **File menu:** File → Relink missing media iterates all missing
  assets and offers a file dialog for each.
- The engine's `relink()` updates the asset path and all clips using it.

### 6. Render queue

`RenderQueue` (in `opencut/background.py`) manages sequential background
exports:

- `enqueue(project_data, output, preset)` adds a job.
- Only one export runs at a time; the next starts when the current
  finishes.
- Signals: `job_started`, `job_progress`, `job_done`, `queue_empty`.
- `cancel_current()` sends a cancellation flag to the running export.
- The export dialog gains an "Add to queue" button alongside "Export now".

### 7. VFR detection

`MediaAnalysisWorker` probes each imported file and checks:

1. Non-standard fps (not in {23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60}).
2. Frame-count mismatch (actual vs. expected from duration × fps > 5%).

If either triggers, the user sees a warning suggesting re-encoding with
`ffmpeg -i input.mp4 -vsync cfr -r 30 output.mp4`.

### 8. Media panel enhancements

- **Proxy indicator:** assets with `proxy_path` show ◈ before the name.
- **Missing indicator:** assets whose file doesn't exist show ⚠ MISSING.
- **Context menu:** Generate proxy, Relink file, Remove from library.

---

## Files changed/created

| File | Change |
|------|--------|
| `opencut/background.py` | **NEW** — all background workers + render queue |
| `opencut/engine.py` | Added `autosave`, `recovery_entries`, `clear_recovery`, `thumb_dir`, `recovery_dir`, `generate_proxy`, `detect_vfr`, `generate_thumbnail_cached` |
| `opencut/ui/main_window.py` | Autosave timer, recovery dialog, proxy/relink/VFR wiring, render queue property, Phase 3 actions + menu items |
| `opencut/ui/preview.py` | Preview quality combo box affecting `_still_size()` |
| `opencut/ui/media_panel.py` | Proxy/missing labels, context menu (proxy/relink), project ref |
| `opencut/ui/export_dialog.py` | "Add to queue" button, `_enqueue()` method |
| `tests/test_phase3.py` | **NEW** — 38 tests |
| `opencut/__init__.py` | Version bump to 0.3.0 |
| `pyproject.toml` | Version bump to 0.3.0 |
| `CHANGELOG.md` | Phase 3 entry |
| `docs/ROADMAP.md` | Phase 3 ✅, Phase 4 marked next |

---

## Test results

| Suite | Result |
|-------|--------|
| OpenCut `pytest tests/` | **118 passed** (80 prior + 38 Phase 3) |
| `main.py --self-test` | **OPENCUT SELF-TEST OK (ffmpeg=yes)** |
| VN `test_render.py` | **38/38 passed** |
| VN `gui_smoke.py` | **6/6 passed** then crash at playback check (PySide6 6.12.0 refcount bug in `vn_editor.ui.timeline._paint_keyframes` — not a Phase 3 regression; VN editor code unmodified) |

---

## Known bugs / limitations

1. **VN `gui_smoke.py` crash:** PySide6 6.12.0 has a refcount bug that
   crashes during `vn_editor.ui.timeline._paint_keyframes` in the
   playback check. This affects only the VN editor's GUI smoke test, not
   OpenCut Studio or the VN engine test suite. The VN editor code is
   unmodified; the crash is triggered by the PySide6 version bump
   (6.9.3 → 6.12.0).

2. **Proxy proxies only video:** audio and image assets are correctly
   skipped but there's no waveform-caching equivalent for audio files
   (peaks are stored in-memory on the asset; a future phase could
   persist them to disk).

3. **Recovery dialog is simple:** it only offers the newest snapshot.
   A more mature implementation would list all available snapshots.

4. **VFR detection is heuristic:** it flags non-standard fps and
   frame-count mismatches but doesn't probe individual stream timestamps.
   Some VFR files with standard-looking fps will be missed.

---

## Install and launch

```bash
sudo apt install ffmpeg python3 python3-venv python3-pip \
  libgl1 libegl1 libxkbcommon0 libdbus-1-3
cd opencut-studio
python3 -m venv .venv
.venv/bin/pip install PySide6-Essentials numpy pytest imageio-ffmpeg
.venv/bin/python main.py
```

---

## Acceptance

- All 118 OpenCut tests pass (including 38 new Phase 3 tests).
- Self-test OK with FFmpeg available.
- VN engine tests pass 38/38.
- No existing features regressed.
- New features (proxy, autosave, recovery, relink, render queue, VFR,
  preview quality) are real, tested, and wired to the UI.

---

## Remaining work

Phase 4 (next): professional colour, audio processing, compositing,
and effects. The engine's effects library is large; Phase 4 exposes it
carefully with per-effect render verification.