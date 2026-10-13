"""The multitrack timeline widget.

Original OpenCut Studio implementation: fixed track headers on the left,
a time ruler on top, clip blocks you can select, drag to move, and drag
at the edges to trim.  All edits go through :mod:`opencut.timeline.ops`
and are recorded in the engine's undo history (a drag is one undo step:
the snapshot is taken when the gesture starts and pushed when it ends).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QMouseEvent, QPainter, QPen,
                           QWheelEvent)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollBar, QSlider, QVBoxLayout, QWidget)

from .. import engine
from ..engine import Clip, History, Project, Track
from ..timeline import ops

HEADER_W = 118
RULER_H = 24
EDGE_PX = 7                       # pixels from a clip edge that mean "trim"
KIND_COLORS = {
    "video": QColor("#3f74a8"),
    "image": QColor("#4f9d7a"),
    "overlay": QColor("#4f9d7a"),
    "audio": QColor("#5d8a3c"),
    "text": QColor("#8a63b8"),
}
TRACK_KIND_LABEL = {"video": "VIDEO", "overlay": "OVERLAY",
                    "text": "TEXT", "audio": "AUDIO"}


def _fmt_ruler(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


class TimelineWidget(QWidget):
    """Multitrack timeline with ruler, playhead and clip editing."""

    playhead_changed = Signal(float)
    selection_changed = Signal(list)
    project_changed = Signal()          # an edit was applied (undo recorded)
    split_requested = Signal()
    duplicate_requested = Signal()
    delete_requested = Signal()
    play_toggle_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.history: Optional[History] = None
        self.px_per_sec = 60.0
        self.playhead = 0.0
        self.selection: set = set()
        self._scroll_x = 0.0            # pixels
        self._scroll_y = 0.0
        self._drag: Optional[dict] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        self._split_btn = QPushButton("Split")
        self._split_btn.setToolTip("Split the selected clip(s) at the playhead (S)")
        self._split_btn.clicked.connect(self.split_requested)
        self._dup_btn = QPushButton("Duplicate")
        self._dup_btn.setToolTip("Duplicate the selected clip(s) (Ctrl+D)")
        self._dup_btn.clicked.connect(self.duplicate_requested)
        self._del_btn = QPushButton("Delete")
        self._del_btn.setToolTip("Delete the selected clip(s) (Del)")
        self._del_btn.clicked.connect(self.delete_requested)
        self._track_btn = QPushButton("Add track")
        self._track_btn.setToolTip("Add a video, overlay, text or audio track")
        self._track_btn.clicked.connect(self._add_track_menu)
        for btn in (self._split_btn, self._dup_btn, self._del_btn, self._track_btn):
            bar.addWidget(btn)
        bar.addStretch(1)
        bar.addWidget(QLabel("Zoom"))
        self._zoom = QSlider(Qt.Horizontal)
        self._zoom.setRange(0, 100)
        self._zoom.setValue(45)
        self._zoom.setFixedWidth(140)
        self._zoom.setToolTip("Timeline zoom (Ctrl+mouse wheel over the timeline)")
        self._zoom.valueChanged.connect(self._zoom_changed)
        bar.addWidget(self._zoom)
        layout.addLayout(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._canvas = QWidget()
        self._canvas.setMouseTracking(True)
        self._canvas.setFocusPolicy(Qt.StrongFocus)
        self._canvas.paintEvent = self._paint_canvas          # type: ignore
        self._canvas.mousePressEvent = self._press            # type: ignore
        self._canvas.mouseMoveEvent = self._move              # type: ignore
        self._canvas.mouseReleaseEvent = self._release        # type: ignore
        self._canvas.mouseDoubleClickEvent = self._double     # type: ignore
        self._canvas.wheelEvent = self._wheel                 # type: ignore
        self._canvas.keyPressEvent = self._key                # type: ignore
        body.addWidget(self._canvas, 1)
        self._vscroll = QScrollBar(Qt.Vertical)
        self._vscroll.setFixedWidth(12)
        self._vscroll.valueChanged.connect(self._scrolled)
        body.addWidget(self._vscroll)
        layout.addLayout(body, 1)

        self._hscroll = QScrollBar(Qt.Horizontal)
        self._hscroll.setFixedHeight(12)
        self._hscroll.valueChanged.connect(self._scrolled)
        layout.addWidget(self._hscroll)
        self.setMinimumHeight(190)

    # ------------------------------------------------------------------ #
    #  Setup
    # ------------------------------------------------------------------ #
    def set_project(self, project: Optional[Project],
                    history: Optional[History]) -> None:
        self.project = project
        self.history = history
        self.selection = set()
        self.playhead = 0.0
        self._update_scrollbars()
        self._canvas.update()

    def refresh(self) -> None:
        self._update_scrollbars()
        self._canvas.update()

    def set_playhead(self, t: float, emit: bool = True) -> None:
        t = max(0.0, float(t))
        if abs(t - self.playhead) > 1e-9:
            self.playhead = t
            self._canvas.update()
            if emit:
                self.playhead_changed.emit(t)

    def set_selection(self, clip_ids: List[str]) -> None:
        new = set(clip_ids)
        if new != self.selection:
            self.selection = new
            self._canvas.update()
            self.selection_changed.emit(sorted(new))

    def selected_clips(self) -> List[Clip]:
        if self.project is None:
            return []
        return [c for c in self.project.all_clips() if c.id in self.selection]

    # ------------------------------------------------------------------ #
    #  Geometry
    # ------------------------------------------------------------------ #
    def _content_width_time(self) -> float:
        if self.project is None:
            return 30.0
        return max(30.0, self.project.duration + 10.0)

    def _track_y(self, index: int) -> float:
        assert self.project is not None
        y = RULER_H - self._scroll_y
        for tr in self.project.tracks[:index]:
            y += tr.height
        return y

    def _tracks_height(self) -> float:
        if self.project is None:
            return 0.0
        return sum(tr.height for tr in self.project.tracks)

    def _x_to_time(self, x: float) -> float:
        return max(0.0, (x - HEADER_W + self._scroll_x) / self.px_per_sec)

    def _time_to_x(self, t: float) -> float:
        return HEADER_W + t * self.px_per_sec - self._scroll_x

    def _update_scrollbars(self) -> None:
        view_w = max(50, self._canvas.width() - HEADER_W)
        content = self._content_width_time() * self.px_per_sec
        self._hscroll.setRange(0, max(0, int(content - view_w)))
        self._hscroll.setPageStep(int(view_w))
        view_h = max(50, self._canvas.height() - RULER_H)
        self._vscroll.setRange(0, max(0, int(self._tracks_height() - view_h)))
        self._vscroll.setPageStep(int(view_h))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._update_scrollbars()

    def _scrolled(self) -> None:
        self._scroll_x = float(self._hscroll.value())
        self._scroll_y = float(self._vscroll.value())
        self._canvas.update()

    def _zoom_changed(self, value: int) -> None:
        # exponential zoom: slider 0..100 -> 8..300 px/sec
        self.px_per_sec = 8.0 * (300.0 / 8.0) ** (value / 100.0)
        self._update_scrollbars()
        self._canvas.update()

    # ------------------------------------------------------------------ #
    #  Painting
    # ------------------------------------------------------------------ #
    def _paint_canvas(self, event) -> None:  # noqa: ARG002
        p = QPainter(self._canvas)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self._canvas.width(), self._canvas.height()
        p.fillRect(0, 0, w, h, QColor("#171a1f"))
        if self.project is None:
            p.setPen(QColor("#9aa3ad"))
            p.drawText(20, 40, "No project open")
            p.end()
            return
        self._paint_ruler(p, w)
        for i, track in enumerate(self.project.tracks):
            self._paint_track(p, i, track, w)
        # playhead
        x = self._time_to_x(self.playhead)
        if HEADER_W - 1 <= x <= w:
            p.setPen(QPen(QColor("#ffcf4d"), 1.6))
            p.drawLine(int(x), 0, int(x), h)
            p.setBrush(QColor("#ffcf4d"))
            p.setPen(Qt.NoPen)
            p.drawPolygon([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 9)])
        p.end()

    def _paint_ruler(self, p: QPainter, w: int) -> None:
        p.fillRect(HEADER_W, 0, w - HEADER_W, RULER_H, QColor("#1d2025"))
        p.fillRect(0, 0, HEADER_W, RULER_H, QColor("#1d2025"))
        step = 1.0
        while step * self.px_per_sec < 55:
            step *= 2 if step >= 1 else 5
            if step > 3600:
                break
        t0 = max(0.0, (self._scroll_x) / self.px_per_sec)
        t = int(t0 / step) * step
        p.setFont(QFont("Sans", 8))
        while True:
            x = self._time_to_x(t)
            if x > w:
                break
            if x >= HEADER_W:
                p.setPen(QColor("#4a5158"))
                p.drawLine(int(x), RULER_H - 7, int(x), RULER_H)
                p.setPen(QColor("#9aa3ad"))
                p.drawText(int(x) + 3, RULER_H - 9, _fmt_ruler(t))
            t += step

    def _paint_track(self, p: QPainter, index: int, track: Track, w: int) -> None:
        y = self._track_y(index)
        th = track.height
        if y + th < RULER_H or y > self._canvas.height():
            return
        lane = QColor("#1b1e24") if index % 2 == 0 else QColor("#20242b")
        if track.hidden:
            lane = QColor("#14161a")
        p.fillRect(HEADER_W, int(y), w - HEADER_W, int(th), lane)
        p.setPen(QColor("#2c313a"))
        p.drawLine(HEADER_W, int(y + th), w, int(y + th))
        # header
        p.fillRect(0, int(y), HEADER_W, int(th), QColor("#262a31"))
        p.setPen(QColor("#e8eaed"))
        p.setFont(QFont("Sans", 9, QFont.Bold))
        p.drawText(8, int(y) + 16, track.name[:16])
        p.setFont(QFont("Sans", 7))
        p.setPen(QColor("#9aa3ad"))
        p.drawText(8, int(y) + 28,
                   TRACK_KIND_LABEL.get(track.kind, track.kind.upper()))
        # hide / mute toggles
        self._header_btn_rects(track, y)
        for key, label, active in (
                ("hidden", "H", track.hidden), ("muted", "M", track.muted)):
            rect = self._btn_rect(key, y)
            p.setBrush(QColor("#e5534b") if active else QColor("#3a4049"))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Sans", 8, QFont.Bold))
            p.drawText(rect, Qt.AlignCenter, label)
        # clips
        for clip in track.clips:
            self._paint_clip(p, track, clip, y, th)

    def _btn_rect(self, key: str, y: float) -> QRectF:
        x = HEADER_W - 46 if key == "hidden" else HEADER_W - 24
        return QRectF(x, y + 6, 18, 16)

    def _header_btn_rects(self, track: Track, y: float) -> None:
        # (kept for symmetry; rects are computed on demand in _btn_rect)
        return None

    def _paint_clip(self, p: QPainter, track: Track, clip: Clip,
                    y: float, th: float) -> None:
        x = self._time_to_x(clip.start)
        wdt = max(3.0, clip.duration * self.px_per_sec)
        if x + wdt < HEADER_W or x > self._canvas.width():
            return
        base = KIND_COLORS.get(clip.kind, QColor("#666666"))
        if track.kind == "overlay" and clip.kind != "text":
            base = KIND_COLORS["overlay"]
        rect = QRectF(x + 1, y + 4, wdt - 2, th - 8)
        selected = clip.id in self.selection
        p.setBrush(base if clip.enabled else base.darker(160))
        p.setPen(QPen(QColor("#ffcf4d") if selected else base.lighter(130),
                      2 if selected else 1))
        p.drawRoundedRect(rect, 5, 5)
        # label
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Sans", 8))
        label = clip.name or clip.kind
        if clip.kind == "text" and clip.text is not None:
            label = clip.text.text.splitlines()[0][:40] or "Text"
        p.drawText(rect.adjusted(6, 3, -4, 0), Qt.AlignLeft | Qt.AlignTop,
                   label)
        p.setFont(QFont("Sans", 7))
        p.setPen(QColor(255, 255, 255, 190))
        info = f"{clip.duration:.1f}s"
        if clip.speed not in (0.0, 1.0):
            info += f"  {clip.speed:g}x"
        p.drawText(rect.adjusted(6, 0, -4, -3), Qt.AlignLeft | Qt.AlignBottom,
                   info)
        # waveform hint for audio clips with cached peaks
        if clip.kind == "audio" and self.project is not None:
            asset = self.project.asset(clip.asset_id)
            peaks = asset.peaks if asset is not None else []
            if peaks:
                p.setPen(QColor(255, 255, 255, 120))
                mid = rect.center().y() + 6
                n = max(1, int(rect.width()))
                for i in range(n):
                    frac = i / max(1, n - 1)
                    peak = peaks[min(len(peaks) - 1, int(frac * len(peaks)))]
                    amp = max(0.02, min(1.0, peak)) * (rect.height() / 2 - 8)
                    xx = rect.x() + i
                    p.drawLine(int(xx), int(mid - amp), int(xx), int(mid + amp))

    # ------------------------------------------------------------------ #
    #  Hit testing
    # ------------------------------------------------------------------ #
    def _track_at(self, y: float) -> Tuple[Optional[Track], int]:
        if self.project is None:
            return None, -1
        yy = RULER_H
        for i, track in enumerate(self.project.tracks):
            if yy - self._scroll_y <= y < yy - self._scroll_y + track.height:
                return track, i
            yy += track.height
        return None, -1

    def _clip_at(self, x: float, y: float) -> Tuple[Optional[Track], Optional[Clip], str]:
        """Returns (track, clip, zone) where zone is body/left/right."""
        track, _ = self._track_at(y)
        if track is None or self.project is None:
            return None, None, ""
        t = self._x_to_time(x)
        for clip in sorted(track.clips, key=lambda c: c.start, reverse=True):
            if clip.start <= t <= clip.end:
                cx = self._time_to_x(clip.start)
                cw = clip.duration * self.px_per_sec
                if x - cx <= EDGE_PX:
                    return track, clip, "left"
                if (cx + cw) - x <= EDGE_PX:
                    return track, clip, "right"
                return track, clip, "body"
        return track, None, ""

    # ------------------------------------------------------------------ #
    #  Mouse
    # ------------------------------------------------------------------ #
    def _press(self, event: QMouseEvent) -> None:
        self._canvas.setFocus()
        if self.project is None or event.button() != Qt.LeftButton:
            return
        pos = event.position()
        x, y = pos.x(), pos.y()
        if x < HEADER_W:
            track, _ = self._track_at(y)
            if track is not None:
                ty = self._track_y(self.project.tracks.index(track))
                for key in ("hidden", "muted"):
                    if self._btn_rect(key, ty).contains(pos):
                        self._toggle_track_flag(track, key)
                        return
            return
        if y < RULER_H:
            self._drag = {"mode": "playhead"}
            self.set_playhead(self._x_to_time(x))
            return
        track, clip, zone = self._clip_at(x, y)
        if clip is None:
            self.set_selection([])
            self.set_playhead(self._x_to_time(x))
            self._drag = {"mode": "playhead"}
            return
        if event.modifiers() & Qt.ShiftModifier:
            new = set(self.selection)
            new.symmetric_difference_update({clip.id})
            self.set_selection(sorted(new))
            return
        if clip.id not in self.selection:
            self.set_selection([clip.id])
        assert self.history is not None
        self._drag = {
            "mode": {"body": "move", "left": "trim_left",
                     "right": "trim_right"}[zone],
            "clip_id": clip.id,
            "start_time": self._x_to_time(x),
            "orig_start": clip.start,
            "orig_end": clip.end,
            "before": self.history.snapshot(),
            "moved": False,
        }
        self._canvas.update()

    def _move(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self.project is None:
            return
        if self._drag is None:
            # cursor feedback
            if pos.x() >= HEADER_W and pos.y() >= RULER_H:
                _, clip, zone = self._clip_at(pos.x(), pos.y())
                if clip is not None and zone in ("left", "right"):
                    self._canvas.setCursor(Qt.SizeHorCursor)
                elif clip is not None:
                    self._canvas.setCursor(Qt.PointingHandCursor)
                else:
                    self._canvas.setCursor(Qt.IBeamCursor)
            return
        mode = self._drag["mode"]
        t = self._x_to_time(pos.x())
        if mode == "playhead":
            self.set_playhead(t)
            return
        clip = self.project.clip(self._drag["clip_id"])
        if clip is None:
            return
        if mode == "move":
            new_start = self._drag["orig_start"] + (t - self._drag["start_time"])
            new_start = self._snapped(new_start, clip)
            ops.move_clip(self.project, clip, new_start)
        elif mode == "trim_left":
            ops.trim_clip(self.project, clip, "left", self._snapped(t, clip))
        elif mode == "trim_right":
            ops.trim_clip(self.project, clip, "right", self._snapped(t, clip))
        self._drag["moved"] = True
        self._canvas.update()

    def _snapped(self, t: float, clip: Clip) -> float:
        """Snap to the playhead and neighbouring clip edges within 8 px."""
        if self.project is None:
            return t
        threshold = 8.0 / self.px_per_sec
        candidates = [self.playhead, 0.0]
        for other in self.project.all_clips():
            if other.id != clip.id:
                candidates += [other.start, other.end]
        best = t
        for cand in candidates:
            if abs(cand - t) < threshold:
                best = cand
                threshold = abs(cand - t)
        return best

    def _release(self, event: QMouseEvent) -> None:  # noqa: ARG002
        if self._drag and self._drag.get("moved") and self.history is not None:
            label = {"move": "Move clip", "trim_left": "Trim clip",
                     "trim_right": "Trim clip"}.get(self._drag["mode"], "Edit")
            after = self.history.snapshot()
            if after != self._drag["before"]:
                self.history.push(
                    engine.SnapshotCommand(label, self._drag["before"], after),
                    _already_applied=True)
                self.project_changed.emit()
        self._drag = None
        self._canvas.update()

    def _double(self, event: QMouseEvent) -> None:
        """Double-click a clip: split it at that point."""
        if self.project is None:
            return
        pos = event.position()
        if pos.x() < HEADER_W or pos.y() < RULER_H:
            return
        _, clip, _zone = self._clip_at(pos.x(), pos.y())
        if clip is None or self.history is None:
            return
        t = self._x_to_time(pos.x())
        made = []
        def apply(p: Project) -> None:
            pair = ops.split_clip_at(p, clip, t)
            if pair:
                made.extend([pair[0].id, pair[1].id])
        self.history.transaction("Split clip", apply)
        if made:
            self.set_selection(made[:1])
            self.project_changed.emit()

    def _wheel(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.ControlModifier:
            centre = self._x_to_time(event.position().x())
            step = 6 if delta > 0 else -6
            self._zoom.setValue(max(0, min(100, self._zoom.value() + step)))
            # keep the time under the cursor stationary
            self._scroll_x = max(
                0.0, centre * self.px_per_sec
                - (event.position().x() - HEADER_W))
            self._hscroll.setValue(int(self._scroll_x))
        else:
            self._hscroll.setValue(int(self._hscroll.value() - delta))
        self._canvas.update()

    def _key(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Delete:
            self.delete_requested.emit()
        elif key == Qt.Key_Space:
            self.play_toggle_requested.emit()
        elif key == Qt.Key_S and not (event.modifiers() & Qt.ControlModifier):
            self.split_requested.emit()
        elif key == Qt.Key_Left:
            self.set_playhead(self.playhead - (1.0 / 30.0))
        elif key == Qt.Key_Right:
            self.set_playhead(self.playhead + (1.0 / 30.0))
        else:
            event.ignore()

    # ------------------------------------------------------------------ #
    #  Track header actions
    # ------------------------------------------------------------------ #
    def _toggle_track_flag(self, track: Track, key: str) -> None:
        if self.history is None or self.project is None:
            return
        def apply(p: Project) -> None:
            tr = p.track(track.id)
            if tr is not None:
                setattr(tr, key, not getattr(tr, key))
        label = ("Show track" if getattr(track, key) else "Hide track") \
            if key == "hidden" else \
            ("Unmute track" if track.muted else "Mute track")
        self.history.transaction(label, apply)
        self.project_changed.emit()

    def _add_track_menu(self) -> None:
        if self.project is None or self.history is None:
            return
        menu = QMenu(self)
        for kind in ("video", "overlay", "text", "audio"):
            act = menu.addAction(f"{kind.capitalize()} track")
            act.triggered.connect(lambda _=False, k=kind: self._add_track(k))
        menu.exec(self._track_btn.mapToGlobal(self._track_btn.rect().bottomLeft()))

    def _add_track(self, kind: str) -> None:
        assert self.project is not None and self.history is not None
        def apply(p: Project) -> None:
            p.add_track(kind=kind)
        self.history.transaction(f"Add {kind} track", apply)
        self.project_changed.emit()
