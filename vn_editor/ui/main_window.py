"""The main editor window.

Layout mirrors VN Video Editor: a top bar with project/undo/export, the video
preview on the left, the contextual inspector on the right, and the multitrack
timeline across the bottom, all over a dark workspace.  Every control is wired
to real project state through the undo history — nothing here is decorative.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import (QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QAction, QColor, QFont, QGuiApplication, QIcon,
                           QKeySequence, QPainter, QPen, QShortcut)
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressDialog, QPushButton,
                               QSizePolicy, QSlider, QSplitter, QStackedWidget,
                               QStatusBar, QToolButton, QVBoxLayout, QWidget)

from ..core import ffmpeg as ff
from ..core import project as pio
from ..core import probe
from ..core.ass import available_fonts
from ..core.model import (Clip, FilterInstance, Keyframe, MediaAsset, Project,
                          TextProps, Transform)
from ..core.undo import History, SnapshotCommand
from . import theme
from .dialogs import (AudioLibraryDialog, BeatsDialog, CaptionsDialog,
                      ExportDialog, PresetsDialog, RecoveryDialog,
                      ShortcutsDialog, SpeedCurveDialog, StickerDialog,
                      TextPresetsDialog, TransitionDialog)
from .inspector import Inspector
from .media import MediaPanel, collect_media_files
from .player import PreviewPanel
from .timeline import TimelinePanel, fmt_timecode
from .widgets import IconButton, SliderField, label


# --------------------------------------------------------------------------- #
#  Application context shared with the panels
# --------------------------------------------------------------------------- #

class EditorContext:
    """Bridge between widgets and the project + undo history."""

    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.project: Project = window.project
        self.history: History = window.history
        self._auto_keyframe = True

    # -- selection / playhead ------------------------------------------------ #
    def selected_clip(self) -> Optional[Clip]:
        ids = self.window.timeline.canvas.selection
        if not ids:
            return None
        for cid in sorted(ids):
            c = self.project.clip(cid)
            if c is not None:
                return c
        return None

    def selected_clips(self) -> List[Clip]:
        return [c for c in (self.project.clip(i)
                            for i in sorted(self.window.timeline.canvas.selection))
                if c is not None]

    def selected_keyframe(self):
        return self.window.timeline.canvas.selected_keyframe

    def playhead(self) -> float:
        return self.window.timeline.canvas.playhead

    def auto_keyframe(self) -> bool:
        return self._auto_keyframe

    def set_auto_keyframe(self, on: bool) -> None:
        self._auto_keyframe = bool(on)

    # -- mutation ------------------------------------------------------------ #
    def commit(self, description: str, fn: Callable[[Project], None],
               coalesce: str = "") -> None:
        self.history.transaction(description, fn, coalesce=coalesce)
        self.window.after_change(description)

    def end_gesture(self) -> None:
        self.history.end_coalescing()
        self.window.after_change("")

    def set_setting(self, key: str, value) -> None:
        pio.update_settings(**{key: value})

    def set_preview_quality(self, quality: str) -> None:
        self.window.preview.widget.quality = quality
        self.window.preview.request_frame(self.playhead())

    # -- ui refresh ---------------------------------------------------------- #
    def refresh_timeline(self) -> None:
        self.window.timeline.refresh()

    def refresh_inspector(self) -> None:
        self.window.inspector.refresh_all()

    def request_preview_refresh(self) -> None:
        self.window.schedule_preview()

    def seek(self, t: float) -> None:
        self.window.seek(t)

    def status(self, text: str, ms: int = 4000) -> None:
        self.window.status(text, ms)


# --------------------------------------------------------------------------- #
#  Small composite widgets
# --------------------------------------------------------------------------- #

class PreviewToolbar(QFrame):
    """Below the preview: play/pause, loop, time, volume, fit, fullscreen."""

    def __init__(self, window: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.setFixedHeight(38)
        self.setStyleSheet(
            f"PreviewToolbar {{ background: {theme.BG_PANEL};"
            f" border-top: 1px solid {theme.BORDER_SOFT}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(3)

        def btn(icon: str, tip: str, fn, checkable: bool = False,
                size: int = 30) -> QToolButton:
            b = QToolButton()
            b.setIcon(theme.icon(icon, theme.TEXT_DIM, 18))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setFixedSize(size, size)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setStyleSheet(
                f"QToolButton {{ border: 1px solid transparent;"
                f" border-radius: 7px; }}"
                f"QToolButton:hover {{ background: {theme.BG_HOVER}; }}"
                f"QToolButton:checked {{ background: {theme.BG_ACTIVE};"
                f" border-color: {theme.ACCENT}; }}")
            if checkable:
                b.toggled.connect(fn)
            else:
                b.clicked.connect(fn)
            lay.addWidget(b)
            return b

        self.btn_start = btn("skip_start", "Go to start  (Home)",
                             lambda: window.seek(0.0))
        self.btn_prev = btn("frame_prev", "Previous frame  (←)",
                            lambda: window.step_frame(-1))
        self.btn_play = btn("play", "Play / pause  (Space)", self._toggle,
                            size=34)
        self.btn_next = btn("frame_next", "Next frame  (→)",
                            lambda: window.step_frame(1))
        self.btn_end = btn("skip_end", "Go to end  (End)",
                           lambda: window.seek(window.project.duration))
        self.btn_loop = btn("loop", "Loop playback", self._loop, checkable=True)
        lay.addSpacing(6)
        self.lbl_time = QLabel("00:00.00")
        self.lbl_time.setStyleSheet(
            f"color:{theme.TEXT}; font-family:'DejaVu Sans Mono',monospace;"
            f"font-size:12px; font-weight:600; padding:0 4px;")
        lay.addWidget(self.lbl_time)
        self.lbl_total = QLabel("/ 00:00.00")
        self.lbl_total.setStyleSheet(f"color:{theme.TEXT_MUTED};"
                                     f"font-size:11px;")
        lay.addWidget(self.lbl_total)
        lay.addStretch(1)

        self.btn_guide = btn("grid", "Safe-area guides", self._guides,
                             checkable=True)
        self.btn_fit = btn("fit", "Fit / fill preview", self._fit)
        self.btn_full = btn("fullscreen", "Fullscreen preview  (F)",
                            window.toggle_fullscreen)

        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(100)
        self.vol.setFixedWidth(78)
        self.vol.setToolTip("Preview volume")
        self.vol.valueChanged.connect(window.set_volume)
        lay.addWidget(self.vol)
        self.btn_mute = btn("volume", "Mute preview", self._mute, checkable=True)

    def _toggle(self) -> None:
        self.window.preview.toggle()

    def _loop(self, on: bool) -> None:
        self.window.loop = on

    def _guides(self, on: bool) -> None:
        self.window.preview.widget.show_guides = on
        self.window.preview.widget.update()

    def _fit(self) -> None:
        w = self.window.preview.widget
        w.fit_mode = {"fit": "fill", "fill": "100", "100": "fit"}[w.fit_mode]
        w.flash(f"Preview: {w.fit_mode}")
        w.update()

    def _mute(self, on: bool) -> None:
        self.window.set_volume(0 if on else self.vol.value())
        self.btn_mute.setIcon(theme.icon("mute" if on else "volume",
                                         theme.ACCENT if on else theme.TEXT_DIM,
                                         18))

    def set_playing(self, playing: bool) -> None:
        self.btn_play.setIcon(theme.icon("pause" if playing else "play",
                                         theme.TEXT_DIM, 18))

    def set_time(self, t: float, total: float, fps: float) -> None:
        self.lbl_time.setText(fmt_timecode(t, fps)[3:] if t < 3600
                              else fmt_timecode(t, fps))
        self.lbl_total.setText(f"/ {fmt_timecode(total, fps)[3:]}"
                               if total < 3600 else f"/ {fmt_timecode(total, fps)}")


class TopBar(QFrame):
    """Project name, undo/redo, quick actions and export."""

    def __init__(self, window: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.setFixedHeight(50)
        self.setStyleSheet(
            f"TopBar {{ background: {theme.BG_PANEL};"
            f" border-bottom: 1px solid {theme.BORDER_SOFT}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        logo = QLabel("VN")
        logo.setFixedSize(30, 30)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            f"background: {theme.ACCENT}; color: white; border-radius: 8px;"
            f" font-weight: 800; font-size: 13px;")
        lay.addWidget(logo)

        self.name = QLabel("Untitled project")
        self.name.setFont(theme.ui_font(13, 600))
        lay.addWidget(self.name)
        self.aspect = QLabel("1080×1920 · 30fps")
        self.aspect.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
        lay.addWidget(self.aspect)

        lay.addSpacing(8)
        self.btn_new = QPushButton("New")
        self.btn_new.setToolTip("New project  (Ctrl+N)")
        self.btn_new.clicked.connect(window.new_project)
        self.btn_open = QPushButton("Open")
        self.btn_open.setToolTip("Open project  (Ctrl+O)")
        self.btn_open.clicked.connect(window.open_project)
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip("Save project  (Ctrl+S)")
        self.btn_save.clicked.connect(window.save_project)
        for b in (self.btn_new, self.btn_open, self.btn_save):
            b.setStyleSheet("padding: 5px 10px;")
            lay.addWidget(b)

        lay.addSpacing(6)
        self.btn_undo = IconButton("undo", "Undo  (Ctrl+Z)", 30, 18)
        self.btn_undo.clicked.connect(window.undo)
        self.btn_redo = IconButton("redo", "Redo  (Ctrl+Shift+Z)", 30, 18)
        self.btn_redo.clicked.connect(window.redo)
        lay.addWidget(self.btn_undo)
        lay.addWidget(self.btn_redo)

        lay.addStretch(1)

        self.tools: List[QToolButton] = []
        for icon, tip, fn in (
            ("template", "Templates and presets", window.open_presets),
            ("captions", "Captions and subtitles", window.open_captions),
            ("magic", "Auto beat detection", window.open_beats),
            ("settings", "Settings and shortcuts", window.open_settings),
        ):
            b = IconButton(icon, tip, 32, 18)
            b.clicked.connect(fn)
            lay.addWidget(b)
            self.tools.append(b)

        lay.addSpacing(6)
        self.btn_export = QPushButton(" Export")
        self.btn_export.setIcon(theme.icon("export", "#ffffff", 16))
        self.btn_export.setProperty("class", "primary")
        self.btn_export.setToolTip("Export video  (Ctrl+E)")
        self.btn_export.setStyleSheet("padding: 6px 14px; font-weight: 600;")
        self.btn_export.clicked.connect(window.export_video)
        lay.addWidget(self.btn_export)

    def refresh(self, project: Project) -> None:
        self.name.setText(project.name)
        st = project.settings
        self.aspect.setText(f"{st.width}×{st.height} · {st.fps:g}fps")


class TransportBar(QWidget):
    """Timeline-side quick actions (VN's row of round tool buttons)."""

    def __init__(self, window: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self.window = window


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self, project: Optional[Project] = None) -> None:
        super().__init__()
        self.project = project or Project.create_default()
        self.history = History(self.project)
        self.ctx = EditorContext(self)
        self.current_path: Optional[str] = None
        self.loop = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(110)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self._autosave = QTimer(self)
        self._autosave.setInterval(120_000)
        self._autosave.timeout.connect(self.autosave)
        self._autosave.start()
        self._fullscreen_restore = None

        self.setWindowTitle("VN Studio — Video Editor")
        self.resize(1500, 940)
        self.setMinimumSize(1080, 700)
        self._build()
        self._install_shortcuts()
        self.after_change("")
        QTimer.singleShot(120, lambda: self.seek(0.0))

    # -- construction -------------------------------------------------------- #
    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top = TopBar(self)
        root.addWidget(self.top)

        # -------- middle splitter: media | preview | inspector -------- #
        self.split_top = QSplitter(Qt.Horizontal)
        self.split_top.setChildrenCollapsible(False)
        self.split_top.setHandleWidth(3)

        media_host = QFrame()
        media_host.setProperty("class", "panelFlat")
        mlay = QVBoxLayout(media_host)
        mlay.setContentsMargins(0, 0, 0, 0)
        self.media = MediaPanel(self.project, self)
        mlay.addWidget(self.media)
        self.split_top.addWidget(media_host)

        centre = QWidget()
        clay = QVBoxLayout(centre)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        self.preview = PreviewPanel(self.project, self)
        clay.addWidget(self.preview, 1)
        self.proof_bar = PreviewToolbar(self)
        clay.addWidget(self.proof_bar)
        self.split_top.addWidget(centre)

        insp_host = QFrame()
        insp_host.setProperty("class", "panelFlat")
        ilay = QVBoxLayout(insp_host)
        ilay.setContentsMargins(0, 0, 0, 0)
        self.inspector = Inspector(self.ctx, self)
        ilay.addWidget(self.inspector)
        self.split_top.addWidget(insp_host)

        self.split_top.setStretchFactor(0, 0)
        self.split_top.setStretchFactor(1, 1)
        self.split_top.setStretchFactor(2, 0)
        self.split_top.setSizes([280, 900, 320])

        # -------- bottom splitter: timeline -------- #
        self.split_main = QSplitter(Qt.Vertical)
        self.split_main.setChildrenCollapsible(False)
        self.split_main.setHandleWidth(3)
        self.split_main.addWidget(self.split_top)
        self.timeline = TimelinePanel(self.project, self)
        self.split_main.addWidget(self.timeline)
        self.split_main.setStretchFactor(0, 3)
        self.split_main.setStretchFactor(1, 2)
        self.split_main.setSizes([560, 330])
        root.addWidget(self.split_main, 1)

        self.setStatusBar(QStatusBar())
        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.ff_label = QLabel("")
        self.statusBar().addPermanentWidget(self.ff_label)
        ff.TOOLCHAIN.probe()
        self.ff_label.setText(ff.TOOLCHAIN.summary())
        self.ff_label.setToolTip(f"Encoder: {ff.TOOLCHAIN.ffmpeg}")
        self.ff_label.setStyleSheet(
            f"color:{theme.TEXT_MUTED if ff.TOOLCHAIN.ok else '#ff6b6b'};")

        # -------- wiring -------- #
        c = self.timeline.canvas
        c.selectionChanged.connect(self.on_selection_changed)
        c.playheadMoved.connect(self.on_playhead)
        c.editRequested.connect(self.on_edit_request)
        c.clipChanged.connect(lambda _cid: self.schedule_preview())
        c.contextMenuRequested.connect(self.on_clip_menu)
        c.trackAction.connect(self.on_track_action)
        c.transitionClicked.connect(self.on_transition_clicked)
        c.keyframeClicked.connect(lambda *a: self.on_selection_changed(
            sorted(c.selection)))

        self.preview.timeChanged.connect(self.on_preview_time)
        self.preview.playingChanged.connect(self.proof_bar.set_playing)
        self.preview.statusMessage.connect(lambda m: self.status(m, 3000))
        self.media.addToTimeline.connect(self.add_asset_to_timeline)
        self.media.filesDropped.connect(self.import_paths)
        self.media.importRequested.connect(self.import_dialog)
        self.media.removeRequested.connect(self.remove_asset)
        self.media.replaceRequested.connect(self.replace_with_asset)

        if not ff.TOOLCHAIN.ok:
            QTimer.singleShot(400, self._warn_missing_ffmpeg)

    def _warn_missing_ffmpeg(self) -> None:
        QMessageBox.warning(
            self, "FFmpeg not found",
            "VN Studio needs FFmpeg to render and preview video.\n\n"
            "Install it with:\n    sudo apt install ffmpeg\n\n"
            "or run ./install.sh from the project folder, then restart "
            "VN Studio.")

    # -- shortcuts ------------------------------------------------------------ #
    def _install_shortcuts(self) -> None:
        def sc(seq: str, fn, context=Qt.WindowShortcut) -> None:
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(context)
            s.activated.connect(fn)

        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+N", self.new_project)
        sc("Ctrl+O", self.open_project)
        sc("Ctrl+S", self.save_project)
        sc("Ctrl+Shift+S", self.save_project_as)
        sc("Ctrl+I", self.import_dialog)
        sc("Ctrl+E", self.export_video)
        sc("Ctrl+T", self.add_text_clip)
        sc("Ctrl+D", lambda: self.on_edit_request("duplicate", ""))
        sc("Ctrl+B", lambda: self.on_edit_request("split", ""))
        sc("Ctrl+K", self.add_keyframe)
        sc("Ctrl+L", lambda: self.timeline.canvas.set_zoom(
            self.timeline.canvas.px_per_sec * 1.4))
        sc("Ctrl+J", lambda: self.timeline.canvas.set_zoom(
            self.timeline.canvas.px_per_sec / 1.4))
        sc("Ctrl+=", self.timeline.canvas.zoom_in)
        sc("Ctrl+-", self.timeline.canvas.zoom_out)
        sc("Shift+Z", self.timeline.canvas.zoom_fit)
        sc("Ctrl+A", self.select_all)
        sc("Ctrl+Shift+D", lambda: self.on_edit_request("deselect", ""))
        sc("Ctrl+M", self.add_marker)
        sc("Ctrl+Shift+B", self.open_beats)
        sc("Ctrl+Shift+C", self.open_captions)
        sc("Delete", lambda: self.on_edit_request("delete", ""))
        sc("Backspace", lambda: self.on_edit_request("delete", ""))
        sc("Space", lambda: self.on_edit_request("play_pause", ""))
        sc("Left", lambda: self.step_frame(-1))
        sc("Right", lambda: self.step_frame(1))
        sc("Shift+Left", lambda: self.ctx.seek(self.ctx.playhead() - 1.0))
        sc("Shift+Right", lambda: self.ctx.seek(self.ctx.playhead() + 1.0))
        sc("Home", lambda: self.seek(0.0))
        sc("End", lambda: self.seek(self.project.duration))
        sc("F", self.toggle_fullscreen)
        sc("I", lambda: self.on_edit_request("trim_in", ""))
        sc("O", lambda: self.on_edit_request("trim_out", ""))
        sc("S", lambda: self.on_edit_request("split", ""))
        sc("T", self.add_text_clip)
        sc("M", lambda: self.on_edit_request("toggle_mute", ""))
        sc("E", lambda: self.on_edit_request("open_effects", ""))
        sc("Shift+S", self.open_presets)
        sc("F1", self.open_shortcuts)
        sc("[", lambda: self.nudge_clip(-1))
        sc("]", lambda: self.nudge_clip(1))
        sc("Ctrl+Up", lambda: self.reorder_track(-1))
        sc("Ctrl+Down", lambda: self.reorder_track(1))
        for i in range(1, 10):
            sc(f"Alt+{i}", lambda idx=i: self.switch_track_by_number(idx))

    # -- state helpers -------------------------------------------------------- #
    def status(self, text: str, ms: int = 4000) -> None:
        self.status_label.setText(text)
        if ms:
            QTimer.singleShot(ms, lambda: self.status_label.setText(
                "" if self.status_label.text() == text else
                self.status_label.text()))

    def after_change(self, description: str) -> None:
        if description:
            self.status(description, 2500)
        self.top.refresh(self.project)
        self.timeline.refresh()
        self.inspector.refresh_all()
        self.top.btn_undo.setEnabled(self.history.can_undo)
        self.top.btn_redo.setEnabled(self.history.can_redo)
        title = self.project.name
        if self.history.is_dirty:
            title += " •"
        self.setWindowTitle(f"{title} — VN Studio")
        self.proof_bar.set_time(self.ctx.playhead(), self.project.duration,
                               self.project.settings.fps)

    def schedule_preview(self) -> None:
        self._preview_timer.start()

    def _refresh_preview(self) -> None:
        self.proof_bar.set_time(self.ctx.playhead(), self.project.duration,
                                self.project.settings.fps)
        self.preview.set_project(self.project)
        self.preview.request_frame(self.ctx.playhead())

    # -- playback / seek ------------------------------------------------------ #
    def seek(self, t: float) -> None:
        t = max(0.0, min(self.project.duration, t))
        self.timeline.canvas.set_playhead(t)
        self.timeline.canvas.ensure_visible(t)
        self.preview.seek(t)

    def on_playhead(self, t: float, final: bool) -> None:
        self.preview.widget.set_time(t)
        self.proof_bar.set_time(t, self.project.duration,
                                self.project.settings.fps)
        if final:
            self.preview.seek(t)
        else:
            self.preview.request_frame(t)

    def on_preview_time(self, t: float) -> None:
        self.timeline.canvas.set_playhead(t)
        self.timeline.canvas.ensure_visible(t)
        self.proof_bar.set_time(t, self.project.duration,
                                self.project.settings.fps)

    def step_frame(self, direction: int) -> None:
        fps = max(1.0, self.project.settings.fps)
        self.seek(self.ctx.playhead() + direction / fps)

    def set_volume(self, value: int) -> None:
        self.preview.set_volume(value / 100.0)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -- editing actions ------------------------------------------------------ #
    def on_edit_request(self, action: str, clip_id: str) -> None:
        handler = {
            "split": self.split_at_playhead,
            "delete": self.delete_selection,
            "duplicate": self.duplicate_selection,
            "trim_to_playhead": self.trim_to_playhead,
            "trim_in": lambda: self.trim_edge("in"),
            "trim_out": lambda: self.trim_edge("out"),
            "add_keyframe": self.add_keyframe,
            "add_text": self.add_text_clip,
            "play_pause": self.toggle_play,
            "toggle_mute": self.toggle_mute,
            "open_effects": lambda: self.inspector.show_tab("Effects"),
            "deselect": self.deselect,
            "select_all": self.select_all,
            "open:transition": self.open_transitions,
            "open:filter": lambda: self.inspector.show_tab("Grade"),
            "open:speed": lambda: self.inspector.show_tab("Speed"),
            "open:audio": self.open_audio_library,
            "open:sticker": self.open_stickers,
            "open:beats": self.open_beats,
            "open_inspector": lambda: self.inspector.show_for(self.ctx.selected_clip()),
        }.get(action)
        if handler:
            handler()
        elif action.startswith("track:"):
            self.after_change(f"Track {action.split(':', 1)[1]}")

    def split_at_playhead(self) -> None:
        t = self.ctx.playhead()
        targets = self.ctx.selected_clips() or [
            c for c in self.project.clips_at(t) if not c.locked]
        targets = [c for c in targets if c.start + 0.02 < t < c.end - 0.02]
        if not targets:
            self.status("Nothing to split at the playhead")
            return

        def apply(p: Project):
            for clip in targets:
                local = t - clip.start
                offset = local * clip.speed
                left = clip.clone()
                right = clip.clone()
                left.id, right.id = clip.id, Clip().id
                left.start = clip.start
                left.duration = local
                right.start = t
                right.duration = max(0.02, clip.duration - local)
                if not clip.freeze:
                    right.src_in = clip.src_in + offset
                # keep the outgoing transition on the left, incoming on right
                right.transition_in = None
                left.transition_out = None
                if clip.speed_curve:      # curves are normalised: share them
                    left.speed_curve = type(clip.speed_curve)(
                        list(clip.speed_curve.points), clip.speed_curve.preset)
                    right.speed_curve = type(clip.speed_curve)(
                        list(clip.speed_curve.points), clip.speed_curve.preset)
                tr = p.track(clip.track_id)
                p.remove_clip(clip.id)
                p.add_clip(left, tr)
                p.add_clip(right, tr)
        self.history.transaction("Split", apply)
        self.after_change("Split clip")
        self.deselect()

    def delete_selection(self) -> None:
        clips = self.ctx.selected_clips()
        if not clips:
            self.status("Nothing selected")
            return
        word = f"Delete {len(clips)} clip{'s' if len(clips) != 1 else ''}"
        self.history.transaction(word, lambda p: [
            p.remove_clip(c.id) for c in clips])
        self.timeline.canvas.selection = set()
        self.after_change(word)
        self.on_selection_changed([])

    def duplicate_selection(self) -> None:
        clips = self.ctx.selected_clips()
        if not clips:
            return
        made: List[str] = []

        def apply(p: Project):
            for clip in clips:
                copy = clip.clone()
                copy.id = Clip().id
                copy.start = clip.end
                tr = p.track(clip.track_id)
                p.add_clip(copy, tr)
                made.append(copy.id)
        self.history.transaction("Duplicate", apply)
        self.timeline.canvas.selection = set(made)
        self.after_change("Duplicated clip(s)")
        self.on_selection_changed(sorted(made))

    def trim_to_playhead(self) -> None:
        clips = self.ctx.selected_clips()
        t = self.ctx.playhead()
        if not clips:
            return

        def apply(p: Project):
            for clip in clips:
                if clip.start < t < clip.end:
                    clip.duration = t - clip.start
        self.history.transaction("Trim", apply)
        self.after_change("Trimmed to playhead")

    def trim_edge(self, edge: str) -> None:
        """Set the clip in/out point to the playhead (I / O keys)."""
        clips = self.ctx.selected_clips()
        t = self.ctx.playhead()
        if not clips:
            return
        clip = clips[0]

        def apply(p: Project):
            if edge == "in":
                delta = t - clip.start
                if 0 < delta < clip.duration:
                    if clip.kind in ("video", "audio") and not clip.freeze:
                        clip.src_in += delta * clip.speed
                    clip.duration -= delta
                    clip.start = t
            else:
                if t > clip.start:
                    clip.duration = t - clip.start
        self.history.transaction(f"Trim {edge}", apply)
        self.after_change(f"Trim {edge}")
        self.schedule_preview()

    def nudge_clip(self, direction: int) -> None:
        clips = self.ctx.selected_clips()
        if not clips:
            return
        step = direction / max(1.0, self.project.settings.fps)

        def apply(p: Project):
            for c in clips:
                c.start = max(0.0, c.start + step)
        self.history.transaction("Nudge", apply, coalesce="nudge")
        self.after_change("Nudge clip")
        self.schedule_preview()

    def select_all(self) -> None:
        ids = [c.id for c in self.project.all_clips()]
        self.timeline.canvas.selection = set(ids)
        self.timeline.canvas.update()
        self.on_selection_changed(ids)

    def deselect(self) -> None:
        self.timeline.canvas.selection = set()
        self.timeline.canvas.selected_keyframe = None
        self.timeline.canvas.update()
        self.on_selection_changed([])

    def add_keyframe(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.status("Select a clip first")
            return
        prop = self.inspector.pages["Motion"]._current_prop() \
            if "Motion" in self.inspector.pages else "scale"
        t_local = max(0.0, min(clip.duration, self.ctx.playhead() - clip.start))
        base = clip.prop_at(prop, t_local)
        self.ctx.commit("Add keyframe",
                        lambda p: clip.set_keyframe(prop, t_local, base))
        self.status(f"Keyframe {prop} at {t_local:.2f}s")

    def add_marker(self) -> None:
        t = self.ctx.playhead()

        def apply(p: Project):
            p.markers.append({"t": round(t, 4), "label": "", "color": "#ff2f6b"})
            p.markers.sort(key=lambda m: m["t"])
        self.history.transaction("Add marker", apply)
        self.after_change(f"Marker at {t:.2f}s")

    def toggle_mute(self) -> None:
        clips = self.ctx.selected_clips()
        if not clips:
            return

        def apply(p: Project):
            for c in clips:
                c.audio.muted = not c.audio.muted
        self.history.transaction("Mute", apply)
        self.after_change("Toggled mute")

    def toggle_play(self) -> None:
        self.preview.toggle()

    def reorder_track(self, direction: int) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        tracks = self.project.tracks
        idx = next((i for i, t in enumerate(tracks) if t.id == clip.track_id), -1)
        if idx < 0:
            return
        new = max(0, min(len(tracks) - 1, idx + direction))
        if new == idx:
            return

        def apply(p: Project):
            tr = p.track(clip.track_id)
            tgt = tracks[new]
            if tr is None or tgt is None or tr.kind != tgt.kind:
                return
            p.remove_clip(clip.id)
            p.add_clip(clip, tgt)
        self.history.transaction("Move track", apply)
        self.after_change("Moved clip to another track")

    def switch_track_by_number(self, idx: int) -> None:
        tracks = self.project.tracks
        if idx <= len(tracks):
            tr = tracks[idx - 1]
            ids = [c.id for c in tr.clips]
            self.timeline.canvas.selection = set(ids)
            self.timeline.canvas.update()
            self.on_selection_changed(ids)

    # -- selection ------------------------------------------------------------ #
    def on_selection_changed(self, ids: List[str]) -> None:
        clips = [self.project.clip(i) for i in ids]
        clips = [c for c in clips if c is not None]
        clip = clips[0] if clips else None
        self.inspector.show_for(clip)
        if clip is not None:
            self.timeline.canvas.update()

    # -- context menus -------------------------------------------------------- #
    def on_clip_menu(self, clip_id: str, pos: QPoint) -> None:
        if not clip_id:
            self._canvas_menu(pos)
            return
        clip = self.project.clip(clip_id)
        if clip is None:
            return
        menu = QMenu(self)
        t = self.ctx.playhead()
        acts = {
            menu.addAction(theme.icon("split", theme.TEXT_DIM, 15),
                           "Split at playhead"): self.split_at_playhead,
            menu.addAction(theme.icon("copy", theme.TEXT_DIM, 15),
                           "Duplicate"): self.duplicate_selection,
            menu.addAction(theme.icon("trim", theme.TEXT_DIM, 15),
                           "Trim to playhead"): self.trim_to_playhead,
        }
        menu.addSeparator()
        acts[menu.addAction(theme.icon("speed", theme.TEXT_DIM, 15),
                            "Speed and curves…")] = self.open_speed_curve
        acts[menu.addAction(theme.icon("reverse", theme.TEXT_DIM, 15),
                            "Reverse clip")] = lambda: self._toggle_prop("reverse")
        acts[menu.addAction(theme.icon("freeze", theme.TEXT_DIM, 15),
                            "Freeze frame here")] = self.freeze_at_playhead
        acts[menu.addAction(theme.icon("transition", theme.TEXT_DIM, 15),
                            "Transition after this clip…")] = self.open_transitions
        menu.addSeparator()
        if clip.kind == "text":
            acts[menu.addAction(theme.icon("text", theme.TEXT_DIM, 15),
                                "Edit text…")] = lambda: self.inspector.show_tab("Text")
        acts[menu.addAction(theme.icon("filter", theme.TEXT_DIM, 15),
                            "Filters and effects…")] = lambda: self.inspector.show_tab("Effects")
        acts[menu.addAction(theme.icon("crop", theme.TEXT_DIM, 15),
                            "Aspect ratio and crop")] = lambda: self.inspector.show_tab("Layout")
        acts[menu.addAction(theme.icon("keyframe_add", theme.TEXT_DIM, 15),
                            "Add keyframe")] = self.add_keyframe
        menu.addSeparator()
        acts[menu.addAction(theme.icon("eye", theme.TEXT_DIM, 15),
                            "Hide" if clip.enabled else "Show")] = \
            lambda: self._toggle_prop("enabled")
        acts[menu.addAction(theme.icon("copy", theme.TEXT_DIM, 15),
                            "Copy properties")] = lambda: self.copy_props(clip)
        acts[menu.addAction(theme.icon("file", theme.TEXT_DIM, 15),
                            "Paste properties")] = lambda: self.paste_props(clip)
        menu.addSeparator()
        acts[menu.addAction(theme.icon("delete", "#ff6b6b", 15),
                            "Delete")] = self.delete_selection

        chosen = menu.exec(pos)
        if chosen in acts:
            acts[chosen]()

    def _canvas_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        t = self.ctx.playhead()
        acts = {
            menu.addAction(theme.icon("plus", theme.TEXT_DIM, 15),
                           "Import media…"): self.import_dialog,
            menu.addAction(theme.icon("play", theme.TEXT_DIM, 15),
                           "Play / pause"): self.toggle_play,
            menu.addAction(theme.icon("marker", theme.TEXT_DIM, 15),
                           "Add marker at playhead"): self.add_marker,
            menu.addAction(theme.icon("beat", theme.TEXT_DIM, 15),
                           "Detect beats…"): self.open_beats,
        }
        menu.addSeparator()
        for kind, label_txt, icon in (("text", "Add text", "text"),
                                      ("sticker", "Add sticker", "sticker"),
                                      ("audio", "Add audio track", "music"),
                                      ("overlay", "Add overlay track", "layers")):
            def add(k=kind):
                self.add_track(k)
            acts[menu.addAction(theme.icon(icon, theme.TEXT_DIM, 15),
                                label_txt)] = add
        menu.addSeparator()
        acts[menu.addAction(theme.icon("magic", theme.TEXT_DIM, 15),
                            "Snapping: on/off")] = self._toggle_snapping
        acts[menu.addAction(theme.icon("zoom_in", theme.TEXT_DIM, 15),
                            "Zoom to fit")] = self.timeline.canvas.zoom_fit
        chosen = menu.exec(pos)
        if chosen in acts:
            acts[chosen]()

    def _toggle_snapping(self) -> None:
        c = self.timeline.canvas
        c.snapping = not c.snapping
        self.timeline.btn_snap.setChecked(c.snapping)
        self.status(f"Snapping {'on' if c.snapping else 'off'}")

    def _toggle_prop(self, prop: str) -> None:
        clips = self.ctx.selected_clips()
        if not clips:
            return

        def apply(p: Project):
            for c in clips:
                setattr(c, prop, not getattr(c, prop))
        self.history.transaction("Toggle", apply)
        self.after_change("Toggled")

    def freeze_at_playhead(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.status("Select a clip to freeze")
            return
        t = self.ctx.playhead()
        local = max(0.0, min(1.0, (t - clip.start)
                             / max(1e-6, clip.duration)))

        def apply(p: Project):
            clip.freeze = True
            clip.freeze_at = round(local, 4)
        self.history.transaction("Freeze frame", apply)
        self.after_change("Freeze frame")
        self.schedule_preview()

    _clipboard_props: Optional[Dict] = None

    def copy_props(self, clip: Clip) -> None:
        self._clipboard_props = {
            "transform": clip.transform.to_dict(),
            "filters": [f.to_dict() for f in clip.filters],
            "speed": clip.speed, "chroma": clip.chroma.to_dict(),
            "mask": clip.mask.to_dict(), "audio": clip.audio.to_dict(),
            "text": clip.text.to_dict() if clip.text else None,
            "animations": {k: [x.to_dict() for x in v]
                           for k, v in clip.animations.items()},
        }
        self.status("Copied clip properties")

    def paste_props(self, clip: Clip) -> None:
        data = self._clipboard_props
        if not data:
            self.status("Nothing copied yet")
            return

        def apply(p: Project):
            clip.transform = Transform.from_dict(data["transform"])
            clip.filters = [FilterInstance.from_dict(d)
                            for d in data["filters"]]
            clip.speed = data["speed"]
            clip.chroma = type(clip.chroma).from_dict(data["chroma"])
            clip.mask = type(clip.mask).from_dict(data["mask"])
            clip.audio = type(clip.audio).from_dict(data["audio"])
            if data.get("text") and clip.text is not None:
                clip.text = TextProps.from_dict(data["text"])
            clip.animations = {k: [Keyframe.from_dict(x) for x in v]
                               for k, v in data.get("animations", {}).items()}
        self.history.transaction("Paste properties", apply)
        self.after_change("Pasted clip properties")
        self.schedule_preview()

    def on_track_action(self, track_id: str, action: str) -> None:
        if action == "menu":
            menu = QMenu(self)
            acts: Dict[QAction, Callable[[], None]] = {}
            tr = self.project.track(track_id)

            def add(text: str, fn: Callable[[], None]) -> None:
                acts[menu.addAction(text)] = fn

            add("Rename track…", lambda: self.rename_track(track_id))
            add("Duplicate track",
                lambda: self.add_track(tr.kind if tr else "overlay"))
            menu.addSeparator()
            add("Add video track", lambda: self.add_track("video"))
            add("Add overlay track", lambda: self.add_track("overlay"))
            add("Add text track", lambda: self.add_track("text"))
            add("Add audio track", lambda: self.add_track("audio"))
            menu.addSeparator()
            add("Delete track", lambda: self.remove_track(track_id))
            chosen = menu.exec(self.cursor().pos())
            if chosen in acts:
                acts[chosen]()

    def rename_track(self, track_id: str) -> None:
        from PySide6.QtWidgets import QInputDialog
        tr = self.project.track(track_id)
        if tr is None:
            return
        name, ok = QInputDialog.getText(self, "Rename track", "Name:",
                                        text=tr.name or "")
        if ok and name.strip():
            self.ctx.commit("Rename track",
                            lambda p: setattr(tr, "name", name.strip()))

    def add_track(self, kind: str) -> None:
        self.ctx.commit(f"Add {kind} track",
                        lambda p: p.add_track(kind))
        self.timeline.refresh()

    def remove_track(self, track_id: str) -> None:
        tr = self.project.track(track_id)
        if tr is None:
            return
        if len(self.project.tracks) <= 1:
            self.status("A project needs at least one track")
            return
        self.ctx.commit("Delete track", lambda p: p.remove_track(track_id))

    def on_transition_clicked(self, clip_id: str, edge: str) -> None:
        self.timeline.canvas.selection = {clip_id}
        self.on_selection_changed([clip_id])
        self.open_transitions(edge)

    # -- media ---------------------------------------------------------------- #
    def import_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Import media", str(Path.home()),
            "Media files (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mp3 *.wav "
            "*.aac *.flac *.ogg *.opus *.m4a *.png *.jpg *.jpeg *.webp "
            "*.bmp *.gif);;All files (*)")
        if files:
            self.import_paths(files)

    def import_paths(self, paths: List[str]) -> None:
        files = collect_media_files(paths)
        if not files:
            self.status("No supported media in that selection")
            return
        added: List[MediaAsset] = []
        failed: List[str] = []
        progress = QProgressDialog("Importing media…", "Cancel", 0, len(files),
                                   self)
        progress.setWindowTitle("Import")
        progress.setMinimumDuration(300)
        for i, path in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"Reading {Path(path).name}")
            QApplication.processEvents()
            info = probe.probe(path)
            if info.duration <= 0 and info.width <= 0 and info.kind != "image":
                failed.append(Path(path).name)
                continue
            asset = MediaAsset.from_info(info, path)
            if self.project.asset_by_path(path) is not None:
                continue
            self.project.assets.append(asset)
            added.append(asset)
        progress.setValue(len(files))
        if added:
            self.history.mark_saved() if False else None
            self.media.set_project(self.project)
            self.status(f"Imported {len(added)} file"
                        f"{'s' if len(added) != 1 else ''}"
                        + (f" · {len(failed)} unreadable" if failed else ""))
            self.after_change("")
        elif failed:
            self.status(f"Could not read {len(failed)} file(s)")
        else:
            self.status("Those files are already in the project")

    def add_asset_to_timeline(self, asset_id: str, at: Optional[float] = None) -> None:
        asset = self.project.asset(asset_id)
        if asset is None:
            return
        t = self.ctx.playhead() if at is None else at
        if asset.kind == "audio":
            track = self.project.ensure_track_for("audio")
        elif asset.kind == "image":
            track = self.project.video_tracks()[0] if self.project.video_tracks() \
                else self.project.ensure_track_for("video")
        else:
            track = self.project.video_tracks()[0] if self.project.video_tracks() \
                else self.project.ensure_track_for("video")
        # place on the first free spot on that track from the playhead
        start = t
        for c in track.clips:
            if c.start <= start < c.end:
                start = c.end
        dur = asset.duration if asset.duration > 0.05 else 4.0
        if asset.kind == "image":
            dur = 3.0
        clip = Clip(kind="image" if asset.kind == "image" else
                    ("audio" if asset.kind == "audio" else "video"),
                    track_id=track.id, start=start, duration=dur)
        clip.path = asset.path
        clip.asset_id = asset.id
        clip.name = asset.name
        clip.meta["has_audio"] = asset.has_audio
        clip.meta["src_w"] = asset.width
        clip.meta["src_h"] = asset.height
        clip.meta["src_duration"] = asset.duration
        if asset.kind == "video" and (asset.width < self.project.settings.width * 0.6
                                      or asset.height < self.project.settings.height * 0.6):
            clip.transform.fit = "cover"
        self.history.transaction("Add clip",
                                 lambda p: p.add_clip(clip, track))
        self.timeline.canvas.selection = {clip.id}
        self.after_change(f"Added {asset.name}")
        self.on_selection_changed([clip.id])
        self.schedule_preview()

    def remove_asset(self, asset_id: str) -> None:
        asset = self.project.asset(asset_id)
        if asset is None:
            return
        used = [c for c in self.project.all_clips() if c.asset_id == asset_id]
        if used:
            ok = QMessageBox.question(
                self, "Remove media",
                f"{asset.name} is used by {len(used)} clip(s).\n"
                "Remove the media and those clips?")
            if ok != QMessageBox.Yes:
                return

        def apply(p: Project):
            p.assets = [a for a in p.assets if a.id != asset_id]
            for c in list(p.all_clips()):
                if c.asset_id == asset_id:
                    p.remove_clip(c.id)
        self.history.transaction("Remove media", apply)
        self.media.set_project(self.project)
        self.after_change("Removed media")

    def replace_with_asset(self, asset_id: str) -> None:
        self._replace_asset = asset_id
        self.status("Select a clip, then use Clip ▸ Replace media to swap it")

    # -- dialogs -------------------------------------------------------------- #
    def open_transitions(self, edge: str = "") -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.status("Select a clip first")
            return
        if edge:
            clip.meta["_transition_edge"] = edge
        dlg = TransitionDialog(self, clip, self.ctx)
        dlg.exec()
        self.after_change("")
        self.schedule_preview()

    def open_stickers(self) -> None:
        dlg = StickerDialog(self, self.ctx)
        dlg.exec()
        self.after_change("")
        self.schedule_preview()

    def open_audio_library(self) -> None:
        dlg = AudioLibraryDialog(self, self.ctx)
        dlg.exec()
        self.after_change("")

    def open_beats(self) -> None:
        dlg = BeatsDialog(self, self.ctx)
        dlg.exec()
        self.after_change("")

    def open_captions(self) -> None:
        dlg = CaptionsDialog(self, self.ctx)
        dlg.exec()
        self.after_change("")

    def open_presets(self) -> None:
        dlg = PresetsDialog(self, self.ctx)
        dlg.exec()
        self.after_change("")

    def open_speed_curve(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        dlg = SpeedCurveDialog(self, clip, self.ctx)
        dlg.exec()
        self.after_change("")
        self.schedule_preview()

    def open_settings(self) -> None:
        dlg = ShortcutsDialog(self)
        dlg.exec()

    def open_shortcuts(self) -> None:
        dlg = ShortcutsDialog(self)
        dlg.exec()

    # -- text ---------------------------------------------------------------- #
    def add_text_clip(self) -> None:
        track = self.project.ensure_track_for("text")
        t = self.ctx.playhead()
        start = t
        for c in track.clips:
            if c.start <= start < c.end:
                start = c.end
        clip = Clip(kind="text", track_id=track.id, start=start, duration=2.5)
        # VN-style big POV caption: the glyphs span roughly 85% of the canvas
        # width, so the size follows the canvas instead of a fixed number.
        width_frac = 0.85
        height = max(1, self.project.settings.height)
        size = max(24.0, width_frac * height * 9.0 / 16.0 * 0.51)
        clip.text = TextProps(text="POV: YOU ARE HIM", font="Bebas Neue",
                              size=size, color="#ffffff", outline_width=6.0,
                              outline_color="#000000", shadow=2.0)
        clip.transform.y = 0.18
        from ..core.ass import apply_text_animation
        apply_text_animation(clip, "pop_in", clip.duration, 0.35)
        self.history.transaction("Add text", lambda p: p.add_clip(clip, track))
        self.timeline.canvas.selection = {clip.id}
        self.after_change("Added text")
        self.inspector.show_for(clip)
        self.inspector.show_tab("Text")
        self.schedule_preview()

    # -- project io ----------------------------------------------------------- #
    def _confirm_discard(self) -> bool:
        if not self.history.is_dirty:
            return True
        res = QMessageBox.question(
            self, "Unsaved changes",
            f"Save changes to “{self.project.name}” before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if res == QMessageBox.Save:
            return self.save_project()
        return res == QMessageBox.Discard

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        dlg = PresetsDialog(self, self.ctx, new_project=True)
        project = dlg.exec_new()
        if project is None:
            return
        self.load_project(project, None)
        self.status("New project created")

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(pio.projects_dir()),
            "VN Studio project (*.vnp);;All files (*)")
        if not path:
            return
        try:
            project = pio.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open project", str(exc))
            return
        pio.add_recent(path)
        self.load_project(project, path)
        self.status(f"Opened {Path(path).name}")

    def load_project(self, project: Project, path: Optional[str]) -> None:
        self.preview.shutdown()
        self.project = project
        self.history = History(project)
        self.ctx.project = project
        self.ctx.history = self.history
        self.current_path = path
        self.timeline.set_project(project)
        self.preview.set_project(project)
        self.media.set_project(project)
        self.inspector.show_for(None)
        self.timeline.canvas.selection = set()
        self.after_change("")
        QTimer.singleShot(80, lambda: self.seek(0.0))

    def save_project(self) -> bool:
        if not self.current_path:
            return self.save_project_as()
        try:
            pio.save(self.project, self.current_path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return False
        pio.add_recent(self.current_path)
        self.history.mark_saved()
        self.after_change("")
        self.status(f"Saved {Path(self.current_path).name}")
        return True

    def save_project_as(self) -> bool:
        default = str(pio.projects_dir() /
                      f"{self.project.name.replace('/', '-')}.vnp")
        path, _ = QFileDialog.getSaveFileName(self, "Save project", default,
                                              "VN Studio project (*.vnp)")
        if not path:
            return False
        if not path.endswith(".vnp"):
            path += ".vnp"
        self.current_path = path
        return self.save_project()

    def autosave(self) -> None:
        if not self.history.is_dirty:
            return
        try:
            pio.autosave(self.project, self.current_path)
            self.status("Autosaved", 2000)
        except Exception:
            pass

    def undo(self) -> None:
        if self.history.undo():
            self.timeline.canvas.selection &= {
                c.id for c in self.project.all_clips()}
            self.after_change("Undo")
            self.inspector.show_for(self.ctx.selected_clip())
            self.schedule_preview()

    def redo(self) -> None:
        if self.history.redo():
            self.timeline.canvas.selection &= {
                c.id for c in self.project.all_clips()}
            self.after_change("Redo")
            self.inspector.show_for(self.ctx.selected_clip())
            self.schedule_preview()

    # -- export --------------------------------------------------------------- #
    def export_video(self) -> None:
        if not ff.TOOLCHAIN.ok:
            self._warn_missing_ffmpeg()
            return
        if not self.project.all_clips():
            QMessageBox.information(self, "Nothing to export",
                                    "Add some media to the timeline first.")
            return
        dlg = ExportDialog(self, self.project)
        dlg.exec()

    # -- events --------------------------------------------------------------- #
    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev) -> None:
        if not self._confirm_discard():
            ev.ignore()
            return
        try:
            self.preview.shutdown()
            self.media.shutdown()
        except Exception:
            pass
        ev.accept()
