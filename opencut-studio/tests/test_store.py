"""Project format: creation presets, save/reopen round-trip, validation."""
import json

import pytest

from opencut import engine
from opencut.project import store
from opencut.timeline import ops


def test_format_presets():
    p = store.create_project("A", "16:9")
    assert (p.settings.width, p.settings.height) == (1920, 1080)
    p = store.create_project("A", "9:16")
    assert (p.settings.width, p.settings.height) == (1080, 1920)
    p = store.create_project("A", "1:1")
    assert p.settings.width == p.settings.height
    p = store.create_project("A", "custom", 800, 600)
    assert (p.settings.width, p.settings.height) == (800, 600)
    with pytest.raises(ValueError):
        store.create_project("A", "3:2")


def test_new_project_has_four_tracks():
    p = store.create_project("A")
    kinds = [t.kind for t in p.tracks]
    assert kinds == ["video", "overlay", "text", "audio"]


def test_save_load_roundtrip(tmp_path):
    project = store.create_project("Round trip", "9:16")
    clip = ops.add_text_clip(project, at=0.0, text="POV: ROUND TRIP")
    clip.text.size = 123.0
    path = store.save_project(project, str(tmp_path / "edit"))
    assert path.endswith(store.EXTENSION)

    raw = json.loads((tmp_path / "edit.opencut").read_text())
    assert raw["format"] == store.FORMAT_ID
    assert raw["format_version"] == store.FORMAT_VERSION
    assert raw["app"]["name"] == "OpenCut Studio"

    loaded = store.load_project(path)
    assert loaded.name == "Round trip"
    assert (loaded.settings.width, loaded.settings.height) == (1080, 1920)
    clips = loaded.all_clips()
    assert len(clips) == 1
    assert clips[0].text.text == "POV: ROUND TRIP"
    assert clips[0].text.font == "Bebas Neue"
    assert clips[0].text.size == 123.0
    assert store.project_file_path(loaded) == path


def test_load_errors_are_actionable(tmp_path):
    with pytest.raises(store.ProjectFormatError, match="not found"):
        store.load_project(str(tmp_path / "missing.opencut"))

    bad = tmp_path / "bad.opencut"
    bad.write_text("{ not json")
    with pytest.raises(store.ProjectFormatError, match="not a valid"):
        store.load_project(str(bad))

    other = tmp_path / "other.opencut"
    other.write_text(json.dumps({"format": "something-else", "project": {}}))
    with pytest.raises(store.ProjectFormatError, match="not an OpenCut"):
        store.load_project(str(other))

    newer = tmp_path / "newer.opencut"
    newer.write_text(json.dumps({
        "format": store.FORMAT_ID,
        "format_version": store.FORMAT_VERSION + 1,
        "project": {}}))
    with pytest.raises(store.ProjectFormatError, match="newer version"):
        store.load_project(str(newer))


def test_relink_asset(tmp_path, gameplay):
    """A moved file can be relinked; clips follow the asset."""
    from opencut.media import importer
    project = store.create_project("Relink")
    report = importer.import_media([str(gameplay)], project)
    asset = report.added[0]
    clip = ops.add_media_clip(project, asset, at=0.0)
    old_id = asset.id

    moved = tmp_path / "moved_gameplay.mp4"
    moved.write_bytes(gameplay.read_bytes())
    asset.path = str(tmp_path / "gone.mp4")
    clip.path = asset.path

    count = engine.relink_media(project, old_id, str(moved))
    assert count == 1
    assert project.asset(old_id).path == str(moved)
    assert project.clip(clip.id).path == str(moved)
    assert project.asset(old_id).missing is False
    assert engine.relink_media(project, "no-such-asset", str(moved)) == 0
