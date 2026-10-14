# Phase 5 report — Plugin architecture, AI hooks, open-source maturity

**Version:** 0.5.0  
**Date:** 2026-10-14  
**Status:** ✅ COMPLETE

---

## Features delivered

### 1. Plugin architecture (`opencut/plugins.py`)

A complete plugin system with discovery, loading, error handling, and
five typed plugin interfaces:

**Discovery sources** (checked in order):
1. `plugins/` directory next to the application (bundled plugins).
2. `~/.local/share/OpenCutStudio/plugins/` (user-installed).
3. Extra directories (for testing).

**Plugin types:**

| Type | Base class | Provides |
|------|-----------|----------|
| Effect | `EffectPlugin` | New effect kind with params and FFmpeg filter builder |
| Transition | `TransitionPlugin` | New transition kind |
| Title | `TitlePlugin` | New title preset |
| Importer | `ImporterPlugin` | New file format support |
| Exporter | `ExporterPlugin` | New export preset or format |

**Plugin lifecycle:**
1. Module discovered in a plugin directory.
2. `importlib.util` loads the module.
3. `register()` is called; must return one or more `Plugin` instances.
4. Plugin metadata validated (name required).
5. Requirements checked (`meta.requires` list).
6. `on_load()` called.
7. Plugin registered in the global `PluginRegistry`.

**Failure handling:** every failure mode is caught and logged, never
crashes the app:
- Import errors (syntax errors, missing dependencies).
- `register()` raises.
- `register()` returns wrong type.
- `on_load()` raises.
- Missing `register()` function.
- Missing `meta.name`.
- Unmet requirements.

### 2. Bundled example plugins (`plugins/`)

| Plugin | Type | What it does |
|--------|------|-------------|
| `example_warm.py` | EffectPlugin | Adds a "Warm (example)" colour temperature effect |
| `example_title.py` | TitlePlugin | Adds a "GG (example plugin)" gaming title preset |
| `bad_register.py` | (test fixture) | register() raises — tests error handling |
| `bad_no_register.py` | (test fixture) | No register() — tests error handling |

### 3. Optional AI architecture (documented, not implemented)

The plugin system includes `AIHookPlugin` as a base class for future
AI features. Design principles documented in SECURITY.md and the plugin
module docstring:

- **Opt-in:** the user must install or enable AI plugins.
- **Local-first:** no network calls without explicit consent.
- **Never required:** the core editor works without any AI features.
- **No media upload:** AI processing happens locally; media is never
  uploaded without explicit user consent.

Specific AI features investigated but not implemented (documented for
future contributors):
- Transcription/subtitles (requires a local whisper-like model).
- Silence detection (can be done with FFmpeg `silencedetect`).
- Scene detection (can be done with FFmpeg `scdet`).
- Smart reframing (requires subject detection; complex).

### 4. Project interchange investigation

Documented findings:

- **Import:** OpenCut Studio imports any format FFmpeg can decode
  (MP4, MOV, MKV, WebM, AVI, MP3, WAV, PNG, JPG, etc.). The
  `ImporterPlugin` interface allows adding support for formats that
  need special handling (e.g., project files from other editors).
- **Export:** currently MP4/H.264+AAC only. The `ExporterPlugin`
  interface allows adding WebM/VP9, MOV/ProRes, GIF, etc.
- **Project interchange:** the `.opencut` format is a versioned JSON
  document. Importing from other editors' project files (Premiere XML,
  DaVinci Resolve DRP, Final Cut XML) would require dedicated
  `ImporterPlugin` implementations — documented as a future opportunity,
  not a current feature.
- **AAF/EDL/OTIO:** these interchange formats are well-documented;
  implementing them as plugins is straightforward but out of scope for
  this phase.

### 5. Repository maturity

