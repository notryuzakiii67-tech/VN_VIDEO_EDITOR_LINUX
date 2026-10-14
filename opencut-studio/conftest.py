"""Pytest bootstrap: make the `opencut` package importable from a checkout.

(The media engine, `vn_editor`, is located by `opencut.engine` itself.)
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Headless machines (CI, containers) have no X/Wayland display.
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
