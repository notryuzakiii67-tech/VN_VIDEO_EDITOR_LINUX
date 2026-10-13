"""The media library panel (left side of the workspace).

Import button + search box + a thumbnail list.  Double-clicking an asset
(or choosing *Add to timeline*) places it on the timeline.  Thumbnails
and audio waveforms are generated on a worker pool so importing never
blocks the interface.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton,
                               QVBoxLayout, QWidget)

from .. import engine
from ..engine import MediaAsset

_KIND_BADGE = {"video": "VIDEO", "audio": "AUDIO", "image": "IMAGE"}


class _ThumbSignals(QObject):
    done = Signal(str, str)                 # asset id, thumbnail path


class _ThumbJob(QRunnable):
    def __init__(self, signals: _ThumbSignals, asset: MediaAsset,
                 out_path: str) -> None:
        super().__init__()
        self.signals = signals
        self.asset = asset
        self.out_path = out_path

    def run(self) -> None:
        asset = self.asset
        out = ""
        try:
            if asset.kind == "audio":
                peaks = engine.waveform_peaks(asset.path, buckets=240)
                if peaks:
                    asset.peaks = [float(p) for p in peaks]
            else:
                at = min(1.0, max(0.0, asset.duration * 0.25))
                if engine.make_thumbnail(asset.path, self.out_path, at=at,
                                         width=192):
                    out = self.out_path
        except Exception:
            out = ""
        try:
            self.signals.done.emit(asset.id, out)
        except RuntimeError:
            pass            # panel went away mid-job (app closing)


def _placeholder_icon(asset: MediaAsset) -> QIcon:
    pix = QPixmap(96, 54)
    colors = {"video": "#3f74a8", "audio": "#5d8a3c", "image": "#4f9d7a"}
    pix.fill(QColor(colors.get(asset.kind, "#3a4049")))
    painter = QPainter(pix)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pix.rect(), Qt.AlignCenter,
                     _KIND_BADGE.get(asset.kind, "?"))
    painter.end()
    return QIcon(pix)


class MediaPanel(QWidget):
    """Media library: import, search, thumbnails, add-to-timeline."""

    import_requested = Signal()
    add_requested = Signal(str)             # asset id
    remove_requested = Signal(str)          # asset id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._assets: Dict[str, MediaAsset] = {}
        self._items: Dict[str, QListWidgetItem] = {}
        self._pool = QThreadPool.globalInstance()
        self._signals = _ThumbSignals(self)
        self._signals.done.connect(self._thumb_done)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Media library")
        title.setObjectName("title")
        layout.addWidget(title)

        row = QHBoxLayout()
        self._import_btn = QPushButton("Import media…")
        self._import_btn.setObjectName("accent")
        self._import_btn.setToolTip("Import video, audio or image files (Ctrl+I)")
        self._import_btn.clicked.connect(self.import_requested)
        row.addWidget(self._import_btn, 1)
        layout.addLayout(row)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search media…")
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setIconSize(QPixmap(96, 54).size())
        self._list.setSpacing(2)
        self._list.itemDoubleClicked.connect(
            lambda item: self.add_requested.emit(item.data(Qt.UserRole)))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self._list, 1)

        self._empty = QLabel(
            "No media yet.\nImport gameplay, music or sound effects\n"
            "to start editing.")
        self._empty.setObjectName("dim")
        self._empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._empty)

    # ------------------------------------------------------------------ #
    def set_assets(self, assets) -> None:
        """Rebuild the list from the project's asset list."""
        self._assets = {a.id: a for a in assets}
        self._list.clear()
        self._items = {}
        for asset in assets:
            item = QListWidgetItem(self._asset_label(asset))
            item.setData(Qt.UserRole, asset.id)
            item.setIcon(_placeholder_icon(asset))
            item.setToolTip(asset.path)
            self._list.addItem(item)
            self._items[asset.id] = item
            self._request_thumbnail(asset)
        self._empty.setVisible(not assets)
        self._filter(self._search.text())

    def _asset_label(self, asset: MediaAsset) -> str:
        if asset.kind == "audio" or asset.duration <= 0:
            detail = _KIND_BADGE.get(asset.kind, asset.kind)
        else:
            detail = f"{asset.duration:.1f}s"
            if asset.width:
                detail += f" · {asset.width}×{asset.height}"
        return f"{asset.name}\n{detail}"

    def _request_thumbnail(self, asset: MediaAsset) -> None:
        out_dir = os.path.join(str(engine.cache_dir()), "opencut-thumbs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{asset.id}.png")
        if asset.kind != "audio" and os.path.exists(out_path):
            self._set_icon(asset.id, out_path)
            return
        self._pool.start(_ThumbJob(self._signals, asset, out_path))

    def _thumb_done(self, asset_id: str, path: str) -> None:
        if path:
            self._set_icon(asset_id, path)
        # audio peaks were stored on the asset: nothing visual to do here,
        # the timeline reads them on its next repaint.

    def _set_icon(self, asset_id: str, path: str) -> None:
        item = self._items.get(asset_id)
        if item is not None and os.path.exists(path):
            item.setIcon(QIcon(path))

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for asset_id, item in self._items.items():
            asset = self._assets.get(asset_id)
            name = asset.name.lower() if asset else ""
            item.setHidden(bool(needle) and needle not in name)

    def _context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        asset_id = item.data(Qt.UserRole)
        menu = QMenu(self)
        add = menu.addAction("Add to timeline")
        add.triggered.connect(lambda: self.add_requested.emit(asset_id))
        remove = menu.addAction("Remove from library")
        remove.triggered.connect(lambda: self.remove_requested.emit(asset_id))
        menu.exec(self._list.mapToGlobal(pos))

    def selected_asset_id(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None
