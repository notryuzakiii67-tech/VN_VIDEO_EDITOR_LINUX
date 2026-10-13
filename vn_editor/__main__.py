"""VN Studio — application entry point.

Run it with::

    python -m vn_editor              # or: ./run.sh
    python -m vn_editor project.vnp  # open a project

The editor is a normal desktop application: it needs no network access, no
account, and no GPU.  Everything it does — importing, previewing, effects and
exporting — happens locally through FFmpeg.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

__version__ = "1.0"
APP_NAME = "VN Studio"


def _preflight() -> list[str]:
    """Environment problems worth telling the user about before the UI opens."""
    problems: list[str] = []
    try:
        from .core import ffmpeg as ff
        ff.TOOLCHAIN.probe()
        if not ff.TOOLCHAIN.ok:
            problems.append(
                "FFmpeg was not found. Install it with\n"
                "    sudo apt install ffmpeg\n"
                "or run ./install.sh, then start VN Studio again.")
        else:
            missing = ff.TOOLCHAIN.missing_essentials()
            if missing:
                problems.append(
                    "This FFmpeg build is missing: " + ", ".join(missing) +
                    "\nSome features will be unavailable. The version from\n"
                    "your distribution's ffmpeg package has everything.")
    except Exception as exc:                          # pragma: no cover
        problems.append(f"Could not start FFmpeg: {exc}")
    return problems


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vn_editor", description=f"{APP_NAME} — video editor")
    p.add_argument("project", nargs="?", default=None,
                   help="project file (.vnp) to open")
    p.add_argument("--new", action="store_true",
                   help="start with an empty project, ignoring recovery files")
    p.add_argument("--version", action="store_true", help="print the version")
    p.add_argument("--ffmpeg", default=None, metavar="PATH",
                   help="use a specific ffmpeg binary")
    p.add_argument("--self-test", action="store_true",
                   help="check the toolchain, fonts and audio device, then exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        from .core import ffmpeg as ff
        print(f"{APP_NAME} {__version__} · {ff.TOOLCHAIN.summary()}")
        return 0

    if args.ffmpeg:
        os.environ["VN_FFMPEG"] = str(Path(args.ffmpeg).expanduser())

    if args.self_test:
        return run_self_test()

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:                        # pragma: no cover
        print("PySide6 is required. Install it with:\n"
              "    pip install -r requirements.txt\n"
              f"({exc})", file=sys.stderr)
        return 2

    # Fractional scaling looks best when Qt is told about it explicitly.
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("vn-studio")
    app.setDesktopFileName("vn-studio")

    from .ui import theme
    theme.register_bundled_fonts()
    app.setStyleSheet(theme.stylesheet())

    from .core import ffmpeg as ff
    from .core import project as pio
    from .core import sfx as sfx_mod
    from .ui.dialogs import RecoveryDialog
    from .ui.main_window import MainWindow

    # Built-in sound effects are generated on first run (a few seconds).
    try:
        sfx_mod.scan_user_library()
    except Exception:
        pass

    project = None
    path: str | None = None
    if args.project:
        path = str(Path(args.project).expanduser())
        try:
            project = pio.load(path)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Could not open project",
                                 f"{path}\n\n{exc}")
            return 1
    elif not args.new:
        entries = pio.recovery_entries()
        if entries:
            dlg = RecoveryDialog(None, entries)
            if dlg.exec() and dlg.choice:
                choice = dlg.choice
                slot = choice.get("slot_path")
                original = choice.get("project_path") or ""
                try:
                    project = pio.load(str(slot))
                    path = original or None
                    project.name = (project.name or "Recovered") + " (recovered)"
                    pio.clear_recovery(str(slot))
                except Exception:
                    project = None

    window = MainWindow(project)
    if path:
        window.current_path = path
        window.history.mark_saved()
    window.show()

    for message in _preflight():
        window.status(" ".join(message.split())[:160], 15000)

    def _quit() -> int:
        try:
            if window.history.is_dirty:
                pio.autosave(window.project, window.current_path, force=True)
            window.preview.shutdown()
        except Exception:
            pass
        return 0

    app.aboutToQuit.connect(_quit)
    return app.exec()


def run_self_test() -> int:
    """`--self-test`: verify the pieces the editor needs, without a GUI."""
    ok = True
    print(f"{APP_NAME} {__version__}")
    print("=" * 58)

    from .core import ffmpeg as ff
    ff.TOOLCHAIN.probe()
    print(f"FFmpeg        : {ff.TOOLCHAIN.ffmpeg}")
    print(f"                {ff.TOOLCHAIN.summary()}")
    missing = ff.TOOLCHAIN.missing_essentials()
    if not ff.TOOLCHAIN.ok:
        print("                MISSING — install ffmpeg to edit video")
        ok = False
    elif missing:
        print(f"                missing filters: {', '.join(missing)}")
        ok = False
    else:
        print("                all required filters and encoders present")

    from .core.ass import FONT_FILES, available_fonts, fontconfig_file
    print(f"Bundled fonts : {len(FONT_FILES)} (SIL Open Font License)")
    print(f"                {', '.join(sorted(FONT_FILES)[:6])}, …")
    fc = fontconfig_file()
    print(f"fontconfig    : {fc} ({'found' if Path(fc).exists() else 'missing'})")
    if not Path(fc).exists():
        ok = False

    try:
        from .core import sfx as sfx_mod
        lib = sfx_mod.ensure_library()
        print(f"Sound effects : {len(lib)} generated in {sfx_mod.library_dir()}")
        print(f"Text to speech: {sfx_mod.available_tts() or 'not installed (optional)'}")
    except Exception as exc:
        print(f"Sound effects : FAILED ({exc})")
        ok = False

    try:
        from .core.project import data_dir, cache_dir, config_dir
        print(f"Data folder   : {data_dir()}")
        print(f"Cache folder  : {cache_dir()}")
        print(f"Config folder : {config_dir()}")
    except Exception as exc:
        print(f"Folders       : FAILED ({exc})")
        ok = False

    try:
        import PySide6
        from PySide6 import QtCore, QtWidgets, QtGui
        print(f"Qt            : {QtCore.qVersion()} (PySide6)")
        from .ui import theme
        from .ui import theme as _t
        print(f"Icons         : {len(_t.ICON_DRAWERS)} built in")
        try:
            from PySide6.QtMultimedia import QMediaDevices
            outputs = QMediaDevices.audioOutputs()
            print(f"Audio output  : {'none found' if not outputs else outputs[0].description()}")
        except Exception as exc:
            print(f"Audio output  : unavailable ({exc})")
    except Exception as exc:
        print(f"Qt            : FAILED ({exc})")
        ok = False

    print("=" * 58)
    print("Everything needed for editing and export is ready."
          if ok else "Some checks failed — see above.")
    return 0 if ok else 1


def cli() -> int:
    """Console-script wrapper that reports crashes usefully."""
    try:
        return main()
    except KeyboardInterrupt:
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(cli())
