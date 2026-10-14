"""Export dialog: preset, destination, progress and cancellation.

The render itself runs on a worker thread via
:func:`opencut.rendering.exporter.export_project`; when it finishes the
file is re-probed and the result reported honestly (streams, size,
duration) — or the encoder's own error message is shown.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QVBoxLayout)

from ..engine import Project
from ..rendering import exporter


class _ExportWorker(QObject):
    progress = Signal(float, str)
    finished = Signal(object, object)       # outcome, validation

    def __init__(self, project_data: dict, output: str, preset: str) -> None:
        super().__init__()
        self.project_data = project_data
        self.output = output
        self.preset = preset
        self.cancel_event = threading.Event()

    def run(self) -> None:
        project = Project.from_dict(self.project_data)
        outcome = exporter.export_project(
            project, self.output, self.preset,
            progress=lambda f, msg: self.progress.emit(f, msg),
            cancel=self.cancel_event.is_set)
        validation = (exporter.validate_output(self.output)
                      if outcome.ok else None)
        self.finished.emit(outcome, validation)


class ExportDialog(QDialog):
    """Modal export flow with progress and cancel."""

    def __init__(self, project: Project, suggested_path: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self._thread: Optional[QThread] = None
        self._worker: Optional[_ExportWorker] = None
        self.setWindowTitle("Export MP4")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Preset"))
        self._preset = QComboBox()
        for key, preset in exporter.PRESETS.items():
            self._preset.addItem(f"{preset.label} — {preset.description}", key)
        self._preset.setCurrentIndex(
            list(exporter.PRESETS).index(exporter.DEFAULT_PRESET))
        layout.addWidget(self._preset)

        layout.addWidget(QLabel("Output file"))
        row = QHBoxLayout()
        self._path = QLineEdit(suggested_path or self._default_name())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self._path, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        layout.addWidget(self._progress)
        self._status = QLabel("Ready")
        self._status.setObjectName("dim")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel)
        self._queue_btn = QPushButton("Add to queue")
        self._queue_btn.setToolTip(
            "Queue this export (runs in the background)")
        self._queue_btn.clicked.connect(self._enqueue)
        self._export_btn = QPushButton("Export now")
        self._export_btn.setObjectName("accent")
        self._export_btn.clicked.connect(self._start)
        buttons.addWidget(self._queue_btn)
        buttons.addWidget(self._cancel_btn)
        buttons.addWidget(self._export_btn)
        layout.addLayout(buttons)

    def _default_name(self) -> str:
        name = (self.project.name or "opencut").strip() or "opencut"
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        return os.path.join(os.path.expanduser("~"), "Videos",
                            f"{safe.strip()}.mp4")

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export MP4", self._path.text(),
            "MP4 video (*.mp4);;All files (*)")
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self._path.setText(path)

    def _enqueue(self) -> None:
        """Add this export to the background render queue and close."""
        output = self._path.text().strip()
        if not output:
            QMessageBox.warning(self, "Export", "Choose an output file first.")
            return
        preset_key = self._preset.currentData()
        # Access the parent window's render queue
        from .main_window import MainWindow
        parent_win = self.parent()
        while parent_win is not None and not isinstance(parent_win, MainWindow):
            parent_win = parent_win.parent()
        if parent_win is not None and hasattr(parent_win, 'render_queue'):
            parent_win.render_queue.enqueue(
                self.project.to_dict(), output, preset_key)
            self.accept()
        else:
            QMessageBox.information(
                self, "Queue unavailable",
                "Could not reach the render queue. Use Export now instead.")

    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        output = self._path.text().strip()
        if not output:
            QMessageBox.warning(self, "Export", "Choose an output file first.")
            return
        preset_key = self._preset.currentData()
        self._worker = _ExportWorker(self.project.to_dict(), output, preset_key)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._export_btn.setEnabled(False)
        self._preset.setEnabled(False)
        self._status.setText("Rendering…")
        self._thread.start()

    def _on_progress(self, fraction: float, message: str) -> None:
        self._progress.setValue(int(fraction * 1000))
        if message:
            self._status.setText(f"Rendering… {message}")

    def _cancel(self) -> None:
        if self._worker is not None and self._thread is not None \
                and self._thread.isRunning():
            self._worker.cancel_event.set()
            self._status.setText("Cancelling…")
            return
        self.reject()

    def _on_finished(self, outcome, validation) -> None:
        self._export_btn.setEnabled(True)
        self._preset.setEnabled(True)
        if outcome.ok and validation is not None and validation.ok:
            self._progress.setValue(1000)
            mb = validation.size_bytes / (1024 * 1024)
            self._status.setText(
                f"Done — {validation.width}×{validation.height}, "
                f"{validation.duration:.1f}s, {mb:.1f} MB"
                + (", with audio" if validation.has_audio else ", no audio"))
            self._cancel_btn.setText("Close")
        elif outcome.ok:
            self._status.setText(
                "The render reported success but the file could not be "
                f"verified: {validation.message if validation else 'unknown'}")
        else:
            self._status.setText(f"Export failed: {outcome.message}")
