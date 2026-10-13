# Contributing to OpenCut Studio

Thanks for helping build a simple, professional open-source editor.
This document gets a development checkout running and describes how we
work. (The project is young: expect these rules to evolve with it.)

## Development setup (Ubuntu)

```bash
sudo apt install -y ffmpeg python3 python3-venv git
git clone <this-repository>
cd VN_VIDEO_EDITOR_LINUX/opencut-studio
bash scripts/dev_setup.sh        # venv + dev dependencies
.venv/bin/python main.py --self-test
.venv/bin/python -m pytest
```

## Ground rules

1. **Working app first.** Every change must leave the editor runnable
   and the test suite green. Small, testable increments beat big-bang
   branches.
2. **No fake features.** A button either does what it says or does not
   exist. Unsupported media/effects are reported, never silently
   skipped.
3. **Preview must not lie.** Anything the UI shows must match what
   export renders; add or extend a render-parity test when you add a
   visual feature.
4. **Respect the layers.** UI → `opencut.engine` (facade) → media
   engine. Timeline maths belongs in `opencut/timeline/ops.py`
   (Qt-free, unit-tested). Project-format changes need a
   `format_version` bump and a migration.
5. **Tests.** New behaviour ships with tests; bug fixes ship with a
   regression test. Generate media fixtures with FFmpeg `lavfi`
   (see `tests/conftest.py`) — do not commit copyrighted samples.
6. **Licences.** New dependencies, fonts, models or binaries must be
   recorded in `docs/DEPENDENCIES.md` before merge. GPL-3.0-compatible
   only.
7. **Style.** Python with `from __future__ import annotations`, type
   hints on public functions, meaningful names, docstrings that say
   *why* when the reason is not obvious.

## Pull requests

- One logical change per PR; update `CHANGELOG.md` and relevant docs.
- State how you tested (commands + results). "Tests pass" means you
  ran them.
- Screenshots for UI changes are appreciated.

## Reporting bugs

Include your Ubuntu version, how FFmpeg is installed (`ffmpeg -version`),
steps to reproduce, and — for render problems — the smallest project and
source file that demonstrate it (synthetic files preferred).
