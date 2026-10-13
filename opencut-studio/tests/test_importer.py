"""Media import: metadata, and the unsupported-media policy.

A file joins the library only when a usable stream was identified in
it; everything else is reported with a reason, never silently dropped.
"""
import pytest

from opencut.media import importer
from opencut.project import store


def test_import_video_metadata(gameplay):
    project = store.create_project("Import")
    report = importer.import_media([str(gameplay)], project)
    assert report.ok and len(report.added) == 1
    asset = report.added[0]
    assert asset.kind == "video"
    assert (asset.width, asset.height) == (1280, 720)
    assert abs(asset.duration - 6.0) < 0.35
    assert asset.has_audio is True
    assert len(project.assets) == 1


def test_import_audio_and_image(sfx, still_image):
    project = store.create_project("Import")
    report = importer.import_media([str(sfx), str(still_image)], project)
    assert report.ok, report.issues
    kinds = {a.kind for a in report.added}
    assert kinds == {"audio", "image"}
    audio = next(a for a in report.added if a.kind == "audio")
    assert abs(audio.duration - 0.5) < 0.1


def test_import_rejects_garbage_and_missing(garbage, tmp_path):
    project = store.create_project("Import")
    report = importer.import_media(
        [str(garbage), str(tmp_path / "nope.mp4")], project)
    assert report.added == []
    assert len(report.issues) == 2
    assert "not found" in report.issues[1].message.lower()
    assert project.assets == []


def test_import_duplicate_is_reported(gameplay):
    project = store.create_project("Import")
    importer.import_media([str(gameplay)], project)
    report = importer.import_media([str(gameplay)], project)
    assert report.added == []
    assert len(report.issues) == 1
    assert "already" in report.issues[0].message.lower()


def test_inspect_file_raises_with_reason(garbage):
    with pytest.raises(ValueError):
        importer.inspect_file(str(garbage))
