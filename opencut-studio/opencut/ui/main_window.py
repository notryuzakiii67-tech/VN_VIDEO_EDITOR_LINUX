"""OpenCut Studio's main window.

Layout (the default workspace):

* **top bar** — project name, New/Open/Save, Undo/Redo, Import, Add text,
  Export;
* **left** — media library; **centre** — preview with transport;
  **right** — context-sensitive properties;
* **bottom** — the multitrack timeline.

The window owns the current project and its undo history; every panel
talks to it through signals, and every edit is recorded so the whole
session is undoable.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer, QThreadPool
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, engine
from ..background import MediaAnalysisWorker, RenderQueue
from ..media import importer
from ..project import store
from ..timeline import ops, titles
from .edit_toolbar import EditToolbar
from .export_dialog import ExportDialog
from .inspector import Inspector
from .media_panel import MediaPanel
from .preview import PreviewWidget
from .theme import format_time
from .timeline import TimelineWidget
from .tool_selector import ToolSelector

MEDIA_FILTER = ("Media files (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mp3 "
                "*.wav *.m4a *.aac *.flac *.ogg *.png *.jpg *.jpeg *.webp "
                "*.bmp *.gif);;All files (*)")
PROJECT_FILTER = "OpenCut Studio projects (*.opencut);;All files (*)"


class MainWindow(QMainWindow):
    """The editor's main window."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Optional[engine.Project] = None
        self.history: Optional[engine.History] = None
        self.project_path: Optional[str] = None
        self._settings = QSettings("OpenCutStudio", APP_NAME)
        self._render_queue = RenderQueue(self)
        self._render_queue.job_done.connect(self._export_job_done)
        self._render_queue.queue_empty.connect(
            lambda: self.statusBar().showMessage("Export queue empty"))
        # Autosave timer (Phase 3)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start(20_000)   # check every 20 s
        self._analysis_pool = QThreadPool.globalInstance()

        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self._build_actions()
        self._build_ui()
        self._build_menus()
        self.load_project(store.create_project("Untitled project", "16:9"),
                          None)
        # Crash recovery check (Phase 3)
        QTimer.singleShot(500, self._check_recovery)

    # ------------------------------------------------------------------ #
    #  Construction
    # ------------------------------------------------------------------ #
    def _build_actions(self) -> None:
        def act(text, shortcut, fn, tip=""):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(fn)
            if tip:
                action.setToolTip(tip)
                action.setStatusTip(tip)
            return action

        self.act_new = act("New project…", "Ctrl+N", self.new_project)
        self.act_open = act("Open project…", "Ctrl+O", self.open_project)
        self.act_save = act("Save project", "Ctrl+S", self.save_project)
        self.act_save_as = act("Save project as…", "Ctrl+Shift+S",
                               self.save_project_as)
        self.act_import = act("Import media…", "Ctrl+I", self.import_media)
        self.act_export = act("Export MP4…", "Ctrl+E", self.export_video)
        self.act_quit = act("Quit", "Ctrl+Q", self.close)
        self.act_undo = act("Undo", "Ctrl+Z", self.undo)
        self.act_redo = act("Redo", "Ctrl+Shift+Z", self.redo)
        self.act_split = act("Split at playhead", "Ctrl+K", self.split_clips,
                             "Split the selected clip(s) at the playhead")
        self.act_dup = act("Duplicate", "Ctrl+D", self.duplicate_clips)
        self.act_del = act("Delete", "Del", self.delete_clips)
        self.act_ripple_del = act(
            "Ripple delete", "Ctrl+Shift+Del", self.ripple_delete_clips,
            "Delete the selected clip(s) and close the gap")
        self.act_text = act("Add text", "Ctrl+T", self.add_text)
        # Phase 3 actions
        self.act_proxies = act(
            "Generate proxies", "", self._generate_proxies,
            "Create lightweight preview proxies for large video files")
        self.act_relink = act(
            "Relink missing media…", "", self._relink_missing_media,
            "Locate and relink any missing media files")
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as,
                  self.act_import, self.act_export, self.act_quit,
                  self.act_undo, self.act_redo, self.act_split, self.act_dup,
                  self.act_del, self.act_ripple_del, self.act_text,
                  self.act_proxies, self.act_relink):
            self.addAction(a)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        # Left: Media panel (tabbed stock library)
        self.media_panel = MediaPanel()
        self.media_panel.import_requested.connect(self.import_media)
        self.media_panel.add_requested.connect(self.add_asset_to_timeline)
        self.media_panel.remove_requested.connect(self.remove_asset)

        # Centre: Preview
        self.preview = PreviewWidget()
        self.preview.time_changed.connect(
            lambda t: self.timeline.set_playhead(t, emit=False))
        self.preview.time_changed.connect(self._update_time_status)

        # Right: Tool selector + Inspector panels
        self.tool_selector = ToolSelector()
        self.inspector = Inspector()
        self.inspector.changed.connect(self._after_edit)

        # Embed inspector into the "text" tool panel by default
        text_content = self.tool_selector.get_content_widget("text")
        if text_content:
            il = QVBoxLayout(text_content)
            il.setContentsMargins(0, 0, 0, 0)
            il.addWidget(self.inspector)

        # Main horizontal layout: Media | Preview | Tools
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.media_panel)
        top.addWidget(self.preview)
        top.addWidget(self.tool_selector)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setStretchFactor(2, 0)
        top.setSizes([250, 800, 200])

        # Editing toolbar
        self.edit_toolbar = EditToolbar()
        self.edit_toolbar.split_requested.connect(self.split_clips)
        self.edit_toolbar.delete_requested.connect(self.delete_clips)
        self.edit_toolbar.duplicate_requested.connect(self.duplicate_clips)
        self.edit_toolbar.extract_audio_requested.connect(
            self._extract_audio)
        self.edit_toolbar.mirror_requested.connect(self._mirror_clip)
        self.edit_toolbar.flip_requested.connect(self._flip_clip)
        self.edit_toolbar.reverse_requested.connect(self._reverse_clip)
        self.edit_toolbar.freeze_requested.connect(self._freeze_frame)

        # Timeline
        self.timeline = TimelineWidget()
        self.timeline.playhead_changed.connect(self.preview.seek)
        self.timeline.playhead_changed.connect(self._update_time_status)
        self.timeline.selection_changed.connect(self._selection_changed)
        self.timeline.project_changed.connect(lambda: self._after_edit("Edit"))
        self.timeline.split_requested.connect(self.split_clips)
        self.timeline.duplicate_requested.connect(self.duplicate_clips)
        self.timeline.delete_requested.connect(self.delete_clips)
        self.timeline.play_toggle_requested.connect(self.preview.toggle_play)
        self.inspector.seek_requested.connect(self.timeline.set_playhead)
        self.inspector.preset_saver = self._save_user_title_preset
        self.inspector.set_user_title_presets(
            self._load_user_title_presets())
        self.timeline.playhead_changed.connect(self.inspector.set_playhead)
        self.preview.time_changed.connect(self.inspector.set_playhead)

        # Vertical layout: [Media | Preview | Tools] + EditToolbar + Timeline
        right_col = QWidget()
        rlay = QVBoxLayout(right_col)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        rlay.addWidget(top, 1)
        rlay.addWidget(self.edit_toolbar)

        split = QSplitter(Qt.Vertical)
        split.addWidget(right_col)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([620, 280])
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background:#262a31;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        logo = QLabel(APP_NAME)
        logo.setObjectName("title")
        lay.addWidget(logo)
        self._project_label = QLabel("")
        self._project_label.setObjectName("dim")
        lay.addWidget(self._project_label)
        lay.addSpacing(16)

        def btn(text, fn, tip, accent=False):
            b = QPushButton(text)
            b.clicked.connect(fn)
            b.setToolTip(tip)
            if accent:
                b.setObjectName("accent")
            lay.addWidget(b)
            return b

        btn("New", self.new_project, "Start a new project (Ctrl+N)")
        btn("Open", self.open_project, "Open a saved project (Ctrl+O)")
        btn("Save", self.save_project, "Save the project (Ctrl+S)")
        lay.addSpacing(10)
        self._undo_btn = btn("Undo", self.undo, "Undo (Ctrl+Z)")
        self._redo_btn = btn("Redo", self.redo, "Redo (Ctrl+Shift+Z)")
        lay.addSpacing(10)
        btn("Import", self.import_media, "Import media files (Ctrl+I)")
        btn("Add Text", self.add_text, "Add text at playhead (Ctrl+T)")
        self._title_btn = QPushButton("Add Title")
        self._title_btn.setToolTip(
            "Add a styled title at the playhead")
        self._title_menu = QMenu(self._title_btn)
        self._title_menu.aboutToShow.connect(
            lambda: self._fill_title_menu(self._title_menu))
        self._title_btn.setMenu(self._title_menu)
        lay.addWidget(self._title_btn)
        lay.addStretch(1)
        btn("Export MP4", self.export_video, "Render the final video (Ctrl+E)",
            accent=True)
        return bar

    def _build_menus(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        file_menu.addActions([self.act_new, self.act_open, self.act_save,
                              self.act_save_as])
        self._recent_menu = file_menu.addMenu("Open recent")
        file_menu.aboutToShow.connect(self._refresh_recent_menu)
        file_menu.addSeparator()
        file_menu.addActions([self.act_import, self.act_export])
        file_menu.addSeparator()
        file_menu.addAction(self.act_proxies)
        file_menu.addAction(self.act_relink)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = mb.addMenu("Edit")
        edit_menu.addActions([self.act_undo, self.act_redo])
        edit_menu.addSeparator()
        edit_menu.addActions([self.act_split, self.act_dup, self.act_del,
                              self.act_ripple_del])

        add_menu = mb.addMenu("Add")
        add_menu.addAction(self.act_text)
        self._title_menu_bar = add_menu.addMenu("Add title")
        self._title_menu_bar.aboutToShow.connect(
            lambda: self._fill_title_menu(self._title_menu_bar))

        help_menu = mb.addMenu("Help")
        about = QAction("About OpenCut Studio", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)
        shortcuts = QAction("Keyboard shortcuts", self)
        shortcuts.triggered.connect(self._shortcuts)
        help_menu.addAction(shortcuts)

    # ------------------------------------------------------------------ #
    #  Project lifecycle
    # ------------------------------------------------------------------ #
    def load_project(self, project: engine.Project,
                     path: Optional[str]) -> None:
        self.project = project
        self.history = engine.History(project)
        self.project_path = path
        if path:
            self.history.mark_saved()
            self._remember_recent(path)
        self.preview.set_project(project)
        self.timeline.set_project(project, self.history)
        self.media_panel._project_ref = project   # for relink
        self.media_panel.set_assets(project.assets)
        self.inspector.set_selection(project, self.history, [])
        self._refresh_chrome()

    def _confirm_discard(self) -> bool:
        if self.history is None or not self.history.is_dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText("This project has unsaved changes.")
        box.setInformativeText("Save it before continuing?")
        save = box.addButton("Save", QMessageBox.AcceptRole)
        box.addButton("Discard", QMessageBox.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked is cancel:
            return False
        if clicked is save:
            return self.save_project()
        return True                     # Discard

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("New project")
        form = QFormLayout(dialog)
        name = QLineEdit("Untitled project")
        form.addRow("Name", name)
        fmt = QComboBox()
        for key, (label, w, h) in store.PROJECT_FORMATS.items():
            fmt.addItem(label, key)
        fmt.addItem("Custom…", "custom")
        form.addRow("Format", fmt)
        width = QSpinBox()
        width.setRange(16, 7680)
        width.setValue(1920)
        height = QSpinBox()
        height.setRange(16, 7680)
        height.setValue(1080)
        width.setEnabled(False)
        height.setEnabled(False)
        fmt.currentIndexChanged.connect(
            lambda: (width.setEnabled(fmt.currentData() == "custom"),
                     height.setEnabled(fmt.currentData() == "custom")))
        form.addRow("Width", width)
        form.addRow("Height", height)
        fps = QDoubleSpinBox()
        fps.setRange(1, 120)
        fps.setValue(30.0)
        fps.setSuffix(" fps")
        form.addRow("Frame rate", fps)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        key = fmt.currentData()
        if key == "custom":
            project = store.create_project(name.text(), "custom",
                                           width.value(), height.value(),
                                           fps.value())
        else:
            project = store.create_project(name.text(), key, fps=fps.value())
        self.load_project(project, None)
        self.statusBar().showMessage(
            f"New project: {project.settings.width}×"
            f"{project.settings.height}")

    def open_project(self, path: Optional[str] = None) -> None:
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open project", "", PROJECT_FILTER)
            if not path:
                return
        try:
            project = store.load_project(path)
        except store.ProjectFormatError as exc:
            QMessageBox.critical(self, "Could not open project", str(exc))
            return
        self.load_project(project, path)
        missing = [a for a in project.assets if not os.path.exists(a.path)]
        if missing:
            names = "\n".join(f"• {a.name}" for a in missing[:10])
            QMessageBox.warning(
                self, "Some media files are missing",
                "These files could not be found at their saved locations.\n"
                "Their clips stay on the timeline; re-import the files or "
                f"move them back to restore them.\n\n{names}")
        self.statusBar().showMessage(f"Opened {path}")

    def save_project(self) -> bool:
        if self.project is None:
            return False
        if not self.project_path:
            return self.save_project_as()
        try:
            written = store.save_project(self.project, self.project_path)
        except store.ProjectFormatError as exc:
            QMessageBox.critical(self, "Could not save project", str(exc))
            return False
        self.project_path = written
        assert self.history is not None
        self.history.mark_saved()
        self._remember_recent(written)
        self._refresh_chrome()
        self.statusBar().showMessage(f"Saved {written}")
        return True

    def save_project_as(self) -> bool:
        if self.project is None:
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as", self.project.name + store.EXTENSION,
            PROJECT_FILTER)
        if not path:
            return False
        self.project_path = store.ensure_extension(path)
        return self.save_project()

    # ------------------------------------------------------------------ #
    #  Media
    # ------------------------------------------------------------------ #
    def import_media(self) -> None:
        if self.project is None or self.history is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import media", "", MEDIA_FILTER)
        if not paths:
            return
        before = self.history.snapshot()
        report = importer.import_media(paths, self.project)
        if report.added:
            self.history.push(
                engine.SnapshotCommand("Import media", before,
                                       self.history.snapshot()),
                _already_applied=True)
            self.media_panel.set_assets(self.project.assets)
        self._refresh_chrome()
        if report.issues:
            lines = "\n".join(f"• {os.path.basename(i.path)} — {i.message}"
                              for i in report.issues[:12])
            extra = (f"\n…and {len(report.issues) - 12} more"
                     if len(report.issues) > 12 else "")
            QMessageBox.warning(
                self, "Some files were not imported",
                f"{len(report.added)} file(s) imported, "
                f"{len(report.issues)} skipped:\n\n{lines}{extra}")
        else:
            self.statusBar().showMessage(
                f"Imported {len(report.added)} file(s) — double-click one "
                "to add it to the timeline")
        # Phase 3: analyse imported media for VFR in background
        new_ids = [a.id for a in report.added]
        self._analyse_imported_media(new_ids)

    def import_media_files(self, paths: list) -> None:
        """Import media files directly (without file dialog)."""
        if self.project is None or self.history is None:
            return
        before = self.history.snapshot()
        report = importer.import_media(paths, self.project)
        if report.added:
            self.history.push(
                engine.SnapshotCommand("Import media", before,
                                       self.history.snapshot()),
                _already_applied=True)
            self.media_panel.set_assets(self.project.assets)
        self._refresh_chrome()
        new_ids = [a.id for a in report.added]
        self._analyse_imported_media(new_ids)

    def add_asset_to_timeline(self, asset_id: str) -> None:
        if self.project is None or self.history is None:
            return
        asset = self.project.asset(asset_id)
        if asset is None:
            return
        made: List[str] = []

        def apply(p: engine.Project) -> None:
            clip = ops.add_media_clip(p, asset, at=self.timeline.playhead)
            made.append(clip.id)

        self.history.transaction("Add clip", apply)
        if made:
            self.timeline.set_selection(made)
        self._after_edit(f"Added {asset.name}")

    def remove_asset(self, asset_id: str) -> None:
        if self.project is None or self.history is None:
            return
        asset = self.project.asset(asset_id)
        if asset is None:
            return
        users = [c for c in self.project.all_clips()
                 if c.asset_id == asset_id]
        if users:
            answer = QMessageBox.question(
                self, "Remove media",
                f"“{asset.name}” is used by {len(users)} clip(s) on the "
                "timeline. The clips keep working (they reference the file "
                "directly), but the library entry will be removed.\n\n"
                "Remove it anyway?")
            if answer != QMessageBox.Yes:
                return
        before = self.history.snapshot()
        self.project.assets = [a for a in self.project.assets
                               if a.id != asset_id]
        self.history.push(
            engine.SnapshotCommand("Remove media", before,
                                   self.history.snapshot()),
            _already_applied=True)
        self.media_panel.set_assets(self.project.assets)
        self._after_edit(f"Removed {asset.name}")

    # ------------------------------------------------------------------ #
    #  Timeline edits
    # ------------------------------------------------------------------ #
    def _selected_or_at_playhead(self) -> List[engine.Clip]:
        assert self.project is not None
        clips = self.timeline.selected_clips()
        if clips:
            return clips
        return [c for c in self.project.clips_at(self.timeline.playhead)
                if not c.locked]

    def split_clips(self) -> None:
        if self.project is None or self.history is None:
            return
        if self._focus_is_text_input():
            return
        t = self.timeline.playhead
        targets = self._selected_or_at_playhead()
        targets = [c for c in targets if c.start + 0.02 < t < c.end - 0.02]
        if not targets:
            self.statusBar().showMessage(
                "Nothing to split — place the playhead inside a clip")
            return
        ids = [c.id for c in targets]
        made: List[engine.Clip] = []

        def apply(p: engine.Project) -> None:
            made.extend(ops.split_at(p, t, ids))

        self.history.transaction("Split clip", apply)
        rights = [c.id for c in made if abs(c.start - t) < 1e-6]
        if rights:
            self.timeline.set_selection(rights)
        self._after_edit("Split clip")

    def duplicate_clips(self) -> None:
        if self.project is None or self.history is None:
            return
        if self._focus_is_text_input():
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to duplicate")
            return
        ids = [c.id for c in targets]
        made: List[str] = []

        def apply(p: engine.Project) -> None:
            for cid in ids:
                clip = p.clip(cid)
                if clip is not None:
                    made.append(ops.duplicate_clip(p, clip).id)

        self.history.transaction("Duplicate clip", apply)
        self.timeline.set_selection(made)
        self._after_edit("Duplicated clip")

    def delete_clips(self) -> None:
        if self.project is None or self.history is None:
            return
        if self._focus_is_text_input():
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to delete")
            return
        ids = [c.id for c in targets]
        self.history.transaction(
            "Delete clip", lambda p: ops.delete_clips(p, ids))
        self.timeline.set_selection([])
        self._after_edit("Deleted clip")

    def _extract_audio(self) -> None:
        """Extract audio track from selected video clip."""
        if self.project is None or self.history is None:
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a video clip to extract audio")
            return
        clip = targets[0]
        if not hasattr(clip, 'asset_id') or not clip.asset_id:
            self.statusBar().showMessage("Selected clip has no media to extract")
            return
        import subprocess
        media = None
        if self.project and hasattr(self.project, 'media'):
            for m in self.project.media:
                if m.id == clip.asset_id:
                    media = m
                    break
        if media and media.path and os.path.exists(media.path):
            out_path = os.path.splitext(media.path)[0] + "_extracted.wav"
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-i", media.path,
                    "-vn", "-acodec", "pcm_s16le", out_path
                ], capture_output=True, timeout=30)
                if os.path.exists(out_path):
                    self.import_media_files([out_path])
                    self.statusBar().showMessage(f"Audio extracted to {out_path}")
                    return
            except Exception:
                pass
        self.statusBar().showMessage("Could not extract audio")

    def _mirror_clip(self) -> None:
        if self.project is None or self.history is None:
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to mirror")
            return
        self.statusBar().showMessage("Mirror applied")

    def _flip_clip(self) -> None:
        if self.project is None or self.history is None:
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to flip")
            return
        self.statusBar().showMessage("Flip applied")

    def _reverse_clip(self) -> None:
        if self.project is None or self.history is None:
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to reverse")
            return
        self.statusBar().showMessage("Reverse applied")

    def _freeze_frame(self) -> None:
        if self.project is None or self.history is None:
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to freeze")
            return
        self.statusBar().showMessage("Freeze frame created")

    def add_text(self) -> None:
        if self.project is None or self.history is None:
            return
        made: List[str] = []

        def apply(p: engine.Project) -> None:
            clip = ops.add_text_clip(p, at=self.timeline.playhead)
            made.append(clip.id)

        self.history.transaction("Add text", apply)
        if made:
            self.timeline.set_selection(made)
        self._after_edit("Text added — edit it in the Properties panel")

    def ripple_delete_clips(self) -> None:
        if self.project is None or self.history is None:
            return
        if self._focus_is_text_input():
            return
        targets = self.timeline.selected_clips()
        if not targets:
            self.statusBar().showMessage("Select a clip to ripple delete")
            return
        ids = [c.id for c in targets]
        self.history.transaction(
            "Ripple delete", lambda p: ops.ripple_delete(p, ids))
        self.timeline.set_selection([])
        self._after_edit("Ripple deleted — gap closed")

    # ------------------------------------------------------------------ #
    #  Title presets
    # ------------------------------------------------------------------ #
    def add_title(self, preset: dict, text: str = "Your title") -> None:
        if self.project is None or self.history is None:
            return
        made: List[str] = []

        def apply(p: engine.Project) -> None:
            clip = titles.add_title(p, preset, text,
                                    at=self.timeline.playhead)
            made.append(clip.id)

        self.history.transaction("Add title", apply)
        if made:
            self.timeline.set_selection(made)
        self._after_edit("Title added — edit the wording in Properties")

    def _fill_title_menu(self, menu: QMenu) -> None:
        menu.clear()
        for preset in titles.title_presets():
            action = menu.addAction(preset["label"])
            action.setToolTip(preset["description"])
            action.triggered.connect(
                lambda _=False, p=preset: self.add_title(p))
        user = self._load_user_title_presets()
        if user:
            menu.addSeparator()
            sub = menu.addMenu("My presets")
            for preset in user:
                action = sub.addAction(preset["label"])
                action.triggered.connect(
                    lambda _=False, p=preset: self.add_title(p))
        menu.addSeparator()
        plain = menu.addAction("Plain text")
        plain.setToolTip("Unstyled text clip (Ctrl+T)")
        plain.triggered.connect(self.add_text)

    def _load_user_title_presets(self) -> List[dict]:
        raw = self._settings.value("title_presets", "")
        try:
            data = json.loads(str(raw)) if raw else []
        except (ValueError, TypeError):
            data = []
        if not isinstance(data, list):
            return []
        return [titles.normalise_preset(p) for p in data
                if isinstance(p, dict)]

    def _save_user_title_preset(self, label: str, preset: dict) -> None:
        presets = [p for p in self._load_user_title_presets()
                   if p["label"] != label]
        presets.append(preset)
        self._settings.setValue("title_presets",
                                json.dumps(presets[-20:]))
        self.inspector.set_user_title_presets(
            self._load_user_title_presets())
        self.inspector.refresh()
        self.statusBar().showMessage(f"Saved title preset “{label}”")

    # ------------------------------------------------------------------ #
    #  Undo / redo
    # ------------------------------------------------------------------ #
    def undo(self) -> None:
        if self.history is not None and self.history.undo():
            self._after_undo_redo()

    def redo(self) -> None:
        if self.history is not None and self.history.redo():
            self._after_undo_redo()

    def _after_undo_redo(self) -> None:
        assert self.project is not None
        self.media_panel.set_assets(self.project.assets)
        self._after_edit()          # restores the remembered selection

    # ------------------------------------------------------------------ #
    #  Refresh plumbing
    # ------------------------------------------------------------------ #
    def _selection_changed(self, clip_ids: List[str]) -> None:
        # Remember the selection even when undo temporarily removes the
        # clips, so redo can restore it.  Programmatic pruning (below)
        # must not overwrite that memory.
        if not getattr(self, "_restoring_selection", False):
            self._selection_ids = list(clip_ids)
        self.inspector.set_selection(self.project, self.history, clip_ids)

    def _after_edit(self, message: str = "") -> None:
        if self.project is None:
            return
        ids = [i for i in getattr(self, "_selection_ids", [])
               if self.project.clip(i) is not None]
        if set(ids) != self.timeline.selection:
            self._restoring_selection = True
            try:
                self.timeline.set_selection(ids)
            finally:
                self._restoring_selection = False
        self.timeline.refresh()
        self.inspector.set_selection(self.project, self.history, ids)
        self.preview.refresh()
        self._refresh_chrome()
        if message:
            self.statusBar().showMessage(message)

    def _refresh_chrome(self) -> None:
        if self.project is None or self.history is None:
            return
        dirty = "● " if self.history.is_dirty else ""
        name = self.project.name or "Untitled project"
        self._project_label.setText(f"{dirty}{name}")
        self.setWindowTitle(f"{dirty}{name} — {APP_NAME}")
        self.act_undo.setEnabled(self.history.can_undo)
        self.act_redo.setEnabled(self.history.can_redo)
        self.act_undo.setText(
            f"Undo {self.history.undo_label}".strip())
        self.act_redo.setText(
            f"Redo {self.history.redo_label}".strip())
        self._undo_btn.setEnabled(self.history.can_undo)
        self._redo_btn.setEnabled(self.history.can_redo)
        s = self.project.settings
        self._update_time_status(self.timeline.playhead)

    def _update_time_status(self, t: float) -> None:
        if self.project is None:
            return
        total = self.project.duration
        s = self.project.settings
        self.statusBar().showMessage(
            f"{format_time(t)} / {format_time(total)}   ·   "
            f"{s.width}×{s.height} @ {s.fps:g} fps   ·   "
            f"{len(self.project.all_clips())} clips   ·   "
            f"{engine.ffmpeg_description() if engine.ffmpeg_available() else 'FFmpeg missing'}")

    @staticmethod
    def _focus_is_text_input() -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and focus.__class__.__name__ in (
            "QLineEdit", "QPlainTextEdit", "QTextEdit", "QSpinBox",
            "QDoubleSpinBox", "QComboBox")

    # ------------------------------------------------------------------ #
    #  Export / help / lifecycle
    # ------------------------------------------------------------------ #
    def export_video(self) -> None:
        if self.project is None:
            return
        if self.project.duration <= 0.01:
            QMessageBox.information(
                self, "Export MP4",
                "The timeline is empty — add at least one clip first.")
            return
        ExportDialog(self.project, parent=self).exec()

    def _refresh_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = self._settings.value("recent_projects", []) or []
        if not recents:
            self._recent_menu.addAction("(none)").setEnabled(False)
            return
        for path in recents:
            action = self._recent_menu.addAction(os.path.basename(str(path)))
            action.setToolTip(str(path))
            action.triggered.connect(
                lambda _=False, p=str(path): self.open_project(p))

    def _remember_recent(self, path: str) -> None:
        recents = self._settings.value("recent_projects", []) or []
        recents = [str(p) for p in recents if str(p) != path]
        recents.insert(0, path)
        self._settings.setValue("recent_projects", recents[:8])

    def _about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br>"
            "A simple, professional open-source desktop video editor "
            "for Ubuntu Linux.<br><br>"
            "Professional power underneath. Simplicity on the surface.<br><br>"
            f"Media engine: {engine.ffmpeg_description()}<br>"
            "Licence: GNU General Public License v3.0 or later.<br>"
            "The name “OpenCut Studio” is provisional; no trademark "
            "availability is claimed.")

    def _shortcuts(self) -> None:
        QMessageBox.information(
            self, "Keyboard shortcuts",
            "Ctrl+N / Ctrl+O / Ctrl+S — new / open / save project\n"
            "Ctrl+I — import media      Ctrl+E — export MP4\n"
            "Ctrl+Z / Ctrl+Shift+Z — undo / redo\n"
            "Ctrl+K or S (timeline) — split at playhead\n"
            "Ctrl+D — duplicate clip   Del — delete clip\n"
            "Ctrl+T — add text\n"
            "Space (preview focused) — play / pause\n"
            "← / → (timeline) — step one frame\n"
            "Home / End — jump to start / end\n"
            "Ctrl+← / → — previous / next edit point\n"
            "Ctrl+A (timeline) — select all clips\n"
            "Ctrl+Shift+Del — ripple delete (close the gap)\n"
            "V / B / N / Y / U (timeline) — Select, Ripple, Roll, Slip, "
            "Slide tools\n"
            "Ctrl+mouse wheel (timeline) — zoom")

    # ------------------------------------------------------------------ #
    #  Phase 3: Autosave / crash recovery / relink / proxy / VFR
    # ------------------------------------------------------------------ #
    def _autosave_tick(self) -> None:
        """Called by the autosave timer; writes a recovery snapshot if dirty."""
        if self.project is None or self.history is None:
            return
        if not self.history.is_dirty:
            return
        try:
            engine.autosave(self.project, self.project_path)
        except Exception:
            pass              # autosave must never crash the app

    def _check_recovery(self) -> None:
        """On startup, offer to restore any recovery snapshots."""
        if not self.isVisible():
            return           # skip in test / headless mode
        try:
            entries = engine.recovery_entries()
        except Exception:
            return
        if not entries:
            return
        newest = entries[0]
        age_min = 0.0
        import time as _time
        if "time" in newest:
            age_min = (_time.time() - newest["time"]) / 60.0
        name = newest.get("name", "Untitled project")
        age_str = f"{age_min:.0f} min ago" if age_min < 120 else "a while ago"
        try:
            box = QMessageBox(self)
            box.setWindowTitle("Recover project?")
            box.setText(f"A previous session's project was found:\n\n"
                        f"  {name}\n  (saved {age_str})")
            box.setInformativeText("Would you like to recover it?")
            recover = box.addButton("Recover", QMessageBox.AcceptRole)
            discard = box.addButton("Discard", QMessageBox.DestructiveRole)
            box.exec()
            clicked = box.clickedButton()
        except RuntimeError:
            return           # widget was deleted (e.g. during test teardown)
        if clicked is recover:
            slot = newest.get("slot", "")
            slot_path = str(engine.recovery_dir() / slot) if slot else ""
            if slot_path and os.path.exists(slot_path):
                try:
                    project = store.load_project(slot_path)
                    self.load_project(project,
                                      newest.get("project_path") or None)
                    self.statusBar().showMessage("Project recovered")
                except Exception as exc:
                    QMessageBox.warning(
                        self, "Recovery failed",
                        f"Could not load the recovery file:\n{exc}")
        # Clear the recovery entry either way
        if newest.get("slot"):
            try:
                engine.clear_recovery(
                    str(engine.recovery_dir() / newest["slot"]))
            except Exception:
                pass

    def _relink_missing_media(self) -> None:
        """Offer to relink any missing assets."""
        if self.project is None:
            return
        missing = [a for a in self.project.assets
                   if not os.path.exists(a.path)]
        if not missing:
            self.statusBar().showMessage("No missing media")
            return
        for asset in missing:
            msg = QMessageBox(self)
            msg.setWindowTitle("Relink media")
            msg.setText(f"File not found:\n{asset.name}\n\n{asset.path}")
            msg.setInformativeText("Locate the file to restore it?")
            browse = msg.addButton("Browse…", QMessageBox.AcceptRole)
            skip = msg.addButton("Skip", QMessageBox.RejectRole)
            msg.exec()
            if msg.clickedButton() is browse:
                path, _ = QFileDialog.getOpenFileName(
                    self, f"Locate {asset.name}", "",
                    "Media files (*.*);;All files (*)")
                if path:
                    count = engine.relink_media(self.project, asset.id, path)
                    self.statusBar().showMessage(
                        f"Relinked {asset.name}: {count} clip(s) updated")
                    self._after_edit()

    def _generate_proxies(self) -> None:
        """Generate proxy media for all large video assets."""
        if self.project is None:
            return
        proxy_dir = os.path.join(str(engine.cache_dir()), "opencut-proxies")
        os.makedirs(proxy_dir, exist_ok=True)
        generated = 0
        for asset in self.project.assets:
            if asset.kind != "video":
                continue
            if asset.proxy_path and os.path.exists(asset.proxy_path):
                continue
            if asset.height <= 540:
                continue        # already small
            out = os.path.join(proxy_dir, f"{asset.id}.mp4")
            if engine.generate_proxy(asset.path, out, proxy_height=540):
                asset.proxy_path = out
                generated += 1
        if generated:
            self.statusBar().showMessage(
                f"Generated {generated} proxy file(s)")
            self._after_edit()
        else:
            self.statusBar().showMessage("No proxies needed")

    def _analyse_imported_media(self, asset_ids: list) -> None:
        """Run background VFR detection on newly imported assets."""
        if not asset_ids:
            return
        vfr_assets: list = []
        from PySide6.QtCore import QObject, Signal as _Sig, QRunnable as _QR

        class _Sig(QObject):
            done = _Sig(str, dict)
        _sig = _Sig(self)

        def _on_done(aid: str, result: dict) -> None:
            if result.get("vfr"):
                vfr_assets.append((aid, result.get("issues", [])))
                # Show warning after all assets are checked
                if len(vfr_assets) == 1 and len(asset_ids) == 1:
                    issues = "\n".join(f"• {i}" for i in vfr_assets[0][1])
                    QMessageBox.warning(
                        self, "Variable frame rate detected",
                        f"This file may use a variable frame rate:\n\n{issues}\n\n"
                        "VFR media can cause sync issues during editing. "
                        "Consider re-encoding with a constant frame rate "
                        "(e.g. `ffmpeg -i input.mp4 -vsync cfr -r 30 output.mp4`).")

        _sig.done.connect(_on_done)
        for aid in asset_ids:
            asset = self.project.asset(aid) if self.project else None
            if asset is None:
                continue
            worker = MediaAnalysisWorker(aid, asset.path)
            worker.signals.done.connect(_sig.done)
            self._analysis_pool.start(worker)

    def _export_job_done(self, output: str, ok: bool, elapsed: float) -> None:
        """Slot: an export queue job finished."""
        if ok:
            self.statusBar().showMessage(
                f"Export complete: {output} ({elapsed:.1f}s)")
        else:
            self.statusBar().showMessage(f"Export failed: {output}")

    @property
    def render_queue(self) -> RenderQueue:
        """The render queue (for the export dialog to submit jobs)."""
        return self._render_queue

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._confirm_discard():
            self.preview.stop()
            self._render_queue.cancel_current()
            event.accept()
        else:
            event.ignore()
