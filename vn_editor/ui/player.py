"""Preview player: real scrubbing and real-time playback.

Two engines, both honest:

* **Scrub** — single frames are compiled through the same filtergraph the
  exporter uses (``render.render_frame``), at preview resolution, on a worker
  thread.  Requests are coalesced so dragging the playhead never queues up.
* **Play** — the whole project is piped out of FFmpeg as raw RGB frames and
  painted in step with a clock.  Audio is decoded from the same mix graph and
  played through ``QAudioSink`` when a device exists, else through FFmpeg's
  ALSA output, else muted.  When audio is live it *is* the clock, so picture
  and sound stay together.

The widget never pretends: if FFmpeg is missing it says so, and if there is no
audio device it says that too.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

from PySide6.QtCore import (QEvent, QObject, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core import ffmpeg as ff
from ..core.model import Project
from ..core import render as render_mod
from . import theme

SCROLL_SCRUB_STEP = 0.04

# --------------------------------------------------------------------------- #
#  audio
# --------------------------------------------------------------------------- #

class AudioPlayer(QObject):
    """Plays the mixed PCM of the project.

    Backends, in order of preference: ``QAudioSink`` (PipeWire/PulseAudio/ALSA
    through Qt), FFmpeg's ALSA sink, silence.  ``backend`` reports which one is
    live so the UI can tell the truth about it.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backend = "none"
        self._sink = None
        self._device = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._volume = 1.0
        self._probe_audio()
        self._alsa_ok = self._probe_alsa()

    # -- capability probe --------------------------------------------------- #
    def _probe_audio(self) -> None:
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        except Exception:
            self.backend = "none"
            return
        self._QAudioSink = QAudioSink
        self._QAudioFormat = QAudioFormat
        self._devices = QMediaDevices.audioOutputs()
        self.backend = "qt" if self._devices else "none"

    def _probe_alsa(self) -> bool:
        if self.backend == "qt":
            return True
        try:
            r = subprocess.run(
                [str(ff.TOOLCHAIN.ffmpeg), "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                 "-t", "0.05", "-f", "alsa", "-y", "default"],
                capture_output=True, timeout=6)
            ok = r.returncode == 0
        except Exception:
            ok = False
        if ok:
            self.backend = "alsa"
        return ok

    def description(self) -> str:
        return {"qt": "system audio (Qt)",
                "alsa": "system audio (ALSA)",
                "none": "no audio device"}[self.backend]

    # -- control ------------------------------------------------------------ #
    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))
        if self._sink is not None:
            try:
                self._sink.setVolume(self._volume)
            except Exception:
                pass

    def start(self, argv: List[str], start_offset: float = 0.0,
              on_clock: Optional[callable] = None) -> bool:
        """``argv`` must emit raw s16le stereo 48 kHz PCM on stdout."""
        self.stop()
        if self.backend == "none" or self._volume <= 0.001:
            return False
        self._stop.clear()
        if self.backend == "qt":
            return self._start_qt(argv, on_clock)
        return self._start_alsa(argv)

    def _start_qt(self, argv: List[str], on_clock) -> bool:
        try:
            fmt = self._QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(self._QAudioFormat.SampleFormat.Int16)
            dev = self._devices[0]
            self._sink = self._QAudioSink(dev, fmt, self)
            self._sink.setVolume(self._volume)
            self._io = self._sink.start()
            self._proc = ff.popen_ffmpeg(argv, stdout=ff.subprocess.PIPE,
                                         stderr=ff.subprocess.DEVNULL)

            def pump():
                assert self._proc and self._proc.stdout
                chunk = 4800 * 2 * 2
                while not self._stop.is_set():
                    data = self._proc.stdout.read(chunk)
                    if not data:
                        break
                    written = 0
                    while written < len(data) and not self._stop.is_set():
                        n = self._io.write(data[written:])
                        if n <= 0:
                            time.sleep(0.004)
                            continue
                        written += n
                try:
                    self._io.write(b"")
                except Exception:
                    pass

            self._thread = threading.Thread(target=pump, daemon=True)
            self._thread.start()
            return True
        except Exception:
            self.stop()
            return False

    def _start_alsa(self, argv: List[str]) -> bool:
        try:
            full = list(argv) + ["-f", "alsa", "-y", "default"]
            self._proc = ff.popen_ffmpeg(full, stdout=ff.subprocess.DEVNULL,
                                         stderr=ff.subprocess.DEVNULL)
            return True
        except Exception:
            self._proc = None
            return False

    def processed_seconds(self) -> float:
        if self._sink is not None:
            try:
                return self._sink.processedUSecs() / 1_000_000.0
            except Exception:
                return 0.0
        return 0.0

    def stop(self) -> None:
        self._stop.set()
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink = None
        if self._proc is not None:
            ff.terminate(self._proc)
            self._proc = None
        self._thread = None


