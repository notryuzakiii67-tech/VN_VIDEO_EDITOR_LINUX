#!/usr/bin/env bash
# OpenCut Studio — create a development virtualenv and install deps.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "note: FFmpeg not found on PATH."
  echo "      Install it with:  sudo apt install ffmpeg"
  echo "      (without it, preview and export are disabled)"
fi

# Create or reuse virtual environment
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt

echo
echo "Setup complete.  Run:"
echo "  .venv/bin/python main.py"
echo "  .venv/bin/python -m pytest"