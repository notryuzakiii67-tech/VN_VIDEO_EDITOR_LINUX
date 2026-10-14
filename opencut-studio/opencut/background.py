"""Background workers for media analysis, proxy generation and caching.

All heavy I/O runs on a QThreadPool so the UI never blocks.  Each worker
posts results back to the UI thread through Qt signals.  Workers are
fire-and-forget: the pool owns their lifecycle.

Workers:
* :class:`ThumbnailWorker` — generate poster frames for the media library.
* :class:`WaveformWorker` — decode peak envelopes for audio clips.
* :class:`ProxyWorker` — transcode high-res video to a lightweight preview proxy.
* :class:`MediaAnalysisWorker` — probe a file and detect VFR, missing audio, etc.
* :class:`ExportWorker` — render a project to MP4 (used by the render queue).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt


# --------------------------------------------------------------------------- #
#  Signals bundles (QRunnable cannot carry signals, so we pair them)
# --------------------------------------------------------------------------- #

class _ThumbnailSignals(QObject):
    done = Signal(str, str)                 # asset_id, thumbnail_path (or '')


class _WaveformSignals(QObject):
    done = Signal(str, list)                # asset_id, peaks[float]


class _ProxySignals(QObject):
    progress = Signal(str, float)           # asset_id, 0.0–1.0
    done = Signal(str, str, bool)           # asset_id, proxy_path, ok


class _AnalysisSignals(QObject):
    done = Signal(str, dict)                # asset_id, analysis dict


class _ExportSignals(QObject):
    progress = Signal(float, str)           # 0.0–1.0, message
    done = Signal(bool, str, float)         # ok, output_path, elapsed
    cancelled = Signal()


# --------------------------------------------------------------------------- #
#  Workers
# --------------------------------------------------------------------------- #

class ThumbnailWorker(QRunnable):
    """Generate a poster frame for a media asset."""

    def __init__(self, asset_id: str, source_path: str, out_path: str,
                 at: float = 1.0, width: int = 192) -> None:
        super().__init__()
        self.asset_id = asset_id
        self.source_path = source_path
        self.out_path = out_path
        self.at = at
        self.width = width
        self.signals = _ThumbnailSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            from . import engine
            if engine.make_thumbnail(self.source_path, self.out_path,
                                     at=self.at, width=self.width):
                self.signals.done.emit(self.asset_id, self.out_path)
                return
        except Exception:
            pass
        self.signals.done.emit(self.asset_id, "")


class WaveformWorker(QRunnable):
    """Decode a peak envelope for an audio asset."""

    def __init__(self, asset_id: str, source_path: str,
                 buckets: int = 240) -> None:
        super().__init__()
        self.asset_id = asset_id
        self.source_path = source_path
        self.buckets = buckets
        self.signals = _WaveformSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            from . import engine
            peaks = engine.waveform_peaks(self.source_path,
                                          buckets=self.buckets)
            self.signals.done.emit(self.asset_id,
                                   [float(p) for p in peaks])
            return
        except Exception:
            pass
        self.signals.done.emit(self.asset_id, [])


class ProxyWorker(QRunnable):
    """Generate a low-resolution proxy for a video asset.

    The proxy is a half-height (or *proxy_height*-capped) MP4 with ultrafast
    encoding, stored in the cache directory.  On success the asset's
    ``proxy_path`` is set.
    """

    def __init__(self, asset_id: str, source_path: str, out_path: str,
                 proxy_height: int = 540) -> None:
        super().__init__()
        self.asset_id = asset_id
        self.source_path = source_path
        self.out_path = out_path
        self.proxy_height = proxy_height
        self.signals = _ProxySignals()
        self._cancel = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            from . import engine
            if not engine.ffmpeg_available():
                self.signals.done.emit(self.asset_id, "", False)
                return
            info = engine.probe_media(self.source_path)
            if info.kind != "video" or info.height <= 0:
                # Audio/images don't need proxies
                self.signals.done.emit(self.asset_id, "", False)
                return
            # Target: half height (or capped), even dimensions
            target_h = min(self.proxy_height, info.height)
            if target_h >= info.height:
                # Already small enough — no proxy needed
                self.signals.done.emit(self.asset_id, "", False)
                return
            target_h = max(16, target_h // 2 * 2)
            scale = target_h / float(info.height)
            target_w = max(16, int(info.width * scale) // 2 * 2)

            import subprocess
            args = [
                engine.ffmpeg_executable(), "-y", "-v", "error",
                "-i", self.source_path,
                "-vf", f"scale={target_w}:{target_h}:flags=bilinear",
                "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
                "-an",  # no audio in proxy (preview uses original audio)
                "-pix_fmt", "yuv420p",
                self.out_path,
            ]
            if hasattr(engine, 'ffmpeg_executable'):
                exe = engine.ffmpeg_executable()
            else:
                exe = "ffmpeg"
            # Use engine's popen_ffmpeg for proper environment
            proc = engine.subprocess.Popen(
                [exe, "-y", "-v", "error",
                 "-i", self.source_path,
                 "-vf", f"scale={target_w}:{target_h}:flags=bilinear",
                 "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
                 "-an", "-pix_fmt", "yuv420p",
                 self.out_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _, stderr = proc.communicate(timeout=120)
            ok = proc.returncode == 0 and os.path.exists(self.out_path)
            if ok and os.path.getsize(self.out_path) < 100:
                ok = False
            if not ok and os.path.exists(self.out_path):
                try:
                    os.unlink(self.out_path)
                except OSError:
                    pass
            self.signals.done.emit(self.asset_id,
                                   self.out_path if ok else "", ok)
        except Exception:
            self.signals.done.emit(self.asset_id, "", False)


class MediaAnalysisWorker(QRunnable):
    """Probe a media file and return analysis metadata.

    Detects VFR (variable frame rate), reports duration/codec/quality info.
    """

    def __init__(self, asset_id: str, source_path: str) -> None:
        super().__init__()
        self.asset_id = asset_id
        self.source_path = source_path
        self.signals = _AnalysisSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        result: Dict[str, Any] = {
            "asset_id": self.asset_id,
            "vfr": False,
            "avg_fps": 0.0,
            "codec": "",
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "has_audio": False,
            "issues": [],
        }
        try:
            from . import engine
            info = engine.probe_media(self.source_path)
            result["duration"] = info.duration
            result["width"] = info.width
            result["height"] = info.height
            result["avg_fps"] = info.fps
            result["codec"] = info.codec
            result["has_audio"] = info.has_audio
            # VFR detection: check if tbr != fps (common heuristic)
            # The engine's MediaInfo doesn't expose tbr directly, but
            # if fps is non-integer or very low it may be VFR
            if info.fps and info.fps > 0:
                # Round fps values that don't match common standards
                standard_fps = {23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60}
                rounded = round(info.fps, 2)
                if not any(abs(rounded - sf) < 0.05 for sf in standard_fps):
                    result["vfr"] = True
                    result["issues"].append(
                        f"Possible VFR: {info.fps:.3f} fps (non-standard)")
            if info.kind == "video" and info.duration > 0 and info.fps > 0:
                expected_frames = info.duration * info.fps
                if (info.frame_count > 0
                        and abs(info.frame_count - expected_frames)
                        > expected_frames * 0.05):
                    result["vfr"] = True
                    result["issues"].append(
                        f"Frame count mismatch: {info.frame_count} actual "
                        f"vs ~{int(expected_frames)} expected — likely VFR")
        except Exception as exc:
            result["issues"].append(f"Analysis failed: {exc}")
        self.signals.done.emit(self.asset_id, result)


class ExportWorker(QRunnable):
    """Render a project to MP4 on a background thread.

    This is used by the render queue.  Emits progress during rendering and
    a final done signal.
    """

    def __init__(self, project_data: dict, output_path: str,
                 preset_key: str = "balanced") -> None:
        super().__init__()
        self.project_data = project_data
        self.output_path = output_path
        self.preset_key = preset_key
        self.signals = _ExportSignals()
        self._cancel = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        t0 = time.time()
        try:
            from . import engine
            from .rendering import exporter
            project = engine.Project.from_dict(self.project_data)

            def progress_fn(frac: float, msg: str) -> None:
                self.signals.progress.emit(frac, msg)

            def cancel_fn() -> bool:
                return self._cancel

            outcome = exporter.export_project(
                project, self.output_path, self.preset_key,
                progress=progress_fn, cancel=cancel_fn)
            elapsed = time.time() - t0
            if self._cancel:
                self.signals.cancelled.emit()
                return
            self.signals.done.emit(outcome.ok,
                                   outcome.output or self.output_path,
                                   elapsed)
        except Exception as exc:
            elapsed = time.time() - t0
            self.signals.done.emit(False, self.output_path, elapsed)


# --------------------------------------------------------------------------- #
#  Render queue
# --------------------------------------------------------------------------- #

class RenderQueue(QObject):
    """A simple FIFO queue for background exports.

    Only one export runs at a time.  The UI can add jobs and connect to
    progress/done signals.
    """

    job_started = Signal(str)               # output path
    job_progress = Signal(float, str)       # fraction, message
    job_done = Signal(str, bool, float)     # output, ok, elapsed
    queue_empty = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: List[Dict[str, Any]] = []
        self._current: Optional[ExportWorker] = None
        self._pool = QThreadPool.globalInstance()

    @property
    def busy(self) -> bool:
        return self._current is not None

    @property
    def pending(self) -> int:
        return len(self._queue)

    def enqueue(self, project_data: dict, output_path: str,
                preset_key: str = "balanced") -> None:
        """Add an export job to the queue."""
        self._queue.append({
            "project_data": project_data,
            "output_path": output_path,
            "preset_key": preset_key,
        })
        if not self._current:
            self._start_next()

    def cancel_current(self) -> None:
        """Cancel the currently running export."""
        if self._current:
            self._current.cancel()

    def clear(self) -> None:
        """Remove all pending jobs (does not cancel the running one)."""
        self._queue.clear()

    def _start_next(self) -> None:
        if not self._queue:
            self._current = None
            self.queue_empty.emit()
            return
        job = self._queue.pop(0)
        worker = ExportWorker(
            job["project_data"], job["output_path"], job["preset_key"])
        worker.signals.progress.connect(self._on_progress)
        worker.signals.done.connect(self._on_done)
        worker.signals.cancelled.connect(self._on_cancelled)
        self._current = worker
        self.job_started.emit(job["output_path"])
        self._pool.start(worker)

    def _on_progress(self, frac: float, msg: str) -> None:
        self.job_progress.emit(frac, msg)

    def _on_done(self, ok: bool, output: str, elapsed: float) -> None:
        self.job_done.emit(output, ok, elapsed)
        self._current = None
        self._start_next()

    def _on_cancelled(self) -> None:
        self._current = None
        self._start_next()