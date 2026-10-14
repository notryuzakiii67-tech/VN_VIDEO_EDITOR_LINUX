"""Phase 2 UI interactions (offscreen Qt, real mouse events).

Drives the timeline canvas with QTest mouse drags in each edit mode,
the track-header lock button, keyframe diamonds, the inspector's
keyframe panel and the title-preset menu.  Geometry constants mirror
``opencut.ui.timeline`` (HEADER_W=118, RULER_H=24, track height 74,
60 px/s default zoom).
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QComboBox, QGroupBox, QPushButton  # noqa: E402

from opencut import engine  # noqa: E402
from opencut.media import importer  # noqa: E402
from opencut.project import store  # noqa: E402
from opencut.timeline import ops  # noqa: E402
from opencut.ui.timeline import TimelineWidget  # noqa: E402

HEADER_W, RULER_H, PPS = 118, 24, 60.0
TRACK0_Y = RULER_H + 37           # middle of the first track lane


def x_at(t: float) -> int:
    return int(HEADER_W + t * PPS)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def stage(qapp, gameplay):
    """A TimelineWidget with three adjacent 2 s clips on the video track."""
    project = store.create_project("UI2", "16:9")
    history = engine.History(project)
    asset = importer.import_media([str(gameplay)], project).added[0]
    clips = []
    for i in range(3):
        c = ops.add_media_clip(project, asset, at=float(i * 2))
        c.duration = 2.0
        c.src_in = float(i * 2)
        clips.append(c)
    timeline = TimelineWidget()
    timeline.set_project(project, history)
    timeline.resize(1000, 330)
    timeline.show()
    qapp.processEvents()
    yield timeline, project, history, clips
    timeline.close()


def _drag(timeline, x0, x1, y=TRACK0_Y):
    canvas = timeline._canvas
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
    QTest.mouseMove(canvas, QPoint(x1, y))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(x1, y))


def test_mode_buttons_and_snap_toggle(stage):
    timeline, _p, _h, _c = stage
    assert timeline.edit_mode == "select"
    timeline.set_edit_mode("ripple")
    assert timeline.edit_mode == "ripple"
    assert timeline._mode_btns["ripple"].isChecked()
    assert not timeline._mode_btns["select"].isChecked()
    timeline.set_edit_mode("bogus")               # ignored
    assert timeline.edit_mode == "ripple"
    timeline.set_snap_enabled(False)
    assert timeline.snap_enabled is False
    assert timeline._snap_btn.isChecked() is False
    timeline.set_snap_enabled(True)
    assert timeline._snap_btn.isChecked() is True


def test_ripple_drag_pulls_the_follower(stage):
    timeline, project, history, (a, b, _c) = stage
    timeline.set_edit_mode("ripple")
    _drag(timeline, x_at(2.0) - 3, x_at(1.0))     # a's right edge -> 1.0s
    assert a.duration == pytest.approx(1.0)
    assert b.start == pytest.approx(1.0)          # gap closed
    # The drag is one undo step ("Ripple trim" — the fixture setup made
    # earlier snapshots too, so exhaust the undo stack to reach them).
    assert history.undo_label == "Ripple trim"
    history.undo()      # undo restores fresh clip objects on the project
    a2 = project.clip(a.id)
    b2 = project.clip(b.id)
    assert a2.duration == pytest.approx(2.0)
    assert b2.start == pytest.approx(2.0)


def test_roll_drag_moves_the_shared_boundary(stage):
    timeline, _p, _h, (a, b, _c) = stage
    timeline.set_edit_mode("roll")
    _drag(timeline, x_at(2.0) - 3, x_at(2.5))
    assert a.duration == pytest.approx(2.5)
    assert (b.start, b.duration) == (pytest.approx(2.5), pytest.approx(1.5))
    assert b.src_in == pytest.approx(2.5)


def test_slip_drag_shifts_the_source_window(stage):
    timeline, _p, _h, (_a, b, _c) = stage
    timeline.set_edit_mode("slip")
    _drag(timeline, x_at(3.0), x_at(3.5))         # drag b's body right
    assert b.src_in == pytest.approx(2.5)
    assert (b.start, b.duration) == (2.0, 2.0)    # timeline untouched


def test_slide_drag_moves_clip_and_neighbours(stage):
    timeline, _p, _h, (a, b, c) = stage
    timeline.set_edit_mode("slide")
    _drag(timeline, x_at(3.0), x_at(3.5))
    assert a.duration == pytest.approx(2.5)
    assert (b.start, b.duration) == (2.5, 2.0)
    assert (c.start, c.duration) == (4.5, 1.5)
    assert c.src_in == pytest.approx(4.5)


def test_lock_button_blocks_editing(stage):
    timeline, project, _h, (a, _b, _c) = stage
    canvas = timeline._canvas
    lock_x = HEADER_W - 68 + 9                    # centre of the L button
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(lock_x, RULER_H + 14))
    track = project.track(a.track_id)
    assert track.locked is True
    _drag(timeline, x_at(1.0), x_at(1.8))         # try to move a
    assert a.start == pytest.approx(0.0)          # blocked
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(lock_x, RULER_H + 14))
    assert track.locked is False


def test_keyframe_diamond_click_seeks(stage):
    timeline, _p, _h, (a, _b, _c) = stage
    ops.add_keyframe(a, "scale", 1.0, value=1.5)
    timeline.refresh()
    cy = RULER_H + 4 + (74 - 8) - 6               # diamond centre row
    QTest.mouseClick(timeline._canvas, Qt.LeftButton, Qt.NoModifier,
                     QPoint(x_at(1.0) + 1, cy))
    assert timeline.selection == {a.id}
    assert timeline.playhead == pytest.approx(1.0)


def test_keyboard_navigation(stage):
    timeline, project, _h, clips = stage
    canvas = timeline._canvas
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key_End)
    assert timeline.playhead == pytest.approx(6.0)
    QTest.keyClick(canvas, Qt.Key_Home)
    assert timeline.playhead == pytest.approx(0.0)
    QTest.keyClick(canvas, Qt.Key_Right, Qt.ControlModifier)
    assert timeline.playhead == pytest.approx(2.0)  # next edit point
    QTest.keyClick(canvas, Qt.Key_A, Qt.ControlModifier)
    assert timeline.selection == {c.id for c in clips}
    QTest.keyClick(canvas, Qt.Key_N)
    assert timeline.edit_mode == "roll"


# --------------------------------------------------------------------------- #
#  Main window: inspector panels, title presets
# --------------------------------------------------------------------------- #
@pytest.fixture()
def window(qapp, gameplay):
    from opencut.ui.main_window import MainWindow
    win = MainWindow()
    report = importer.import_media([str(gameplay)], win.project)
    win.media_panel.set_assets(win.project.assets)
    win.add_asset_to_timeline(report.added[0].id)
    clip = win.project.all_clips()[0]
    clip.duration = 4.0
    yield win, clip
    win.history.mark_saved()
    win.close()


def _group(window, title):
    for box in window.inspector.findChildren(QGroupBox):
        if box.title() == title:
            return box
    raise AssertionError(f"inspector group {title!r} not found")


def test_inspector_has_phase2_sections(window):
    _win, _clip = window
    titles_found = {b.title()
                    for b in _win.inspector.findChildren(QGroupBox)}
    assert {"Animation (keyframes)", "Transition in", "Speed",
            "Transform"} <= titles_found


def test_inspector_keyframe_workflow(window):
    win, clip = window
    win.timeline.set_playhead(clip.start + 0.5)
    kf = win.inspector._kf
    kf["prop"].setCurrentIndex(kf["prop"].findData("scale"))
    kf["value"].setValue(150.0)
    kf["add"].click()
    keys = ops.keyframes(clip, "scale")
    assert len(keys) == 1 and keys[0].t == pytest.approx(0.5)
    assert keys[0].value == pytest.approx(1.5)
    win.timeline.set_playhead(clip.start + 3.5)
    kf = win.inspector._kf                        # rebuilt after the edit
    assert kf["value"].value() == pytest.approx(150.0)  # evaluated value
    kf["value"].setValue(100.0)
    kf["add"].click()
    assert len(ops.keyframes(clip, "scale")) == 2
    assert ops.evaluate(clip, "scale", 2.0) == pytest.approx(1.25, abs=0.1)
    assert win.inspector._kf["list"].count() == 2


def test_inspector_speed_curve_controls(window):
    win, clip = window
    speed_group = _group(win, "Speed")
    combo = next(w for w in speed_group.findChildren(QComboBox)
                 if "Slow Start" in [w.itemText(i) for i in range(w.count())])
    combo.setCurrentText("Slow Start")
    apply_btn = next(b for b in speed_group.findChildren(QPushButton)
                     if b.text() == "Apply")
    apply_btn.click()
    assert clip.speed_curve is not None
    assert clip.speed_curve.preset == "Slow Start"


def test_title_menu_adds_styled_clip(window):
    win, _clip = window
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    win._fill_title_menu(menu)
    action = next(a for a in menu.actions() if a.text() == "Bold Impact")
    action.trigger()
    text_clips = [c for c in win.project.all_clips() if c.kind == "text"]
    assert len(text_clips) == 1
    clip = text_clips[0]
    assert clip.text.font == "Bebas Neue"
    assert clip.meta.get("text_anim") == "pop_in"
    assert win.timeline.selection == {clip.id}


def test_user_title_preset_persistence(window):
    win, _clip = window
    win.inspector.preset_saver("Phase2 Test Style",
                               {"label": "Phase2 Test Style",
                                "font": "Oswald", "size_rel": 0.06})
    loaded = [p for p in win._load_user_title_presets()
              if p["label"] == "Phase2 Test Style"]
    assert len(loaded) == 1                       # saved once, re-saves replace
    assert loaded[0]["font"] == "Oswald"
