#!/usr/bin/env bash
# OpenCut Studio — run the full test suite (headless-friendly).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

"$PY" -m pytest "$@"
"$PY" main.py --self-test
