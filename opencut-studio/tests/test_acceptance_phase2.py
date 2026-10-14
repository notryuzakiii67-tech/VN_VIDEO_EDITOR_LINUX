"""Phase 2 acceptance test.

Builds the Phase 2 scenario end to end — two gameplay clips joined by
a transition, scale keyframes on the first, a speed curve on the
second, an animated title from a preset, a music bed with volume
keyframes and a fade curve, and a reversed picture-in-picture overlay
— then:

1. saves the project and reopens it, asserting every edit survived;
2. exports to MP4 and validates the file;
3. proves preview and export agree (the still renderer and the export
   renderer produce the same frames at a transition midpoint, during
   the title animation and over the speed curve / reversed overlay);
4. measures the exported audio envelope to prove the volume keyframes
   were applied (quiet intro -> full bed -> quiet outro).
"""
import subprocess

import numpy as np
import pytest

# note: no pytest-timeout plugin in this environment; the suite-level
# timeout in scripts/run_tests.sh bounds the run instead

from opencut import engine
from opencut.media import importer
from opencut.project import store
from opencut.rendering import exporter
from opencut.timeline import ops, titles


def _run(ffmpeg, args, capture_text=False):
    """Run ffmpeg.  Binary output (raw frames/audio) stays bytes; only
    invocations that write files use text mode for readable errors."""
    proc = subprocess.run([ffmpeg, "-hide_banner", "-y", *args],
                          capture_output=True, text=capture_text,
                          timeout=300)
    if proc.returncode != 0:
        err = proc.stderr
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        raise AssertionError(f"ffmpeg failed: {err[-1500:]}")
    return proc


def _frame_pixels(ffmpeg, video, t, width, height):
    proc = _run(ffmpeg, ["-ss", f"{t:.3f}", "-i", str(video),
                         "-frames:v", "1", "-f", "rawvideo",
                         "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-"])
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    assert raw.size == width * height * 3, "short frame from export"
    return raw.reshape(height, width, 3).astype(np.float32)


