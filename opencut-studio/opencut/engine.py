"""Adapter between OpenCut Studio and its media engine.

OpenCut Studio's own code never imports the engine's internals directly;
everything goes through this module.  That keeps the boundary explicit:
the engine (package ``vn_editor.core``, GPL-3.0, developed alongside this
project) owns the project data model, media probing, the FFmpeg filter
graph compiler, preview-frame rendering and MP4 export.  OpenCut Studio
owns the user interface, its ``.opencut`` project file format, media
import policy and timeline editing operations.

If the engine package cannot be found, importing this module raises an
``ImportError`` that explains how to run the application.
"""
from __future__ import annotations

import subprocess  # noqa: F401  (re-exported: preview spawns engine processes)
import sys
from pathlib import Path


def _ensure_engine_on_path() -> None:
    try:
        import vn_editor.core  # noqa: F401
        return
    except ImportError:
        pass
    # opencut-studio/opencut/engine.py -> repository root is two levels up.
    root = Path(__file__).resolve().parents[2]
    if (root / "vn_editor" / "core").is_dir():
        sys.path.insert(0, str(root))
        try:
            import vn_editor.core  # noqa: F401
            return
        except ImportError:
            pass
    raise ImportError(
        "OpenCut Studio could not find its media engine (the 'vn_editor' "
        "package). Run the application from the source repository with "
        "`python main.py`, or install the engine next to this project. "
        "See docs/ARCHITECTURE.md."
    )


_ensure_engine_on_path()

from vn_editor.core import ffmpeg as _ffmpeg          # noqa: E402
from vn_editor.core import probe as _probe            # noqa: E402
from vn_editor.core import project as _engine_files   # noqa: E402
from vn_editor.core import render as _render          # noqa: E402
from vn_editor.core import undo as _undo              # noqa: E402
from vn_editor.core.model import (                    # noqa: E402
    AudioProps,
    Clip,
    Keyframe,
    MediaAsset,
    Project,
    ProjectSettings,
    SpeedCurve,
    TextProps,
    Track,
    Transform,
)
from vn_editor.core.probe import MediaInfo            # noqa: E402

__all__ = [
    "AudioProps", "Clip", "History", "Keyframe", "MediaAsset", "MediaInfo",
    "Project", "ProjectSettings", "RenderOptions", "RenderResult",
    "TextProps", "Track", "Transform",
    "build_audio_pipe_command", "build_pipe_command", "cache_dir",
    "ffmpeg_available", "ffmpeg_description", "make_thumbnail",
    "new_project", "options_from_settings", "popen_ffmpeg", "probe_media",
    "render_frame", "render_project", "terminate_process", "waveform_peaks",
]

History = _undo.History
SnapshotCommand = _undo.SnapshotCommand
RenderOptions = _render.RenderOptions
RenderResult = _render.RenderResult

probe_media = _probe.probe
make_thumbnail = _probe.make_thumbnail
waveform_peaks = _probe.waveform_peaks

render_project = _render.render
render_frame = _render.render_frame
build_pipe_command = _render.build_pipe_command
build_audio_pipe_command = _render.build_audio_pipe_command
options_from_settings = _render.options_from_settings

popen_ffmpeg = _ffmpeg.popen_ffmpeg
terminate_process = _ffmpeg.terminate
cache_dir = _engine_files.cache_dir


def ffmpeg_available() -> bool:
    """True when an FFmpeg binary (system or bundled fallback) exists."""
    return _ffmpeg.TOOLCHAIN.ffmpeg is not None


def ffmpeg_executable() -> str:
    """Path of the active FFmpeg binary ('' when none is available)."""
    return str(_ffmpeg.TOOLCHAIN.ffmpeg or "")


def relink_media(project: Project, asset_id: str, new_path: str) -> int:
    """Point an asset — and every clip using it — at a moved file.

    Returns how many clips were updated.
    """
    return _engine_files.relink(project, asset_id, new_path)


def ffmpeg_description() -> str:
    """Human-readable description of the active FFmpeg, for About/errors."""
    tc = _ffmpeg.TOOLCHAIN
    if tc.ffmpeg is None:
        return "FFmpeg not found"
    tc.probe()
    return f"FFmpeg {tc.version} ({tc.ffmpeg})"


def new_project(name: str = "Untitled project", width: int = 1920,
                height: int = 1080, fps: float = 30.0) -> Project:
    """Create a project with the standard four-track layout."""
    project = Project.create_default(name=name, width=int(width),
                                     height=int(height), fps=float(fps))
    return project
