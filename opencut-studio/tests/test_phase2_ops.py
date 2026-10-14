"""Phase 2 timeline operations: ripple / roll / slip / slide, ripple
delete, keyframes, transitions, speed curves, reverse, fade curves.

The edit modes are pure arithmetic on the project model, so these
tests assert exact timeline numbers: no clip ever overlaps, source
windows never leave [0, source duration] and the minimum clip length
is respected everywhere.
"""
import pytest

from opencut import engine
from opencut.media import importer
from opencut.project import store
from opencut.timeline import ops


@pytest.fixture()
def pair_project(gameplay):
    """Two adjacent 3 s clips (src 0..3 and 3..6) on the video track."""
    project = store.create_project("Phase2", "16:9")
    asset = importer.import_media([str(gameplay)], project).added[0]
    a = ops.add_media_clip(project, asset, at=0.0)
    a.duration = 3.0
    b = ops.add_media_clip(project, asset, at=3.0)
    b.duration = 3.0
    b.src_in = 3.0
    return project, asset, a, b


@pytest.fixture()
def trio_project(gameplay):
    """Three adjacent 2 s clips; source windows 0..2, 2..4, 4..6."""
    project = store.create_project("Phase2 trio", "16:9")
    asset = importer.import_media([str(gameplay)], project).added[0]
    clips = []
    for i in range(3):
        c = ops.add_media_clip(project, asset, at=float(i * 2))
        c.duration = 2.0
        c.src_in = float(i * 2)
        clips.append(c)
    return project, clips


def _layout(project, track_id):
    return sorted(
        (round(c.start, 3), round(c.end, 3), round(c.src_in, 3))
        for c in project.track(track_id).clips)


# --------------------------------------------------------------------------- #
#  Ripple trim / roll / slip / slide / ripple delete
# --------------------------------------------------------------------------- #
def test_ripple_trim_right_pulls_followers(pair_project):
    project, _asset, a, b = pair_project
    boundary = ops.ripple_trim(project, a, "right", 2.0)
    assert boundary == pytest.approx(2.0)
    assert a.duration == pytest.approx(2.0)
    assert b.start == pytest.approx(2.0)          # gap closed
    assert b.duration == pytest.approx(3.0)
    assert _layout(project, a.track_id) == [(0.0, 2.0, 0.0), (2.0, 5.0, 3.0)]


def test_ripple_trim_left_keeps_head_glued(pair_project):
    project, _asset, a, b = pair_project
    ops.ripple_trim(project, b, "left", b.start + 1.0)
    assert b.start == pytest.approx(3.0)          # head did not move
    assert b.duration == pytest.approx(2.0)
    assert b.src_in == pytest.approx(4.0)         # source window slid
    assert a.end == pytest.approx(3.0)


def test_ripple_delete_closes_the_gap(trio_project):
    project, (a, b, c) = trio_project
    assert ops.ripple_delete(project, [b.id]) == 1
    assert project.clip(b.id) is None
    assert c.start == pytest.approx(2.0)
    assert a.start == pytest.approx(0.0)


def test_ripple_delete_several_clips(trio_project):
    project, (a, b, c) = trio_project
    assert ops.ripple_delete(project, [a.id, c.id]) == 2
    assert b.start == pytest.approx(0.0)
    assert len(project.all_clips()) == 1


def test_roll_edit_moves_the_shared_boundary(pair_project):
    project, _asset, a, b = pair_project
    boundary = ops.roll_edit(project, a, 4.0)
    assert boundary == pytest.approx(4.0)
    assert a.duration == pytest.approx(4.0)
    assert (b.start, b.duration) == (pytest.approx(4.0), pytest.approx(2.0))
    assert b.src_in == pytest.approx(4.0)         # 1 s consumed at 1x


def test_roll_edit_clamps_at_the_neighbours(pair_project):
    project, _asset, a, b = pair_project
    # b may keep only the minimum clip length: 3.0 + 3.0 - 0.1 = 5.9.
    boundary = ops.roll_edit(project, a, 99.0)
    assert boundary == pytest.approx(5.9)
    assert b.duration == pytest.approx(ops.MIN_CLIP_SECONDS)


def test_roll_edit_needs_an_adjacent_neighbour(pair_project):
    project, _asset, a, b = pair_project
    b.start = 10.0                                 # a gap appears
    assert ops.roll_edit(project, a, 4.0) is None


def test_slip_edit_moves_the_source_window(trio_project):
    project, (a, b, c) = trio_project
    b.duration = 1.5                               # free head and tail room
    applied = ops.slip_edit(project, b, 1.0)
    assert applied == pytest.approx(1.0)
    assert b.src_in == pytest.approx(3.0)
    assert (b.start, b.duration) == (2.0, 1.5)     # timeline untouched