def _render_pixels(project, t, width, height, tmp_path, name):
    out = tmp_path / name
    assert engine.render_frame(project, t, width, height, str(out))
    proc = _run(engine.ffmpeg_executable(),
                ["-i", str(out), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    assert raw.size == width * height * 3, "short frame from renderer"
    return raw.reshape(height, width, 3).astype(np.float32)


def _segment_rms(ffmpeg, video, start, duration):
    proc = _run(ffmpeg, ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                         "-i", str(video), "-vn", "-ac", "1", "-ar", "8000",
                         "-f", "f32le", "-"])
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    assert samples.size > 1000, "no audio decoded from export"
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def test_phase2_multitrack_round_trip(gameplay, music, tmp_path):
    ffmpeg = engine.ffmpeg_executable()
    assert ffmpeg

    # ------------------------------------------------------------ build
    project = store.create_project("Phase 2 acceptance", "16:9")
    report = importer.import_media([str(gameplay), str(music)], project)
    assert len(report.added) == 2 and not report.issues
    by_name = {a.name: a for a in report.added}
    game_asset = next(a for a in report.added if a.kind == "video")
    music_asset = next(a for a in report.added if a.kind == "audio")

    a = ops.add_media_clip(project, game_asset, at=0.0)
    a.duration = 3.0
    b = ops.add_media_clip(project, game_asset, at=3.0)
    b.duration = 3.0
    b.src_in = 3.0
    ops.set_transition(project, b, "fade", 0.6)
    # scale keyframes 1.0 -> 1.6 across clip A
    ops.add_keyframe(a, "scale", 0.0, value=1.0, easing="linear")
    ops.add_keyframe(a, "scale", 3.0, value=1.6, easing="linear")
    # speed curve on clip B; mute both video clips so the exported
    # envelope is the music bed alone
    ops.apply_speed_curve(b, "Slow Start")
    a.audio.muted = True
    b.audio.muted = True
    # animated title from a built-in preset
    title = titles.add_title(project, titles.preset_by_id("bold_impact"),
                             "POV: PHASE 2", at=0.5)
    # music bed with volume keyframes + smooth fade curve
    bed = ops.add_media_clip(project, music_asset, at=0.0)
    bed.duration = 6.0
    for t, v in ((0.0, 0.0), (1.0, 0.9), (5.0, 0.9), (6.0, 0.0)):
        ops.add_keyframe(bed, "volume", t, value=v, easing="linear")
    ops.set_fade_type(bed, "smooth")
    # reversed picture-in-picture overlay; muted as well — it is also a
    # video clip with audio, and the envelope assertions below are meant
    # to measure the music bed alone
    overlay_track = project.ensure_track_for("overlay")
    pip = ops.add_media_clip(project, game_asset, at=4.5,
                             track=overlay_track)
    pip.duration = 1.5
    pip.src_in = 0.0
    pip.transform.scale = 0.35
    ops.set_reverse(pip, True)
    pip.audio.muted = True

    # ------------------------------------------------- save and reopen
    path = store.save_project(project, str(tmp_path / "phase2.opencut"))
    reopened = store.load_project(path)
    ra, rb = reopened.clip(a.id), reopened.clip(b.id)
    rt, rbed, rpip = (reopened.clip(c.id) for c in (title, bed, pip))

    assert rb.transition_in is not None
    assert rb.transition_in.kind == "fade"
    assert rb.transition_in.duration == pytest.approx(0.6)
    assert [k.value for k in ops.keyframes(ra, "scale")] == [1.0, 1.6]
    assert rb.speed_curve is not None
    assert rb.speed_curve.preset == "Slow Start"
    assert rt.text.text == "POV: PHASE 2"
    assert rt.meta.get("text_anim") == "pop_in"
    assert any(rt.animations.values())
    vol = [(k.t, k.value) for k in ops.keyframes(rbed, "volume")]
    assert vol == [(0.0, 0.0), (1.0, 0.9), (5.0, 0.9), (6.0, 0.0)]
    assert rbed.audio.fade_type == "smooth"
    assert rpip.reverse is True
    assert rpip.transform.scale == pytest.approx(0.35)
    assert reopened.duration == pytest.approx(6.0)

    # ------------------------------------------------------------ export
    outcome = exporter.export_project(reopened, str(tmp_path / "out.mp4"),
                                      preset_key="draft")
    assert outcome.ok, outcome.message
    info = exporter.validate_output(outcome.output)
    assert info is not None and info.has_audio
    assert info.duration == pytest.approx(6.0, abs=0.6)
    out = outcome.output

    # ------------------------------- preview and export must agree ---
    # t=3.0 is mid-transition; t=1.2 is inside the animated title;
    # t=5.2 sits on the speed curve with the reversed overlay visible.
    for t, name in ((3.0, "mid_transition.png"), (1.2, "title.png"),
                    (5.2, "curve_overlay.png")):
        exported_frame = _frame_pixels(ffmpeg, out, t, 960, 540)
        rendered_frame = _render_pixels(reopened, t, 960, 540,
                                        tmp_path, name)
        diff = float(np.mean(np.abs(exported_frame - rendered_frame)))
        assert diff < 8.0, f"preview/export disagree at t={t}: {diff:.2f}"

    # the title is really on screen (large near-white Bebas glyphs in
    # the lower band where the preset places it)
    frame = _frame_pixels(ffmpeg, out, 1.2, 960, 540)
    band = frame[int(0.66 * 540):int(0.92 * 540)]
    near_white = float(np.mean(np.all(band > 225, axis=2)))
    assert near_white > 0.002, f"title not visible (white px {near_white})"

    # --------------------------- volume keyframes shaped the audio ---
    intro = _segment_rms(ffmpeg, out, 0.05, 0.4)   # ~0.2 of the bed
    middle = _segment_rms(ffmpeg, out, 2.8, 0.4)   # bed at 0.9
    outro = _segment_rms(ffmpeg, out, 5.55, 0.4)   # ~0.1 of the bed
    assert middle > 2.0 * intro, (intro, middle)
    assert middle > 3.0 * outro, (middle, outro)