# --------------------------------------------------------------------------- #
#  frame reader
# --------------------------------------------------------------------------- #

class FrameReader(QThread):
    """Reads raw RGB frames from an FFmpeg pipe with backpressure."""

    frameReady = Signal(int, QImage)
    streamEnded = Signal()

    def __init__(self, argv: List[str], width: int, height: int,
                 slots: int = 3, parent=None) -> None:
        super().__init__(parent)
        self.argv = argv
        self.width = width
        self.height = height
        self.frame_bytes = width * height * 3
        self._slots = threading.Semaphore(slots)
        self._running = True
        self._proc: Optional[subprocess.Popen] = None
        self.error = ""

    def release_slot(self) -> None:
        try:
            self._slots.release()
        except ValueError:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            self._slots.release()
            self._slots.release()
            self._slots.release()
        except ValueError:
            pass
        if self._proc is not None:
            ff.terminate(self._proc)
            self._proc = None

    def run(self) -> None:
        try:
            self._proc = ff.popen_ffmpeg(self.argv, stdout=ff.subprocess.PIPE,
                                         stderr=ff.subprocess.DEVNULL)
        except Exception as exc:
            self.error = str(exc)
            self.streamEnded.emit()
            return
        assert self._proc.stdout is not None
        idx = 0
        try:
            while self._running:
                buf = self._read_exact(self._proc.stdout, self.frame_bytes)
                if not buf or len(buf) < self.frame_bytes:
                    break
                if not self._running:
                    break
                self._slots.acquire(timeout=2.0)
                if not self._running:
                    break
                img = QImage(buf, self.width, self.height,
                             self.width * 3, QImage.Format_RGB888).copy()
                self.frameReady.emit(idx, img)
                idx += 1
        except Exception:
            pass
        finally:
            if self._proc is not None:
                ff.terminate(self._proc)
                self._proc = None
            self.streamEnded.emit()

    @staticmethod
    def _read_exact(stream, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            chunk = stream.read(n - len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)


# --------------------------------------------------------------------------- #
#  scrub worker
# --------------------------------------------------------------------------- #

class ScrubWorker(QThread):
    """Renders single preview frames on demand, newest request wins."""

    frameReady = Signal(float, QImage)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._request: Optional[Tuple[Project, float, int, int]] = None
        self._quit = False
        self._wake = threading.Event()

    def request(self, project: Project, t: float, w: int, h: int) -> None:
        with self._lock:
            self._request = (project, t, w, h)
        self._wake.set()

    def stop(self) -> None:
        self._quit = True
        self._wake.set()

    def run(self) -> None:
        import tempfile
        from ..core import probe as probe_mod
        while not self._quit:
            self._wake.wait(0.3)
            self._wake.clear()
            with self._lock:
                req = self._request
                self._request = None
            if req is None or self._quit:
                continue
            project, t, w, h = req
            tmp = os.path.join(tempfile.gettempdir(),
                               f"vn-scrub-{os.getpid()}.png")
            try:
                ok = render_mod.render_frame(project, t, w, h, tmp)
                if ok and os.path.exists(tmp):
                    img = QImage(tmp)
                    if not img.isNull():
                        self.frameReady.emit(t, img)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
#  preview widget
# --------------------------------------------------------------------------- #

class PreviewWidget(QWidget):
    """Video preview surface with scrub, play and safe-area guides."""

    requestPlayToggle = Signal()
    seeked = Signal(float)

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.setMinimumSize(220, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._image: Optional[QImage] = None
        self._scrub_image: Optional[QImage] = None
        self._time = 0.0
        self._zoom_image: Optional[QImage] = None
        self.show_guides = False
        self.fit_mode = "fit"          # fit | fill | 100
        self.quality = "auto"
        self.message = ""
        self._overlay_message = ""
        self._overlay_until = 0.0
        self._drag = None
        self._loading = False
        self._blink = 0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._animate)

    # -- state -------------------------------------------------------------- #
    def set_image(self, img: Optional[QImage], t: Optional[float] = None) -> None:
        self._image = img
        if t is not None:
            self._time = t
        self._loading = False
        self.update()

    def set_scrub_image(self, img: QImage, t: float) -> None:
        self._scrub_image = img
        self._image = img
        self._time = t
        self._loading = False
        self.update()

    def set_time(self, t: float) -> None:
        self._time = t

    def set_loading(self, on: bool) -> None:
        if on != self._loading:
            self._loading = on
            self.update()

    def flash(self, text: str, seconds: float = 1.8) -> None:
        self._overlay_message = text
        self._overlay_until = time.time() + seconds
        self._timer.start()
        self.update()

    def _animate(self) -> None:
        if self._overlay_message and time.time() > self._overlay_until:
            self._overlay_message = ""
            self.update()
        if self._loading:
            self._blink = (self._blink + 1) % 12
            self.update()
        if not self._overlay_message and not self._loading:
            self._timer.stop()

    # -- painting ------------------------------------------------------------ #
    def canvas_rect(self) -> QRectF:
        w = max(1, self.width())
        h = max(1, self.height())
        pw, ph = self.project.settings.width, self.project.settings.height
        if pw <= 0 or ph <= 0:
            return QRectF(0, 0, w, h)
        if self.fit_mode == "100":
            scale = 1.0
        else:
            scale = min(w / pw, h / ph) if self.fit_mode == "fit" \
                else max(w / pw, h / ph)
        cw, ch = pw * scale, ph * scale
        return QRectF((w - cw) / 2, (h - ch) / 2, cw, ch)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#07090c"))
        rect = self.canvas_rect()

        p.save()
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setClipPath(path)
        p.fillRect(rect, QColor("#000000"))
        img = self._image
        if img is not None and not img.isNull():
            target = rect
            if self.fit_mode != "fill" and self.fit_mode != "100":
                # preserve aspect for the picture itself
                iw, ih = img.width(), img.height()
                s = min(rect.width() / max(1, iw), rect.height() / max(1, ih))
                tw, th = iw * s, ih * s
                target = QRectF(rect.center().x() - tw / 2,
                                rect.center().y() - th / 2, tw, th)
            p.drawImage(target, img)
        else:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.setFont(theme.ui_font(12))
            p.drawText(rect, Qt.AlignCenter,
                       self.message or "No preview yet\nImport a clip to begin")
        if self.show_guides:
            self._paint_guides(p, rect)
        if self._loading:
            self._paint_spinner(p, rect)
        p.restore()

        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        if self._overlay_message:
            self._paint_toast(p, rect, self._overlay_message)
        p.end()

    def _paint_guides(self, p: QPainter, rect: QRectF) -> None:
        p.setPen(QPen(QColor(255, 255, 255, 60), 1, Qt.DashLine))
        for f in (1 / 3, 2 / 3):
            p.drawLine(QPointF(rect.x() + rect.width() * f, rect.y()),
                       QPointF(rect.x() + rect.width() * f, rect.bottom()))
            p.drawLine(QPointF(rect.x(), rect.y() + rect.height() * f),
                       QPointF(rect.right(), rect.y() + rect.height() * f))
        m = 0.06
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(rect.width() * m, rect.height() * m,
                                        -rect.width() * m, -rect.height() * m),
                          3, 3)
        r = rect
        cx, cy = r.center().x(), r.center().y()
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawLine(QPointF(r.x(), cy), QPointF(r.right(), cy))
        p.drawLine(QPointF(cx, r.y()), QPointF(cx, r.bottom()))

    def _paint_spinner(self, p: QPainter, rect: QRectF) -> None:
        r = QRectF(rect.center().x() - 13, rect.center().y() - 13, 26, 26)
        p.setPen(QPen(QColor(0, 0, 0, 130), 5))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(theme.ACCENT), 3))
        start = int(self._blink * 30 * 16)
        p.drawArc(r, start, 90 * 16)

    def _paint_toast(self, p: QPainter, rect: QRectF, text: str) -> None:
        p.setFont(theme.ui_font(11, 600))
        fm = p.fontMetrics()
        w = min(rect.width() - 20, fm.horizontalAdvance(text) + 26)
        h = 26
        box = QRectF(rect.center().x() - w / 2, rect.bottom() - h - 14, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 200))
        p.drawRoundedRect(box, 13, 13)
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignCenter, text)

    # -- interaction --------------------------------------------------------- #
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._drag = ev.position()
        elif ev.button() == Qt.MiddleButton:
            self.fit_mode = "fit" if self.fit_mode != "fit" else "fill"
            self.update()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is None:
            return
        dx = ev.position().x() - self._drag.x()
        if abs(dx) > 3:
            # horizontal drag scrubs, like VN's preview
            self._drag = ev.position()
            delta = -dx * SCROLL_SCRUB_STEP * 0.05
            self.seeked.emit(max(0.0, self._time + delta))

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, ev) -> None:
        self.requestPlayToggle.emit()

    def wheelEvent(self, ev) -> None:
        step = 1 / max(1.0, self.project.settings.fps)
        ticks = ev.angleDelta().y() / 120.0
        self.seeked.emit(max(0.0, self._time + ticks * step))
        ev.accept()


