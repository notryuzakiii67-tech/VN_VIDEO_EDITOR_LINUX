"""Editor dialogs: export, transitions, stickers, captions, audio library,
beat detection, templates and shortcuts.

Each dialog edits the live project through the shared ``EditorContext`` so that
changes are undoable, exactly like edits made in the timeline.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import (QPoint, QRect, QRectF, QSize, Qt, QThread, QTimer,
                            Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QImage,
                           QKeySequence, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QDoubleSpinBox, QStackedWidget, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import audio as audio_mod
from ..core import effects as fx_mod
from ..core import ffmpeg as ff
from ..core import probe as probe_mod
from ..core import model
from ..core import project as pio
from ..core import render as render_mod
from ..core import sfx as sfx_mod
from ..core import transitions as trans_mod
from ..core.ass import FONT_FILES, apply_text_animation
from ..core.model import (Clip, MediaAsset, Project, RESOLUTION_PRESETS,
                          SpeedCurve, TextProps, Transition)
from . import theme
from .widgets import (ChipGroup, ColorButton, IconButton, PresetStrip,
                      RowField, Section, SliderField, SwatchGrid, label,
                      hline)

#: The preset table lives in the core so the templates panel and the tools that
#: verify the presets cannot drift apart.
TEXT_PRESETS = list(model.TEXT_PRESETS)


def dialog_frame_style() -> str:
    return (f"QDialog {{ background: {theme.BG_ROOT}; }}"
            f"QWidget {{ color: {theme.TEXT}; }}")


class BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 520,
                 height: int = 520) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(dialog_frame_style())
        self.resize(width, height)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(14, 12, 14, 12)
        self.root.setSpacing(10)

    def add_buttons(self, ok_text: str = "Apply",
                    cancel_text: str = "Close") -> QDialogButtonBox:
        box = QDialogButtonBox()
        self.buttons = box
        self.ok_button = box.addButton(ok_text, QDialogButtonBox.AcceptRole)
        self.ok_button.setProperty("class", "primary")
        box.addButton(cancel_text, QDialogButtonBox.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.root.addWidget(box)
        return box

    def header(self, title: str, subtitle: str = "") -> None:
        self.root.addWidget(label(title, "h2", 15, 700))
        if subtitle:
            sub = label(subtitle, "muted", 11)
            sub.setWordWrap(True)
            self.root.addWidget(sub)


# --------------------------------------------------------------------------- #
#  Export
# --------------------------------------------------------------------------- #

class ExportWorker(QThread):
    progress = Signal(float, str)
    finished = Signal(bool, str, object)

    def __init__(self, project: Project, opts, out_path: str, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.opts = opts
        self.out_path = out_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = render_mod.render(
                self.project, self.out_path, self.opts,
                progress=lambda f, m: self.progress.emit(f, m),
                cancel=lambda: self._cancel)
        except Exception as exc:                     # pragma: no cover
            self.finished.emit(False, f"{type(exc).__name__}: {exc}", None)
            return
        self.finished.emit(result.ok, result.error or "", result)


class ExportDialog(BaseDialog):
    def __init__(self, parent, project: Project) -> None:
        super().__init__(parent, "Export video", 620, 660)
        self.project = project
        self.worker: Optional[ExportWorker] = None
        self._build()

    def _build(self) -> None:
        self.header("Export video",
                    "Renders with the same engine as the preview, so what you "
                    "see is what you get.")
        tabs = QTabWidget()
        self.root.addWidget(tabs, 1)

        # ---- video tab ---- #
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        box = QVBoxLayout(page)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(9)

        self.preset_strip = PresetStrip(
            [(k, k, c) for k, c, _w, _h, _b in EXPORT_PRESETS],
            card_w=86, card_h=48, first_text="Custom")
        self.preset_strip.picked.connect(self._preset)
        box.addWidget(label("Presets", "sectionTitle"))
        box.addWidget(self.preset_strip)

        self.res_combo = QComboBox()
        for name, w, h in RESOLUTION_PRESETS:
            self.res_combo.addItem(f"{name}  ·  {w}×{h}", (w, h))
        self.res_combo.addItem("Custom…", None)
        self.res_combo.currentIndexChanged.connect(self._resolution)
        box.addWidget(RowField("Resolution", self.res_combo))

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.w_spin = QSpinBox()
        self.w_spin.setRange(64, 7680)
        self.w_spin.setSingleStep(2)
        self.h_spin = QSpinBox()
        self.h_spin.setRange(64, 7680)
        self.h_spin.setSingleStep(2)
        rl.addWidget(QLabel("W"))
        rl.addWidget(self.w_spin, 1)
        rl.addWidget(QLabel("H"))
        rl.addWidget(self.h_spin, 1)
        box.addWidget(RowField("Size", row))

        self.fps_combo = QComboBox()
        for f in (24, 25, 30, 50, 60):
            self.fps_combo.addItem(f"{f} fps", float(f))
        box.addWidget(RowField("Frame rate", self.fps_combo))

        self.quality = SliderField(0, 51, 20, decimals=0, factor=1.0,
                                   default=20)
        self.quality.setToolTip("Lower is better quality. 18–23 is a good range.")
        box.addWidget(RowField("Quality (CRF)", self.quality))

        self.speed_combo = QComboBox()
        for name in ("ultrafast", "superfast", "veryfast", "faster", "fast",
                     "medium", "slow", "veryslow"):
            self.speed_combo.addItem(name, name)
        self.speed_combo.setToolTip("Faster encoding trades file size for time.")
        box.addWidget(RowField("Encoder speed", self.speed_combo))

        self.codec_combo = QComboBox()
        for key, text in (("libx264", "H.264 (best compatibility)"),
                          ("libx265", "H.265 / HEVC (smaller)"),
                          ("mpeg4", "MPEG-4 (legacy devices)")):
            self.codec_combo.addItem(text, key)
        box.addWidget(RowField("Codec", self.codec_combo))

        row2 = QWidget()
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        self.audio_cb = QCheckBox("Include audio")
        self.audio_cb.setChecked(True)
        self.faststart_cb = QCheckBox("Web-optimised (faststart)")
        self.faststart_cb.setChecked(True)
        r2.addWidget(self.audio_cb)
        r2.addWidget(self.faststart_cb)
        box.addWidget(row2)

        self.range_combo = QComboBox()
        self.range_combo.addItem("Whole timeline", "all")
        self.range_combo.addItem("Selection only", "selection")
        box.addWidget(RowField("Range", self.range_combo))
        box.addStretch(1)
        tabs.addTab(scroll, "Video")

        # ---- summary tab ---- #
        info = QWidget()
        il = QVBoxLayout(info)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        il.addWidget(self.summary)
        self.size_estimate = QLabel("")
        self.size_estimate.setStyleSheet(f"color:{theme.TEXT_MUTED};")
        il.addWidget(self.size_estimate)
        il.addStretch(1)
        tabs.addTab(info, "Summary")

        self.out_edit = QLineEdit(str(self._default_output()))
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)
        rrow = QWidget()
        rrl = QHBoxLayout(rrow)
        rrl.setContentsMargins(0, 0, 0, 0)
        rrl.addWidget(self.out_edit, 1)
        rrl.addWidget(browse)
        self.root.addWidget(RowField("Save to", rrow, wide=True))

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.root.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color:{theme.TEXT_DIM};")
        self.root.addWidget(self.progress_label)

        self.buttons = self.add_buttons("Export", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self.start_export)
        self._preset("1080p Vertical")
        self._update_summary()

    def _default_output(self) -> Path:
        base = Path.home() / "Videos"
        if not base.exists():
            base = Path.home()
        stem = re.sub(r"[^\w\- ]+", "", self.project.name).strip() or "export"
        return base / f"{stem}.mp4"

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to", self.out_edit.text(), "MP4 video (*.mp4)")
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.out_edit.setText(path)
            self._update_summary()

    def _preset(self, name: str) -> None:
        for key, _c, w, h, bitrate in EXPORT_PRESETS:
            if key != name:
                continue
            idx = self.res_combo.findData((w, h))
            if idx < 0:
                self.res_combo.setCurrentIndex(self.res_combo.count() - 1)
                self.w_spin.setValue(w)
                self.h_spin.setValue(h)
            else:
                self.res_combo.setCurrentIndex(idx)
            idx = self.fps_combo.findData(float(self.project.settings.fps))
            self.quality.set_value({1: 22, 2: 24, 3: 20, 4: 18}.get(
                _grade_of(key), 21))
            self._update_summary()
            return

    def _resolution(self) -> None:
        data = self.res_combo.currentData()
        if isinstance(data, tuple):
            self.w_spin.setValue(data[0])
            self.h_spin.setValue(data[1])
        self._update_summary()

    def _update_summary(self) -> None:
        dur = self.project.duration
        w, h = self.w_spin.value(), self.h_spin.value()
        fps = self.fps_combo.currentData() or self.project.settings.fps
        clips = len(self.project.all_clips())
        beats = len(self.project.beats)
        self.summary.setText(
            f"<b>Output</b> {w}×{h} @ {fps:g}fps, H.264/AAC MP4<br>"
            f"<b>Duration</b> {dur:.2f} s  ·  {int(dur * fps)} frames<br>"
            f"<b>Content</b> {clips} clip(s), {beats} beat marker(s)<br>"
            f"<b>File</b> {self.out_edit.text()}")
        est = (w * h * fps * 0.07 * max(0.2, dur)) / 1000.0
        self.size_estimate.setText(
            f"Estimated size ≈ {est:.1f} MB (CRF "
            f"{int(self.quality.value())})")

    def start_export(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "Export", "Choose a destination file.")
            return
        if Path(out).suffix.lower() != ".mp4":
            out += ".mp4"
            self.out_edit.setText(out)
        if Path(out).exists():
            res = QMessageBox.question(
                self, "Overwrite?",
                f"{Path(out).name} already exists. Replace it?")
            if res != QMessageBox.Yes:
                return
        opts = render_mod.options_from_settings(self.project)
        opts.width = self.w_spin.value()
        opts.height = self.h_spin.value()
        opts.fps = float(self.fps_combo.currentData()
                         or self.project.settings.fps)
        opts.crf = int(self.quality.value())
        opts.preset = self.speed_combo.currentData() or "medium"
        opts.codec = self.codec_combo.currentData() or "libx264"
        opts.audio = self.audio_cb.isChecked()
        opts.faststart = self.faststart_cb.isChecked()
        opts.preview = False
        opts.pixel_format = "yuv420p"
        if opts.codec == "libx265":
            opts.pixel_format = "yuv420p"

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.ok_button.setText("Cancel export")
        self.worker = ExportWorker(self.project, opts, out, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, frac: float, message: str) -> None:
        self.progress.setValue(int(frac * 100))
        self.progress_label.setText(message)

    def _on_finished(self, ok: bool, error: str, result) -> None:
        self.ok_button.setText("Export")
        self.progress.setVisible(False)
        self.progress_label.setText("")
        if ok and result is not None:
            size = os.path.getsize(result.output) if os.path.exists(result.output) else 0
            self.progress_label.setText(
                f"Done in {result.elapsed:.1f}s · {result.frames} frames · "
                f"{size / 1e6:.1f} MB")
            QMessageBox.information(
                self, "Export complete",
                f"Saved to:\n{result.output}\n\n"
                f"{result.frames} frames · {result.elapsed:.1f} s · "
                f"{size / 1e6:.1f} MB")
        else:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Export failed")
            box.setText("FFmpeg could not finish this export.")
            box.setDetailedText(error or "Unknown error")
            box.exec()

    def closeEvent(self, ev) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2500)
        ev.accept()


def _grade_of(key: str) -> int:
    return {"720p": 1, "1080p": 2, "4K": 3, "Vertical 1080p": 2,
            "Square": 2, "GIF": 1}.get(key, 2)


EXPORT_PRESETS = [
    ("1080p", "#2f7fd4", 1920, 1080, 12),
    ("720p", "#38a169", 1280, 720, 6),
    ("4K", "#a855f7", 3840, 2160, 40),
    ("Vertical 1080p", "#ff2f6b", 1080, 1920, 10),
    ("Square", "#f59e0b", 1080, 1080, 8),
]


# --------------------------------------------------------------------------- #
#  Transitions
# --------------------------------------------------------------------------- #

class TransitionDialog(BaseDialog):
    def __init__(self, parent, clip: Clip, ctx) -> None:
        super().__init__(parent, "Transitions", 640, 620)
        self.clip = clip
        self.ctx = ctx
        self.edge = clip.meta.pop("_transition_edge", "") or "out"
        self.all = trans_mod.TRANSITIONS
        self.keys = list(self.all.keys())
        self._build()

    def _build(self) -> None:
        self.header("Transitions",
                    "Applied between clips on the same track. The renderer "
                    "reads into the media past the clip edge so the cut never "
                    "moves.")
        edge_row = QWidget()
        el = QHBoxLayout(edge_row)
        el.setContentsMargins(0, 0, 0, 0)
        self.edge_chips = ChipGroup(["After this clip", "Before this clip"],
                                   0 if self.edge == "out" else 1, small=True)
        self.edge_chips.changed.connect(self._edge_changed)
        el.addWidget(self.edge_chips)
        el.addStretch(1)
        self.root.addWidget(edge_row)

        search = QLineEdit()
        search.setPlaceholderText("Search transitions…")
        search.textChanged.connect(self._filter)
        self.root.addWidget(search)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(84, 48))
        self.list.setGridSize(QSize(100, 84))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.itemClicked.connect(lambda _i: self._preview_hint())
        self.list.itemDoubleClicked.connect(lambda _i: self.apply())
        self.root.addWidget(self.list, 1)
        self._populate()

        self.duration = SliderField(0.1, 3.0, 0.6, decimals=2, suffix="s",
                                    default=0.6)
        self.root.addWidget(RowField("Duration", self.duration))

        self.wave_cb = QCheckBox("Crossfade the audio too")
        self.wave_cb.setChecked(True)
        self.root.addWidget(self.wave_cb)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        remove = QPushButton("Remove transition")
        remove.clicked.connect(self.remove)
        rl.addWidget(remove)
        rl.addStretch(1)
        self.root.addWidget(row)
        self.add_buttons("Apply", "Close")

    def _edge_changed(self, idx: int, _t: str) -> None:
        self.edge = "out" if idx == 0 else "in"
        self._populate()

    def _populate(self) -> None:
        self.list.clear()
        existing = self._existing()
        for key in self.keys:
            spec = self.all[key]
            item = QListWidgetItem(spec.label if hasattr(spec, "label")
                                   else str(key))
            item.setData(Qt.UserRole, key)
            item.setIcon(_transition_icon(key))
            if existing is not None and existing.kind == key:
                item.setBackground(QColor(theme.BG_ACTIVE))
                self.list.setCurrentItem(item)
            self.list.addItem(item)
        if existing is not None:
            self.duration.set_value(existing.duration)

    def _existing(self) -> Optional[Transition]:
        return self.clip.transition_out if self.edge == "out" \
            else self.clip.transition_in

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            key = item.data(Qt.UserRole)
            spec = self.all[key]
            label_txt = getattr(spec, "label", str(key))
            keywords = getattr(spec, "keywords", "")
            haystack = f"{label_txt} {key} {keywords}".lower()
            item.setHidden(bool(text) and text not in haystack)

    def _preview_hint(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        key = item.data(Qt.UserRole)
        spec = self.all[key]
        desc = getattr(spec, "description", "") or ""
        self.ctx.status(f"{getattr(spec, 'label', key)} — {desc}")

    def apply(self) -> None:
        item = self.list.currentItem()
        if item is None:
            self._preview_hint()
            return
        key = item.data(Qt.UserRole)
        dur = self.duration.value()
        tr = Transition(key, dur, self.edge)
        fade_audio = self.wave_cb.isChecked()

        def mutate(p: Project):
            if self.edge == "out":
                self.clip.transition_out = tr
            else:
                self.clip.transition_in = tr
            tr.audio = fade_audio
        self.ctx.commit(f"Transition {key}", mutate)
        self.ctx.status(f"Applied {key} ({dur:.1f}s)")

    def remove(self) -> None:
        def mutate(p: Project):
            if self.edge == "out":
                self.clip.transition_out = None
            else:
                self.clip.transition_in = None
        self.ctx.commit("Remove transition", mutate)
        self._populate()


def _transition_icon(key: str) -> QIcon:
    spec = trans_mod.TRANSITIONS.get(key)
    group = getattr(spec, "group", "fade") if spec else "fade"
    pm = QPixmap(84, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    a = QColor(theme.CLIP_VIDEO)
    b = QColor(theme.CLIP_TEXT)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0f1216"))
    p.drawRoundedRect(QRectF(0.5, 0.5, 83, 47), 5, 5)
    for i in range(28):
        f = i / 27.0
        if group == "wipe":
            col = a if f < 0.5 else b
            p.setBrush(col)
            p.drawRect(QRectF(3 + i * 2.8, 3, 2.9, 42))
        elif group == "circle":
            p.setBrush(a)
            p.drawRect(QRectF(3, 3, 78, 42))
            r = 21 * min(1.0, (1 - abs(f - 0.5) * 2) + 0.02)
            p.setBrush(b)
            p.drawEllipse(QRectF(42 - r, 24 - r, r * 2, r * 2))
        elif group == "slide":
            p.setBrush(a)
            p.drawRect(QRectF(3, 3, 78, 42))
            off = (0.5 - f) * 78
            p.setBrush(b)
            p.drawRect(QRectF(42 + off, 3, 39, 42))
        elif group == "spin":
            p.setBrush(a)
            p.drawRect(QRectF(3, 3, 78, 42))
            p.setBrush(b)
            p.save()
            p.translate(42, 24)
            p.rotate(f * 180)
            p.drawRect(QRectF(-39, -10, 78, 20))
            p.restore()
        else:
            c = QColor(int(a.red() * (1 - f) + b.red() * f),
                       int(a.green() * (1 - f) + b.green() * f),
                       int(a.blue() * (1 - f) + b.blue() * f))
            p.setBrush(c)
            p.drawRect(QRectF(3 + i * 2.8, 3, 2.9, 42))
    p.end()
    return QIcon(pm)


# --------------------------------------------------------------------------- #
#  Stickers
# --------------------------------------------------------------------------- #

class StickerDialog(BaseDialog):
    def __init__(self, parent, ctx) -> None:
        super().__init__(parent, "Stickers", 680, 600)
        from ..core.stickers import STICKER_CATALOG, STICKER_GROUPS
        self.ctx = ctx
        self.catalog = STICKER_CATALOG
        self.groups = ["All"] + [g for g in STICKER_GROUPS
                                 if any(v[1] == g for v in
                                        STICKER_CATALOG.values())]
        self.current_group = "All"
        self._build()

    def _build(self) -> None:
        self.header("Stickers",
                    "Original vector art drawn at export resolution, so it "
                    "stays sharp at 4K.")
        self.group_chips = ChipGroup(self.groups, 0, small=True)
        self.group_chips.changed.connect(self._group)
        self.root.addWidget(self.group_chips)

        search = QLineEdit()
        search.setPlaceholderText("Search stickers…")
        search.textChanged.connect(self._filter)
        self.root.addWidget(search)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.host = QWidget()
        self.host.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.host)
        self.grid.setSpacing(7)
        self.scroll.setWidget(self.host)
        self.root.addWidget(self.scroll, 1)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(label("Size", size=11))
        self.size = SliderField(0.03, 0.6, 0.16, decimals=2, default=0.16)
        rl.addWidget(self.size, 1)
        self.color = ColorButton("#ffffff")
        self.color.setToolTip("Override the sticker colour")
        rl.addWidget(self.color)
        self.use_default = QCheckBox("Default colours")
        self.use_default.setChecked(True)
        rl.addWidget(self.use_default)
        self.root.addWidget(row)

        anim_row = QWidget()
        al = QHBoxLayout(anim_row)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(label("Animation", size=11))
        self.anim = QComboBox()
        for name in ("pop_in", "fade", "spin", "slide_left", "slide_right",
                     "slide_up", "slide_down", "pulse", "none"):
            self.anim.addItem(name.replace("_", " ").title(), name)
        al.addWidget(self.anim, 1)
        self.root.addWidget(anim_row)

        self.add_buttons("Add sticker", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self._add)
        self._populate()

    def _group(self, idx: int, text: str) -> None:
        self.current_group = text
        self._populate()

    def _filter(self, text: str) -> None:
        self._search = text.strip().lower()
        self._populate()

    def _populate(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        search = getattr(self, "_search", "")
        keys = []
        for key, (label_txt, group, color, _d) in self.catalog.items():
            if self.current_group != "All" and group != self.current_group:
                continue
            if search and search not in f"{label_txt} {key} {group}".lower():
                continue
            keys.append(key)
        cols = 6
        for i, key in enumerate(keys):
            btn = _StickerButton(key, self.catalog[key], self)
            btn.clicked.connect(lambda _=False, k=key: self._add(k))
            self.grid.addWidget(btn, i // cols, i % cols)
        if not keys:
            self.grid.addWidget(label("No stickers match that search.",
                                      "muted"), 0, 0)
        # A scroll area would otherwise squeeze the tiles; pin the host size so
        # the dialog scrolls instead.
        rows = max(1, (len(keys) + cols - 1) // cols)
        cell_w = _StickerButton.WIDTH + self.grid.spacing()
        cell_h = _StickerButton.HEIGHT + self.grid.spacing()
        self.host.setMinimumSize(min(cols, max(1, len(keys))) * cell_w,
                                 rows * cell_h)

    def _add(self, key: Optional[str] = None) -> None:
        if not isinstance(key, str):
            btn = self.sender()
            key = getattr(btn, "_key", "star")
        track = self.ctx.project.ensure_track_for("sticker")
        t = self.ctx.playhead()
        start = t
        for c in track.clips:
            if c.start <= start < c.end:
                start = c.end
        clip = Clip(kind="sticker", track_id=track.id, start=start,
                    duration=1.8)
        clip.meta["sticker"] = key
        clip.meta["sticker_size"] = self.size.value()
        clip.meta["sticker_anim"] = self.anim.currentData()
        if not self.use_default.isChecked():
            clip.meta["sticker_color"] = self.color.color()
        clip.transform.y = 0.5
        self.ctx.commit(f"Add {key}", lambda p: p.add_clip(clip, track))
        self.ctx.window.timeline.canvas.selection = {clip.id}
        self.ctx.status(f"Added sticker “{key}”")
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()


class _StickerButton(QPushButton):
    """One sticker tile: the vector glyph on top, its name underneath."""

    WIDTH = 98
    HEIGHT = 92
    GLYPH = 46
    LABEL_H = 16

    def __init__(self, key: str, spec, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        label_txt, group, colour, _d = spec
        self._label = label_txt
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setToolTip(f"{label_txt}  ·  {group}")
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)
        self.setFlat(True)
        self.hover = False
        self._pix = _sticker_pixmap(key, self.GLYPH)

    def enterEvent(self, ev) -> None:
        self.hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self.hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.ACCENT), 1.4) if self.hover
                 else QPen(QColor(theme.BORDER_SOFT), 1))
        p.setBrush(QColor(theme.BG_PANEL_ALT if self.hover else theme.BG_PANEL))
        p.drawRoundedRect(r, 9, 9)
        # glyph, centred in the upper area
        gx = int(r.center().x() - self.GLYPH / 2)
        gy = int(r.y() + (r.height() - self.LABEL_H - self.GLYPH) / 2) + 3
        p.drawPixmap(gx, gy, self._pix)
        # label
        p.setPen(QColor(theme.TEXT if self.hover else theme.TEXT_DIM))
        p.setFont(theme.ui_font(9, 600 if self.hover else 400))
        fm = QFontMetrics(p.font())
        text = fm.elidedText(self._label, Qt.ElideRight,
                             int(r.width()) - 8)
        p.drawText(QRectF(r.x() + 4, r.bottom() - self.LABEL_H - 2,
                          r.width() - 8, self.LABEL_H),
                   Qt.AlignHCenter | Qt.AlignVCenter, text)
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, self.HEIGHT)


def _sticker_pixmap(key: str, size: int) -> QPixmap:
    """Render a sticker to a pixmap with the same geometry code the exporter
    uses (converted to Qt paths so the preview matches the render)."""
    from PySide6.QtGui import QPainterPath
    from ..core.stickers import sticker_drawing, STICKER_CATALOG
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    spec = STICKER_CATALOG.get(key)
    color = QColor(spec[2] if spec else "#ffffff")
    path, detail, detail_color = sticker_drawing(key, 0.0, 0.0, size * 0.62)
    qpath = _ass_to_qpath(path)
    p.translate(size / 2, size / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(qpath)
    if detail and detail_color:
        qd = _ass_to_qpath(detail)
        pen = QPen(QColor(detail_color))
        pen.setWidthF(max(1.0, size * 0.03))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(qd)
    p.end()
    return pm


def _ass_to_qpath(drawing: str):
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    tokens = drawing.replace("m", " m ").replace("l", " l ").replace(
        "b", " b ").replace("c", " c ").split()
    i = 0
    cur = (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        try:
            if t == "m":
                cur = (float(tokens[i + 1]), float(tokens[i + 2]))
                path.moveTo(*cur)
                i += 3
            elif t == "l":
                cur = (float(tokens[i + 1]), float(tokens[i + 2]))
                path.lineTo(*cur)
                i += 3
            elif t == "b":
                c1 = float(tokens[i + 1]), float(tokens[i + 2])
                c2 = float(tokens[i + 3]), float(tokens[i + 4])
                end = float(tokens[i + 5]), float(tokens[i + 6])
                path.cubicTo(c1[0], c1[1], c2[0], c2[1], end[0], end[1])
                cur = end
                i += 7
            elif t == "c":
                path.closeSubpath()
                i += 1
            else:
                i += 1
        except (IndexError, ValueError):
            break
    return path


# --------------------------------------------------------------------------- #
#  Audio library / sound effects
# --------------------------------------------------------------------------- #

class AudioLibraryDialog(BaseDialog):
    def __init__(self, parent, ctx) -> None:
        super().__init__(parent, "Audio", 660, 620)
        self.ctx = ctx
        self.paths: Dict[str, Path] = {}
        self._build()

    def _build(self) -> None:
        self.header("Audio library",
                    "Generated locally with synthesis — no downloads, no "
                    "licensing questions.")
        self.status_label = label("Preparing sound effects…", "muted", 11)
        self.root.addWidget(self.status_label)

        cats = sorted({s.category for s in sfx_mod.CATALOG.values()})
        self.cat_chips = ChipGroup(["All", "Meme", "Impact", "Voice"] +
                                   [c for c in cats
                                    if c not in ("Meme", "Impact", "Voice")],
                                   0, small=True)
        self.cat_chips.changed.connect(self._populate)
        self.root.addWidget(self.cat_chips)

        search = QLineEdit()
        search.setPlaceholderText("Search sounds…  (fahhh, thud, boom…)")
        search.textChanged.connect(lambda t: (setattr(self, "_search",
                                                      t.strip().lower()),
                                              self._populate()))
        self.root.addWidget(search)

        self.list = QListWidget()
        self.list.setIconSize(QSize(20, 20))
        self.list.itemDoubleClicked.connect(lambda _i: self._add())
        self.root.addWidget(self.list, 1)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.gain = SliderField(0.0, 2.0, 1.0, decimals=2, default=1.0)
        rl.addWidget(label("Volume", size=11))
        rl.addWidget(self.gain, 1)
        rl.addWidget(label("Snap to beats", size=11))
        self.snap = QCheckBox()
        self.snap.setChecked(True)
        rl.addWidget(self.snap)
        self.root.addWidget(row)

        btns = QHBoxLayout()
        preview = QPushButton(" Preview")
        preview.setIcon(theme.icon("play", theme.TEXT_DIM, 15))
        preview.clicked.connect(self._preview)
        btns.addWidget(preview)
        import_btn = QPushButton(" Add my own file…")
        import_btn.setIcon(theme.icon("folder", theme.TEXT_DIM, 15))
        import_btn.clicked.connect(self._import_own)
        btns.addWidget(import_btn)
        btns.addStretch(1)
        self.root.addLayout(btns)
        self.add_buttons("Add to timeline", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self._add)

        QTimer.singleShot(60, self._ensure)
        self._populate()

    def _ensure(self) -> None:
        try:
            self.paths = sfx_mod.ensure_library()
            got = len(self.paths)
            tts = sfx_mod.available_tts()
            extra = f" · text-to-speech: {tts}" if tts else \
                " · text-to-speech: not installed"
            self.status_label.setText(f"{got} sounds ready{extra}")
        except Exception as exc:
            self.status_label.setText(f"Could not build the sound library: {exc}")
        self._populate()

    def _populate(self, *_a) -> None:
        self.list.clear()
        cat = "All"
        for b in getattr(self.cat_chips, "_buttons", []):
            if b.isChecked():
                cat = b.text()
                break
        search = getattr(self, "_search", "")
        for spec in sfx_mod.search(search):
            if cat != "All" and spec.category != cat:
                continue
            item = QListWidgetItem(theme.icon("music", theme.TEXT_DIM, 16),
                                   f"{spec.label}    ·    {spec.category}")
            item.setData(Qt.UserRole, spec.key)
            item.setToolTip(f"{spec.keywords}  ·  {spec.duration:.2f}s")
            self.list.addItem(item)
        if self.list.count() == 0:
            item = QListWidgetItem("No sounds match — clear the search box.")
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)

    def _selected_key(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _preview(self) -> None:
        key = self._selected_key()
        if not key:
            return
        path = self.paths.get(key) or sfx_mod.resolve(key)
        if not path:
            return
        player = getattr(self.ctx.window.preview, "audio", None)
        if player is None:
            return
        argv = [str(ff.TOOLCHAIN.ffmpeg), "-hide_banner", "-loglevel", "error",
                "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", "48000", "-ac", "2", "pipe:1"]
        if player.start(argv):
            QTimer.singleShot(1500, player.stop)
            self.ctx.status(f"Previewing {key}")
        else:
            self.ctx.status("No audio device available for preview")

    def _import_own(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add sound files", str(Path.home()),
            "Audio (*.wav *.mp3 *.ogg *.flac *.m4a *.aac *.opus)")
        if not files:
            return
        dest = sfx_mod.library_dir() / "user"
        dest.mkdir(parents=True, exist_ok=True)
        keys = sfx_mod.import_user_audio(files)
        for k in keys:
            p = sfx_mod.resolve(k)
            if p:
                self.paths[k] = Path(p)
        self._populate()
        self.status_label.setText(
            f"{len(self.paths)} sounds available · {len(keys)} imported")
        self.ctx.status(f"Imported {len(keys)} sound file(s) into your library")

    def _add(self) -> None:
        key = self._selected_key()
        if not key:
            self.ctx.status("Pick a sound first")
            return
        path = self.paths.get(key) or sfx_mod.resolve(key)
        if not path:
            self.ctx.status("That sound is not available")
            return
        info = probe_mod.probe(str(path), use_cache=False)
        asset = MediaAsset.from_info(info, str(path))
        self.ctx.project.assets.append(asset)
        t = self.ctx.playhead()
        if self.snap.isChecked() and self.ctx.project.beats:
            t = min(self.ctx.project.beats, key=lambda b: abs(b - t))
        track = self.ctx.project.ensure_track_for("audio")
        clip = Clip(kind="audio", track_id=track.id, start=t,
                    duration=min(info.duration, 3.0))
        clip.path = str(path)
        clip.asset_id = asset.id
        clip.name = sfx_mod.CATALOG[key].label
        clip.meta["has_audio"] = True
        clip.audio.volume = self.gain.value()
        self.ctx.commit(f"Add sound {key}",
                        lambda p: p.add_clip(clip, track))
        self.ctx.refresh_timeline()
        self.ctx.status(f"Added “{clip.name}” at {t:.2f}s")


# --------------------------------------------------------------------------- #
#  Beat detection
# --------------------------------------------------------------------------- #

class BeatWorker(QThread):
    finished_ = Signal(object)
    progress = Signal(float)

    def __init__(self, peaks: Sequence[float], duration: float,
                 sensitivity: float, parent=None) -> None:
        super().__init__(parent)
        self.peaks = list(peaks)
        self.duration = duration
        self.sensitivity = sensitivity

    def run(self) -> None:
        beats = detect_beats(self.peaks, self.duration, self.sensitivity)
        self.finished_.emit(beats)


def detect_beats(peaks: Sequence[float], duration: float,
                 sensitivity: float = 1.0) -> List[float]:
    """Onset detection over decoded waveform peaks.

    A simple energy-novelty approach: the positive difference between the
    smoothed envelope and a slower-moving average, thresholded adaptively. It
    is not a full spectral flux detector, but on music it lands beats within a
    few tens of milliseconds — enough to snap edits to.
    """
    n = len(peaks)
    if n < 8 or duration <= 0:
        return []
    env = list(peaks)
    # smooth a little to remove sample noise
    win = max(1, n // 400)
    smooth = []
    acc = 0.0
    for i, v in enumerate(env):
        acc += v
        if i >= win:
            acc -= env[i - win]
        smooth.append(acc / min(i + 1, win))
    # moving average baseline
    base_win = max(3, n // 24)
    base = []
    acc = 0.0
    for i, v in enumerate(smooth):
        acc += v
        if i >= base_win:
            acc -= smooth[i - base_win]
        base.append(acc / min(i + 1, base_win))
    novelty = [max(0.0, smooth[i] - base[i]) for i in range(n)]
    if not any(novelty):
        return []
    mean = sum(novelty) / n
    thr = mean * (0.85 / max(0.15, sensitivity))
    # local maxima above threshold, with a minimum spacing
    min_gap = max(1, int(n / max(1.0, duration) * 0.18))
    beats: List[int] = []
    last = -min_gap
    for i in range(1, n - 1):
        if novelty[i] >= thr and novelty[i] >= novelty[i - 1] \
                and novelty[i] > novelty[i + 1] and i - last >= min_gap:
            beats.append(i)
            last = i
    return [round(i / n * duration, 4) for i in beats]


class BeatsDialog(BaseDialog):
    def __init__(self, parent, ctx) -> None:
        super().__init__(parent, "Beats and markers", 620, 560)
        self.ctx = ctx
        self.beats: List[float] = list(ctx.project.beats)
        self._build()

    def _build(self) -> None:
        self.header("Beats and markers",
                    "Detect beats from the audio, then snap clips, cuts and "
                    "sound effects to them.")
        self.track_combo = QComboBox()
        self._fill_tracks()
        self.root.addWidget(RowField("Analyse", self.track_combo))

        self.sensitivity = SliderField(0.3, 3.0, 1.0, decimals=2, default=1.0)
        self.root.addWidget(RowField("Sensitivity", self.sensitivity))

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        detect = QPushButton(" Detect beats")
        detect.setIcon(theme.icon("magic", theme.TEXT_ON_ACCENT, 15))
        detect.setProperty("class", "primary")
        detect.clicked.connect(self._detect)
        rl.addWidget(detect)
        clear = QPushButton("Clear beats")
        clear.clicked.connect(self._clear)
        rl.addWidget(clear)
        rl.addStretch(1)
        self.root.addWidget(row)

        self.info = label("No beats detected yet.", "muted", 11)
        self.root.addWidget(self.info)

        self.canvas = BeatCanvas(self, self.ctx)
        self.root.addWidget(self.canvas, 1)

        mrow = QWidget()
        ml = QHBoxLayout(mrow)
        ml.setContentsMargins(0, 0, 0, 0)
        add_marker = QPushButton("Add marker at playhead")
        add_marker.clicked.connect(self._add_marker)
        ml.addWidget(add_marker)
        snap_all = QPushButton("Snap every clip to nearest beat")
        snap_all.clicked.connect(self._snap_clips)
        ml.addWidget(snap_all)
        self.root.addWidget(mrow)
        self.add_buttons("Done", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self.accept)

    def _fill_tracks(self) -> None:
        self.track_combo.clear()
        for tr in self.ctx.project.tracks:
            if tr.kind == "audio" or any(c.meta.get("has_audio")
                                         for c in tr.clips):
                self.track_combo.addItem(tr.label(), tr.id)
        if self.track_combo.count() == 0:
            self.track_combo.addItem("No audio tracks yet", "")

    def _detect(self) -> None:
        track_id = self.track_combo.currentData()
        if not track_id:
            self.ctx.status("Add audio to the timeline first")
            return
        track = self.ctx.project.track(track_id)
        if track is None:
            return
        clip = next((c for c in track.clips if c.path), None)
        if clip is None:
            return
        self.info.setText("Decoding audio…")
        peaks = probe_mod.waveform_peaks(clip.path, 4000,
                                         start=clip.src_in,
                                         duration=clip.src_duration)
        if not peaks:
            self.info.setText("Could not decode that audio.")
            return
        self.beats = detect_beats(peaks, clip.src_duration,
                                  self.sensitivity.value())
        self.beats = [round(clip.start + b / max(0.001, clip.src_duration)
                            * clip.duration, 4) for b in self.beats]
        self.info.setText(f"Found {len(self.beats)} beats in "
                          f"{len(peaks)} analysed windows.")
        self.canvas.set_beats(self.beats)
        self.canvas.update()

    def _clear(self) -> None:
        self.beats = []
        self.canvas.set_beats([])
        self.info.setText("Beats cleared.")
        self.canvas.update()

    def _add_marker(self) -> None:
        t = self.ctx.playhead()
        self.ctx.project.markers.append({"t": round(t, 4), "label": "",
                                         "color": "#ff2f6b"})
        self.ctx.project.markers.sort(key=lambda m: m["t"])
        self.ctx.status(f"Marker at {t:.2f}s")

    def _snap_clips(self) -> None:
        if not self.beats:
            self.ctx.status("Detect beats first")
            return
        self.ctx.commit("Snap to beats", lambda p: self._snap(p))
        self.ctx.refresh_timeline()
        self.ctx.status("Snapped clips to the nearest beat")

    def _snap(self, project: Project) -> None:
        for clip in project.all_clips():
            nearest = min(self.beats, key=lambda b: abs(b - clip.start))
            if abs(nearest - clip.start) <= 0.22:
                delta = nearest - clip.start
                clip.start = round(nearest, 4)

    def accept(self) -> None:
        if self.beats != list(self.ctx.project.beats):
            beats = list(self.beats)
            self.ctx.commit("Set beats",
                            lambda p: setattr(p, "beats", beats))
            self.ctx.refresh_timeline()
        super().accept()


class BeatCanvas(QWidget):
    """Waveform + detected beats + playhead, drawn for the dialog."""

    def __init__(self, dialog: "BeatsDialog", ctx) -> None:
        super().__init__(dialog)
        self.dialog = dialog
        self.ctx = ctx
        self.beats: List[float] = list(ctx.project.beats)
        self.peaks: List[float] = []
        self.setMinimumHeight(120)
        self.setMouseTracking(True)

    def set_beats(self, beats: List[float]) -> None:
        self.beats = beats
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_INPUT))
        p.drawRoundedRect(r, 7, 7)
        total = max(0.5, self.ctx.project.duration)
        if self.peaks:
            mid = r.center().y()
            amp = r.height() * 0.35
            p.setBrush(QColor(90, 120, 160, 150))
            step = max(1, len(self.peaks) // max(1, int(r.width())))
            x = r.x()
            i = 0
            while x < r.right() and i < len(self.peaks):
                v = max(self.peaks[i:i + step] or [0])
                h = max(1.0, v * amp)
                p.drawRect(QRectF(x, mid - h, 1.2, h * 2))
                x += 1.2
                i += step
        p.setPen(QPen(QColor("#5ad1a0"), 1.5))
        for b in self.beats:
            x = r.x() + (b / total) * r.width()
            p.drawLine(QRectF(x, r.y() + 2, 0, r.height() - 4).topLeft(),
                       QRectF(x, r.y() + 2, 0, r.height() - 4).bottomLeft())
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        x = r.x() + (self.ctx.playhead() / total) * r.width()
        p.drawLine(QRectF(x, r.y(), 0, r.height()).topLeft(),
                   QRectF(x, r.y(), 0, r.height()).bottomLeft())
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(9))
        p.drawText(QRectF(r.x() + 4, r.bottom() - 14, r.width() - 8, 12),
                   Qt.AlignLeft, f"{len(self.beats)} beats · {total:.1f}s")
        p.end()

    def mousePressEvent(self, ev) -> None:
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        total = max(0.5, self.ctx.project.duration)
        t = (ev.position().x() - r.x()) / max(1.0, r.width()) * total
        self.ctx.seek(max(0.0, min(total, t)))


# --------------------------------------------------------------------------- #
#  Captions
# --------------------------------------------------------------------------- #

class CaptionsDialog(BaseDialog):
    def __init__(self, parent, ctx) -> None:
        super().__init__(parent, "Captions and subtitles", 700, 640)
        self.ctx = ctx
        self.captions: List[Dict] = []
        self._build()

    def _build(self) -> None:
        self.header("Captions and subtitles",
                    "Paste a script or import an .srt file, then lay it over "
                    "the timeline.")
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        load = QPushButton(" Open .srt…")
        load.setIcon(theme.icon("folder", theme.TEXT_DIM, 15))
        load.clicked.connect(self._load_srt)
        bl.addWidget(load)
        save = QPushButton(" Save .srt…")
        save.setIcon(theme.icon("save", theme.TEXT_DIM, 15))
        save.clicked.connect(self._save_srt)
        bl.addWidget(save)
        transcribe = QPushButton(" Transcribe audio")
        transcribe.setIcon(theme.icon("magic", theme.TEXT_DIM, 15))
        transcribe.setToolTip("Uses a local whisper/whisper.cpp install if one "
                              "is present; nothing is uploaded.")
        transcribe.clicked.connect(self._transcribe)
        bl.addWidget(transcribe)
        bl.addStretch(1)
        self.root.addWidget(bar)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "One caption per line — use SRT timing blocks for precise cues:\n\n"
            "1\n00:00:00,000 --> 00:00:02,000\nPOV: you are him\n\n"
            "or just paste plain lines and they will be spread evenly.")
        self.editor.setPlainText(
            "POV: YOU ARE HIM\nINSANE CLUTCH\nGG EASY")
        self.editor.textChanged.connect(self._reparse)
        self.root.addWidget(self.editor, 1)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(label("Start at", size=11))
        self.start_at = SliderField(0.0, 120.0, 0.0, decimals=1, suffix="s",
                                    factor=10.0, default=0.0)
        rl.addWidget(self.start_at, 1)
        self.use_gaps = QCheckBox("Use SRT timings")
        self.use_gaps.setChecked(True)
        rl.addWidget(self.use_gaps)
        self.root.addWidget(row)

        style_row = QWidget()
        sl = QHBoxLayout(style_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(label("Font", size=11))
        self.font = QComboBox()
        for f in sorted(FONT_FILES):
            self.font.addItem(f, f)
        sl.addWidget(self.font, 1)
        self.size = SliderField(20.0, 200.0, 64.0, decimals=0, factor=1.0,
                                default=64.0)
        sl.addWidget(label("Size", size=11))
        sl.addWidget(self.size, 1)
        sl.addWidget(label("Colour", size=11))
        self.color = ColorButton("#ffffff")
        sl.addWidget(self.color)
        self.root.addWidget(style_row)

        self.preview_label = label("0 cues", "muted", 11)
        self.root.addWidget(self.preview_label)
        self.add_buttons("Add to timeline", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self._apply)
        self._reparse()

    # -- parsing ------------------------------------------------------------- #
    def _reparse(self) -> None:
        text = self.editor.toPlainText()
        self.captions = parse_captions(text, self.ctx.project.duration)
        n = len(self.captions)
        dur = sum(c["end"] - c["start"] for c in self.captions)
        self.preview_label.setText(
            f"{n} cue{'s' if n != 1 else ''} · {dur:.1f}s of text"
            + (f" · first at {self.captions[0]['start']:.2f}s" if n else ""))

    def _load_srt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open subtitles", str(Path.home()),
            "Subtitles (*.srt *.vtt *.txt);;All files (*)")
        if not path:
            return
        try:
            text = Path(path).read_text("utf-8", "replace")
        except OSError as exc:
            QMessageBox.warning(self, "Could not read", str(exc))
            return
        self.editor.setPlainText(text)

    def _save_srt(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save subtitles", str(Path.home() / "captions.srt"),
            "SubRip (*.srt)")
        if not path:
            return
        Path(path).write_text(to_srt(self.captions), "utf-8")
        self.ctx.status(f"Saved {len(self.captions)} cues to {Path(path).name}")

    def _transcribe(self) -> None:
        exe = shutil.which("whisper") or shutil.which("whisper-cpp") \
            or shutil.which("main")
        if not exe:
            QMessageBox.information(
                self, "No speech-to-text engine",
                "No local speech-to-text engine was found.\n\n"
                "Install one to transcribe on-device:\n"
                "    pip install openai-whisper\n\n"
                "Nothing is ever uploaded — VN Studio only uses what is "
                "installed locally.")
            return
        tracks = [t for t in self.ctx.project.tracks if t.kind == "audio"]
        clip = next((c for t in tracks for c in t.clips if c.path), None)
        if clip is None:
            self.ctx.status("Add audio to the timeline first")
            return
        out_dir = Path(os.path.expanduser("~/.local/share/vn-studio/asr"))
        out_dir.mkdir(parents=True, exist_ok=True)
        base = out_dir / Path(clip.path).stem
        cmd = [exe, str(clip.path), "--output_dir", str(out_dir),
               "--output_format", "srt"]
        self.ctx.status("Transcribing… this can take a while")
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.ctx.status(f"Could not start {Path(exe).name}: {exc}")
            return
        QMessageBox.information(
            self, "Transcription started",
            f"{Path(exe).name} is running in the background.\n\n"
            f"When it finishes, load the .srt from:\n{out_dir}")

    def _apply(self) -> None:
        if not self.captions:
            self.ctx.status("No captions to add")
            return
        track = self.ctx.project.ensure_track_for("text")
        base = self.start_at.value()
        use_gaps = self.use_gaps.isChecked()
        caps = list(self.captions)
        font = self.font.currentData()
        size = self.size.value()
        color = self.color.color()
        height = self.ctx.project.settings.height
        made: List[str] = []

        def apply(p: Project):
            start_base = base
            for cap in caps:
                if use_gaps:
                    start = start_base + cap["start"]
                    duration = max(0.2, cap["end"] - cap["start"])
                else:
                    start = start_base
                    duration = max(0.4, min(3.5,
                                            len(cap["text"]) * 0.075))
                    start_base += duration
                clip = Clip(kind="text", track_id=track.id, start=start,
                            duration=duration)
                clip.text = TextProps(text=cap["text"], font=font, size=size,
                                      color=color, outline_width=4.0,
                                      outline_color="#000000", shadow=2.0)
                clip.transform.y = 0.86
                clip.meta["caption"] = True
                p.add_clip(clip, track)
                made.append(clip.id)
        self.ctx.commit(f"Add {len(made)} captions", apply)
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()
        self.ctx.status(f"Added {len(made)} caption(s)")


def parse_captions(text: str, project_duration: float = 0.0) -> List[Dict]:
    """Parse SRT/VTT text, or evenly spread plain lines."""
    text = text.replace("\r\n", "\n")
    cues: List[Dict] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    time_re = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        m = time_re.search(block)
        if m:
            def secs(h, mm, ss, ms):
                return int(h) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
            start = secs(*m.group(1, 2, 3, 4))
            end = secs(*m.group(5, 6, 7, 8))
            body = [l for l in lines if not time_re.search(l)
                    and not l.strip().isdigit()]
            cues.append({"start": start, "end": max(start + 0.2, end),
                         "text": " ".join(body).strip()})
        else:
            for line in lines:
                if line.strip().isdigit():
                    continue
                cues.append({"start": -1.0, "end": -1.0, "text": line.strip()})
    if cues and all(c["start"] < 0 for c in cues):
        # plain text: spread evenly over the project (or 2.5s each)
        total = project_duration if project_duration > 1 else len(cues) * 2.5
        step = max(0.4, min(4.0, total / max(1, len(cues))))
        for i, c in enumerate(cues):
            c["start"] = i * step
            c["end"] = i * step + min(step * 0.92, 3.2)
    return [c for c in cues if c["text"]]


def to_srt(cues: Sequence[Dict]) -> str:
    def stamp(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int(round((t - int(t)) * 1000))
        if ms == 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n"
                   f"{c['text']}\n")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
#  Templates / presets
# --------------------------------------------------------------------------- #

class PresetsDialog(BaseDialog):
    """Project templates and quick-start presets."""

    def __init__(self, parent, ctx, new_project: bool = False) -> None:
        super().__init__(parent, "Templates and presets", 700, 620)
        self.ctx = ctx
        self.new_project_mode = new_project
        self._new_project: Optional[Project] = None
        self._build()

    def _build(self) -> None:
        self.header("Templates" if not self.new_project_mode
                    else "New project",
                    "Start from a canvas preset, or apply a ready-made look to "
                    "the selected clip.")
        tabs = QTabWidget()
        self.root.addWidget(tabs, 1)

        # canvas tab
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        self.canvas_strip = PresetStrip(
            [(f"{n}", n, _canvas_color(w, h))
             for n, w, h in RESOLUTION_PRESETS], card_w=96, card_h=58)
        self.canvas_strip.picked.connect(self._canvas)
        lay.addWidget(label("Canvas", "sectionTitle"))
        lay.addWidget(self.canvas_strip)
        self.canvas_info = label("", "muted", 11)
        lay.addWidget(self.canvas_info)
        for name, w, h in RESOLUTION_PRESETS:
            if (w, h) == (self.ctx.project.settings.width,
                          self.ctx.project.settings.height):
                self.canvas_strip.current = name
        lay.addStretch(1)
        tabs.addTab(page, "Canvas")

        # looks tab
        looks = QWidget()
        ll = QVBoxLayout(looks)
        self.look_strip = PresetStrip(
            [(name, name, _grade_color(name)) for name in fx_mod.GRADES],
            card_w=74, card_h=50)
        self.look_strip.picked.connect(self._apply_look)
        ll.addWidget(label("Colour look", "sectionTitle"))
        ll.addWidget(self.look_strip)
        self.look_info = label("Applies to the selected clip.", "muted", 11)
        ll.addWidget(self.look_info)
        ll.addStretch(1)
        tabs.addTab(looks, "Looks")

        # text presets tab
        text_page = QWidget()
        tl = QVBoxLayout(text_page)
        for text, font, color, outline, anim in TEXT_PRESETS:
            b = QPushButton(f"{text}")
            b.setStyleSheet(
                f"text-align: left; padding: 7px 12px; font-family:"
                f" '{font}', sans-serif; font-size: 15px; color: {color};"
                f" background: {theme.BG_PANEL_ALT};"
                f" border: 1px solid {theme.BORDER_SOFT}; border-radius: 8px;")
            b.clicked.connect(
                lambda _=False, t=text, f=font, c=color, o=outline, a=anim:
                self._add_text(t, f, c, o, a))
            tl.addWidget(b)
        tl.addStretch(1)
        tabs.addTab(text_page, "Text")

        # workflow tab
        flow = QWidget()
        fl = QVBoxLayout(flow)
        fl.addWidget(label("Guided workflows", "sectionTitle"))
        for title, subtitle, fn in (
            ("YouTube short (9:16)",
             "Adds a vertical canvas and a caption track", self._vertical_short),
            ("Screen recording cleanup",
             "Removes the toolbars area and normalises the frame rate",
             self._screen_cleanup),
            ("Music video",
             "Adds an audio track and detects beats", self._music_video),
        ):
            card = QPushButton(f"{title}\n{subtitle}")
            card.setStyleSheet(
                f"text-align: left; padding: 10px 12px; background:"
                f" {theme.BG_PANEL_ALT}; border: 1px solid {theme.BORDER_SOFT};"
                f" border-radius: 9px;")
            card.clicked.connect(fn)
            fl.addWidget(card)
        fl.addStretch(1)
        tabs.addTab(flow, "Workflows")

        if self.new_project_mode:
            self.add_buttons("Create project", "Cancel")
            self.buttons.accepted.disconnect()
            self.buttons.accepted.connect(self._create)
        else:
            self.add_buttons("Done", "Close")
            self.buttons.accepted.disconnect()
            self.buttons.accepted.connect(self.accept)

    def exec_new(self) -> Optional[Project]:
        self._new_project = None
        self.exec()
        return self._new_project

    def _create(self) -> None:
        name = self.ctx.project.name if self.new_project_mode else "Project"
        from PySide6.QtWidgets import QInputDialog
        w = self._pending_w if hasattr(self, "_pending_w") else 1080
        h = self._pending_h if hasattr(self, "_pending_h") else 1920
        text, ok = QInputDialog.getText(self, "New project", "Project name:",
                                        text="Untitled project")
        if not ok:
            return
        self._new_project = Project.create_default(text.strip() or "Untitled",
                                                   w, h, 30.0)
        self.accept()

    def _canvas(self, name: str) -> None:
        for n, w, h in RESOLUTION_PRESETS:
            if n == name:
                self._pending_w, self._pending_h = w, h
                self.canvas_info.setText(f"{n} · {w}×{h}")
                if not self.new_project_mode:
                    self.ctx.commit(
                        f"Canvas {w}×{h}",
                        lambda p: (setattr(p.settings, "width", w),
                                   setattr(p.settings, "height", h)))
                    self.ctx.refresh_timeline()
                    self.ctx.request_preview_refresh()
                break

    def _apply_look(self, name: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.look_info.setText("Select a clip first, then pick a look.")
            return
        grade = {"name": name,
                 "grade": float(list(fx_mod.GRADES).index(name)),
                 "intensity": 1.0}

        def apply(p: Project):
            existing = clip.find_filter("grade")
            if existing:
                existing.params.update(grade)
            else:
                clip.add_filter("grade", dict(grade))
        self.ctx.commit(f"Look {name}", apply)
        self.ctx.request_preview_refresh()
        self.look_info.setText(f"Applied “{name}” to the selected clip.")

    def _add_text(self, text: str, font: str, color: str, outline: str,
                  anim: str) -> None:
        track = self.ctx.project.ensure_track_for("text")
        t = self.ctx.playhead()
        clip = Clip(kind="text", track_id=track.id, start=t, duration=2.5)
        clip.text = TextProps(text=text, font=font,
                              size=max(28.0, self.ctx.project.settings.height * 0.062),
                              color=color, outline_width=5.0,
                              outline_color=outline, shadow=2.0)
        clip.transform.y = 0.18
        apply_text_animation(clip, anim, clip.duration, 0.35)
        self.ctx.commit(f"Text {text[:12]}", lambda p: p.add_clip(clip, track))
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()
        self.ctx.status(f"Added text “{text}”")

    def _vertical_short(self) -> None:
        def apply(p: Project):
            p.settings.width, p.settings.height = 1080, 1920
            p.ensure_track_for("text")
        self.ctx.commit("9:16 workflow", apply)
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()
        self.ctx.status("Canvas set to 1080×1920 with a caption track")

    def _screen_cleanup(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.ctx.status("Select the recording first")
            return

        def apply(p: Project):
            clip.transform.crop_t = 0.08
            clip.transform.crop_b = 0.06
            clip.speed = 1.0
        self.ctx.commit("Screen cleanup", apply)
        self.ctx.request_preview_refresh()

    def _music_video(self) -> None:
        def apply(p: Project):
            p.ensure_track_for("audio")
        self.ctx.commit("Add audio track", apply)
        self.ctx.refresh_timeline()
        self.ctx.status("Add your music, then run Detect beats")


def _canvas_color(w: int, h: int) -> str:
    ratio = w / max(1, h)
    if ratio < 0.8:
        return "#ff2f6b"
    if ratio < 1.3:
        return "#f59e0b"
    return "#2f7fd4"


def _grade_color(name: str) -> str:
    from .inspector import _grade_color as gc
    return gc(name)


# --------------------------------------------------------------------------- #
#  Speed curve editor dialog
# --------------------------------------------------------------------------- #

class SpeedCurveDialog(BaseDialog):
    def __init__(self, parent, clip: Clip, ctx) -> None:
        super().__init__(parent, "Speed and speed curves", 620, 560)
        self.clip = clip
        self.ctx = ctx
        self._build()

    def _build(self) -> None:
        self.header("Speed",
                    "Playback speed and speed ramping. Curves are rendered by "
                    "slicing the clip and resampling each slice, so audio stays "
                    "in sync.")
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.speed = SliderField(0.05, 8.0, self.clip.speed, decimals=2,
                                 suffix="×", default=1.0, log=True)
        rl.addWidget(self.speed, 1)
        self.presets = QComboBox()
        for name in ("0.1×", "0.25×", "0.5×", "1×", "1.5×", "2×", "4×", "8×"):
            self.presets.addItem(name, float(name.replace("×", "")))
        self.presets.currentIndexChanged.connect(self._preset)
        rl.addWidget(self.presets)
        self.root.addWidget(RowField("Speed", row))

        self.reverse_cb = QCheckBox("Reverse")
        self.reverse_cb.setChecked(self.clip.reverse)
        self.root.addWidget(self.reverse_cb)
        self.freeze_cb = QCheckBox("Freeze this clip (hold one frame)")
        self.freeze_cb.setChecked(self.clip.freeze)
        self.root.addWidget(self.freeze_cb)

        from .inspector import CURVE_PRESETS
        self.root.addWidget(label("Speed curve", "sectionTitle"))
        self.curve_presets = PresetStrip(
            [(n, n, "#3b4252") for n, _p in CURVE_PRESETS], card_w=88,
            card_h=40)
        self.curve_presets.picked.connect(self._curve)
        self.root.addWidget(self.curve_presets)
        self.curve_info = label("", "muted", 11)
        self.root.addWidget(self.curve_info)

        self.pitch = ChipGroup(["Pitch corrected", "Shift pitch", "Mute"],
                               {"pitch_correct": 0, "pitch_shift": 1,
                                "mute": 2}.get(self.clip.audio.speed_mode, 0),
                               small=True)
        self.root.addWidget(label("Audio", "sectionTitle"))
        self.root.addWidget(self.pitch)
        self.add_buttons("Apply", "Close")

    def _preset(self) -> None:
        self.speed.set_value(float(self.presets.currentData() or 1.0))

    def _curve(self, name: str) -> None:
        from .inspector import CURVE_PRESETS
        for n, pts in CURVE_PRESETS:
            if n != name:
                continue
            curve = SpeedCurve(list(pts), n) if pts else None
            self.clip.speed_curve = curve
            self.curve_info.setText(
                f"{n}: " + " → ".join(f"{s:g}×" for _p, s in pts)
                if pts else "No speed curve")
            return

    def accept(self) -> None:
        speed = self.speed.value()
        reverse = self.reverse_cb.isChecked()
        freeze = self.freeze_cb.isChecked()
        mode = ["pitch_correct", "pitch_shift", "mute"][
            next((i for i, b in enumerate(self.pitch._buttons) if b.isChecked()), 0)]
        src_span = self.clip.duration * self.clip.speed
        curve = self.clip.speed_curve
        if curve and curve.is_ramp():
            new_dur = self.clip.duration
        else:
            new_dur = src_span / max(0.05, speed)

        def apply(p: Project):
            self.clip.speed = max(0.05, speed)
            self.clip.duration = max(0.03, new_dur)
            self.clip.reverse = reverse
            self.clip.freeze = freeze
            self.clip.audio.speed_mode = mode
        self.ctx.commit("Apply speed", apply)
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()
        super().accept()


class TextPresetsDialog(BaseDialog):
    """Text style presets with a live preview and full manual overrides."""

    def __init__(self, parent, ctx) -> None:
        super().__init__(parent, "Text styles", 640, 600)
        self.ctx = ctx
        from ..core.ass import available_fonts
        from ..core.model import TEXT_ANIMATIONS
        self.animations = list(TEXT_ANIMATIONS)
        self.fonts = available_fonts()
        self._build()

    def _build(self) -> None:
        self.header("Text styles",
                    "Big, readable captions in the bundled OFL fonts — the "
                    "same look the renderer produces, at canvas resolution.")
        self.root.addWidget(label("Presets", "sectionTitle"))
        self.presets = PresetStrip(
            [(t[:16], t[:16], c) for t, _f, c, _o, _a in TEXT_PRESETS],
            card_w=104, card_h=44)
        self.presets.picked.connect(self._preset)
        self.root.addWidget(self.presets)

        self.preview = QLabel("POV: YOU ARE HIM")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(96)
        self.preview.setStyleSheet(
            f"background: {theme.BG_INPUT}; border-radius: 9px;"
            f" border: 1px solid {theme.BORDER_SOFT};"
            f" color: {theme.TEXT_DIM}; padding: 6px;")
        self.root.addWidget(self.preview)

        self.text_edit = QLineEdit("POV: YOU ARE HIM")
        self.text_edit.textChanged.connect(self._sync_preview)
        self.root.addWidget(RowField("Text", self.text_edit, wide=True))

        self.font_combo = QComboBox()
        for f in self.fonts:
            self.font_combo.addItem(f, f)
        self.font_combo.setCurrentText("Bebas Neue")
        self.font_combo.currentIndexChanged.connect(self._sync_preview)
        self.root.addWidget(RowField("Font", self.font_combo))

        self.size = SliderField(16.0, 240.0, 118.0, decimals=0, default=118.0)
        self.size.valueChanged.connect(lambda _v: self._sync_preview())
        self.root.addWidget(RowField("Size", self.size))

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.color = ColorButton("#ffffff")
        self.color.colorChanged.connect(lambda _c: self._sync_preview())
        self.outline = SliderField(0.0, 20.0, 5.0, decimals=1, default=5.0)
        self.outline.valueChanged.connect(lambda _v: self._sync_preview())
        rl.addWidget(label("Colour", size=11))
        rl.addWidget(self.color)
        rl.addSpacing(8)
        rl.addWidget(label("Outline", size=11))
        rl.addWidget(self.outline, 1)
        self.root.addWidget(row)

        anim_row = QWidget()
        al = QHBoxLayout(anim_row)
        al.setContentsMargins(0, 0, 0, 0)
        self.anim = QComboBox()
        for a in self.animations:
            self.anim.addItem(a.replace("_", " ").title(), a)
        idx = self.anim.findData("pop_in")
        self.anim.setCurrentIndex(max(0, idx))
        al.addWidget(label("Animation", size=11))
        al.addWidget(self.anim, 1)
        self.shadow = QCheckBox("Drop shadow")
        self.shadow.setChecked(True)
        al.addWidget(self.shadow)
        self.root.addWidget(anim_row)

        apply_to = QPushButton("Apply to selected clip")
        apply_to.clicked.connect(self._apply_selected)
        row2 = QWidget()
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.addWidget(apply_to)
        r2.addStretch(1)
        self.root.addWidget(row2)

        self.add_buttons("Add as new text", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self._add)
        self._sync_preview()

    # -- helpers ------------------------------------------------------------- #
    def _preset(self, key: str) -> None:
        for text, font, color, outline, anim in TEXT_PRESETS:
            if text[:16] != key:
                continue
            self.text_edit.setText(text)
            self.font_combo.setCurrentText(font)
            self.color.set_color(color)
            self.outline.set_value(max(1.0, float(len(outline) and 5.0 or 4.0)))
            idx = self.anim.findData(anim)
            if idx >= 0:
                self.anim.setCurrentIndex(idx)
            return
        self._sync_preview()

    def _sync_preview(self, *_a) -> None:
        font = self.font_combo.currentData() or "Bebas Neue"
        size = max(10, int(min(46, self.size.value() * 0.32)))
        px = self.size.value()
        outline = self.outline.value()
        color = self.color.color()
        stroke = f"-{outline:.1f}px -{outline:.1f}px 2px #000" if outline else ""
        self.preview.setText(self.text_edit.text() or " ")
        self.preview.setStyleSheet(
            f"background: {theme.BG_INPUT}; border-radius: 9px;"
            f" border: 1px solid {theme.BORDER_SOFT};"
            f" color: {color}; padding: 6px;"
            f" font-family: '{font}'; font-size: {size}px;"
            f" font-weight: 700; text-shadow: {stroke if stroke else 'none'};")
        self.preview.setToolTip(f"{font} · {px:.0f}px on the canvas "
                                f"(preview scaled)")

    def _props(self) -> TextProps:
        return TextProps(
            text=self.text_edit.text() or "Text",
            font=self.font_combo.currentData() or "Bebas Neue",
            size=self.size.value(),
            color=self.color.color(),
            outline_width=self.outline.value(),
            outline_color="#000000",
            shadow=3.0 if self.shadow.isChecked() else 0.0)

    def _apply_selected(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None or clip.kind != "text":
            self.ctx.status("Select a text clip first")
            return
        props = self._props()
        anim = self.anim.currentData()
        applied_text = props.text
        applied_font = props.font
        applied_size = props.size
        applied_color = props.color
        applied_outline = props.outline_width
        applied_shadow = props.shadow

        def apply(p: Project):
            clip.text.text = applied_text
            clip.text.font = applied_font
            clip.text.size = applied_size
            clip.text.color = applied_color
            clip.text.outline_width = applied_outline
            clip.text.shadow = applied_shadow
            apply_text_animation(clip, anim, clip.duration, 0.35)
        self.ctx.commit("Text style", apply)
        self.ctx.request_preview_refresh()
        self.ctx.status("Applied the text style to the selected clip")

    def _add(self) -> None:
        track = self.ctx.project.ensure_track_for("text")
        t = self.ctx.playhead()
        start = t
        for c in track.clips:
            if c.start <= start < c.end:
                start = c.end
        clip = Clip(kind="text", track_id=track.id, start=start, duration=2.5)
        clip.text = self._props()
        clip.transform.y = 0.18
        apply_text_animation(clip, self.anim.currentData(), clip.duration, 0.35)
        self.ctx.commit("Add text", lambda p: p.add_clip(clip, track))
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()
        self.ctx.status(f"Added “{clip.text.text}”")


# --------------------------------------------------------------------------- #
#  Shortcuts / about
# --------------------------------------------------------------------------- #

SHORTCUTS = [
    ("Playback", [
        ("Space", "Play / pause"),
        ("← / →", "Previous / next frame"),
        ("Shift + ← / →", "Jump one second"),
        ("Home / End", "Start / end of timeline"),
        ("F", "Fullscreen preview"),
        ("Ctrl + Scroll", "Zoom the timeline"),
        ("Shift + Scroll", "Zoom the timeline"),
    ]),
    ("Editing", [
        ("S", "Split at playhead"),
        ("Ctrl + B", "Split at playhead"),
        ("Delete", "Delete selection"),
        ("Ctrl + D", "Duplicate selection"),
        ("I / O", "Set clip in / out point"),
        ("[ / ]", "Nudge one frame"),
        ("Ctrl + Z / Ctrl + Shift + Z", "Undo / redo"),
        ("Ctrl + A", "Select all clips"),
        ("M", "Mute the selected clip"),
        ("Ctrl + K", "Add a keyframe"),
        ("Ctrl + M", "Add a marker"),
    ]),
    ("Project", [
        ("Ctrl + N", "New project"),
        ("Ctrl + O", "Open project"),
        ("Ctrl + S", "Save"),
        ("Ctrl + Shift + S", "Save as"),
        ("Ctrl + I", "Import media"),
        ("Ctrl + E", "Export"),
        ("Ctrl + T", "Add text"),
        ("Ctrl + Shift + C", "Captions"),
        ("Ctrl + Shift + B", "Beats"),
        ("F1", "This help"),
    ]),
]


class ShortcutsDialog(BaseDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Settings and shortcuts", 620, 620)
        self.header("Settings and shortcuts",
                    "Everything runs locally: no account, no upload, no "
                    "subscription.")
        tabs = QTabWidget()
        self.root.addWidget(tabs, 1)

        keys = QScrollArea()
        keys.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setSpacing(4)
        for group, items in SHORTCUTS:
            lay.addWidget(label(group, "sectionTitle"))
            for keys_txt, desc in items:
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                k = QLabel(keys_txt)
                k.setFixedWidth(190)
                k.setStyleSheet(
                    f"background: {theme.BG_INPUT}; border-radius: 5px;"
                    f" padding: 3px 7px; font-family: 'DejaVu Sans Mono',"
                    f" monospace; font-size: 11px;")
                rl.addWidget(k)
                d = label(desc, size=11)
                d.setStyleSheet(f"color: {theme.TEXT_DIM};")
                rl.addWidget(d, 1)
                lay.addWidget(row)
            lay.addSpacing(8)
        lay.addStretch(1)
        keys.setWidget(host)
        tabs.addTab(keys, "Shortcuts")

        prefs = QWidget()
        pl = QVBoxLayout(prefs)
        settings = pio.load_settings()
        self.autosave = QCheckBox("Autosave every 2 minutes")
        self.autosave.setChecked(bool(settings.get("autosave", True)))
        self.autosave.toggled.connect(
            lambda on: pio.update_settings(autosave=bool(on)))
        pl.addWidget(self.autosave)
        self.recover = QCheckBox("Keep recovery files for crashes")
        self.recover.setChecked(bool(settings.get("recovery", True)))
        self.recover.toggled.connect(
            lambda on: pio.update_settings(recovery=bool(on)))
        pl.addWidget(self.recover)
        self.quality = QComboBox()
        for key, text in (("low", "Low (fastest)"), ("auto", "Automatic"),
                          ("medium", "Medium"), ("high", "High (sharpest)")):
            self.quality.addItem(text, key)
        idx = self.quality.findData(settings.get("preview_quality", "auto"))
        self.quality.setCurrentIndex(max(0, idx))
        self.quality.currentIndexChanged.connect(
            lambda: pio.update_settings(preview_quality=self.quality.currentData()))
        pl.addWidget(RowField("Preview quality", self.quality))
        pl.addWidget(hline())
        pl.addWidget(label("Data folders", "sectionTitle"))
        for name, path in (("Projects", pio.projects_dir()),
                           ("Cache", pio.cache_dir()),
                           ("Sounds", sfx_mod.library_dir())):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(label(name, size=11), 0)
            edit = QLineEdit(str(path))
            edit.setReadOnly(True)
            rl.addWidget(edit, 1)
            open_btn = QPushButton("Open")
            open_btn.setFixedWidth(56)
            open_btn.clicked.connect(
                lambda _=False, p=path: self._open_folder(p))
            rl.addWidget(open_btn)
            pl.addWidget(row)
        pl.addStretch(1)
        tabs.addTab(prefs, "Preferences")

        about = QWidget()
        al = QVBoxLayout(about)
        for line in (
            "<b>VN Studio</b> — an open video editor for Ubuntu desktops.",
            "",
            "Built with PySide6 (Qt 6) and FFmpeg. All editing, preview and "
            "export happens on this machine.",
            "",
            "• No account, no cloud, no subscription.",
            "• No GPU required — H.264 encoding runs on the CPU.",
            "• Projects are plain, readable .vnp files you can move anywhere.",
            "",
            f"FFmpeg: {ff.TOOLCHAIN.version or 'not found'}",
            f"Filters available: {len(ff.TOOLCHAIN.filters)}",
            f"Fonts bundled: {len(FONT_FILES)} (SIL Open Font License)",
            "",
            "Stickers, sounds and animations are generated by VN Studio "
            "itself, so they are original assets.",
        ):
            lb = QLabel(line)
            lb.setWordWrap(True)
            al.addWidget(lb)
        al.addStretch(1)
        tabs.addTab(about, "About")
        self.add_buttons("Close", "Close")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self.accept)

    def _open_folder(self, path: Path) -> None:
        for cmd in (["xdg-open", str(path)], ["nautilus", str(path)]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue


# --------------------------------------------------------------------------- #
#  Recovery
# --------------------------------------------------------------------------- #

class RecoveryDialog(BaseDialog):
    def __init__(self, parent, entries: List[Dict]) -> None:
        super().__init__(parent, "Recover unsaved work", 520, 380)
        self.entries = entries
        self.choice: Optional[Dict] = None
        self.header("Unsaved work found",
                    "VN Studio closed unexpectedly, or a project was open when "
                    "the app was quit. Pick what to restore.")
        self.list = QListWidget()
        for e in entries:
            stamp = time.strftime("%H:%M", time.localtime(e.get("mtime", 0)))
            item = QListWidgetItem(f"{e.get('name', 'Project')}  ·  {stamp}")
            item.setData(Qt.UserRole, e)
            self.list.addItem(item)
        if entries:
            self.list.setCurrentRow(0)
        self.root.addWidget(self.list, 1)
        self.add_buttons("Recover", "Discard")
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self._pick)

    def _pick(self) -> None:
        item = self.list.currentItem()
        if item:
            self.choice = item.data(Qt.UserRole)
        self.accept()
