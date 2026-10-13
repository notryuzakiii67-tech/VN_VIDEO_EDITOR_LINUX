"""The multitrack timeline.

Implements VN's core timeline behaviours:

* horizontally zoomed/scrollable time ruler with frame-accurate playhead
* a fixed track-header column and a scrolling lane area
* rounded clip widgets with name, thumbnail strip, speed/sfx/filter badges,
  keyframe diamonds and transition wedges drawn on the clip
* audio waveform rendering
* drag to move (with snapping to clip edges, playhead, markers and beats),
  drag the leading/trailing 8 px to trim, drag between tracks
* marquee-less multi-select is supported with Ctrl/Shift
* an overlay for transitions, keyframe editing and the beat strip

The widget owns no project logic: it emits intent signals and the window
applies them through the undo history.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import (QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                            Signal, QTimer)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from ..core.model import Clip, Project, Track, clip_z_order
from . import theme
from .theme import pixmap as icon_pm

# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #

HEADER_W = 132
RULER_H = 30
LANE_GAP = 4
MIN_CLIP_PX = 3
HANDLE_PX = 9
SNAP_PX = 9
KEYFRAME_ROW_H = 13
TRANSITION_WEDGE_PX = 15

CLIP_COLORS = {
    "video": theme.CLIP_VIDEO,
    "image": theme.CLIP_IMAGE,
    "audio": theme.CLIP_AUDIO,
    "text": theme.CLIP_TEXT,
    "sticker": theme.CLIP_STICKER,
    "shape": "#0ea5a5",
    "adjustment": "#64748b",
}


def clip_color(clip: Clip) -> str:
    if clip.color:
        return clip.color
    return CLIP_COLORS.get(clip.kind, theme.CLIP_VIDEO)


def shade(hex_color: str, factor: float) -> QColor:
    c = QColor(hex_color)
    if factor >= 1.0:
        return c.lighter(int(factor * 100))
    return c.darker(int(100 / max(0.05, factor)))


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #

def fmt_timecode(t: float, fps: float = 30.0, frames: bool = True) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    if not frames:
        return f"{h:d}:{m:02d}:{s:02d}"
    f = int(round((t - int(t)) * fps)) % max(1, int(round(fps)))
    if h:
        return f"{h:d}:{m:02d}:{s:02d}:{f:02d}"
    return f"{m:02d}:{s:02d}:{f:02d}"


def fmt_short(t: float) -> str:
    if t < 1:
        return f"{int(round(t * 1000))}ms"
    if t < 60:
        return f"{t:.2f}s" if t != int(t) else f"{int(t)}s"
    m = int(t // 60)
    s = t - m * 60
    return f"{m}m{s:04.1f}s"


# --------------------------------------------------------------------------- #
#  Thumbnail cache
# --------------------------------------------------------------------------- #

_THUMB_CACHE: Dict[Tuple[str, int], QPixmap] = {}


def thumb_for(path: str, height: int, at: float = 1.0) -> Optional[QPixmap]:
    key = (path, height)
    hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit if not hit.isNull() else None
    from ..core.probe import make_thumbnail
    from ..core.project import thumb_dir
    import hashlib, os
    digest = hashlib.sha1(f"{path}|{height}|{at:.2f}".encode()).hexdigest()[:20]
    out = thumb_dir() / f"{digest}.png"
    if not out.exists():
        make_thumbnail(path, str(out), at=at, width=max(48, height * 2))
    if out.exists():
        pm = QPixmap(str(out))
        if not pm.isNull():
            pm = pm.scaledToHeight(height, Qt.SmoothTransformation)
            _THUMB_CACHE[key] = pm
            return pm
    _THUMB_CACHE[key] = QPixmap()
    return None


def waveform_for(path: str, buckets: int = 600) -> List[float]:
    key = (path, -buckets)
    if key in _WAVE_CACHE:
        return _WAVE_CACHE[key]
    from ..core.probe import waveform_peaks
    peaks = waveform_peaks(path, buckets)
    _WAVE_CACHE[key] = peaks
    return peaks


_WAVE_CACHE: Dict[Tuple[str, int], List[float]] = {}


# --------------------------------------------------------------------------- #
#  Track header
# --------------------------------------------------------------------------- #

class TrackHeader(QWidget):
    muteClicked = Signal(str)
    lockClicked = Signal(str)
    hideClicked = Signal(str)
    removeClicked = Signal(str)
    soloClicked = Signal(str)
    addClicked = Signal(str)

    def __init__(self, track: Track, parent=None) -> None:
        super().__init__(parent)
        self.track = track
        self.setFixedWidth(HEADER_W)
        self.setMouseTracking(True)
        self._hover = ""

    def set_track(self, track: Track) -> None:
        self.track = track
        self.update()

    def _btn_rects(self) -> Dict[str, QRectF]:
        h = self.height()
        y = h - 22
        r = {}
        x = 8
        for key, w in (("mute", 18), ("hide", 18), ("lock", 18)):
            r[key] = QRectF(x, y, w, 16)
            x += w + 3
        r["solo"] = QRectF(x, y, 26, 16)
        r["remove"] = QRectF(HEADER_W - 20, y, 14, 16)
        return r

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        base = {"video": theme.TRACK_VIDEO, "overlay": theme.TRACK_OVERLAY,
                "text": theme.TRACK_TEXT, "audio": theme.TRACK_AUDIO,
                "sticker": theme.TRACK_STICKER}.get(self.track.kind,
                                                    theme.BG_PANEL_ALT)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_PANEL_ALT))
        p.drawRoundedRect(QRectF(rect), 6, 6)
        p.setBrush(QColor(base).darker(190))
        p.drawRoundedRect(QRectF(rect.x(), rect.y(), 3, rect.height()), 2, 2)

        p.setPen(QColor(theme.TEXT))
        f = theme.ui_font(11, 600)
        p.setFont(f)
        name = self.track.label()
        label = QFontMetrics(f).elidedText(name, Qt.ElideRight,
                                           rect.width() - 34)
        p.drawText(QRectF(rect.x() + 10, rect.y() + 6, rect.width() - 30, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, label)

        counts = len(self.track.clips)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(9))
        p.drawText(QRectF(rect.x() + 10, rect.y() + 22, rect.width() - 20, 12),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"{counts} clip{'s' if counts != 1 else ''}")

        for key, r in self._btn_rects().items():
            active = False
            label_txt = ""
            if key == "mute":
                active = self.track.muted
                label_txt = "M"
            elif key == "hide":
                active = self.track.hidden
                label_txt = "V"
            elif key == "lock":
                active = self.track.locked
                label_txt = "L"
            elif key == "solo":
                active = self.track.solo
                label_txt = "SOLO"
            elif key == "remove":
                label_txt = "×"
            color = QColor(theme.ACCENT if active else theme.TEXT_MUTED)
            if not active and self._hover == key:
                color = QColor(theme.TEXT)
            p.setPen(QPen(color, 1))
            p.setBrush(QColor(theme.ACCENT).darker(140) if active
                       else QColor(theme.BG_INPUT))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(color)
            p.setFont(theme.ui_font(9 if key != "solo" else 7, 700))
            p.drawText(r, Qt.AlignCenter, label_txt)
        p.end()

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        hover = ""
        for key, r in self._btn_rects().items():
            if r.contains(pos):
                hover = key
                break
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = ""
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mousePressEvent(self, ev) -> None:
        pos = ev.position()
        for key, r in self._btn_rects().items():
            if not r.contains(pos):
                continue
            if key == "mute" and self.track.kind == "audio":
                self.muteClicked.emit(self.track.id)
            elif key == "hide" and self.track.kind != "audio":
                self.hideClicked.emit(self.track.id)
            elif key == "mute" and self.track.kind != "audio":
                self.hideClicked.emit(self.track.id)
            elif key == "lock":
                self.lockClicked.emit(self.track.id)
            elif key == "solo":
                self.soloClicked.emit(self.track.id)
            elif key == "remove":
                self.removeClicked.emit(self.track.id)
            self.update()
            return


# --------------------------------------------------------------------------- #
#  Timeline widget
# --------------------------------------------------------------------------- #

class TimelineWidget(QWidget):
    """Canvas holding the ruler, lanes and all clip interaction."""

    playheadMoved = Signal(float, bool)          # seconds, final
    selectionChanged = Signal(list)              # clip ids
    clipChanged = Signal(str)                    # clip id (geometry edited)
    clipMoved = Signal(str, float, str)          # clip id, start, track id
    editRequested = Signal(str, str)             # action, clip id
    transitionClicked = Signal(str, str)         # clip id, edge
    keyframeClicked = Signal(str, str, float)    # clip id, prop, local t
    trackAction = Signal(str, str)               # track id, action
    contextMenuRequested = Signal(str, QPoint)   # clip id, global pos
    zoomChanged = Signal(float)

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.px_per_sec = 84.0
        self.scroll_x = 0.0
        self.scroll_y = 0
        self.playhead = 0.0
        self.selection: Set[str] = set()
        self.snapping = True
        self.ripple = False
        self.show_waveforms = True
        self.show_thumbnails = True
        self.selected_keyframe: Optional[Tuple[str, str, float]] = None
        self.active_keyframe_prop = "scale"
        self.beat_editing = False
        self._drag: Optional[Dict] = None
        self._hover_clip: Optional[str] = None
        self._hover_zone = ""
        self._hover_track: Optional[str] = None
        self._marquee: Optional[QRectF] = None
        self._last_click_t = 0.0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self._blink = False
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- geometry ---------------------------------------------------------- #
    def _tick(self) -> None:
        if self._drag:
            self._blink = not self._blink
            self.update()

    def content_width(self) -> int:
        dur = self.project.duration
        tail = max(8.0, (self.width() - HEADER_W) / max(1.0, self.px_per_sec))
        return int((max(dur, 0.0) + tail) * self.px_per_sec)

    def time_to_x(self, t: float) -> float:
        return HEADER_W + t * self.px_per_sec - self.scroll_x

    def x_to_time(self, x: float) -> float:
        return max(0.0, (x - HEADER_W + self.scroll_x) / max(1.0, self.px_per_sec))

    def track_rects(self) -> List[Tuple[Track, QRectF]]:
        out: List[Tuple[Track, QRectF]] = []
        y = RULER_H + 2 - self.scroll_y
        for tr in self.project.tracks:
            h = tr.height
            out.append((tr, QRectF(0, y, self.width(), h)))
            y += h + LANE_GAP
        return out

    def total_height(self) -> int:
        return RULER_H + sum(t.height + LANE_GAP for t in self.project.tracks) + 30

    def lane_rect(self, track: Track) -> QRectF:
        for tr, r in self.track_rects():
            if tr.id == track.id:
                return QRectF(HEADER_W, r.y() + 2, self.width() - HEADER_W,
                              r.height() - 4)
        return QRectF()

    def clip_rect(self, clip: Clip) -> QRectF:
        tr = self.project.track(clip.track_id)
        if tr is None:
            return QRectF()
        lane = self.lane_rect(tr)
        if lane.isEmpty():
            return QRectF()
        x0 = self.time_to_x(clip.start)
        x1 = self.time_to_x(clip.end)
        if x1 - x0 < MIN_CLIP_PX:
            x1 = x0 + MIN_CLIP_PX
        return QRectF(x0, lane.y() + 1, x1 - x0, lane.height() - 2)

    def clip_at(self, pos: QPointF) -> Optional[Clip]:
        for tr, r in self.track_rects():
            if pos.y() < r.y() or pos.y() > r.y() + r.height():
                continue
            for c in sorted(tr.clips, key=lambda x: -x.start):
                cr = self.clip_rect(c)
                if not cr.isEmpty() and cr.x() <= pos.x() <= cr.right():
                    return c
            return None
        return None

    def zone_at(self, clip: Clip, pos: QPointF) -> str:
        r = self.clip_rect(clip)
        if r.isEmpty():
            return "body"
        if pos.x() - r.x() < HANDLE_PX and r.width() > HANDLE_PX * 3:
            return "trim_in"
        if r.right() - pos.x() < HANDLE_PX and r.width() > HANDLE_PX * 3:
            return "trim_out"
        return "body"

    # -- zoom / scroll ------------------------------------------------------ #
    def set_zoom(self, px_per_sec: float, anchor_x: Optional[float] = None) -> None:
        old = self.px_per_sec
        new = max(3.0, min(1600.0, px_per_sec))
        if abs(new - old) < 1e-6:
            return
        if anchor_x is None:
            anchor_x = self.width() * 0.5
        t_at = self.x_to_time(anchor_x)
        self.px_per_sec = new
        self.scroll_x = max(0.0, t_at * new - (anchor_x - HEADER_W))
        self.zoomChanged.emit(new)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self.px_per_sec * 1.3)

    def zoom_out(self) -> None:
        self.set_zoom(self.px_per_sec / 1.3)

    def zoom_fit(self) -> None:
        dur = max(1.0, self.project.duration)
        self.px_per_sec = max(4.0, (self.width() - HEADER_W - 30) / dur)
        self.scroll_x = 0.0
        self.zoomChanged.emit(self.px_per_sec)
        self.update()

    def ensure_visible(self, t: float) -> None:
        x = self.time_to_x(t)
        if x < HEADER_W + 40:
            self.scroll_x = max(0.0, self.scroll_x - (HEADER_W + 40 - x))
            self.update()
        elif x > self.width() - 60:
            self.scroll_x += (x - (self.width() - 60))
            self.update()

    def set_playhead(self, t: float, emit: bool = False) -> None:
        self.playhead = max(0.0, t)
        self.update()
        if emit:
            self.playheadMoved.emit(self.playhead, True)

    # -- snapping ---------------------------------------------------------- #
    def snap_targets(self, exclude: Set[str]) -> List[float]:
        pts = [0.0, self.playhead]
        for tr in self.project.tracks:
            for c in tr.clips:
                if c.id in exclude:
                    continue
                pts.append(c.start)
                pts.append(c.end)
        pts += [float(m.get("t", 0.0)) for m in self.project.markers]
        if self.snapping:
            pts += [b for b in self.project.beats]
        return pts

    def snap(self, t: float, exclude: Set[str]) -> float:
        if not self.snapping:
            return t
        best = t
        best_d = SNAP_PX / max(1.0, self.px_per_sec)
        for p in self.snap_targets(exclude):
            d = abs(p - t)
            if d < best_d:
                best_d = d
                best = p
        return best

    # -- painting ----------------------------------------------------------- #
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(theme.BG_ROOT))
        self._paint_ruler(p)
        self._paint_lanes(p)
        self._paint_clips(p)
        self._paint_overlays(p)
        self._paint_playhead(p)
        p.end()

    def _paint_ruler(self, p: QPainter) -> None:
        r = QRectF(0, 0, self.width(), RULER_H)
        p.fillRect(r, QColor(theme.BG_PANEL))
        p.setPen(QPen(QColor(theme.BORDER_SOFT), 1))
        p.drawLine(QPointF(0, RULER_H - 0.5), QPointF(self.width(), RULER_H - 0.5))

        pps = self.px_per_sec
        # choose a tick step that keeps labels readable
        candidates = [0.02, 0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60,
                      120, 300, 600]
        step = candidates[-1]
        for c in candidates:
            if c * pps >= 60:
                step = c
                break
        minor = step / 5.0
        t0 = math.floor(self.x_to_time(HEADER_W) / minor) * minor
        t = t0
        p.setFont(theme.ui_font(9))
        while True:
            x = self.time_to_x(t)
            if x > self.width():
                break
            if x >= HEADER_W - 1:
                major = abs(t / step - round(t / step)) < 1e-6
                h = 10 if major else 5
                p.setPen(QPen(QColor(theme.TEXT_MUTED if major
                                     else theme.GRID_STRONG), 1))
                p.drawLine(QPointF(x, RULER_H - h), QPointF(x, RULER_H - 1))
                if major:
                    p.setPen(QColor(theme.TEXT_DIM))
                    p.drawText(QRectF(x + 3, 2, 70, 12),
                               Qt.AlignLeft | Qt.AlignVCenter,
                               fmt_timecode(t, self.project.settings.fps, False)
                               if step >= 1 else f"{t:.2f}s")
            t += minor
            if t > 1e7:
                break

        # beat markers under the ruler
        if self.project.beats:
            p.setPen(QPen(QColor("#5ad1a0"), 1))
            for b in self.project.beats[:4000]:
                x = self.time_to_x(b)
                if HEADER_W <= x <= self.width():
                    p.drawLine(QPointF(x, RULER_H - 4), QPointF(x, RULER_H))

        # header block
        p.fillRect(QRectF(0, 0, HEADER_W, RULER_H), QColor(theme.BG_PANEL_ALT))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(HEADER_W - 0.5, 0), QPointF(HEADER_W - 0.5, self.height()))
        # markers
        for m in self.project.markers:
            x = self.time_to_x(float(m.get("t", 0.0)))
            if HEADER_W <= x <= self.width():
                color = QColor(m.get("color") or theme.ACCENT_SOFT)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawPolygon(QPolygonF([QPointF(x - 4, 0), QPointF(x + 4, 0),
                                         QPointF(x, 7)]))
        p.setPen(QColor(theme.TEXT_DIM))
        p.setFont(theme.ui_font(9, 600))
        p.drawText(QRectF(8, 4, HEADER_W - 16, 14), Qt.AlignLeft,
                   fmt_timecode(self.playhead, self.project.settings.fps))

    def _paint_lanes(self, p: QPainter) -> None:
        for tr, r in self.track_rects():
            if r.bottom() < RULER_H or r.y() > self.height():
                continue
            base = {"video": theme.TRACK_VIDEO, "overlay": theme.TRACK_OVERLAY,
                    "text": theme.TRACK_TEXT, "audio": theme.TRACK_AUDIO,
                    "sticker": theme.TRACK_STICKER}.get(tr.kind,
                                                        theme.BG_PANEL_ALT)
            lane = QRectF(HEADER_W, r.y() + 2, self.width() - HEADER_W,
                          r.height() - 4)
            p.setPen(Qt.NoPen)
            fill = QColor(theme.BG_PANEL)
            if self._hover_track == tr.id:
                fill = QColor(theme.BG_PANEL_ALT)
            p.setBrush(fill)
            p.drawRoundedRect(lane, 6, 6)
            p.setBrush(QColor(base).darker(230))
            p.drawRoundedRect(QRectF(lane.x(), lane.y(), 3, lane.height()), 2, 2)
            if tr.hidden or tr.muted:
                p.setBrush(QColor(0, 0, 0, 90))
                p.drawRoundedRect(lane, 6, 6)

        # vertical second grid
        if self.px_per_sec >= 24:
            p.setPen(QPen(QColor(theme.GRID), 1))
            step = 1.0 if self.px_per_sec < 200 else 3.0
            t = math.floor(self.x_to_time(HEADER_W) / step) * step
            while True:
                x = self.time_to_x(t)
                if x > self.width():
                    break
                if x >= HEADER_W:
                    p.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))
                t += step

    def _paint_clips(self, p: QPainter) -> None:
        for tr, r in self.track_rects():
            if r.bottom() < RULER_H - 20 or r.y() > self.height():
                continue
            for c in tr.clips:
                cr = self.clip_rect(c)
                if cr.right() < HEADER_W - 4 or cr.x() > self.width() + 40:
                    continue
                self._paint_clip(p, c, cr, tr)

    def _paint_clip(self, p: QPainter, clip: Clip, cr: QRectF,
                    track: Track) -> None:
        selected = clip.id in self.selection
        base = QColor(clip_color(clip))
        if not clip.enabled:
            base = base.darker(200)
        radius = 7.0
        path = QPainterPath()
        path.addRoundedRect(cr, radius, radius)

        p.save()
        p.setClipPath(path)

        # body gradient
        grad = QLinearGradient(cr.topLeft(), cr.bottomLeft())
        grad.setColorAt(0.0, base.lighter(126))
        grad.setColorAt(0.32, base)
        grad.setColorAt(1.0, base.darker(150))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRect(cr)

        # thumbnails
        if (self.show_thumbnails and clip.kind in ("video", "image")
                and clip.path and cr.width() > 28 and cr.height() > 22):
            th = int(max(20, cr.height() - 4))
            pm = thumb_for(clip.path, th, at=max(0.05, clip.src_in + 0.5))
            if pm and not pm.isNull():
                p.setOpacity(0.92)
                x = cr.x() + 2
                while x < cr.right() - 2:
                    p.drawPixmap(QPointF(x, cr.y() + 2), pm,
                                 QRectF(min(cr.right() - 2, x + pm.width())
                                        - x, 0, 0, 0) and None or QRectF())
                    # simple full draw; clipping handles the edge
                    p.drawPixmap(QPointF(x, cr.y() + 2), pm)
                    x += pm.width() * 0.75
                p.setOpacity(1.0)
                veil = QColor(0, 0, 0, 60)
                p.setBrush(veil)
                p.drawRect(cr)

        # audio waveform
        if (clip.kind == "audio" and self.show_waveforms and clip.path
                and cr.width() > 16):
            self._paint_waveform(p, clip, cr)

        # keyframe markers
        self._paint_keyframes(p, clip, cr)

        # text / sticker content hint
        if clip.kind == "text" and clip.text and cr.width() > 40:
            p.setPen(QColor(255, 255, 255, 210))
            p.setFont(theme.ui_font(10, 700))
            txt = clip.text.text.replace("\n", " ")
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(cr.x() + 7, cr.y() + 2, cr.width() - 14,
                              cr.height() - 4),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(txt, Qt.ElideRight, int(cr.width() - 16)))

        p.restore()

        # top highlight + border
        p.setBrush(Qt.NoBrush)
        hl = QPainterPath()
        hl.addRoundedRect(QRectF(cr.x() + 1, cr.y() + 1,
                                 max(2.0, cr.width() - 2), 2.0), 1, 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 55))
        p.drawPath(hl)
        p.setBrush(Qt.NoBrush)
        if selected:
            p.setPen(QPen(QColor(theme.CLIP_SELECTED), 2))
        elif self._hover_clip == clip.id:
            p.setPen(QPen(QColor(255, 255, 255, 130), 1.5))
        else:
            p.setPen(QPen(QColor(0, 0, 0, 130), 1))
        p.drawRoundedRect(cr.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # trim handles
        if selected and cr.width() > 22:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.CLIP_SELECTED))
            p.drawRoundedRect(QRectF(cr.x() + 1.5, cr.y() + cr.height() * 0.3,
                                     3, cr.height() * 0.4), 1.5, 1.5)
            p.drawRoundedRect(QRectF(cr.right() - 4.5,
                                     cr.y() + cr.height() * 0.3, 3,
                                     cr.height() * 0.4), 1.5, 1.5)

        # badges
        self._paint_badges(p, clip, base)

        # transition wedges
        self._paint_transition_wedge(p, clip, cr, "in")
        self._paint_transition_wedge(p, clip, cr, "out")

        # name (for non-text clips, once there is room)
        if cr.width() > 46 and clip.kind != "text":
            p.setFont(theme.ui_font(10, 600))
            p.setPen(QColor(255, 255, 255, 235))
            fm = QFontMetrics(p.font())
            name = fm.elidedText(clip.display_name(), Qt.ElideRight,
                                 int(cr.width() - 12))
            p.drawText(QRectF(cr.x() + 7, cr.y() + 2, cr.width() - 12, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, name)

    def _paint_waveform(self, p: QPainter, clip: Clip, cr: QRectF) -> None:
        peaks = waveform_for(clip.path)
        if not peaks:
            return
        mid = cr.center().y()
        amp = cr.height() * 0.36
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 105))
        n = max(1, int(cr.width()))
        step = max(1, len(peaks) // n)
        x = cr.x()
        i = 0
        while x < cr.right() and i < len(peaks):
            v = max(peaks[i:i + step] or [0.0])
            h = max(1.0, v * amp)
            p.drawRect(QRectF(x, mid - h, 1.2, h * 2))
            x += 1.2
            i += step

    def _paint_keyframes(self, p: QPainter, clip: Clip, cr: QRectF) -> None:
        props = clip.animated_props()
        if not props or clip.duration <= 0 or cr.width() < 12:
            return
        y = cr.bottom() - KEYFRAME_ROW_H + 1
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRect(QRectF(cr.x(), y - 1, cr.width(), KEYFRAME_ROW_H - 1))
        shown = 0
        for prop in props:
            for k in clip.animations.get(prop, []):
                if clip.duration <= 0:
                    continue
                fx = cr.x() + (k.t / clip.duration) * cr.width()
                sel = (self.selected_keyframe is not None
                       and self.selected_keyframe[0] == clip.id
                       and abs(self.selected_keyframe[2] - k.t) < 1e-3)
                p.setBrush(QColor(theme.ACCENT if sel else "#ffd166"))
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                r = 4.0 if sel else 3.2
                p.drawPolygon(QPolygonF([
                    QPointF(fx, y + KEYFRAME_ROW_H / 2 - r),
                    QPointF(fx + r, y + KEYFRAME_ROW_H / 2),
                    QPointF(fx, y + KEYFRAME_ROW_H / 2 + r),
                    QPointF(fx - r, y + KEYFRAME_ROW_H / 2)]))
                shown += 1
                if shown > 220:
                    return

    def _paint_badges(self, p: QPainter, clip: Clip, base: QColor) -> None:
        cr = self.clip_rect(clip)
        badges: List[Tuple[str, str]] = []
        if abs(clip.speed - 1.0) > 0.01 or (clip.speed_curve
                                            and clip.speed_curve.is_ramp()):
            badges.append((f"{clip.speed:.2f}x", "#f59e0b"))
        if clip.reverse:
            badges.append(("REV", "#8b5cf6"))
        if clip.freeze:
            badges.append(("FREEZE", "#0ea5e9"))
        if clip.chroma.enabled:
            badges.append(("KEY", "#22c55e"))
        if clip.mask.shape != "none":
            badges.append(("MASK", "#e879f9"))
        if clip.filters:
            badges.append((f"FX{len(clip.filters)}", "#38bdf8"))
        if clip.audio.muted and clip.kind in ("audio", "video"):
            badges.append(("MUTE", "#f43f5e"))
        if clip.audio.volume < 0.99 and clip.kind == "audio":
            badges.append((f"{int(clip.audio.volume * 100)}%", "#facc15"))
        if not clip.enabled:
            badges.append(("OFF", "#94a3b8"))
        if clip.meta.get("missing") or (clip.path and not
                                        __import__("os").path.exists(clip.path)):
            badges.append(("?", "#ef4444"))
        if not badges:
            return
        x = cr.x() + 5
        y = cr.bottom() - 13
        p.setFont(theme.ui_font(8, 700))
        fm = QFontMetrics(p.font())
        for text, color in badges:
            w = fm.horizontalAdvance(text) + 8
            if x + w > cr.right() - 3 or cr.height() < 26:
                break
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(x, y, w, 11), 3, 3)
            p.setPen(QColor(color))
            p.drawText(QRectF(x, y, w, 11), Qt.AlignCenter, text)
            x += w + 3

    def _paint_transition_wedge(self, p: QPainter, clip: Clip, cr: QRectF,
                               edge: str) -> None:
        tr = clip.transition_in if edge == "in" else clip.transition_out
        if tr is None or tr.kind == "none":
            return
        w = min(TRANSITION_WEDGE_PX,
                max(6.0, (tr.duration * self.px_per_sec) * 0.5))
        x = cr.x() if edge == "in" else cr.right() - w
        p.setPen(Qt.NoPen)
        grad = QLinearGradient(QPointF(x, 0), QPointF(x + w, 0))
        c = QColor(theme.ACCENT)
        if edge == "out":
            grad.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 40))
            grad.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 210))
        else:
            grad.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 210))
            grad.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 40))
        p.setBrush(QBrush(grad))
        p.drawRect(QRectF(x, cr.y(), w, cr.height()))
        p.setPen(QPen(QColor(255, 255, 255, 120), 1))
        p.drawLine(QPointF(x, cr.y() + 2), QPointF(x, cr.bottom() - 2))

    def _paint_overlays(self, p: QPainter) -> None:
        # keyframe guide line
        if self.selected_keyframe is not None:
            cid, prop, lt = self.selected_keyframe
            clip = self.project.clip(cid)
            if clip:
                x = self.time_to_x(clip.start + lt)
                p.setPen(QPen(QColor("#ffd166"), 1, Qt.DashLine))
                p.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))
        if self._marquee is not None:
            p.setPen(QPen(QColor(theme.ACCENT), 1, Qt.DashLine))
            p.setBrush(QColor(theme.ACCENT).lighter(160))
            c = QColor(theme.ACCENT)
            p.setBrush(QColor(c.red(), c.green(), c.blue(), 40))
            p.drawRect(self._marquee)
        # empty-state hint
        if not any(t.clips for t in self.project.tracks):
            p.setPen(QColor(theme.TEXT_MUTED))
            p.setFont(theme.ui_font(12))
            p.drawText(QRectF(HEADER_W, RULER_H, self.width() - HEADER_W,
                              max(60, self.height() - RULER_H)),
                       Qt.AlignCenter,
                       "Drag media here, or press Import to get started")

    def _paint_playhead(self, p: QPainter) -> None:
        x = self.time_to_x(self.playhead)
        if x < HEADER_W - 1 or x > self.width():
            return
        pen = QPen(QColor(theme.PLAYHEAD), 2)
        p.setPen(pen)
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PLAYHEAD))
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0),
                                 QPointF(x, 9)]))

    # -- mouse interaction --------------------------------------------------- #
    def mousePressEvent(self, ev) -> None:
        pos = ev.position()
        if ev.button() == Qt.MiddleButton:
            self._drag = {"mode": "pan", "x0": pos.x(), "scroll0": self.scroll_x}
            self.setCursor(Qt.ClosedHandCursor)
            return
        if pos.y() <= RULER_H:
            if pos.x() >= HEADER_W:
                self._drag = {"mode": "playhead"}
                self._set_playhead_from(pos.x(), final=False)
            return
        if pos.x() < HEADER_W:
            return
        clip = self.clip_at(pos)
        mods = ev.modifiers()
        if clip is None:
            if not (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
                self.selection.clear()
                self.selectionChanged.emit([])
            self._drag = {"mode": "marquee", "x0": pos.x(), "y0": pos.y()}
            self.update()
            return
        if clip.locked:
            return
        zone = self.zone_at(clip, pos)
        if mods & Qt.ControlModifier:
            if clip.id in self.selection:
                self.selection.discard(clip.id)
            else:
                self.selection.add(clip.id)
        elif mods & Qt.ShiftModifier and self.selection:
            self.selection.add(clip.id)
        elif clip.id not in self.selection:
            self.selection = {clip.id}
        self.selectionChanged.emit(sorted(self.selection))

        # keyframe diamond hit test
        if zone == "body":
            cr = self.clip_rect(clip)
            ky = cr.bottom() - KEYFRAME_ROW_H / 2
            if abs(pos.y() - ky) <= 6:
                for prop in clip.animated_props():
                    for k in clip.animations.get(prop, []):
                        fxx = cr.x() + (k.t / max(1e-6, clip.duration)) * cr.width()
                        if abs(pos.x() - fxx) <= 6:
                            self.selected_keyframe = (clip.id, prop, k.t)
                            self.active_keyframe_prop = prop
                            self.keyframeClicked.emit(clip.id, prop, k.t)
                            self.update()
                            return
            # transition wedge
            tr_in = clip.transition_in
            if tr_in and tr_in.kind != "none":
                if pos.x() - cr.x() <= TRANSITION_WEDGE_PX:
                    self.transitionClicked.emit(clip.id, "in")
                    return
            tr_out = clip.transition_out
            if tr_out and tr_out.kind != "none":
                if cr.right() - pos.x() <= TRANSITION_WEDGE_PX:
                    self.transitionClicked.emit(clip.id, "out")
                    return

        drag_clips = sorted(self.selection) or [clip.id]
        snapshots = {}
        for cid in drag_clips:
            c = self.project.clip(cid)
            if c:
                snapshots[cid] = (c.start, c.track_id, c.duration, c.src_in)
        self._drag = {
            "mode": "trim" if zone.startswith("trim") else "move",
            "zone": zone,
            "clip": clip.id,
            "clips": drag_clips,
            "x0": pos.x(),
            "y0": pos.y(),
            "t0": self.x_to_time(pos.x()),
            "orig": snapshots,
            "moved": False,
        }
        self.update()

    def _set_playhead_from(self, x: float, final: bool) -> None:
        t = self.x_to_time(x)
        fps = max(1.0, self.project.settings.fps or 30.0)
        t = round(t * fps) / fps
        self.playhead = max(0.0, t)
        self.update()
        self.playheadMoved.emit(self.playhead, final)

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        if self._drag:
            mode = self._drag["mode"]
            if mode == "pan":
                self.scroll_x = max(0.0, self._drag["scroll0"]
                                    - (pos.x() - self._drag["x0"]))
                self.update()
                return
            if mode == "playhead":
                self._set_playhead_from(pos.x(), final=False)
                return
            if mode == "marquee":
                self._marquee = QRectF(QPointF(self._drag["x0"],
                                               self._drag["y0"]),
                                       pos).normalized()
                self.update()
                return
            if mode in ("move", "trim"):
                self._drag["moved"] = True
                self._apply_drag(pos, ev.modifiers())
                return
        # hover feedback
        clip = self.clip_at(pos) if pos.x() >= HEADER_W else None
        hover_id = clip.id if clip else None
        zone = self.zone_at(clip, pos) if clip else ""
        new_track = None
        for tr, r in self.track_rects():
            if r.y() <= pos.y() <= r.bottom():
                new_track = tr.id
                break
        if (hover_id != self._hover_clip or zone != self._hover_zone
                or new_track != self._hover_track):
            self._hover_clip = hover_id
            self._hover_zone = zone
            self._hover_track = new_track
            if zone in ("trim_in", "trim_out"):
                self.setCursor(Qt.SizeHorCursor)
            elif clip:
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            self.update()

    def _apply_drag(self, pos: QPointF, mods) -> None:
        d = self._drag
        dt = (pos.x() - d["x0"]) / max(1.0, self.px_per_sec)
        clip = self.project.clip(d["clip"])
        if clip is None:
            return
        if d["mode"] == "move":
            anchor = d["orig"][d["clip"]][0]
            new_start = max(0.0, anchor + dt)
            new_start = self.snap(new_start, set(d["clips"]))
            # also snap the clip end
            dur = clip.duration
            snapped_end = self.snap(new_start + dur, set(d["clips"])) - dur
            if abs(snapped_end - new_start) < SNAP_PX / self.px_per_sec:
                new_start = max(0.0, snapped_end)
            delta = new_start - anchor
            dest_track = self._track_at_y(pos.y())
            for cid in d["clips"]:
                c = self.project.clip(cid)
                if c is None or c.locked:
                    continue
                o_start, o_track, o_dur, o_src = d["orig"][cid]
                c.start = max(0.0, o_start + delta)
                if dest_track and dest_track != d["orig"][d["clip"]][1]:
                    src_tr = self.project.track(d["orig"][d["clip"]][1])
                    dst_tr = self.project.track(dest_track)
                    if (src_tr and dst_tr
                            and _track_accepts(dst_tr, c)
                            and _track_accepts(src_tr, c)):
                        self.project.move_clip(cid, c.start, dest_track)
            self.project.track(d["clip"]) and None
            for tr in self.project.tracks:
                tr.sort()
            self.clipChanged.emit(d["clip"])
            self.clipMoved.emit(d["clip"], new_start,
                                self.project.clip(d["clip"]).track_id
                                if self.project.clip(d["clip"]) else "")
            self.update()
            return
        if d["mode"] == "trim":
            zone = d["zone"]
            o_start, o_track, o_dur, o_src = d["orig"][d["clip"]]
            min_dur = 2.0 / max(1.0, self.project.settings.fps)
            if zone == "trim_in":
                new_start = max(0.0, o_start + dt)
                new_start = min(self.snap(new_start, {d["clip"]}),
                                o_start + o_dur - min_dur)
                new_start = min(new_start, o_start + o_dur - min_dur)
                delta = new_start - o_start
                if clip.kind in ("video", "audio", "image") and not clip.freeze:
                    clip.src_in = max(0.0, o_src + delta * clip.speed)
                clip.duration = max(min_dur, o_dur - delta)
                clip.start = new_start
            else:
                new_end = max(o_start + min_dur, o_start + o_dur + dt)
                new_end = max(self.snap(new_end, {d["clip"]}),
                              o_start + min_dur)
                clip.duration = max(min_dur, new_end - o_start)
            self.clipChanged.emit(d["clip"])
            self.update()

    def _track_at_y(self, y: float) -> Optional[str]:
        for tr, r in self.track_rects():
            if r.y() <= y <= r.bottom():
                return tr.id
        return None

    def mouseReleaseEvent(self, ev) -> None:
        d = self._drag
        self._drag = None
        self.setCursor(Qt.ArrowCursor)
        if d is None:
            return
        if d["mode"] == "marquee" and self._marquee is not None:
            sel = set(self.selection)
            rect = self._marquee
            for tr in self.project.tracks:
                for c in tr.clips:
                    cr = self.clip_rect(c)
                    if cr.isValid() and rect.intersects(cr):
                        sel.add(c.id)
            self.selection = sel
            self.selectionChanged.emit(sorted(sel))
            self._marquee = None
        if d["mode"] == "playhead":
            self._set_playhead_from(ev.position().x(), final=True)
        if d["mode"] in ("move", "trim") and d.get("moved"):
            self.editRequested.emit(f"gesture:{d['mode']}", d["clip"])
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        pos = ev.position()
        clip = self.clip_at(pos)
        if clip:
            self.selection = {clip.id}
            self.selectionChanged.emit([clip.id])
            self.editRequested.emit("open_inspector", clip.id)

    def contextMenuEvent(self, ev) -> None:
        pos = QPointF(ev.pos())
        clip = self.clip_at(pos)
        if clip:
            if clip.id not in self.selection:
                self.selection = {clip.id}
                self.selectionChanged.emit([clip.id])
            self.contextMenuRequested.emit(clip.id, ev.globalPos())
            return
        tr = self._track_at_y(pos.y())
        if tr:
            self.trackAction.emit(tr, "menu")
            return
        self.contextMenuRequested.emit("", ev.globalPos())

    def wheelEvent(self, ev) -> None:
        mods = ev.modifiers()
        delta = ev.angleDelta()
        if mods & Qt.ControlModifier or mods & Qt.ShiftModifier:
            factor = 1.0 + (delta.y() / 1200.0)
            self.set_zoom(self.px_per_sec * factor, anchor_x=ev.position().x())
        elif mods & Qt.AltModifier:
            self.scroll_y = max(0, self.scroll_y - delta.y() // 2)
            self.update()
        else:
            # horizontal scrub with a vertical wheel is what mobile editors do
            self.scroll_x = max(0.0, self.scroll_x - delta.y() * 1.4)
            self.update()
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        mods = ev.modifiers()
        if key in (Qt.Key_Left, Qt.Key_Right):
            step = 1.0 / max(1.0, self.project.settings.fps)
            if mods & Qt.ShiftModifier:
                step = 1.0
            elif mods & Qt.ControlModifier:
                step = 0.1
            delta = -step if key == Qt.Key_Left else step
            self.set_playhead(self.playhead + delta, emit=True)
            self.ensure_visible(self.playhead)
            return
        if key in (Qt.Key_Home, Qt.Key_End):
            self.set_playhead(0.0 if key == Qt.Key_Home
                              else self.project.duration, emit=True)
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editRequested.emit("delete", "")
            return
        if key == Qt.Key_Space:
            self.editRequested.emit("play_pause", "")
            return
        super().keyPressEvent(ev)


def _track_accepts(track: Track, clip: Clip) -> bool:
    if clip.kind == "audio":
        return track.kind == "audio"
    if clip.kind == "text":
        return track.kind == "text"
    if clip.kind == "sticker":
        return track.kind in ("sticker", "overlay")
    return track.kind in ("video", "overlay", "sticker")


# --------------------------------------------------------------------------- #
#  Combined timeline panel (toolbar + headers + canvas)
# --------------------------------------------------------------------------- #

class TimelinePanel(QWidget):
    """Toolbar + ruler + track headers + scrolling canvas in one module."""

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.setObjectName("timelinePanel")
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.canvas = TimelineWidget(self.project)

        # ---- toolbar ---- #
        bar = QFrame()
        bar.setObjectName("tlBar")
        bar.setStyleSheet(
            f"#tlBar {{ background: {theme.BG_PANEL};"
            f" border-top: 1px solid {theme.BORDER_SOFT};"
            f" border-bottom: 1px solid {theme.BORDER_SOFT}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 5, 8, 5)
        bl.setSpacing(4)

        def tool(name: str, tip: str, signal, checkable=False,
                 icon_name: Optional[str] = None) -> QToolButton:
            b = QToolButton()
            b.setObjectName("tlTool")
            b.setToolTip(tip)
            b.setIconSize(QSize(19, 19))
            b.setIcon(theme.icon(icon_name or name, theme.TEXT_DIM, 20))
            b.setFixedSize(30, 27)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            b.setStyleSheet(
                f"QToolButton {{ border: 1px solid transparent; border-radius: 8px;"
                f" background: transparent; }}"
                f"QToolButton:hover {{ background: {theme.BG_HOVER}; }}"
                f"QToolButton:checked {{ background: {theme.BG_ACTIVE};"
                f" border-color: {theme.ACCENT}; }}")
            if signal is not None:
                if checkable:
                    b.toggled.connect(signal)
                else:
                    b.clicked.connect(signal)
            bl.addWidget(b)
            return b

        self.btn_split = tool("split", "Split at playhead  (S)",
                              lambda: self._emit("split"))
        self.btn_delete = tool("delete", "Delete selected  (Del)",
                               lambda: self._emit("delete"))
        self.btn_trim = tool("trim", "Trim selected to playhead",
                             lambda: self._emit("trim_to_playhead"))
        self.btn_copy = tool("copy", "Duplicate selected  (Ctrl+D)",
                             lambda: self._emit("duplicate"))
        bl.addSpacing(6)
        self.btn_transition = tool("transition", "Transitions",
                                   lambda: self._emit("open:transition"))
        self.btn_filter = tool("filter", "Filters and effects",
                               lambda: self._emit("open:filter"))
        self.btn_speed = tool("speed", "Speed and curves",
                              lambda: self._emit("open:speed"))
        self.btn_text = tool("text", "Add text  (T)",
                             lambda: self._emit("add_text"))
        self.btn_audio = tool("music", "Audio and sound effects",
                              lambda: self._emit("open:audio"))
        self.btn_sticker = tool("sticker", "Stickers",
                                lambda: self._emit("open:sticker"))
        bl.addSpacing(6)
        self.btn_keyframe = tool("keyframe_add", "Add keyframe at playhead  (K)",
                                 lambda: self._emit("add_keyframe"))
        self.btn_beat = tool("beat", "Beat / marker tools",
                             lambda: self._emit("open:beats"))
        self.btn_snap = tool("magic", "Snapping  (toggle)", None,
                             checkable=True)
        self.btn_snap.setChecked(True)
        self.btn_snap.toggled.connect(self._toggle_snap)
        self.btn_snap.setToolTip("Snap to clip edges, markers and beats")
        self.btn_wave = tool("volume", "Show audio waveforms", None,
                             checkable=True)
        self.btn_wave.setChecked(True)
        self.btn_wave.toggled.connect(self._toggle_wave)
        self.btn_wave.setToolTip("Show audio waveforms on clips")

        bl.addStretch(1)

        self.lbl_time = QLabel("00:00:00")
        self.lbl_time.setStyleSheet(
            f"color:{theme.TEXT}; font-family: 'DejaVu Sans Mono', monospace;"
            f"font-size:12px; font-weight:600; padding:0 6px;")
        bl.addWidget(self.lbl_time)
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
        bl.addWidget(self.lbl_info)
        bl.addSpacing(8)

        def small(text: str, tip: str, fn) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedSize(26, 25)
            b.setAutoRaise(True)
            b.setStyleSheet(
                f"QToolButton {{ border: none; border-radius: 6px; color:"
                f" {theme.TEXT_DIM}; font-size: 14px; font-weight: 700; }}"
                f"QToolButton:hover {{ background: {theme.BG_HOVER};"
                f" color: {theme.TEXT}; }}")
            b.clicked.connect(fn)
            bl.addWidget(b)
            return b

        small("−", "Zoom out  (Ctrl+-)", self.canvas.zoom_out)
        small("+", "Zoom in  (Ctrl+=)", self.canvas.zoom_in)
        small("⛶", "Fit timeline  (Shift+Z)", self.canvas.zoom_fit)
        root.addWidget(bar)

        # ---- header column + canvas ---- #
        body = QFrame()
        body.setStyleSheet("background: transparent;")
        blay = QHBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(0)

        self.headers = _HeaderColumn(self.project, self.canvas)
        blay.addWidget(self.headers)

        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        blay.addWidget(self.canvas, 1)
        root.addWidget(body, 1)

        # ---- scrollbar ---- #
        self.hbar = _HBar(self.canvas)
        root.addWidget(self.hbar)

        self.canvas.zoomChanged.connect(lambda _: self._sync_scrollbar())
        self.canvas.playheadMoved.connect(self._on_playhead)

    def _emit(self, action: str) -> None:
        self.canvas.editRequested.emit(action, "")

    def _toggle_snap(self, on: bool) -> None:
        self.canvas.snapping = on

    def _toggle_wave(self, on: bool) -> None:
        self.canvas.show_waveforms = on
        self.canvas.update()

    def _on_playhead(self, t: float, final: bool) -> None:
        self.lbl_time.setText(fmt_timecode(t, self.project.settings.fps))

    def _sync_scrollbar(self) -> None:
        self.hbar.update_from_canvas()

    def set_project(self, project: Project) -> None:
        self.project = project
        self.canvas.project = project
        self.headers.set_project(project)
        self.canvas.player = None
        self.canvas.update()
        self.headers.rebuild()

    def refresh(self) -> None:
        self.headers.rebuild()
        self.canvas.update()
        self._sync_scrollbar()
        self._update_info()

    def _update_info(self) -> None:
        clips = len(self.project.all_clips())
        dur = self.project.duration
        self.lbl_info.setText(
            f"{clips} clip{'s' if clips != 1 else ''} · "
            f"{self.project.settings.width}×{self.project.settings.height} · "
            f"{len(self.project.beats)} beats · {fmt_short(dur)}")


class _HeaderColumn(QWidget):
    """Vertical stack of track headers, aligned to the canvas lanes."""

    def __init__(self, project: Project, canvas: TimelineWidget, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.canvas = canvas
        self.headers: List[TrackHeader] = []
        self.setFixedWidth(HEADER_W)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setStyleSheet(f"background: {theme.BG_ROOT};")
        self.rebuild()

    def set_project(self, project: Project) -> None:
        self.project = project
        self.rebuild()

    def rebuild(self) -> None:
        for h in self.headers:
            h.setParent(None)
            h.deleteLater()
        self.headers = []
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(theme.BG_ROOT))
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(9, 700))
        p.drawText(QRectF(10, 8, HEADER_W - 20, 14), Qt.AlignLeft,
                   "TRACKS")
        y = RULER_H + 2 - self.canvas.scroll_y
        for tr in self.project.tracks:
            h = tr.height
            rect = QRectF(6, y, HEADER_W - 12, h)
            p.setPen(Qt.NoPen)
            base = {"video": theme.TRACK_VIDEO, "overlay": theme.TRACK_OVERLAY,
                    "text": theme.TRACK_TEXT, "audio": theme.TRACK_AUDIO,
                    "sticker": theme.TRACK_STICKER}.get(tr.kind,
                                                        theme.BG_PANEL_ALT)
            p.setBrush(QColor(theme.BG_PANEL_ALT))
            p.drawRoundedRect(rect, 6, 6)
            p.setBrush(QColor(base).darker(190))
            p.drawRoundedRect(QRectF(rect.x(), rect.y(), 3, rect.height()), 2, 2)

            p.setPen(QColor(theme.TEXT))
            f = theme.ui_font(11, 600)
            p.setFont(f)
            label = QFontMetrics(f).elidedText(tr.label(), Qt.ElideRight,
                                               int(rect.width() - 24))
            p.drawText(QRectF(rect.x() + 10, rect.y() + 5,
                              rect.width() - 20, 15),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(QColor(theme.TEXT_MUTED))
            p.setFont(theme.ui_font(9))
            p.drawText(QRectF(rect.x() + 10, rect.y() + 20,
                              rect.width() - 20, 12),
                       Qt.AlignLeft, f"{len(tr.clips)} clips")

            # buttons
            by = rect.bottom() - 20
            x = rect.x() + 8
            for key, txt, active in (
                ("mute", "M", tr.muted),
                ("hide", "V", tr.hidden),
                ("lock", "L", tr.locked),
                ("solo", "SOLO", tr.solo),
            ):
                w = 26 if key == "solo" else 18
                r = QRectF(x, by, w, 15)
                color = QColor(theme.ACCENT if active else theme.TEXT_MUTED)
                p.setPen(QPen(color, 1))
                p.setBrush(QColor(theme.ACCENT).darker(150) if active
                           else QColor(theme.BG_INPUT))
                p.drawRoundedRect(r, 4, 4)
                p.setPen(color)
                p.setFont(theme.ui_font(7 if key == "solo" else 9, 700))
                p.drawText(r, Qt.AlignCenter, txt)
                x += w + 3
            y += h + LANE_GAP

        # playhead time
        p.setPen(QColor(theme.TEXT_DIM))
        p.setFont(theme.ui_font(10, 700))
        p.drawText(QRectF(10, 8, HEADER_W - 20, 14), Qt.AlignLeft,
                   "TRACKS")
        p.end()

    def mousePressEvent(self, ev) -> None:
        pos = ev.position()
        y = RULER_H + 2 - self.canvas.scroll_y
        for tr in self.project.tracks:
            rect = QRectF(6, y, HEADER_W - 12, tr.height)
            if rect.contains(pos):
                by = rect.bottom() - 20
                x = rect.x() + 8
                for key, w in (("mute", 18), ("hide", 18), ("lock", 18),
                               ("solo", 26)):
                    r = QRectF(x, by, w, 15)
                    if r.contains(pos):
                        self._toggle(tr, key)
                        return
                    x += w + 3
                self.canvas.selection = {c.id for c in tr.clips}
                self.canvas.selectionChanged.emit(sorted(self.canvas.selection))
                self.canvas.update()
                return
            y += tr.height + LANE_GAP

    def _toggle(self, tr: Track, key: str) -> None:
        if key == "mute":
            tr.muted = not tr.muted
        elif key == "hide":
            tr.hidden = not tr.hidden
        elif key == "lock":
            tr.locked = not tr.locked
        elif key == "solo":
            tr.solo = not tr.solo
        self.canvas.editRequested.emit(f"track:{key}", tr.id)
        self.update()
        self.canvas.update()


class _HBar(QWidget):
    """Thin horizontal scrollbar wired to the canvas."""

    def __init__(self, canvas: TimelineWidget, parent=None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.setFixedHeight(11)
        self._drag = None
        self.setMouseTracking(True)
        canvas.zoomChanged.connect(lambda _: self.update())

    def update_from_canvas(self) -> None:
        self.update()

    def _metrics(self) -> Tuple[float, float, float]:
        cw = max(1.0, float(self.canvas.content_width()))
        vw = max(1.0, float(self.canvas.width() - HEADER_W))
        ratio = min(1.0, vw / cw)
        handle_w = max(30.0, self.width() * ratio)
        max_scroll = max(1.0, cw - vw)
        pos = min(1.0, self.canvas.scroll_x / max_scroll)
        return handle_w, (self.width() - handle_w) * pos, max_scroll

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG_PANEL))
        if self.canvas.content_width() <= self.canvas.width() - HEADER_W:
            return
        hw, hx, _ = self._metrics()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#39414f"))
        r = self.rect().adjusted(1, 2, -1, -2)
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(QColor("#5b6474"))
        p.drawRoundedRect(QRectF(hx, 2, hw, self.height() - 4), 3, 3)
        p.end()

    def mousePressEvent(self, ev) -> None:
        hw, hx, max_scroll = self._metrics()
        if QRectF(hx, 0, hw, self.height()).contains(ev.position()):
            self._drag = (ev.position().x() - hx, max_scroll, hw)
        else:
            target = max(0.0, ev.position().x() - hw / 2)
            self.canvas.scroll_x = max(0.0, min(max_scroll,
                                    target / max(1.0, self.width() - hw)
                                    * max_scroll))
            self.canvas.update()
            self.update()

    def mouseMoveEvent(self, ev) -> None:
        if not self._drag:
            return
        off, max_scroll, hw = self._drag
        x = ev.position().x() - off
        frac = x / max(1.0, self.width() - hw)
        self.canvas.scroll_x = max(0.0, min(max_scroll, frac * max_scroll))
        self.canvas.update()
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None
