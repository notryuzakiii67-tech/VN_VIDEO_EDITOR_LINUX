"""Export: presets, rendering on demand, and output validation.

Export runs the engine's real render pipeline (the same filter graph
family as the preview, compiled for the full timeline).  Nothing is
faked: a successful :class:`ExportOutcome` means FFmpeg encoded the
file, and :func:`validate_output` re-probes it.

Failures are reported, never swallowed: if FFmpeg is missing, the
timeline is empty, or the encoder errors, the outcome says so in
user-actionable language.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from ..engine import (Project, RenderOptions, ffmpeg_available,
                      options_from_settings, probe_media, render_project)

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


@dataclass(frozen=True)
class ExportPreset:
    key: str
    label: str
    description: str
    max_height: int          # 0 = keep the project's resolution
    crf: int                 # x264 quality (lower = better/larger)
    ffmpeg_preset: str
    audio_bitrate: str


PRESETS: Dict[str, ExportPreset] = {
    "original": ExportPreset(
        "original", "Original quality",
        "Project resolution, high quality. Best for archiving.",
        0, 18, "medium", "192k"),
    "balanced": ExportPreset(
        "balanced", "Balanced (recommended)",
        "Up to 1080p, visually transparent for most uploads.",
        1080, 20, "medium", "160k"),
    "small": ExportPreset(
        "small", "Small file",
        "Up to 720p. Good for quick sharing.",
        720, 23, "fast", "128k"),
    "draft": ExportPreset(
        "draft", "Draft",
        "Up to 540p, encodes fast. For reviewing an edit.",
        540, 26, "veryfast", "96k"),
}
DEFAULT_PRESET = "balanced"


@dataclass
class ExportOutcome:
    ok: bool
    output: str
    message: str
    preset: str = ""
    seconds: float = 0.0     # timeline seconds rendered
    frames: int = 0
    elapsed: float = 0.0
    width: int = 0
    height: int = 0


def get_preset(key: str) -> ExportPreset:
    try:
        return PRESETS[key]
    except KeyError:
        raise ValueError(
            f"Unknown export preset {key!r}; choose one of "
            f"{', '.join(sorted(PRESETS))}") from None


def options_for(project: Project, preset_key: str) -> RenderOptions:
    """Build engine render options for a preset (even dimensions, yuv420p)."""
    preset = get_preset(preset_key)
    opts = options_from_settings(project)
    opts.crf = preset.crf
    opts.preset = preset.ffmpeg_preset
    opts.audio_bitrate = preset.audio_bitrate
    if preset.max_height and project.settings.height > preset.max_height:
        scale = preset.max_height / float(project.settings.height)
        opts.width = max(16, int(round(project.settings.width * scale)) // 2 * 2)
        opts.height = max(16, int(round(project.settings.height * scale)) // 2 * 2)
    return opts


def export_project(project: Project, output: str, preset_key: str = DEFAULT_PRESET,
                   progress: Optional[ProgressFn] = None,
                   cancel: Optional[CancelFn] = None) -> ExportOutcome:
    """Render the whole timeline to ``output`` (MP4/H.264 + AAC)."""
    preset = get_preset(preset_key)
    if not ffmpeg_available():
        return ExportOutcome(
            False, output,
            "FFmpeg was not found, so the video cannot be rendered.\n"
            "Install it with `sudo apt install ffmpeg` and try again.")
    if project.duration <= 0.01:
        return ExportOutcome(
            False, output,
            "The timeline is empty — add at least one clip before exporting.")
    opts = options_for(project, preset_key)
    result = render_project(project, output, opts,
                            progress=progress, cancel=cancel)
    if not result.ok:
        return ExportOutcome(False, output,
                             result.error or "The render failed.",
                             preset=preset.key, elapsed=result.elapsed)
    return ExportOutcome(
        True, result.output or output, "Export complete",
        preset=preset.key, seconds=result.duration, frames=result.frames,
        elapsed=result.elapsed, width=opts.width, height=opts.height)


@dataclass
class OutputInfo:
    ok: bool
    message: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False
    size_bytes: int = 0


def validate_output(path: str) -> OutputInfo:
    """Re-probe an exported file: streams, dimensions, duration, size."""
    if not os.path.exists(path):
        return OutputInfo(False, f"Output file was not created: {path}")
    size = os.path.getsize(path)
    if size <= 0:
        return OutputInfo(False, "Output file is empty", size_bytes=size)
    info = probe_media(path)
    if not info.has_video:
        return OutputInfo(False, "Output file has no readable video stream",
                          duration=info.duration, size_bytes=size)
    if info.duration <= 0:
        return OutputInfo(False, "Output file has no readable duration",
                          width=info.width, height=info.height,
                          has_video=True, has_audio=info.has_audio,
                          size_bytes=size)
    return OutputInfo(
        True, "Valid MP4", duration=info.duration, width=info.width,
        height=info.height, has_video=True, has_audio=info.has_audio,
        size_bytes=size)