| Item | File | Status |
|------|------|--------|
| Contributing guide | `CONTRIBUTING.md` | ✅ Exists (updated with plugin dev guide) |
| Code of conduct | `CODE_OF_CONDUCT.md` | ✅ Exists |
| Security policy | `SECURITY.md` | ✅ Exists (updated with plugin security) |
| CI workflow | `.github/workflows/tests.yml` | ✅ Exists |
| PR template | `.github/PULL_REQUEST_TEMPLATE.md` | **NEW** |
| Bug report template | `.github/ISSUE_TEMPLATE/bug_report.md` | **NEW** |
| Feature request template | `.github/ISSUE_TEMPLATE/feature_request.md` | **NEW** |
| Release checklist | `docs/RELEASE_CHECKLIST.md` | **NEW** |
| Changelog | `CHANGELOG.md` | ✅ Updated each phase |
| Dependencies | `docs/DEPENDENCIES.md` | ✅ Exists |

---

## Files changed/created

| File | Change |
|------|--------|
| `opencut/plugins.py` | **NEW** — full plugin architecture |
| `plugins/example_warm.py` | **NEW** — bundled example effect plugin |
| `plugins/example_title.py` | **NEW** — bundled example title plugin |
| `plugins/bad_register.py` | **NEW** — test fixture (register failure) |
| `plugins/bad_no_register.py` | **NEW** — test fixture (no register) |
| `tests/test_phase5.py` | **NEW** — 31 tests |
| `.github/PULL_REQUEST_TEMPLATE.md` | **NEW** |
| `.github/ISSUE_TEMPLATE/bug_report.md` | **NEW** |
| `.github/ISSUE_TEMPLATE/feature_request.md` | **NEW** |
| `docs/RELEASE_CHECKLIST.md` | **NEW** |
| `docs/PHASE5_REPORT.md` | **NEW** — this file |
| `opencut/__init__.py` | Version 0.5.0 |
| `pyproject.toml` | Version 0.5.0 |
| `CHANGELOG.md` | Phase 5 entry |
| `docs/ROADMAP.md` | Phase 5 ✅ |
| `README.md` | Phase 5 summary |
| `SECURITY.md` | Updated with plugin security guidance |

---

## Test results

| Suite | Result |
|-------|--------|
| OpenCut `pytest tests/` | **177 passed** (146 prior + 31 Phase 5) |
| `main.py --self-test` | **OPENCUT SELF-TEST OK (ffmpeg=yes)** |
| VN `test_render.py` | **38/38 passed** |

---

## Known bugs / limitations

1. **No pip-installable plugins yet:** the entry-point discovery
   (`[tool.opencut.plugins]` in pyproject.toml) is documented but not
   yet implemented. Currently plugins must be placed in the plugins
   directory.

2. **No plugin settings UI:** plugins can use QSettings directly, but
   there's no dedicated plugin settings panel yet.

3. **AI features not implemented:** the `AIHookPlugin` base class and
   architecture are documented, but no actual AI features are shipped.
   This is intentional — AI features must be local-first, opt-in, and
   carefully tested.

4. **No project interchange importers:** the `ImporterPlugin` interface
   exists but no importer for Premiere/DaVinci/Final Cut project files
   is shipped.

5. **VN `gui_smoke.py` crash:** same PySide6 6.12.0 refcount bug —
   not a Phase 5 regression.

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

- All 177 OpenCut tests pass (including 31 Phase 5 tests).
- Self-test OK with FFmpeg available.
- VN engine tests pass 38/38.
- Core workflow (create project → import media → edit → export) works
  with no plugins installed and with broken plugins present.
- Plugin loading and failure modes are all tested (import errors,
  register failures, on_load failures, missing requirements, bad
  return types, missing names, syntax errors).
- Repository maturity items (templates, checklist, updated docs) are
  in place.

---

## All five phases — complete

| Phase | Version | Status |
|-------|---------|--------|
| Phase 1 — MVP | v0.1.0 | ✅ |
| Phase 2 — Advanced editing & motion graphics | v0.2.0 | ✅ |
| Phase 3 — Performance & pro media workflows | v0.3.0 | ✅ |
| Phase 4 — Colour, compositing, effects, audio | v0.4.0 | ✅ |
| Phase 5 — Plugins, AI hooks, open-source maturity | v0.5.0 | ✅ |

The five-phase development plan from the master prompt is complete.
OpenCut Studio is a working, tested, documented open-source video
editor with: proxy media, preview quality, autosave/recovery, render
queue, VFR detection, colour grading, compositing (blend modes, masks,
chroma key), 38 effects, audio processing, a plugin architecture, and
a mature repository structure.