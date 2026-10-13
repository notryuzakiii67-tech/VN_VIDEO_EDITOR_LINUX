"""Media library panel: import, browse, search and drag media onto the
timeline.  Thumbnails are generated with the bundled FFmpeg and cached on
disk, so a big library stays responsive on an old machine."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import (QMimeData, QPoint, QRect, QRectF, QSize, Qt,
                            QThread, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QDrag, QFontMetrics, QIcon, QImage,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import probe
from ..core.model import MediaAsset, Project
from . import theme
from .player import thumb_for
from .widgets import IconButton, label

MEDIA_ROLES = ("video", "audio", "image")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg",
             ".mpeg", ".wmv", ".flv", ".ts", ".3gp", ".m2ts", ".ogv"}
AUDIO_EXT = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus",
             ".wma", ".aiff", ".amr", ".mka"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff",
             ".tif", ".heic"}


def classify(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "video"


class ThumbWorker(QThread):
    """Generates missing thumbnails one at a time (never blocks the UI)."""

    ready = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: List[str] = []
        self._lock = __import__("threading").Lock()
        self._quit = False
        self._wake = __import__("threading").Event()

    def request(self, path: str) -> None:
        with self._lock:
            if path not in self._queue:
                self._queue.append(path)
        self._wake.set()

    def stop(self) -> None:
        self._quit = True
        self._wake.set()

    def run(self) -> None:
        while not self._quit:
            self._wake.wait(0.4)
            self._wake.clear()
            with self._lock:
                path = self._queue.pop(0) if self._queue else None
            if path is None or self._quit:
                continue
            try:
                pm = thumb_for(path, 86)
                if pm is not None:
                    self.ready.emit(path)
            except Exception:
                pass


class MediaCard(QWidget):
    """One media tile; supports drag-out to the timeline."""

    doubleClicked = Signal(str)
    contextRequested = Signal(str, QPoint)

    def __init__(self, asset: MediaAsset, parent=None, compact: bool = False) -> None:
        super().__init__(parent)
        self.asset = asset
        self.compact = compact
        self.hover = False
        self.setMouseTracking(True)
        self.setFixedHeight(58 if compact else 118)
        self.setMinimumWidth(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.OpenHandCursor)
        self._drag_from: Optional[QPoint] = None

    # -- painting ------------------------------------------------------------ #
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_PANEL_ALT if self.hover else theme.BG_PANEL))
        p.drawRoundedRect(r, 8, 8)

        if self.compact:
            thumb_rect = QRectF(r.x() + 4, r.y() + 4, 50, r.height() - 8)
        else:
            thumb_rect = QRectF(r.x() + 4, r.y() + 4, r.width() - 8, 64)
        self._paint_thumb(p, thumb_rect)

        p.setPen(QColor(theme.TEXT if self.hover else theme.TEXT_DIM))
        p.setFont(theme.ui_font(10 if self.compact else 10, 600))
        name_rect = QRectF(thumb_rect.right() + 7 if self.compact
                           else r.x() + 5,
                           thumb_rect.center().y() - 8 if self.compact
                           else thumb_rect.bottom() + 3,
                           r.width() - (thumb_rect.width() + 18)
                           if self.compact else r.width() - 10,
                           16 if not self.compact else 16)
        fm = QFontMetrics(p.font())
        p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.asset.name, Qt.ElideMiddle,
                                 int(name_rect.width())))

        info = self._info_text()
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(9))
        irect = QRectF(name_rect.x(), name_rect.bottom() + 1,
                       name_rect.width(), 14)
        if self.compact:
            irect = QRectF(name_rect.x(), name_rect.bottom() + 1,
                           name_rect.width(), 14)
        p.drawText(irect, Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(info, Qt.ElideRight, int(irect.width())))

        if self.asset.kind != "audio" and self.asset.width and not self.compact:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.setFont(theme.ui_font(8))
            p.drawText(QRectF(r.right() - 70, thumb_rect.y() + 2, 66, 12),
                       Qt.AlignRight, f"{self.asset.width}×{self.asset.height}")

        p.setPen(QPen(QColor(theme.ACCENT) if self.hover
                      else QColor(0, 0, 0, 80), 2 if self.hover else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        p.end()

    def _info_text(self) -> str:
        a = self.asset
        bits = []
        if a.duration:
            d = a.duration
            bits.append(f"{int(d // 60):d}:{d % 60:04.1f}" if d >= 60
                        else f"{d:.1f}s")
        if a.kind == "video":
            bits.append(f"{a.fps:.0f}fps" if a.fps else "")
        if a.kind == "audio":
            if a.sample_rate:
                bits.append(f"{a.sample_rate // 1000}kHz")
            if a.channels:
                bits.append(f"{a.channels}ch")
        if a.size_bytes:
            bits.append(_human(a.size_bytes))
        return " · ".join(b for b in bits if b)

    def _paint_thumb(self, p: QPainter, rect: QRectF) -> None:
        p.save()
        path = QPainterPath_round(rect, 6)
        p.setClipPath(path)
        pm = thumb_for(self.asset.path, int(rect.height()))
        if pm and not pm.isNull():
            scaled = pm.scaled(rect.size().toSize(), Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
            p.drawPixmap(rect.topLeft(), scaled)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BG_INPUT))
            p.drawRect(rect)
            icon_name = {"audio": "music", "image": "file",
                         "video": "play"}.get(self.asset.kind, "file")
            ic = theme.pixmap(icon_name, theme.TEXT_MUTED, 26)
            p.drawPixmap(int(rect.center().x() - 13),
                         int(rect.center().y() - 13), ic)
        p.restore()

    # -- interaction --------------------------------------------------------- #
    def enterEvent(self, ev) -> None:
        self.hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self.hover = False
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._drag_from = ev.pos()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_from is None:
            return
        if (ev.pos() - self._drag_from).manhattanLength() < 8:
            return
        drag = QDrag(self)
        md = QMimeData()
        md.setData("application/x-vn-asset", self.asset.id.encode())
        md.setText(self.asset.path)
        drag.setMimeData(md)
        pm = self.grab()
        drag.setPixmap(pm.scaled(int(pm.width() * 0.7), int(pm.height() * 0.7),
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.setHotSpot(QPoint(20, 20))
        drag.exec(Qt.CopyAction)
        self._drag_from = None

    def mouseDoubleClickEvent(self, ev) -> None:
        self.doubleClicked.emit(self.asset.id)

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_from = None

    def contextMenuEvent(self, ev) -> None:
        self.contextRequested.emit(self.asset.id, ev.globalPos())


def QPainterPath_round(rect: QRectF, radius: float):
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


# --------------------------------------------------------------------------- #
#  panel
# --------------------------------------------------------------------------- #

class MediaPanel(QWidget):
    """Import / browse media, and drag onto the timeline."""

    addToTimeline = Signal(str)          # asset id
    replaceRequested = Signal(str)       # asset id
    removeRequested = Signal(str)
    importRequested = Signal()
    filesDropped = Signal(list)          # paths from outside the app
    selectionChanged = Signal(str)

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.selected_asset: Optional[str] = None
        self.filter_kind = "all"
        self.search = ""
        self.compact = False
        self._cards: Dict[str, List[MediaCard]] = {}
        self.setAcceptDrops(True)
        self._thumb_worker = ThumbWorker(self)
        # NOTE: the receiver must be a bound method of this widget.  Connecting
        # to a bare lambda gives the connection the *worker's* thread affinity,
        # which would run widget code off the GUI thread.
        self._thumb_worker.ready.connect(self._on_thumb_ready)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(140)
        self._thumb_timer.timeout.connect(self._refresh_cards)
        self._thumb_worker.start()
        self._build()
        self._refresh_cards()

    # -- construction -------------------------------------------------------- #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 6)
        root.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(5)
        ttl = label("Media", "h2")
        head.addWidget(ttl)
        head.addStretch(1)
        self.btn_view = IconButton("list", "Switch layout", 24, 15)
        self.btn_view.clicked.connect(self._toggle_view)
        head.addWidget(self.btn_view)
        btn_import = QPushButton(" Import")
        btn_import.setIcon(theme.icon("plus", theme.TEXT_ON_ACCENT, 15))
        btn_import.setProperty("class", "primary")
        btn_import.setToolTip("Import media  (Ctrl+I)")
        btn_import.clicked.connect(self.importRequested.emit)
        head.addWidget(btn_import)
        root.addLayout(head)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search media…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        root.addWidget(self.search_edit)

        chips = QHBoxLayout()
        chips.setSpacing(4)
        self._chip_buttons: Dict[str, QPushButton] = {}
        for key, text in (("all", "All"), ("video", "Video"),
                          ("audio", "Audio"), ("image", "Image")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("class", "chip")
            b.setChecked(key == "all")
            b.setStyleSheet("padding: 3px 10px; font-size: 11px;")
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            chips.addWidget(b)
            self._chip_buttons[key] = b
        chips.addStretch(1)
        self.count_label = label("0 items", "muted", 10)
        chips.addWidget(self.count_label)
        root.addLayout(chips)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._grid = QVBoxLayout(self._container)
        self._grid.setContentsMargins(0, 0, 2, 0)
        self._grid.setSpacing(0)
        self.scroll.setWidget(self._container)
        root.addWidget(self.scroll, 1)

        self.empty = QLabel("No media yet.\nImport clips, music or images —\n"
                            "or drag files straight in.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px;"
                                 f"line-height:150%;")
        root.addWidget(self.empty)

    def _on_thumb_ready(self, _path: str) -> None:
        """A thumbnail was generated — repaint soon, coalescing bursts."""
        self._thumb_timer.start()

    def _toggle_view(self) -> None:
        self.compact = not self.compact
        self.btn_view.set_icon_name("list" if not self.compact else "grid")
        self._refresh_cards()

    # -- data ---------------------------------------------------------------- #
    def set_project(self, project: Project) -> None:
        self.project = project
        self.selected_asset = None
        self._refresh_cards()

    def set_filter(self, key: str) -> None:
        self.filter_kind = key
        for k, b in self._chip_buttons.items():
            b.setChecked(k == key)
        self._refresh_cards()

    def _on_search(self, text: str) -> None:
        self.search = text.strip().lower()
        self._refresh_cards()

    def _visible_assets(self) -> List[MediaAsset]:
        out = []
        for a in self.project.assets:
            if self.filter_kind != "all" and a.kind != self.filter_kind:
                continue
            if self.search and self.search not in a.name.lower():
                continue
            out.append(a)
        out.sort(key=lambda a: a.added or 0.0)
        return out

    def _refresh_cards(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._cards = {}
        assets = self._visible_assets()
        self.count_label.setText(f"{len(assets)} item"
                                 f"{'s' if len(assets) != 1 else ''}")
        self.empty.setVisible(not assets and not self.project.assets)
        if not assets:
            return
        if self.compact:
            for a in assets:
                card = MediaCard(a, self, compact=True)
                self._wire(card)
                self._grid.addWidget(card)
        else:
            grid = QGridLayout()
            grid.setSpacing(6)
            for i, a in enumerate(assets):
                card = MediaCard(a, self, compact=False)
                self._wire(card)
                grid.addWidget(card, i // 2, i % 2)
            wrap = QWidget()
            wrap.setStyleSheet("background: transparent;")
            wrap.setLayout(grid)
            self._grid.addWidget(wrap)
            for a in assets[:60]:
                self._thumb_worker.request(a.path)
        self._grid.addStretch(1)

    def _wire(self, card: MediaCard) -> None:
        card.doubleClicked.connect(self.addToTimeline.emit)
        card.contextRequested.connect(self._context_menu)
        self._cards.setdefault(card.asset.id, []).append(card)

    def _context_menu(self, asset_id: str, pos: QPoint) -> None:
        asset = self.project.asset(asset_id)
        if asset is None:
            return
        menu = QMenu(self)
        a1 = menu.addAction(theme.icon("plus", theme.TEXT_DIM, 15),
                            "Add to timeline")
        a2 = menu.addAction(theme.icon("filter", theme.TEXT_DIM, 15),
                            "Replace clip(s) with this media")
        menu.addSeparator()
        a3 = menu.addAction(theme.icon("info", theme.TEXT_DIM, 15),
                            "Show file info")
        a4 = menu.addAction(theme.icon("folder", theme.TEXT_DIM, 15),
                            "Open containing folder")
        menu.addSeparator()
        a5 = menu.addAction(theme.icon("delete", theme.TEXT_DIM, 15),
                            "Remove from project")
        chosen = menu.exec(pos)
        if chosen is a1:
            self.addToTimeline.emit(asset_id)
        elif chosen is a2:
            self.replaceRequested.emit(asset_id)
        elif chosen is a3:
            self._show_info(asset)
        elif chosen is a4:
            self._open_folder(asset)
        elif chosen is a5:
            self.removeRequested.emit(asset_id)

    def _show_info(self, asset: MediaAsset) -> None:
        from PySide6.QtWidgets import QMessageBox
        info = probe.probe(asset.path, use_cache=False)
        rows = [f"<b>{asset.name}</b>", "", f"<b>Path</b> {asset.path}", ""]
        for k, v in (("Kind", info.kind), ("Duration", f"{info.duration:.3f}s"),
                     ("Size", f"{info.width}×{info.height}"
                      if info.width else "—"),
                     ("Frame rate", f"{info.fps:.3f}" if info.fps else "—"),
                     ("Video codec", info.codec or "—"),
                     ("Audio codec", info.audio_codec or "—"),
                     ("Sample rate", f"{info.sample_rate} Hz"
                      if info.sample_rate else "—"),
                     ("Channels", str(info.channels) if info.channels else "—"),
                     ("Pixel format", info.pix_fmt or "—"),
                     ("Bitrate", f"{info.bitrate / 1000:.0f} kb/s"
                      if info.bitrate else "—")):
            rows.append(f"<b>{k}</b>&nbsp;&nbsp;{v}")
        QMessageBox.information(self, "Media info", "<br>".join(rows))

    def _open_folder(self, asset: MediaAsset) -> None:
        import subprocess
        folder = str(Path(asset.path).parent)
        for cmd in (["xdg-open", folder], ["nautilus", folder]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue

    def select(self, asset_id: str) -> None:
        self.selected_asset = asset_id
        self.selectionChanged.emit(asset_id)
        for cid, cards in self._cards.items():
            for c in cards:
                c.update()

    # -- drag & drop from outside -------------------------------------------- #
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls()
                 if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            ev.acceptProposedAction()

    def shutdown(self) -> None:
        self._thumb_worker.stop()
        self._thumb_worker.wait(800)


def collect_media_files(paths: List[str]) -> List[str]:
    """Expand folders into a flat list of supported media files."""
    exts = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT
    out: List[str] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file() and f.suffix.lower() in exts:
                    out.append(str(f))
        elif path.is_file():
            out.append(str(path))
    return out
