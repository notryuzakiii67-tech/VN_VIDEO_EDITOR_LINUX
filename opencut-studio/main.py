#!/usr/bin/env python3
"""OpenCut Studio — launch the desktop editor.

Run from the repository root of this project:

    python main.py                # new empty project
    python main.py my_edit.opencut

On Ubuntu you can also install the project (`pip install .`) and use the
`opencut-studio` command.  See README.md for full install instructions.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from opencut.app.main import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
