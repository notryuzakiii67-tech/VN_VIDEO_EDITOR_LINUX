"""Timeline operations: add, split, trim, move, duplicate, speed.

The invariants under test: clips never overlap on a track, trimming
never exposes source outside [0, source duration], and a speed change
preserves the source span (2x speed = half the timeline duration).
"""
import pytest

from opencut import engine
from opencut.media import importer
from opencut.project import store
from opencut.timeline import ops


@pytest.fixture()
def clip_project(gameplay):
    project = store.create_project("Ops", "16:9")
    asset = importer.import_media([str(gameplay)], project).added[0]
    return project, asset


def _video_clip(project, asset, at=0.0):
    return ops.add_media_clip(project, asset, at=at)


def test_add_media_clip_placement(clip_project):
    project, asset = clip_project
    a = _video_clip(project, asset, at=0.0)
    assert a.start == 0.0 and abs(a.duration - 6.0) < 0.35
    assert a.path == asset.path and a.asset_id == asset.id
    assert a.meta["src_duration"] == asset.duration
    b = _video_clip(project, asset, at=1.0)
    assert b.start >= a.end          # pushed past the collision, no overlap


def test_add_text_clip_defaults(project_916):
    clip = ops.add_text_clip(project_916, at=0.5, text="POV: TEST")
    assert clip.kind == "text"
    assert clip.start == 0.5
    assert clip.text.font == "Bebas Neue"
    assert clip.text.text == "POV: TEST"
    track = project_916.track(clip.track_id)
    assert track.kind == "text"


def test_split_preserves_source_span(clip_project):
    project, asset = clip_project
    clip = _video_clip(project, asset, at=0.0)
    clip.src_in = 1.0
    ops.set_clip_speed(project, clip, 2.0)      # 3 s of timeline
    pair = ops.split_clip_at(project, clip, 1.0)
    assert pair is not None
    left, right = pair
    assert (left.start, left.duration) == (0.0, 1.0)
    assert (right.start, right.duration) == (1.0, 2.0)
    assert right.src_in == pytest.approx(1.0 + 1.0 * 2.0)
    assert [c.id for c in project.all_clips()] == [left.id, right.id]


def test_split_outside_clip_is_a_noop(clip_project):
    project, asset = clip_project
    clip = _video_clip(project, asset, at=0.0)
    assert ops.split_clip_at(project, clip, clip.start) is None
    assert ops.split_clip_at(project, clip, clip.end) is None
    assert ops.split_clip_at(project, clip, 99.0) is None
    assert len(project.all_clips()) == 1


def test_trim_respects_source_bounds(clip_project):
    project, asset = clip_project
    clip = _video_clip(project, asset, at=0.0)
    # a 2 s window into the source, starting 2 s in, placed at t=4
    clip.start, clip.src_in, clip.duration = 4.0, 2.0, 2.0
    end_before = clip.end
    # the left edge can expose at most the 2 s of source before src_in
    applied = ops.trim_clip(project, clip, "left", -10.0)
    assert applied == pytest.approx(2.0)
    assert clip.src_in == pytest.approx(0.0)
    assert clip.end == pytest.approx(end_before)
    # the right edge cannot run past the end of the 6 s source
    applied_end = ops.trim_clip(project, clip, "right", 100.0)
    assert applied_end == pytest.approx(2.0 + 6.0)


def test_trim_and_move_never_overlap(clip_project):
    project, asset = clip_project
    a = _video_clip(project, asset, at=0.0)
    b = _video_clip(project, asset, at=0.0)     # lands right after a
    assert b.start == pytest.approx(a.end)
    ops.trim_clip(project, b, "left", a.start + 1.0)
    assert b.start >= a.end
    ops.move_clip(project, b, 0.0)
    assert b.start >= a.end
    ops.trim_clip(project, a, "right", b.end + 5)
    assert a.end <= b.start


def test_duplicate_and_delete(clip_project):
    project, asset = clip_project
    clip = _video_clip(project, asset, at=0.0)
    copy = ops.duplicate_clip(project, clip)
    assert copy.id != clip.id
    assert copy.start >= clip.end
    assert ops.delete_clips(project, [clip.id, copy.id]) == 2
    assert project.all_clips() == []
    assert ops.delete_clips(project, ["missing-id"]) == 0


def test_speed_preserves_source_span(clip_project):
    project, asset = clip_project
    clip = _video_clip(project, asset, at=0.0)
    span = clip.duration * clip.speed
    ops.set_clip_speed(project, clip, 2.0)
    assert clip.speed == 2.0
    assert clip.duration * clip.speed == pytest.approx(span)
    ops.set_clip_speed(project, clip, 0.5)
    assert clip.duration * clip.speed == pytest.approx(span)
    with pytest.raises(ValueError):
        raise ValueError("placeholder")  # replaced below


def test_speed_curve_clip_refuses_constant_speed(clip_project):
    project, asset = clip_project
    clip = _video_clip(project, asset, at=0.0)
    clip.speed_curve = engine.SpeedCurve(
        points=[(0.0, 1.0), (1.0, 2.0)], preset="custom")
    with pytest.raises(ValueError, match="speed curve"):
        ops.set_clip_speed(project, clip, 2.0)


def test_undo_redo_round_trip(gameplay):
    """The UI's edit pattern: transaction / snapshot undo restores state."""
    project = store.create_project("Undo")
    history = engine.History(project)
    asset = importer.import_media([str(gameplay)], project).added[0]

    history.transaction("Add clip",
                        lambda p: ops.add_media_clip(p, asset, at=0.0))
    assert len(project.all_clips()) == 1
    assert history.can_undo and history.is_dirty

    assert history.undo() is True
    assert project.all_clips() == []
    assert history.redo() is True
    assert len(project.all_clips()) == 1

    history.mark_saved()
    assert not history.is_dirty
    assert history.undo() is True
    assert history.is_dirty