# --------------------------------------------------------------------------- #
#  controller
# --------------------------------------------------------------------------- #

class PreviewPanel(QWidget):
    """Preview surface + the player engine + transport signals."""

    timeChanged = Signal(float)
    playingChanged = Signal(bool)
    statusMessage = Signal(str)

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.audio = AudioPlayer(self)
        self.widget = PreviewWidget(project, self)
        self._reader: Optional[FrameReader] = None
        self._scrub: Optional[ScrubWorker] = None
        self._frames: "deque[Tuple[int, QImage]]" = deque()
        self._playing = False
        self._t0 = 0.0
        self._wall0 = 0.0
        self._audio_clock = False
        self._last_shown = -1
        self._ended = False
        self._preview_scale = 0.5
        self._preview_size = (270, 480)
        self._volume = 1.0
        self._tick = QTimer(self)
        self._tick.setInterval(12)
        self._tick.timeout.connect(self._pump)

        from PySide6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget)
        self.widget.seeked.connect(self.seek)
        self.widget.requestPlayToggle.connect(self.toggle)
        self._start_scrubber()

    # -- helpers ------------------------------------------------------------- #
    def _start_scrubber(self) -> None:
        if self._scrub is None:
            self._scrub = ScrubWorker(self)
            self._scrub.frameReady.connect(self._on_scrub_frame)
            self._scrub.start()

    def set_project(self, project: Project) -> None:
        self.stop()
        self.project = project
        self.widget.project = project
        self.widget.set_image(None)
        self._frames.clear()

    def preview_size(self, quality: Optional[str] = None) -> Tuple[int, int]:
        q = quality or self.widget.quality
        pw, ph = self.project.settings.width, self.project.settings.height
        target = {"low": 360, "medium": 540, "high": 720,
                  "auto": 480}.get(q, 480)
        if q == "auto":
            # scale with the widget but never above 720p
            target = max(270, min(720, int(max(self.widget.width(),
                                               self.widget.height()))))
        scale = target / max(1, max(pw, ph))
        w = max(64, int(pw * scale) // 2 * 2)
        h = max(64, int(ph * scale) // 2 * 2)
        return w, h

    # -- scrubbing ----------------------------------------------------------- #
    def seek(self, t: float) -> None:
        t = max(0.0, min(self.project.duration, t))
        if self._playing:
            self.stop()
        self.widget.set_time(t)
        self.timeChanged.emit(t)
        self.request_frame(t)

    def request_frame(self, t: float) -> None:
        self._start_scrubber()
        w, h = self.preview_size()
        self.widget.set_loading(True)
        assert self._scrub is not None
        self._scrub.request(self.project, max(0.0, t), w, h)

    def _on_scrub_frame(self, t: float, img: QImage) -> None:
        self.widget.set_scrub_image(img, t)

    # -- playback ------------------------------------------------------------ #
    def toggle(self) -> None:
        if self._playing:
            self.stop()
        else:
            self.play()

    def play(self, from_time: Optional[float] = None) -> None:
        self.stop()
        if ff.TOOLCHAIN.ffmpeg is None:
            self.widget.flash("FFmpeg not found — cannot preview")
            return
        total = self.project.duration
        if total <= 0.02:
            self.widget.flash("Nothing to play yet")
            return
        t0 = self.widget._time if from_time is None else from_time
        if t0 >= total - 0.03:
            t0 = 0.0
        w, h = self.preview_size()
        opts = render_mod.options_from_settings(self.project)
        opts.preview = True
        opts.preview_scale = w / max(1, self.project.settings.width)
        w, h = opts.out_size()      # exactly what the pipe will emit
        opts.pixel_format = "rgb24"
        opts.audio = True
        try:
            argv, builder, dur = render_mod.build_pipe_command(
                self.project, opts, start=t0, duration=total - t0)
        except Exception as exc:
            self.widget.flash(f"Preview graph failed: {exc}")
            return
        self._reader = FrameReader(argv, w, h, slots=3, parent=self)
        self._reader.frameReady.connect(self._on_frame)
        self._reader.streamEnded.connect(self._on_stream_end)
        self._reader.start()

        # audio
        audio_started = False
        try:
            aopts = render_mod.options_from_settings(self.project)
            aopts.audio = True
            aopts.video = False
            aargv = render_mod.build_audio_pipe_command(
                self.project, aopts, start=t0, duration=total - t0)
            audio_started = self.audio.start(aargv)
            self.audio.set_volume(self._volume)
        except Exception:
            audio_started = False

        self._frames.clear()
        self._playing = True
        self._ended = False
        self._t0 = t0
        self._wall0 = time.monotonic()
        self._audio_clock = audio_started and self.audio.backend == "qt"
        self._last_shown = -1
        self._tick.start()
        self.playingChanged.emit(True)
        note = f"Playing · audio: {self.audio.description()}"
        self.statusMessage.emit(note)

    def stop(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader.wait(1500)
            self._reader = None
        self.audio.stop()
        self._tick.stop()
        self._frames.clear()
        if self._playing:
            self._playing = False
            self.playingChanged.emit(False)

    def _on_frame(self, idx: int, img: QImage) -> None:
        self._frames.append((idx, img))
        if len(self._frames) > 4:
            self._frames.popleft()

    def _on_stream_end(self) -> None:
        self._ended = True

    def _clock(self) -> float:
        if self._audio_clock:
            return self._t0 + self.audio.processed_seconds()
        return self._t0 + (time.monotonic() - self._wall0)

    def _pump(self) -> None:
        if not self._playing:
            return
        now = self._clock()
        fps = max(1.0, self.project.settings.fps)
        desired = int((now - self._t0) * fps)
        shown = None
        while self._frames and self._frames[0][0] <= desired:
            shown = self._frames.popleft()
        if shown is not None:
            idx, img = shown
            self.widget.set_scrub_image(img, self._t0 + idx / fps)
            self._last_shown = idx
            if self._reader is not None:
                self._reader.release_slot()
            self.timeChanged.emit(self._t0 + idx / fps)
        if self._ended and not self._frames:
            if self._last_shown < desired - 1:
                total = self.project.duration
                self.stop()
                self.widget.set_time(total)
                self.timeChanged.emit(total)
                self.request_frame(total)

    # -- misc ---------------------------------------------------------------- #
    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))
        self.audio.set_volume(self._volume)

    def current_time(self) -> float:
        return self._clock() if self._playing else self.widget._time

    def shutdown(self) -> None:
        self.stop()
        if self._scrub is not None:
            self._scrub.stop()
            self._scrub.wait(1200)
            self._scrub = None


# --------------------------------------------------------------------------- #
#  thumbnails (media bin + timeline)
# --------------------------------------------------------------------------- #

_THUMB_CACHE: "dict[Tuple[str, int], QPixmap]" = {}
_WAVE_CACHE: "dict[Tuple[str, int], QPixmap]" = {}


def thumb_for(path: str, height: int = 96) -> Optional[QPixmap]:
    """Poster frame (or a drawn waveform for audio) as a cached QPixmap.

    Frames come from FFmpeg through :func:`probe.make_thumbnail` and are stored
    under the app cache directory, so importing a large folder stays quick on
    the second run.
    """
    if not path:
        return None
    key = (str(path), int(height))
    hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from ..core import probe as probe_mod
        from ..core import project as pio
        if not os.path.exists(path):
            return None
        info = probe_mod.probe(path)
        if info.kind == "audio":
            pm = _waveform_pixmap(path, int(height))
            if pm is not None:
                _THUMB_CACHE[key] = pm
            return pm
        target = pio.thumb_dir() / (
            f"{abs(hash((str(path), os.path.getmtime(path)))):x}_"
            f"{int(height)}.jpg")
        if not target.exists() or target.stat().st_size < 256:
            ok = probe_mod.make_thumbnail(
                path, str(target),
                at=min(1.0, max(0.0, info.duration * 0.25)) if info.duration
                else 0.0,
                width=max(96, int(height * 1.8)))
            if not ok:
                return None
        pm = QPixmap(str(target))
        if pm.isNull():
            return None
        _THUMB_CACHE[key] = pm
        return pm
    except Exception:
        return None


def _waveform_pixmap(path: str, height: int) -> Optional[QPixmap]:
    """A small waveform card so audio assets look like audio."""
    key = (str(path), int(height))
    hit = _WAVE_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from ..core import probe as probe_mod
        peaks = probe_mod.waveform_peaks(path, 160)
    except Exception:
        peaks = []
    w = max(96, int(height * 1.8))
    pm = QPixmap(w, height)
    pm.fill(QColor(theme.BG_INPUT))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, False)
    if peaks:
        mid = height / 2
        p.setPen(QPen(QColor(theme.CLIP_AUDIO), 1))
        step = w / max(1, len(peaks))
        for i, v in enumerate(peaks):
            h = max(1.0, v * (height * 0.42))
            x = i * step
            p.drawLine(QPointF(x, mid - h), QPointF(x, mid + h))
    else:
        p.setPen(QPen(QColor(theme.TEXT_MUTED), 1))
        p.drawLine(QPointF(4, height / 2), QPointF(w - 4, height / 2))
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(9))
        p.drawText(QRectF(0, height - 14, w, 12), Qt.AlignCenter, "audio")
    p.end()
    _WAVE_CACHE[key] = pm
    return pm


def clear_thumb_cache() -> None:
    _THUMB_CACHE.clear()
    _WAVE_CACHE.clear()
