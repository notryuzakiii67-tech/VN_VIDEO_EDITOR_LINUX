"""Application entry point.

``python main.py [--self-test] [project.opencut]``
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="opencut-studio",
        description="OpenCut Studio — a simple, professional open-source "
                    "desktop video editor for Ubuntu Linux.")
    parser.add_argument("project", nargs="?", default="",
                        help="an .opencut project file to open")
    parser.add_argument("--self-test", action="store_true",
                        help="run a headless smoke test and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        return _self_test()

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .. import APP_NAME, __version__, engine
    from ..ui.main_window import MainWindow
    from ..ui.theme import STYLESHEET

    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("OpenCutStudio")
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.show()
    if args.project:
        window.open_project(args.project)
    elif not engine.ffmpeg_available():
        QMessageBox.warning(
            window, "FFmpeg not found",
            "OpenCut Studio edits projects without FFmpeg, but it cannot "
            "preview frames or export video until FFmpeg is installed.\n\n"
            "Install it with:\n    sudo apt install ffmpeg\n\n"
            "Alternatively install the Python fallback:\n"
            "    pip install imageio-ffmpeg")
    return app.exec()


def _self_test() -> int:
    """Headless smoke test: build a project, add a caption, render a frame."""
    import tempfile

    from PySide6.QtWidgets import QApplication

    from .. import engine
    from ..project import store
    from ..timeline import ops
    from ..ui.main_window import MainWindow
    from ..ui.theme import STYLESHEET, apply_dark_palette

    app = QApplication([sys.argv[0]])
    apply_dark_palette(app)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    project = store.create_project("Self test", "9:16")
    window.load_project(project, None)
    clip = ops.add_text_clip(project, at=0.0, text="POV: SELF TEST")
    window.timeline.set_selection([clip.id])
    ok = True
    out = os.path.join(tempfile.mkdtemp(), "frame.png")
    if engine.ffmpeg_available():
        ok = engine.render_frame(project, 0.5, 270, 480, out) \
            and os.path.getsize(out) > 0
    print(f"OPENCUT SELF-TEST {'OK' if ok else 'FAILED'} "
          f"(ffmpeg={'yes' if engine.ffmpeg_available() else 'no'})")
    window.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
