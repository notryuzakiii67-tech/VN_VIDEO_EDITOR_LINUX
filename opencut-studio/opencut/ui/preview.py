"""Preview: scrub stills and real-time playback.

Two paths, both using the engine's own graph compiler (the same code
that renders exports, so the preview is honest):

* **Scrubbing** renders one composited frame at the requested time with
  ``engine.render_frame`` on a worker thread.  The project is snapshotted
  to plain data first, so edits on the UI thread can never race a render.
* **Playback** spawns the engine's raw-frame pipe (video) and PCM pipe
  (audio), reads frames on a thread, paces them against the audio clock
  (``QAudioSink.processedUSecs``) when audio exists, and falls back to a
  wall clock for silent timelines.  If the platform has no audio device,
  playback continues video-only.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QToolButton,
                               QVBoxLayout, QWidget)

from .. import engine
from ..engine import Project

try:  # audio output is optional at runtime (headless machines, no device)
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink
except Exception:  # pragma: no cover
    QAudioFormat = None  # type: ignore
    QAudioSink = None  # type: ignore

MAX_STILL_W = 960                 # scrub stills are capped for responsiveness


class _StillSignals(QObject):
    done = Signal(int, float, str)          # generation, time, png path


class _StillJob(QRunnable):
    def __init__(self, signals: _StillSignals, generation: int,
                 project_data: dict, t: float, width: int, height: int,
                 out_path: str) -> None:
        super().__init__()
        self.signals = signals
        self.generation = generation
        self.project_data = project_data
        self.t = t
        self.width = width
        self.height = height
        self.out_path = out_path

    def run(self) -> None:
        try:
            project = Project.from_dict(self.project_data)
            ok = engine.render_frame(project, self.t, self.width, self.height,
                                     self.out_path)
        except Exception:
            ok = False
        try:
            self.signals.done.emit(self.generation, self.t,
                                   self.out_path if ok else "")
        except RuntimeError:
            pass            # preview widget went away mid-render (app closing)


class PreviewWidget(QWidget):
    """Video preview with transport controls."""

    time_changed = Signal(float)
    playing_changed = Signal(bool)
    _frame_ready = Signal(object, object)   # QImage, playback state
    _playback_done = Signal(object)         # playback state

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.time = 0.0
        self._generation = 0
        self._pool = QThreadPool.globalInstance()
        self._signals = _StillSignals(self)
        self._signals.done.connect(self._still_done)
        self._playback: Optional[dict] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._screen = QLabel("Import media and add it to the timeline")
        self._screen.setAlignment(QtAlignCenter())
        self._screen.setMinimumSize(320, 180)
        self._screen.setStyleSheet(
            "background:#000000; color:#9aa3ad; border-radius:8px;")
        layout.addWidget(self._screen, 1)

        row = QHBoxLayout()
        self._play_btn = QToolButton()
        self._play_btn.setText("▶")
        self._play_btn.setToolTip("Play / pause (Space, over the preview)")
        self._play_btn.clicked.connect(self.toggle_play)
        self._stop_btn = QToolButton()
        self._stop_btn.setText("■")
        self._stop_btn.setToolTip("Stop and return to the playhead")
        self._stop_btn.clicked.connect(self.stop)
        self._time_label = QLabel("0:00.0 / 0:00.0")
        self._time_label.setObjectName("dim")
        row.addWidget(self._play_btn)
        row.addWidget(self._stop_btn)
        row.addWidget(self._time_label)
        row.addStretch(1)
        # Phase 3: preview quality selector
        self._quality = QComboBox()
        self._quality.addItem("Draft (fast)", 0.25)
        self._quality.addItem("Low", 0.4)
        self._quality.addItem("Half", 0.5)
        self._quality.addItem("Full", 1.0)
        self._quality.setCurrentIndex(2)   # Half is default
        self._quality.setToolTip("Preview quality (lower = faster)")
        self._quality.currentIndexChanged.connect(
            lambda _: self.refresh())
        row.addWidget(QLabel("Quality:"))
        row.addWidget(self._quality)
        layout.addLayout(row)

        self._clock = QTimer(self)
        self._clock.setInterval(50)
        self._clock.timeout.connect(self._tick_clock)
        self.setFocusPolicy(QtStrongFocus())

    # ------------------------------------------------------------------ #
    def set_project(self, project: Optional[Project]) -> None:
        self.stop()
        self.project = project
        self.time = 0.0
        self._update_time_label()
        self.request_still(0.0)

    def seek(self, t: float) -> None:
        """Scrub to time ``t`` (pauses playback, like other editors)."""
        if self._playback is not None:
            self.stop()
        self.time = max(0.0, float(t))
        self._update_time_label()
        self.request_still(self.time)

    def refresh(self) -> None:
        """Re-render the current frame (after an edit)."""
        if self._playback is None:
            self.request_still(self.time)

    # ------------------------------------------------------------------ #
    #  Scrub stills
    # ------------------------------------------------------------------ #
    def _still_size(self) -> tuple:
        assert self.project is not None
        w = int(self.project.settings.width)
        h = int(self.project.settings.height)
        quality = self._quality.currentData() if hasattr(self, '_quality') else 0.5
        if quality is None:
            quality = 0.5
        scale = min(float(quality), MAX_STILL_W / max(1, w))
        return max(16, int(w * scale) // 2 * 2), max(16, int(h * scale) // 2 * 2)

    def request_still(self, t: float) -> None:
        if self.project is None or not engine.ffmpeg_available():
            if self.project is not None and not engine.ffmpeg_available():
                self._screen.setText(
                    "FFmpeg is not installed, so frames cannot be rendered.\n"
                    "Install it with:  sudo apt install ffmpeg")
            return
        self._generation += 1
        w, h = self._still_size()
        out_dir = os.path.join(str(engine.cache_dir()), "opencut-preview")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"still_{self._generation % 8}.png")
        job = _StillJob(self._signals, self._generation,
                        self.project.to_dict(), float(t), w, h, out_path)
        self._pool.start(job)

    def _still_done(self, generation: int, t: float, path: str) -> None:
        if generation != self._generation or self._playback is not None:
            return                                    # superseded request
        if not path or not os.path.exists(path):
            self._screen.setText("Could not render this frame")
            return
        pix = QPixmap(path)
        if pix.isNull():
            return
        self._show_pixmap(pix)

    def _show_pixmap(self, pix: QPixmap) -> None:
        scaled = pix.scaled(self._screen.size(),
                            QtKeepAspectRatio(), QtSmoothTransformation())
        self._screen.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        pix = self._screen.pixmap()
        if pix is not None and not pix.isNull() and self._playback is None:
            self._show_pixmap(pix)

    # ------------------------------------------------------------------ #
    #  Playback
    # ------------------------------------------------------------------ #
    @property
    def playing(self) -> bool:
        return self._playback is not None

    def toggle_play(self) -> None:
        if self._playback is not None:
            self.stop()
            self.request_still(self.time)
        else:
            self.play(self.time)

    def play(self, from_time: float = 0.0) -> None:
        if self.project is None or not engine.ffmpeg_available():
            return
        total = self.project.duration
        if total <= 0.01:
            return
        start = float(from_time)
        if start >= total - 0.03:
            start = 0.0
        self.stop()
        opts = engine.options_from_settings(self.project, preview=True)
        w, h = opts.out_size()
        fps = float(opts.fps or 30.0)
        state: dict = {"stop": False, "start": start, "t0": start,
                       "w": w, "h": h, "fps": fps, "audio": None,
                       "video_proc": None, "audio_proc": None,
                       "wall": time.monotonic()}
        self._playback = state
        try:
            argv, _builder, _dur = engine.build_pipe_command(
                self.project, opts, start=start, duration=total - start)
            proc = engine.popen_ffmpeg(argv, stdout=engine.subprocess.PIPE,
                                       stderr=engine.subprocess.DEVNULL)
            state["video_proc"] = proc
        except Exception:
            self._playback = None
            return
        self._start_audio(state, total)
        reader = threading.Thread(target=self._read_frames, args=(state,),
                                  daemon=True)
        reader.start()
        self._clock.start()
        self._play_btn.setText("⏸")
        self.playing_changed.emit(True)


    def _start_audio(self, state: dict, total: float) -> None:
        if QAudioSink is None:
            return
        try:
            opts = engine.options_from_settings(self.project, preview=True)
            argv = engine.build_audio_pipe_command(
                self.project, opts, start=state["start"],
                duration=total - state["start"], sample_rate=48000)
            if not argv:
                return
            proc = engine.popen_ffmpeg(argv, stdout=engine.subprocess.PIPE,
                                       stderr=engine.subprocess.DEVNULL)
            fmt = QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.Int16)
            sink = QAudioSink(fmt)
            device = sink.start()
            if device is None:
                engine.terminate_process(proc)
                return
            state["audio"] = (sink, device)
            state["audio_proc"] = proc
            threading.Thread(target=self._pump_audio, args=(state,),
                             daemon=True).start()
        except Exception:
            state["audio"] = None

    def _pump_audio(self, state: dict) -> None:
        _sink, device = state["audio"]
        proc = state["audio_proc"]
        try:
            while not state["stop"]:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                device.write(chunk)
        except Exception:
            pass

    def _read_frames(self, state: dict) -> None:
        proc = state["video_proc"]
        w, h, fps = state["w"], state["h"], state["fps"]
        frame_bytes = w * h * 3
        index = 0
        try:
            while not state["stop"]:
                data = proc.stdout.read(frame_bytes)
                if not data or len(data) < frame_bytes:
                    break
                # pace against the audio clock when present, else the wall
                target = index / fps
                while not state["stop"] and self._played_seconds(state) < target:
                    time.sleep(0.004)
                if state["stop"]:
                    break
                image = QImage(data, w, h, w * 3, QImage.Format_RGB888).copy()
                state["last_image"] = image
                state["last_index"] = index
                index += 1
                # hand the frame to the UI thread (queued signal)
                self._frame_ready.emit(image, state)
        except Exception:
            pass
        finally:
            self._playback_done.emit(state)

    def _played_seconds(self, state: dict) -> float:
        audio = state.get("audio")
        if audio is not None:
            sink, _device = audio
            try:
                return sink.processedUSecs() / 1_000_000.0
            except Exception:
                pass
        return time.monotonic() - state["wall"]

    def _present(self, image: QImage, state: dict) -> None:
        if self._playback is not state:
            return
        self._show_pixmap(QPixmap.fromImage(image))

    def _playback_finished(self, state: dict) -> None:
        if self._playback is not state:
            return
        total = self.project.duration if self.project else 0.0
        at_end = self.time >= total - 0.05
        self.stop()
        if at_end:
            self.time = total
        self._update_time_label()
        self.request_still(self.time)
        self.time_changed.emit(self.time)

    def _tick_clock(self) -> None:
        state = self._playback
        if state is None or self.project is None:
            return
        self.time = min(self.project.duration,
                        state["t0"] + self._played_seconds(state))
        self._update_time_label()
        self.time_changed.emit(self.time)

    def stop(self) -> None:
        state = self._playback
        if state is None:
            return
        state["stop"] = True
        self._playback = None
        self._clock.stop()
        for key in ("video_proc", "audio_proc"):
            proc = state.get(key)
            if proc is not None:
                engine.terminate_process(proc)
        audio = state.get("audio")
        if audio is not None:
            try:
                audio[0].stop()
            except Exception:
                pass
        self._play_btn.setText("▶")
        self.playing_changed.emit(False)

    # ------------------------------------------------------------------ #
    def _update_time_label(self) -> None:
        from .theme import format_time
        total = self.project.duration if self.project else 0.0
        self._time_label.setText(
            f"{format_time(self.time)} / {format_time(total)}")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space:
            self.toggle_play()
            event.accept()
            return
        super().keyPressEvent(event)


def QtAlignCenter():  # tiny helper to keep the import list short
    from PySide6.QtCore import Qt
    return Qt.AlignCenter


def QtKeepAspectRatio():
    from PySide6.QtCore import Qt
    return Qt.KeepAspectRatio


def QtSmoothTransformation():
    from PySide6.QtCore import Qt
    return Qt.SmoothTransformation


def QtStrongFocus():
    from PySide6.QtCore import Qt
    return Qt.StrongFocus
