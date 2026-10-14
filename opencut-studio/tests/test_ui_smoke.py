"""Headless UI smoke tests (offscreen Qt).

Builds the real main window and drives the same methods the buttons
drive: add text, split, undo/redo, delete, save, reopen, export dialog.
"""
import pytest

pytest.importorskip("PySide6")

from opencut.project import store  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp):
    from opencut.ui.main_window import MainWindow
    win = MainWindow()
    yield win
    win.history.mark_saved()       # don't trigger the unsaved-changes prompt
    win.close()


def test_window_builds_with_empty_project(window):
    assert window.project is not None
    assert len(window.project.tracks) == 4
    assert window.project.all_clips() == []


def test_add_split_undo_redo_delete(window):
    window.add_text()
    assert len(window.project.all_clips()) == 1
    window.timeline.set_playhead(1.0)
    window.split_clips()
    assert len(window.project.all_clips()) == 2
    window.undo()
    assert len(window.project.all_clips()) == 1
    window.redo()
    assert len(window.project.all_clips()) == 2
    window.delete_clips()
    assert len(window.project.all_clips()) == 1   # the selected half went
    window.undo()
    assert len(window.project.all_clips()) == 2


def test_save_and_reopen_through_window(window, tmp_path):
    window.add_text()
    window.project_path = str(tmp_path / "ui_edit")
    assert window.save_project() is True
    saved = tmp_path / "ui_edit.opencut"
    assert saved.exists()
    assert window.history.is_dirty is False
    window.open_project(str(saved))
    assert len(window.project.all_clips()) == 1


def test_export_dialog_lists_presets(window):
    from opencut.rendering import exporter
    from opencut.ui.export_dialog import ExportDialog
    dialog = ExportDialog(window.project, parent=window)
    labels = [dialog._preset.itemText(i)
              for i in range(dialog._preset.count())]
    assert len(labels) == len(exporter.PRESETS)
    assert any("Balanced" in label for label in labels)
    dialog.close()


def test_import_media_through_window(window, gameplay):
    from opencut.media import importer
    project = window.project
    report = importer.import_media([str(gameplay)], project)
    assert report.ok
    window.media_panel.set_assets(project.assets)
    window.add_asset_to_timeline(report.added[0].id)
    clips = window.project.all_clips()
    assert len(clips) == 1 and clips[0].kind == "video"
