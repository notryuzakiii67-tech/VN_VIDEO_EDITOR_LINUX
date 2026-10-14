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
from vn_editor.core import effects as _fx_mod         # noqa: E402
from vn_editor.core import undo as _undo              # noqa: E402
from vn_editor.core import ass as _ass                  # noqa: E402
from vn_editor.core import audio as _audio              # noqa: E402
from vn_editor.core import speedcurve as _speedcurve    # noqa: E402
from vn_editor.core import transitions as _transitions  # noqa: E402
from vn_editor.core.model import (                    # noqa: E402
    AudioProps,
    Clip,
    Keyframe,
    MediaAsset,
    Project,
    ProjectSettings,
    SpeedCurve,
    TextProps,
    Transition,
    Track,
    Transform,
)
from vn_editor.core.probe import MediaInfo            # noqa: E402

__all__ = [
    "AudioProps", "Clip", "History", "Keyframe", "MediaAsset", "MediaInfo",
    "Project", "ProjectSettings", "RenderOptions", "RenderResult",
    "TextProps", "Track", "Transform",
    "Transition", "ANIM_PROPS", "EASINGS", "FADE_TYPES",
    "apply_text_animation", "clear_text_animation", "eval_keyframes",
    "speed_curve_points", "speed_curve_presets", "text_animation_names",
    "transition_catalogue", "transition_default_duration",
    "transition_label",
    "build_audio_pipe_command", "build_pipe_command", "cache_dir",
    "ffmpeg_available", "ffmpeg_description", "make_thumbnail",
    "new_project", "options_from_settings", "popen_ffmpeg", "probe_media",
    "render_frame", "render_project", "terminate_process", "waveform_peaks",
    # Phase 3:
    "autosave", "recovery_entries", "clear_recovery", "thumb_dir",
    "recovery_dir", "generate_proxy", "detect_vfr",
    "generate_thumbnail_cached",
    # Phase 4:
    "effect_categories", "effect_params", "effect_label", "effect_defaults",
    "BLEND_MODES", "MASK_SHAPES",
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


# --------------------------------------------------------------------------- #
#  Phase 2 catalogues: transitions, text animation, speed curves, fades
# --------------------------------------------------------------------------- #
from vn_editor.core.model import ANIM_PROPS, EASINGS  # noqa: E402
from vn_editor.core.model import eval_keyframes  # noqa: E402

#: Audio fade curve names understood by the engine (AudioProps.fade_type).
FADE_TYPES = list(_audio.FADE_CURVES)


def transition_catalogue() -> dict:
    """Transition specs grouped by category: {category: [spec, ...]}."""
    return _transitions.by_category()


def transition_label(key: str) -> str:
    return _transitions.label(key)


def transition_default_duration(key: str) -> float:
    return _transitions.default_duration(key)


def text_animation_names() -> list:
    """All text-animation preset names ('none' first, specials included)."""
    names = list(_ass.ANIM_PRESETS)
    for special in sorted(_ass.SPECIAL_ANIMS):
        if special not in names:
            names.append(special)
    return names


def apply_text_animation(clip: Clip, name: str, anim_in: float = 0.4) -> None:
    """Apply a text-animation preset: real keyframes on the clip."""
    _ass.apply_text_animation(clip, name, max(0.05, clip.duration), anim_in)


def clear_text_animation(clip: Clip) -> None:
    _ass.clear_text_animation(clip)


def speed_curve_presets() -> list:
    """Labels of the built-in speed-curve presets ('None' first)."""
    return [label for label, _points in _speedcurve.CURVE_PRESETS]


def speed_curve_points(label: str) -> list:
    """Normalised (progress, speed) points for a curve preset label."""
    return [(float(a), float(b))
            for a, b in _speedcurve.curve_preset_points(label)]


# --------------------------------------------------------------------------- #
#  Phase 3: autosave, recovery, relink, proxy, VFR detection
# --------------------------------------------------------------------------- #
import os as _os
from pathlib import Path as _Path

def autosave(project: Project, path: str = None) -> Optional[_Path]:
    """Write a recovery snapshot.  Returns the slot path, or None on failure."""
    return _engine_files.autosave(project, path)

def recovery_entries() -> list:
    """Return metadata for available recovery snapshots."""
    return _engine_files.recovery_entries()

def clear_recovery(slot_path: str) -> None:
    """Dismiss a recovery entry (after the user has dealt with it)."""
    _engine_files.clear_recovery(slot_path)

def thumb_dir() -> _Path:
    """Directory for cached media thumbnails."""
    return _engine_files.thumb_dir()

def recovery_dir() -> _Path:
    """Directory for recovery snapshots."""
    return _engine_files.recovery_dir()

def generate_proxy(source_path: str, out_path: str,
                   proxy_height: int = 540) -> bool:
    """Generate a low-res proxy for a video file.

    Returns True on success.  Audio-only and image files are skipped.
    """
    info = probe_media(source_path)
    if info.kind != "video" or info.height <= 0:
        return False
    target_h = min(proxy_height, info.height)
    if target_h >= info.height:
        return False                     # already small enough
    target_h = max(16, target_h // 2 * 2)
    scale = target_h / float(info.height)
    target_w = max(16, int(info.width * scale) // 2 * 2)
    args = ["-y", "-v", "error",
            "-i", source_path,
            "-vf", f"scale={target_w}:{target_h}:flags=bilinear",
            "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
            "-an", "-pix_fmt", "yuv420p", out_path]
    r = _ffmpeg.ffmpeg(*args, timeout=120)
    return r.ok and _os.path.exists(out_path) and _os.path.getsize(out_path) > 100

def detect_vfr(source_path: str) -> dict:
    """Probe a media file and detect variable frame rate.

    Returns ``{"vfr": bool, "fps": float, "issues": [str, ...]}``.
    """
    info = probe_media(source_path)
    result = {"vfr": False, "fps": info.fps, "issues": []}
    if info.fps and info.fps > 0:
        standard = {23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0}
        if not any(abs(info.fps - s) < 0.05 for s in standard):
            result["vfr"] = True
            result["issues"].append(
                f"Non-standard frame rate: {info.fps:.3f} fps")
    if (info.kind == "video" and info.frame_count > 0
            and info.fps > 0 and info.duration > 0):
        expected = info.duration * info.fps
        if abs(info.frame_count - expected) > expected * 0.05:
            result["vfr"] = True
            result["issues"].append(
                f"Frame count mismatch: {info.frame_count} actual "
                f"vs ~{int(expected)} expected")
    return result

def generate_thumbnail_cached(source_path: str, asset_id: str,
                              width: int = 192, at: float = 1.0) -> str:
    """Generate (or return cached) thumbnail for a media asset.

    Returns the thumbnail path, or '' on failure.
    """
    out_dir = str(thumb_dir() / "opencut-thumbs")
    _os.makedirs(out_dir, exist_ok=True)
    out_path = _os.path.join(out_dir, f"{asset_id}.png")
    if _os.path.exists(out_path):
        return out_path
    info = probe_media(source_path)
    seek_at = min(at, max(0.0, info.duration * 0.25)) if info.duration > 0 else 1.0
    if make_thumbnail(source_path, out_path, at=seek_at, width=width):
        return out_path
    return ""


# --------------------------------------------------------------------------- #
#  Phase 4: effects, colour grading, compositing, audio processing
# --------------------------------------------------------------------------- #

def effect_categories() -> dict:
    """Effect categories and their effect kinds.

    Returns ``{category: [kind, ...], ...}``.
    """
    return _fx_mod.categories()

def effect_params(kind: str) -> list:
    """Parameter specs for an effect kind.

    Returns a list of ``Param`` objects (key, label, lo, hi, step, default).
    """
    return _fx_mod.effect_params(kind)

def effect_label(kind: str) -> str:
    """Human-readable label for an effect kind."""
    return _fx_mod.effect_label(kind)

def effect_defaults(kind: str) -> dict:
    """Default parameter values for an effect kind."""
    return _fx_mod.default_params(kind)

BLEND_MODES: list = _render.BLEND_MODES
MASK_SHAPES: list = _render.MASK_SHAPES
