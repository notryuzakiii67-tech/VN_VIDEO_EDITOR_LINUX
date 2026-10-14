"""Media import: inspect files and build library assets.

Policy for unsupported media (a Phase 1 requirement): a file is added to
the library only when the engine's probe can actually identify a usable
stream in it —

* **video**: a video stream with a positive duration;
* **audio**: an audio stream with a positive duration;
* **image**: a readable still image (dimensions identified).

Everything else is reported back with a specific reason and is *never*
silently skipped or silently degraded.  The decision is based on the
streams found in the file, not on its filename extension.  Importing
never modifies the source file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Sequence

from ..engine import MediaAsset, Project, probe_media


@dataclass
class ImportIssue:
    """One file that was not imported, and why."""

    path: str
    message: str


@dataclass
class ImportReport:
    added: List[MediaAsset] = field(default_factory=list)
    issues: List[ImportIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def inspect_file(path: str) -> MediaAsset:
    """Probe one file and return a library asset for it.

    Raises ``ValueError`` with a user-actionable message when the file
    cannot be used.
    """
    path = str(path)
    if not os.path.exists(path):
        raise ValueError("File not found")
    if not os.path.isfile(path):
        raise ValueError("Not a regular file")
    info = probe_media(path)
    # A video container holding only audio (e.g. an .mp4 voice note) is
    # audio for editing purposes.
    if info.kind == "video" and not info.has_video and info.has_audio:
        asset = MediaAsset.from_info(info, path)
        asset.kind = "audio"
        return asset
    if info.kind == "video":
        if not info.has_video:
            raise ValueError("No video stream could be read from this file")
        if info.duration <= 0:
            raise ValueError("The video's duration could not be determined")
    elif info.kind == "audio":
        if not info.has_audio:
            raise ValueError("No audio stream could be read from this file")
        if info.duration <= 0:
            raise ValueError("The audio's duration could not be determined")
    elif info.kind == "image":
        if info.width <= 0 or info.height <= 0:
            raise ValueError("This image could not be read")
    else:  # pragma: no cover - probe only yields the three kinds above
        raise ValueError(f"Unsupported media kind: {info.kind}")
    return MediaAsset.from_info(info, path)


def import_media(paths: Sequence[str], project: Project) -> ImportReport:
    """Inspect ``paths`` and append the usable ones to the library.

    Files already in the library are reported as issues (skipped), as is
    every unusable file — the caller always learns the full outcome.
    """
    report = ImportReport()
    known = {a.path for a in project.assets}
    for raw in paths:
        path = str(raw)
        if path in known:
            report.issues.append(
                ImportIssue(path, "Already in the media library"))
            continue
        try:
            asset = inspect_file(path)
        except ValueError as exc:
            report.issues.append(ImportIssue(path, str(exc)))
            continue
        project.assets.append(asset)
        known.add(path)
        report.added.append(asset)
    return report
