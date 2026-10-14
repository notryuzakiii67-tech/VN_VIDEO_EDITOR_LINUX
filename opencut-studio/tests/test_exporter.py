"""Export presets, rendering configuration, and a real end-to-end render."""
import pytest

from opencut.media import importer
from opencut.project import store
from opencut.rendering import exporter
from opencut.timeline import ops


def test_preset_options_downscale_keeps_aspect():
    project = store.create_project("Big", "9:16")       # 1080x1920
    opts = exporter.options_for(project, "small")       # max height 720
    assert opts.height == 720
    assert opts.width == 404                            # 1080 * 720/1920, even
    assert opts.width % 2 == 0 and opts.height % 2 == 0
    opts = exporter.options_for(project, "original")
    assert (opts.width, opts.height) == (1080, 1920)


def test_unknown_preset_rejected():
    project = store.create_project("X")
    with pytest.raises(ValueError, match="Unknown export preset"):
        exporter.options_for(project, "huge")
    with pytest.raises(ValueError):
        exporter.export_project(project, "out.mp4", "huge")


def test_empty_timeline_export_is_an_error(tmp_path):
    project = store.create_project("Empty")
    outcome = exporter.export_project(project, str(tmp_path / "x.mp4"))
    assert not outcome.ok
    assert "empty" in outcome.message.lower()


@pytest.mark.skipif(not __import__("opencut.engine", fromlist=["engine"]).ffmpeg_available(),
                    reason="FFmpeg unavailable")
def test_real_export_and_validation(tmp_path, gameplay):
    project = store.create_project("Render me", "16:9")
    # small canvas so the test render is quick
    project.settings.width, project.settings.height = 320, 180
    asset = importer.import_media([str(gameplay)], project).added[0]
    clip = ops.add_media_clip(project, asset, at=0.0)
    clip.duration = 1.0

    out = tmp_path / "render.mp4"
    outcome = exporter.export_project(project, str(out), "draft")
    assert outcome.ok, outcome.message
    assert outcome.frames > 0

    info = exporter.validate_output(str(out))
    assert info.ok, info.message
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (320, 180)
    assert abs(info.duration - 1.0) < 0.35
    assert info.size_bytes > 10_000


def test_validate_output_reports_missing_file(tmp_path):
    info = exporter.validate_output(str(tmp_path / "nope.mp4"))
    assert not info.ok
    assert "not created" in info.message
