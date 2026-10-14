"""Phase 2 engine regression tests.

Three engine bugs were found by the Phase 2 acceptance test while the
older suites stayed green (they never asserted audio *placement* or
overlay *geometry*):

1. ``amix`` aligns its inputs by sample count and ignores frame
   timestamps, so a clip placed later on the timeline with ``adelay``
   was mixed from t=0, overlapping whatever was already playing
   (fix: placement is prepended as real silence samples).

2. The speed-curve (ramp) audio branch windowed its slices with an
   absolute ``atrim`` on a stream that was already input-seeked to
   ``src_in`` — a double seek that silenced every ramped clip with
   ``src_in > 0`` — and placed the slices with the same broken
   ``adelay`` trick (fix: clip-relative windows + silence placement).

3. The movie render path ignored a clip's static ``transform.scale``,
   so a picture-in-picture rendered full-frame (fix: fold the static
   scale into the fitted size).

Each test below pins one of those behaviours in the exported file.
"""
import subprocess

import numpy as np

from opencut import engine
from opencut.media import importer
from opencut.project import store
from opencut.rendering import exporter
from opencut.timeline import ops

LOUD = 0.02     # a 0.4-1.0 amplitude sine sits far above this
QUIET = 0.004   # encoder noise / AAC padding sits far below this


def _run(ffmpeg, args):
    proc = subprocess.run([ffmpeg, "-hide_banner", "-y", *args],
                          capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise AssertionError(
            f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[-1200:]}")
    return proc


def _rms(ffmpeg, video, start, duration=0.8):
    proc = _run(ffmpeg, ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                         "-i", str(video), "-vn", "-ac", "1", "-ar", "8000",
                         "-f", "f32le", "-"])
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    assert samples.size > 1000, "no audio decoded from export"
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def _frame(ffmpeg, video, t, width=960, height=540):
    proc = _run(ffmpeg, ["-ss", f"{t:.3f}", "-i", str(video),
                         "-frames:v", "1", "-f", "rawvideo",
                         "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-"])
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    assert raw.size == width * height * 3, "short frame from export"
    return raw.reshape(height, width, 3).astype(np.float32)


def _export(project, tmp_path, name="out.mp4"):
    outcome = exporter.export_project(project, str(tmp_path / name),
                                      preset_key="draft")
    assert outcome.ok, outcome.message
    return outcome.output


def test_audio_clip_sounds_at_its_timeline_position(solid_blue, solid_red,
                                                    tmp_path):
    """Clip B placed at 4 s must be silent before 4 s and loud after.

    During Phase 2 development this arrangement was measured broken on
    the then-current engine (B mixed from t=0, overlapping A at ~sqrt(2)x
    loudness, its real position silent).  Placement is now done by
    prepending real silence samples, which pins B to its position
    regardless of how the mixer treats frame timestamps.
    """
    ffmpeg = engine.ffmpeg_executable()
    project = store.create_project("Audio placement", "16:9")
    report = importer.import_media([str(solid_blue), str(solid_red)], project)
    assert not report.issues
    blue = next(a for a in report.added if a.name.startswith("solid_blue"))
    red = next(a for a in report.added if a.name.startswith("solid_red"))

    a = ops.add_media_clip(project, blue, at=0.0)
    a.duration = 2.0
    b_track = project.ensure_track_for("overlay")
    b = ops.add_media_clip(project, red, at=4.0, track=b_track)
    b.duration = 2.0
    b.transform.scale = 0.3

    out = _export(project, tmp_path)
    assert _rms(ffmpeg, out, 0.4) > LOUD          # A playing
    assert _rms(ffmpeg, out, 2.6) < QUIET         # the gap stays a gap
    assert _rms(ffmpeg, out, 4.6) > LOUD          # B starts at 4 s


def test_speed_curve_audio_is_audible_and_placed(solid_blue, gameplay,
                                                 tmp_path):
    """A ramped clip with ``src_in > 0`` must not render as silence,
    and must still start at its timeline position (6 s here).

    ``src_in=3.0`` is chosen so the pre-fix double-seek pushed every
    slice window past the end of the input-seeked stream: the buggy
    build was measurably silent (RMS 0.0), the fixed one is not.
    """
    ffmpeg = engine.ffmpeg_executable()
    project = store.create_project("Curve audio", "16:9")
    report = importer.import_media([str(solid_blue), str(gameplay)], project)
    assert not report.issues
    blue = next(a for a in report.added if a.name.startswith("solid_blue"))
    game = next(a for a in report.added if a.name.startswith("gameplay"))

    a = ops.add_media_clip(project, blue, at=0.0)
    a.duration = 2.0
    b_track = project.ensure_track_for("overlay")
    b = ops.add_media_clip(project, game, at=6.0, track=b_track)
    b.duration = 2.0
    b.src_in = 3.0                      # the double-seek trigger
    b.transform.scale = 0.3
    ops.apply_speed_curve(b, "Slow Start")
    assert b.speed_curve is not None

    out = _export(project, tmp_path)
    assert _rms(ffmpeg, out, 3.0) < QUIET         # nothing before B
    assert _rms(ffmpeg, out, 6.6) > LOUD          # ramped B is audible
    assert _rms(ffmpeg, out, 7.5) > LOUD          # ... to its end


def test_static_scale_pip_does_not_cover_the_frame(solid_blue, solid_red,
                                                   tmp_path):
    """A 0.5-scale PiP must leave the base frame visible in the corners.

    Pre-fix the overlay rendered full-frame, so every pixel was red.
    """
    ffmpeg = engine.ffmpeg_executable()
    project = store.create_project("PiP scale", "16:9")
    report = importer.import_media([str(solid_blue), str(solid_red)], project)
    assert not report.issues
    blue = next(a for a in report.added if a.name.startswith("solid_blue"))
    red = next(a for a in report.added if a.name.startswith("solid_red"))

    base = ops.add_media_clip(project, blue, at=0.0)
    base.duration = 2.0
    base.audio.muted = True
    pip_track = project.ensure_track_for("overlay")
    pip = ops.add_media_clip(project, red, at=0.0, track=pip_track)
    pip.duration = 2.0
    # add_media_clip switches small sources to "cover" (which fills the
    # frame by design); the static-scale bug lived in the "fit" path
    pip.transform.fit = "fit"
    pip.transform.scale = 0.5
    pip.audio.muted = True

    out = _export(project, tmp_path)
    frame = _frame(ffmpeg, out, 1.0)
    h, w = frame.shape[:2]
    corner = frame[int(0.03 * h), int(0.03 * w)]   # base only
    centre = frame[h // 2, w // 2]                 # inside the PiP
    assert corner[2] > 150 and corner[0] < 80, f"corner not blue: {corner}"
    assert centre[0] > 150 and centre[2] < 80, f"centre not red: {centre}"