def test_slip_edit_clamps_to_the_source(trio_project):
    project, (a, b, c) = trio_project
    # b's window is [2, 4] of a 6 s source: it can slide back to 0 and
    # forward until its tail reaches the source end (src_in = 4).
    assert ops.slip_edit(project, b, -99.0) == pytest.approx(-2.0)
    assert b.src_in == pytest.approx(0.0)
    assert ops.slip_edit(project, b, 99.0) == pytest.approx(4.0)
    assert b.src_in == pytest.approx(4.0)


def test_slide_edit_moves_clip_and_neighbours(trio_project):
    project, (a, b, c) = trio_project
    applied = ops.slide_edit(project, b, 1.0)
    assert applied == pytest.approx(1.0)
    assert a.duration == pytest.approx(3.0)        # grew at its tail
    assert (b.start, b.duration) == (3.0, 2.0)     # moved, same length
    assert (c.start, c.duration) == (5.0, 1.0)     # shrank at its head
    assert c.src_in == pytest.approx(5.0)


def test_slide_edit_without_two_neighbours_is_a_noop(trio_project):
    project, (a, b, c) = trio_project
    assert ops.slide_edit(project, a, 1.0) == 0.0
    assert ops.slide_edit(project, c, -1.0) == 0.0


# --------------------------------------------------------------------------- #
#  Keyframes
# --------------------------------------------------------------------------- #
def test_keyframe_defaults_match_the_static_value(pair_project):
    _project, _asset, a, _b = pair_project
    kf = ops.add_keyframe(a, "scale", 0.0)
    assert kf.value == pytest.approx(a.transform.scale)
    assert ops.evaluate(a, "scale", 0.0) == pytest.approx(a.transform.scale)


def test_keyframes_interpolate_between_static_values(pair_project):
    _project, _asset, a, _b = pair_project
    ops.add_keyframe(a, "scale", 0.0, value=1.0)
    ops.add_keyframe(a, "scale", a.duration, value=2.0)
    assert ops.evaluate(a, "scale", 0.0) == pytest.approx(1.0)
    assert ops.evaluate(a, "scale", a.duration) == pytest.approx(2.0)
    mid = ops.evaluate(a, "scale", a.duration / 2)
    assert 1.0 < mid < 2.0                          # easing curve applied
    assert ops.evaluate(a, "scale", 99.0) == pytest.approx(2.0)  # clamps


def test_keyframes_require_a_valid_property(pair_project):
    _project, _asset, a, _b = pair_project
    with pytest.raises(ValueError):
        ops.add_keyframe(a, "hue", 0.0)


def test_remove_and_clear_keyframes(pair_project):
    _project, _asset, a, _b = pair_project
    ops.add_keyframe(a, "opacity", 0.0, value=0.5)
    ops.add_keyframe(a, "opacity", 2.0, value=1.0)
    assert ops.remove_keyframe(a, "opacity", 0.0)
    assert not ops.remove_keyframe(a, "opacity", 0.0)
    ops.clear_keyframes(a, "opacity")
    assert "opacity" not in a.animations
    assert ops.evaluate(a, "opacity", 0.0) == pytest.approx(1.0)


def test_volume_keyframes_use_the_audio_store(pair_project):
    _project, _asset, a, _b = pair_project
    a.audio.volume = 0.8
    ops.add_keyframe(a, "volume", 0.0, value=0.0)
    ops.add_keyframe(a, "volume", 3.0, value=0.8)
    assert "volume" in a.audio_animations
    assert "volume" not in a.animations
    assert ops.evaluate(a, "volume", 1.5) == pytest.approx(0.4, abs=0.05)


def test_split_remaps_keyframe_times(pair_project):
    project, _asset, a, _b = pair_project
    ops.add_keyframe(a, "scale", 0.0, value=1.0)
    ops.add_keyframe(a, "scale", 2.5, value=2.0)
    ops.add_keyframe(a, "volume", 1.5, value=0.2)
    left, right = ops.split_clip_at(project, a, 1.0)
    assert [k.t for k in ops.keyframes(left, "scale")] == [0.0]
    right_keys = ops.keyframes(right, "scale")
    assert [k.t for k in right_keys] == [pytest.approx(1.5)]
    assert ops.evaluate(right, "scale", 1.5) == pytest.approx(2.0)
    assert [k.t for k in ops.keyframes(right, "volume")] == \
        [pytest.approx(0.5)]


