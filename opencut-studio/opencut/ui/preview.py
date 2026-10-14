"""Video preview widget with enhanced controls.

Features:
- Large video preview canvas with aspect ratio support
- Play/pause, seek bar, time display
- Aspect ratio selector (16:9, 9:16, 1:1, 4:3, Free)
- Fit/Fill toggle
- Fullscreen toggle
- Preview quality selector (Draft/Quarter/Half/Full)
- Volume slider
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QPainter, QPixmap
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    _HAS_MULTIMEDIA = True
except ImportError:
    _HAS_MULTIMEDIA = False
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, ACCENT_DARK, BG, BORDER, PANEL, PANEL_LIGHT, TEXT, TEXT_DIM
from .. import engine


class PreviewCanvas(QWidget):
    """Black video canvas supporting pixmap overlays and direct paint."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._aspect_ratio = 16 / 9
        self._fit_mode = "fit"  # "fit" or "fill"
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAutoFillBackground(False)

    def setPixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self.update()

    def set_aspect_ratio(self, ratio: float) -> None:
        self._aspect_ratio = ratio
        self.update()

    def set_fit_mode(self, mode: str) -> None:
        self._fit_mode = mode
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0a0c10"))
        src = self._pixmap
        if src and not src.isNull():
            canvas = self.rect()
            if self._fit_mode == "fill":
                scale = max(canvas.width() / src.width(),
                            canvas.height() / src.height())
            else:
                scale = min(canvas.width() / src.width(),
                            canvas.height() / src.height())
            w = int(src.width() * scale)
            h = int(src.height() * scale)
            x = (canvas.width() - w) // 2
            y = (canvas.height() - h) // 2
            p.drawPixmap(x, y, w, h, src)
        else:
            p.setPen(QColor(TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Import media to begin editing")
        p.end()


class PreviewWidget(QWidget):
    """Video preview panel with enhanced controls."""

    time_changed = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: Optional[str] = None
        self._duration: float = 0.0
        self._current: float = 0.0
        self._playing = False
        self._player: Optional[QMediaPlayer] = None
        self._audio_out: Optional[QAudioOutput] = None
        self._video_w: Optional[QVideoWidget] = None
        self._seeking = False
        self._quality_str = "Half"
        self._project = None
        self._pool = engine.subprocess
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Video canvas
        self._canvas = PreviewCanvas()
        root.addWidget(self._canvas, 1)

        # Aspect ratio + quality toolbar
        toolbar = QWidget()
        toolbar.setStyleSheet(f"background:{PANEL};")
        tlay = QHBoxLayout(toolbar)
        tlay.setContentsMargins(8, 4, 8, 4)
        tlay.setSpacing(6)

        # Aspect ratio selector
        ar_label = QLabel("Ratio:")
        ar_label.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px;")
        tlay.addWidget(ar_label)
        self._ar_combo = QComboBox()
        self._ar_combo.addItems(["16:9", "9:16", "1:1", "4:3", "Free"])
        self._ar_combo.setCurrentText("16:9")
        self._ar_combo.currentTextChanged.connect(self._on_aspect_ratio)
        self._ar_combo.setStyleSheet(f"""
            QComboBox {{ background:{PANEL_LIGHT}; border:1px solid {BORDER};
                        border-radius:4px; padding:2px 6px; font-size:11px; }}
            QComboBox QAbstractItemView {{ background:{PANEL_LIGHT};
                selection-background-color:{ACCENT_DARK}; }}
        """)
        tlay.addWidget(self._ar_combo)

        # Fit/Fill toggle
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedWidth(36)
        self._fit_btn.setStyleSheet(f"""
            QPushButton {{ background:{PANEL_LIGHT}; border:1px solid {BORDER};
                          border-radius:4px; padding:2px; font-size:11px; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
        """)
        self._fit_btn.clicked.connect(self._toggle_fit)
        tlay.addWidget(self._fit_btn)

        tlay.addStretch(1)

        # Preview quality
        q_label = QLabel("Quality:")
        q_label.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px;")
        tlay.addWidget(q_label)
        self._quality = QComboBox()
        self._quality.addItems(["Draft", "Quarter", "Half", "Full"])
        self._quality.setCurrentIndex(2)  # Default to Half
        self._quality.currentTextChanged.connect(self._on_quality)
        self._quality.setStyleSheet(self._ar_combo.styleSheet())
        tlay.addWidget(self._quality)

        # Fullscreen button
        self._fs_btn = QPushButton("⛶")
        self._fs_btn.setFixedSize(28, 28)
        self._fs_btn.setToolTip("Toggle fullscreen (F11)")
        self._fs_btn.setStyleSheet(f"""
            QPushButton {{ background:{PANEL_LIGHT}; border:1px solid {BORDER};
                          border-radius:4px; font-size:14px; }}
            QPushButton:hover {{ border-color:{ACCENT}; }}
        """)
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        tlay.addWidget(self._fs_btn)

        root.addWidget(toolbar)

        # Playback controls
        controls = QWidget()
        controls.setStyleSheet(f"background:{PANEL};")
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(10, 6, 10, 6)
        cl.setSpacing(8)

        # Play/Pause
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(36, 36)
        self._play_btn.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT_DARK}; border:1px solid {ACCENT};
                          border-radius:18px; font-size:16px; font-weight:bold; }}
            QPushButton:hover {{ background:{ACCENT}; color:#0b1520; }}
        """)
        self._play_btn.clicked.connect(self.toggle_play)
        cl.addWidget(self._play_btn)

        # Time display
        self._time_label = QLabel("0:00.0 / 0:00.0")
        self._time_label.setStyleSheet(f"color:{TEXT_DIM}; font-size:12px;")
        self._time_label.setFixedWidth(120)
        cl.addWidget(self._time_label)

        # Seek slider
        self._seek = QSlider(Qt.Horizontal)
        self._seek.setRange(0, 1000)
        self._seek.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self._seek.sliderReleased.connect(self._seek_release)
        cl.addWidget(self._seek, 1)

        # Volume
        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("font-size:14px;")
        cl.addWidget(vol_icon)
        self._volume = QSlider(Qt.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(100)
        self._volume.setFixedWidth(80)
        self._volume.valueChanged.connect(self._set_volume)
        cl.addWidget(self._volume)

        root.addWidget(controls)

    # ------------------------------------------------------------------ #
    #  Aspect ratio / fit / quality / fullscreen
    # ------------------------------------------------------------------ #
    def _on_aspect_ratio(self, text: str) -> None:
        ratios = {
            "16:9": 16/9, "9:16": 9/16, "1:1": 1.0,
            "4:3": 4/3, "Free": 16/9,
        }
        self._canvas.set_aspect_ratio(ratios.get(text, 16/9))

    def _toggle_fit(self) -> None:
        if self._fit_btn.text() == "Fit":
            self._fit_btn.setText("Fill")
            self._canvas.set_fit_mode("fill")
        else:
            self._fit_btn.setText("Fit")
            self._canvas.set_fit_mode("fit")

    def _on_quality(self, text: str) -> None:
        self._quality_str = text

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win.isFullScreen():
            win.showNormal()
        else:
            win.showFullScreen()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_F11:
            self._toggle_fullscreen()
        else:
            super().keyPressEvent(ev)

    # ------------------------------------------------------------------ #
    #  Media loading and playback
    # ------------------------------------------------------------------ #
    def load(self, path: str, *, duration: float = 0.0) -> None:
        self._path = path
        self._duration = duration
        self._current = 0.0
        self._update_time()
        self._seek.setValue(0)
        ext = os.path.splitext(path)[1].lower()
        if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts"):
            self._init_player(path)
            self._render_at(0.0)
        else:
            self._kill_player()
            self._pool.submit(
                lambda: engine.make_thumbnail(path, None, at=0.0, width=640),
                lambda thumb: self._show_thumb(thumb))

    def _init_player(self, path: str) -> None:
        self._kill_player()
        if not _HAS_MULTIMEDIA:
            return
        try:
            self._audio_out = QAudioOutput()
            self._audio_out.setVolume(self._volume.value() / 100)
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._audio_out)
            self._video_w = QVideoWidget()
            self._player.setVideoOutput(self._video_w)
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.positionChanged.connect(self._on_pos)
            self._player.durationChanged.connect(self._on_dur)
        except Exception:
            self._player = None

    def _kill_player(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
                self._player.setSource(QUrl())
            except Exception:
                pass
            self._player = None
            self._audio_out = None
            self._video_w = None

    def _show_thumb(self, thumb: object) -> None:
        if isinstance(thumb, str) and os.path.exists(thumb):
            self._canvas.setPixmap(QPixmap(thumb))

    def _render_at(self, t: float) -> None:
        if self._path:
            self._pool.submit(
                lambda: engine.make_thumbnail(self._path, None,
                                              at=t, width=640),
                lambda thumb: self._show_thumb(thumb))

    # ------------------------------------------------------------------ #
    #  Playback
    # ------------------------------------------------------------------ #
    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        self._playing = True
        self._play_btn.setText("⏸")
        if self._player is not None:
            self._player.play()
        self._timer.start()

    def pause(self) -> None:
        self._playing = False
        self._play_btn.setText("▶")
        if self._player is not None:
            self._player.pause()
        self._timer.stop()

    def stop(self) -> None:
        self._playing = False
        self._play_btn.setText("▶")
        if self._player is not None:
            self._player.stop()
        self._timer.stop()

    def seek(self, t: float) -> None:
        t = max(0.0, min(t, self._duration))
        self._current = t
        self._update_time()
        if not self._seeking:
            self._seek.blockSignals(True)
            self._seek.setValue(
                int(t / self._duration * 1000) if self._duration else 0)
            self._seek.blockSignals(False)
        if self._player is not None:
            self._player.setPosition(int(t * 1000))
        else:
            self._render_at(t)

    def _seek_release(self) -> None:
        self._seeking = False
        frac = self._seek.value() / 1000
        self.seek(frac * self._duration)

    def _tick(self) -> None:
        if self._playing:
            if self._player is None:
                self._current += 0.1
                if self._current > self._duration > 0:
                    self._current = 0.0
                self._update_time()
                self.time_changed.emit(self._current)

    def _on_pos(self, ms: int) -> None:
        self._current = ms / 1000.0
        self._update_time()
        if not self._seeking:
            self._seek.blockSignals(True)
            self._seek.setValue(
                int(self._current / self._duration * 1000)
                if self._duration else 0)
            self._seek.blockSignals(False)
        self.time_changed.emit(self._current)

    def _on_dur(self, ms: int) -> None:
        d = ms / 1000.0
        if d > 0:
            self._duration = d

    def _update_time(self) -> None:
        def fmt(s: float) -> str:
            m = int(s // 60)
            sec = s - m * 60
            return f"{m}:{sec:04.1f}"
        self._time_label.setText(
            f"{fmt(self._current)} / {fmt(self._duration)}")

    def _set_volume(self, v: int) -> None:
        if self._audio_out is not None:
            self._audio_out.setVolume(v / 100)

    def refresh(self) -> None:
        """Refresh the preview (re-render current frame)."""
        if self._current > 0 and self._path:
            self._render_at(self._current)

    def set_project(self, project) -> None:
        """Store reference to the current project."""
        self._project = project

    def _still_size(self):
        """Return (width, height) for still image rendering based on quality."""
        quality_map = {
            "Draft": 320, "Quarter": 480,
            "Half": 640, "Full": 1280,
        }
        name = self._quality.currentText()
        w = quality_map.get(name, 640)
        h = int(w * 9 / 16)
        return (w, h)