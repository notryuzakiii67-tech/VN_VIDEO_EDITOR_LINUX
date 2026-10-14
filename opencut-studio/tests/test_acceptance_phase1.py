"""PHASE 1 ACCEPTANCE TEST (automated).

The workflow from the project brief, end to end:

1. Create a vertical 9:16 project.
2. Import Minecraft-style gameplay (a landscape clip stands in) and a
   meme sound effect.
3. Put the gameplay on the timeline, zoom in and reposition it for the
   vertical canvas, and speed it up.
4. Add a large Bebas Neue POV caption.
5. Place the sound effect at an exact timeline position.
6. Save the project, close it, reopen it.
7. Export to MP4 and verify — by decoding the file, not by trusting the
   exporter — that the streams, duration, caption pixels and the sound
   effect at the right moment are all really there.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from opencut import engine
from opencut.media import importer
from opencut.project import store
from opencut.rendering import exporter
from opencut.timeline import ops

pytestmark = pytest.mark.skipif(
    not engine.ffmpeg_available(), reason="FFmpeg unavailable")

GAMEPLAY_TONE_DB = 0.15        # gameplay clip volume (quiet bed)
SFX_AT = 2.0                   # the exact moment the thud lands


def _decode_pcm(path: Path, start: float, seconds: float) -> np.ndarray:
    exe = engine.ffmpeg_executable()
    proc = subprocess.run(
        [exe, "-hide_banner", "-ss", str(start), "-t", str(seconds),
         "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", "48000",
         "pipe:1"], capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-400:]
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if len(samples) else 0.0


def _frame_rgb(path: Path, at: float, width: int = 152) -> np.ndarray:
    exe = engine.ffmpeg_executable()
    proc = subprocess.run(
        [exe, "-hide_banner", "-ss", str(at), "-i", str(path),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-400:]
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    height = len(raw) // (width * 3)
    return raw[: height * width * 3].reshape(height, width, 3)


def test_phase1_acceptance(tmp_path, gameplay, sfx):
    # 1-2. project + imports -------------------------------------------------
    project = store.create_project("POV acceptance", "9:16")
    report = importer.import_media([str(gameplay), str(sfx)], project)
    assert report.ok, report.issues
    game = next(a for a in report.added if a.kind == "video")
    thud = next(a for a in report.added if a.kind == "audio")
    assert (game.width, game.height) == (1280, 720)      # landscape source

    # 3. gameplay: fill the vertical canvas, zoom in, reposition, speed up --
    clip = ops.add_media_clip(project, game, at=0.0)
    clip.transform.fit = "cover"          # 16:9 -> 9:16 crop-fill
    clip.transform.scale = 1.35           # zoom in slightly
    clip.transform.zoom_x = 0.62          # reposition the focus right
    clip.transform.zoom_y = 0.45
    clip.audio.volume = GAMEPLAY_TONE_DB  # quiet bed under the SFX
    ops.set_clip_speed(project, clip, 1.5)
    assert clip.duration == pytest.approx(6.0 / 1.5, abs=0.25)

    # 4. the big POV caption --------------------------------------------------
    text = ops.add_text_clip(project, at=0.5, text="POV: YOU FOUND IT")
    text.duration = 3.0
    text.text.font = "Bebas Neue"
    text.text.size = 150.0
    text.transform.y = 0.16

    # 5. the meme sound, placed precisely --------------------------------------
    sfx_clip = ops.add_media_clip(project, thud, at=SFX_AT)
    assert sfx_clip.start == pytest.approx(SFX_AT)
    sfx_clip.audio.volume = 1.0
    sfx_clip.audio.fade_out = 0.1

    # 6. save, close, reopen -----------------------------------------------------
    path = store.save_project(project, str(tmp_path / "pov.opencut"))
    reopened = store.load_project(path)
    assert (reopened.settings.width, reopened.settings.height) == (1080, 1920)
    assert len(reopened.all_clips()) == 3
    r_game = next(c for c in reopened.all_clips() if c.kind == "video")
    r_text = next(c for c in reopened.all_clips() if c.kind == "text")
    r_sfx = next(c for c in reopened.all_clips() if c.kind == "audio")
    assert r_game.speed == pytest.approx(1.5)
    assert r_game.transform.scale == pytest.approx(1.35)
    assert r_game.transform.zoom_x == pytest.approx(0.62)
    assert r_text.text.text == "POV: YOU FOUND IT"
    assert r_text.text.font == "Bebas Neue"
    assert r_sfx.start == pytest.approx(SFX_AT)

    # 7. export and verify the actual file ---------------------------------------
    out = tmp_path / "pov_final.mp4"
    outcome = exporter.export_project(reopened, str(out), "draft")
    assert outcome.ok, outcome.message

    info = exporter.validate_output(str(out))
    assert info.ok, info.message
    assert info.has_video and info.has_audio
    assert info.height == 540 and info.width == 304      # 9:16 at draft size
    assert info.duration == pytest.approx(4.0, abs=0.4)  # 6 s at 1.5x speed

    # the caption is really in the pixels at t=1s (white Bebas glyphs)...
    frame = _frame_rgb(out, 1.0)
    near_white = float(np.mean(np.all(frame > 235, axis=2)))
    assert 0.002 < near_white < 0.6, f"caption pixels: {near_white:.4f}"
    # ...and gone after the caption ends (t=3.9, gameplay only)
    late = _frame_rgb(out, 3.9)
    late_white = float(np.mean(np.all(late > 235, axis=2)))
    assert late_white < near_white, (late_white, near_white)

    # the thud lands at SFX_AT: clearly louder than the quiet bed around it
    bed = _rms(_decode_pcm(out, 0.4, 0.4))
    hit = _rms(_decode_pcm(out, SFX_AT + 0.03, 0.3))
    tail = _rms(_decode_pcm(out, 3.2, 0.4))
    assert hit > 3.0 * max(bed, 1e-4), f"bed={bed:.4f} hit={hit:.4f}"
    assert hit > 2.0 * max(tail, 1e-4), f"tail={tail:.4f} hit={hit:.4f}"
