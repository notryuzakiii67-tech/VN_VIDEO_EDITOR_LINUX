"""Shared fixtures: small media files generated with FFmpeg itself.

All fixtures are synthetic (FFmpeg `lavfi` sources) — legally
redistributable and reproducible on any machine with FFmpeg.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opencut import engine


def _run(args) -> None:
    exe = engine.ffmpeg_executable()
    assert exe, "FFmpeg is required to build the test fixtures"
    proc = subprocess.run([exe, "-hide_banner", "-y", *args],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"fixture generation failed: {proc.stderr[-800:]}"


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def gameplay(media_dir) -> Path:
    """6 s of 1280x720 'gameplay' (moving test pattern) with a quiet tone."""
    out = media_dir / "gameplay.mp4"
    if not out.exists():
        _run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
              "-filter:a", "volume=0.4", "-shortest",
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
              "-c:a", "aac", str(out)])
    return out


@pytest.fixture(scope="session")
def sfx(media_dir) -> Path:
    """0.5 s low 'thud' burst (180 Hz) used as a meme sound effect."""
    out = media_dir / "thud.wav"
    if not out.exists():
        _run(["-f", "lavfi", "-i", "sine=frequency=180:duration=0.5",
              "-af", "afade=t=out:st=0.3:d=0.2", "-c:a", "pcm_s16le", str(out)])
    return out


@pytest.fixture(scope="session")
def still_image(media_dir) -> Path:
    out = media_dir / "title_card.png"
    if not out.exists():
        _run(["-f", "lavfi", "-i", "color=c=teal:size=640x480",
              "-frames:v", "1", str(out)])
    return out


@pytest.fixture(scope="session")
def garbage(media_dir) -> Path:
    out = media_dir / "not_media.mp4"
    if not out.exists():
        out.write_bytes(b"this is not a media file at all" * 64)
    return out


@pytest.fixture()
def project_916():
    from opencut.project import store
    return store.create_project("Acceptance", "9:16")