# --------------------------------------------------------------------------- #
#  Transitions
# --------------------------------------------------------------------------- #
def test_transition_is_stored_on_the_incoming_clip(pair_project):
    project, _asset, a, b = pair_project
    transition = ops.set_transition(project, b, "dissolve", 0.8)
    assert transition is b.transition_in
    assert a.transition_out is None
    assert ops.effective_transition(project, b) is transition
    assert ops.effective_transition(project, a) is None


def test_transition_duration_is_clamped_to_the_neighbours(pair_project):
    project, _asset, a, b = pair_project
    transition = ops.set_transition(project, b, "dissolve", 99.0)
    assert transition.duration == pytest.approx(3.0)
    transition = ops.set_transition(project, b, "dissolve", 0.001)
    assert transition.duration == pytest.approx(0.05)


def test_transition_none_and_remove(pair_project):
    project, _asset, a, b = pair_project
    ops.set_transition(project, b, "dissolve")
    assert ops.set_transition(project, b, "none") is None
    assert b.transition_in is None
    ops.set_transition(project, b, "dissolve")
    ops.remove_transition(project, b)
    assert ops.effective_transition(project, b) is None


# --------------------------------------------------------------------------- #
#  Speed curves, reverse, fade curves, text animation
# --------------------------------------------------------------------------- #
def test_speed_curve_preset_and_clear(pair_project):
    _project, _asset, a, _b = pair_project
    ops.apply_speed_curve(a, "Slow Start")
    assert a.speed_curve is not None
    assert a.speed_curve.preset == "Slow Start"
    # Source is consumed along the curve, and clearing restores linear.
    assert a.src_time(a.end) - a.src_in > 0
    ops.clear_speed_curve(a)
    assert a.speed_curve is None


def test_custom_speed_curve_points_are_clamped(pair_project):
    _project, _asset, a, _b = pair_project
    ops.set_speed_curve_points(a, [(0.0, 0.5), (2.0, 99.0)])
    points = a.speed_curve.points
    assert points[-1] == (1.0, 10.0)                # both axes clamped


def test_curve_clips_reject_constant_speed(pair_project):
    project, _asset, a, _b = pair_project
    ops.apply_speed_curve(a, "Slow Start")
    with pytest.raises(ValueError):
        ops.set_clip_speed(project, a, 2.0)


def test_reverse_flag_and_fade_type(pair_project):
    _project, _asset, a, _b = pair_project
    ops.set_reverse(a, True)
    assert a.reverse is True
    ops.set_fade_type(a, "smooth")
    assert a.audio.fade_type == "smooth"
    with pytest.raises(ValueError):
        ops.set_fade_type(a, "wobbly")


def test_text_animation_wrappers(pair_project):
    project, _asset, a, _b = pair_project
    with pytest.raises(ValueError):
        ops.apply_text_animation(a, "pop_in")          # not a text clip
    text_clip = ops.add_text_clip(project, at=0.0, text="POV")
    ops.apply_text_animation(text_clip, "pop_in")
    assert _all_animation_keys(text_clip)            # keyframes created
    ops.clear_text_animation(text_clip)
    assert not _all_animation_keys(text_clip)


def _all_animation_keys(clip):
    return [k for keys in clip.animations.values() for k in keys]


def test_everything_survives_a_save_load_round_trip(pair_project, tmp_path):
    project, _asset, a, b = pair_project
    ops.set_transition(project, b, "dissolve", 0.75)
    ops.add_keyframe(a, "scale", 0.0, value=1.0)
    ops.add_keyframe(a, "scale", 3.0, value=2.0)
    ops.add_keyframe(b, "volume", 0.0, value=0.0)
    ops.add_keyframe(b, "volume", 3.0, value=1.0)
    ops.apply_speed_curve(a, "Bullet Time")
    ops.set_reverse(b, True)
    ops.set_fade_type(b, "gentle")

    path = store.save_project(project, str(tmp_path / "phase2.opencut"))
    reopened = store.load_project(path)
    ra = reopened.clip(a.id)
    rb = reopened.clip(b.id)
    assert rb.transition_in.kind == "dissolve"
    assert rb.transition_in.duration == pytest.approx(0.75)
    assert [k.value for k in ops.keyframes(ra, "scale")] == [1.0, 2.0]
    assert [k.value for k in ops.keyframes(rb, "volume")] == [0.0, 1.0]
    assert ra.speed_curve is not None and ra.speed_curve.preset == "Bullet Time"
    assert rb.reverse is True
    assert rb.audio.fade_type == "gentle"
    assert ops.effective_transition(reopened, rb).kind == "dissolve"
