"""The ``.opencut`` project file format: create, save, reopen.

A project file is a single JSON document:

.. code-block:: json

    {
      "format": "opencut-studio/project",
      "format_version": 1,
      "app": {"name": "OpenCut Studio", "version": "0.1.0"},
      "saved": "2026-10-13T12:00:00Z",
      "project": { "...": "engine project model, see opencut.engine" }
    }

Design rules (see docs/ARCHITECTURE.md):

* The envelope is ours; the ``project`` payload is the engine model's
  own serialisation, so every editing feature the engine supports can be
  stored without a second data model.
* Loading validates the envelope and migrates older format versions
  explicitly.  A file from a *newer* format version is refused with an
  actionable message — never silently misread.
* Saving is atomic (write to a temporary file in the same directory,
  then replace), so a crash cannot leave a half-written project.
* Source media is referenced by path and never embedded or modified.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import __version__
from ..engine import Project, new_project

FORMAT_ID = "opencut-studio/project"
FORMAT_VERSION = 1
EXTENSION = ".opencut"

# Aspect/format presets offered when creating a project:
#   key -> (label, width, height)
PROJECT_FORMATS: Dict[str, Tuple[str, int, int]] = {
    "16:9": ("16:9  Landscape HD", 1920, 1080),
    "9:16": ("9:16  Vertical (Shorts/Reels/TikTok)", 1080, 1920),
    "1:1": ("1:1  Square", 1080, 1080),
    "4:5": ("4:5  Portrait", 1080, 1350),
    "4:3": ("4:3  Classic", 1440, 1080),
    "21:9": ("21:9  Ultrawide", 2560, 1080),
    "4K 16:9": ("4K 16:9  UHD", 3840, 2160),
    "4K 9:16": ("4K 9:16  Vertical UHD", 2160, 3840),
    "2.39:1": ("2.39:1  Cinematic scope", 1920, 804),
    "1.85:1": ("1.85:1  Cinematic flat", 1920, 1038),
}


class ProjectFormatError(Exception):
    """Raised when a file cannot be read as an OpenCut Studio project."""


def create_project(name: str = "Untitled project", format_key: str = "16:9",
                   width: int = 0, height: int = 0,
                   fps: float = 30.0) -> Project:
    """Create a new project from a format preset or a custom size."""
    label = "Custom"
    if width <= 0 or height <= 0:
        if format_key not in PROJECT_FORMATS:
            raise ValueError(f"Unknown project format: {format_key!r}")
        label, width, height = PROJECT_FORMATS[format_key]
    project = new_project(name=name, width=width, height=height, fps=fps)
    project.settings.ratio_name = label
    return project


def ensure_extension(path: str) -> str:
    path = str(path)
    if not path.lower().endswith(EXTENSION):
        path += EXTENSION
    return path


def save_project(project: Project, path: str) -> str:
    """Write ``project`` to ``path`` atomically. Returns the path used."""
    path = ensure_extension(path)
    payload = {
        "format": FORMAT_ID,
        "format_version": FORMAT_VERSION,
        "app": {"name": "OpenCut Studio", "version": __version__},
        "saved": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": project.to_dict(),
    }
    text = json.dumps(payload, indent=1, ensure_ascii=False)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise ProjectFormatError(f"Could not save project to {path}: {exc}") from exc
    return str(target)


def load_project(path: str) -> Project:
    """Read a project file, validating and migrating its envelope."""
    path = str(path)
    if not os.path.exists(path):
        raise ProjectFormatError(f"Project file not found:\n{path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectFormatError(f"Could not read project file:\n{path}\n{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectFormatError(
            f"This file is not a valid OpenCut Studio project "
            f"(damaged or not JSON):\n{path}\n{exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT_ID:
        raise ProjectFormatError(
            "This file is not an OpenCut Studio project.\n"
            f"Expected a {EXTENSION} file created by OpenCut Studio.")
    payload = _migrate(payload, path)
    body = payload.get("project")
    if not isinstance(body, dict):
        raise ProjectFormatError(f"Project file is missing its project data:\n{path}")
    try:
        project = Project.from_dict(body)
    except Exception as exc:  # model rejected the payload: say so, don't guess
        raise ProjectFormatError(
            f"The project data in this file could not be loaded:\n{path}\n{exc}"
        ) from exc
    project.editor_state = dict(project.editor_state or {})
    project.editor_state["file_path"] = path
    return project


def _migrate(payload: Dict[str, Any], path: str) -> Dict[str, Any]:
    """Bring an older envelope up to FORMAT_VERSION, or refuse a newer one."""
    version = payload.get("format_version")
    if not isinstance(version, int):
        raise ProjectFormatError(
            f"Project file has no readable format version:\n{path}")
    if version > FORMAT_VERSION:
        raise ProjectFormatError(
            "This project was saved by a newer version of OpenCut Studio "
            f"(format {version}; this build reads up to {FORMAT_VERSION}).\n"
            "Update OpenCut Studio to open it.")
    # version == 1 is current; future migrations chain from here.
    return payload


def project_file_path(project: Project) -> Optional[str]:
    """The path a project was loaded from / saved to, if known."""
    state = project.editor_state or {}
    path = state.get("file_path")
    return str(path) if path else None
