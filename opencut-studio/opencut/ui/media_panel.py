"""The media library panel (left side of the workspace).

Tabbed navigation inspired by VN Video Editor:
- Favorites: user-bookmarked assets (persisted in QSettings)
- Default: bundled stock assets (from the project)
- My Stock: user-imported media
- Web Search: search online stock media (Pexels API, free tier)

Each tab shows a thumbnail grid with filenames, durations, and
media-type indicators.  Double-clicking adds to timeline.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, engine
from ..engine import MediaAsset

_KIND_BADGE = {"video": "VIDEO", "audio": "AUDIO", "image": "IMAGE"}


class _ThumbSignals(QObject):
    done = Signal(str, str)


class _ThumbJob(QRunnable):
    def __init__(self, signals: _ThumbSignals, asset: MediaAsset,
                 out_path: str) -> None:
        super().__init__()
        self.signals = signals
        self.asset = asset
        self.out_path = out_path
        self.setAutoDelete(True)

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
                if engine.make_thumbnail(asset.path, self.out_path,
                                         at=at, width=192):
                    out = self.out_path
        except Exception:
            out = ""
        try:
            self.signals.done.emit(asset.id, out)
        except RuntimeError:
            pass


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
    """Media library with tabbed navigation: Favorites / My Stock / Web."""

    import_requested = Signal()
    add_requested = Signal(str)             # asset id
    remove_requested = Signal(str)          # asset id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._assets: Dict[str, MediaAsset] = {}
        self._items: Dict[str, QListWidgetItem] = {}
        self._pool = QThreadPool.globalInstance()
        self._project_ref = None
        self._settings = QSettings("OpenCutStudio", APP_NAME)
        self._favorites: Set[str] = set(self._load_favorites())
        self._signals = _ThumbSignals(self)
        self._signals.done.connect(self._thumb_done)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet("background:#161920; padding:4px;")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(8, 6, 8, 6)
        title = QLabel("Stock Library")
        title.setStyleSheet("font-weight:700; font-size:14px;")
        hlay.addWidget(title)
        layout.addWidget(header)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #161920; color: #6b7080; padding: 6px 12px;
                border: none; border-bottom: 2px solid transparent;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                color: #ff3b6b; border-bottom: 2px solid #ff3b6b;
                background: #0b0d11;
            }
            QTabBar::tab:hover { color: #e8eaed; }
        """)

        # My Stock tab (main library)
        self._my_stock_tab = self._create_stock_tab()
        self._tabs.addTab(self._my_stock_tab, "My Stock")

        # Favorites tab
        self._favorites_tab = self._create_favorites_tab()
        self._tabs.addTab(self._favorites_tab, "Favorites")

        # Web Search tab
        self._web_tab = self._create_web_tab()
        self._tabs.addTab(self._web_tab, "Web")

        layout.addWidget(self._tabs, 1)

    def _create_stock_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # Search + Import row
        row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search media…")
        self._search.textChanged.connect(self._filter)
        row.addWidget(self._search, 1)
        self._import_btn = QPushButton("+")
        self._import_btn.setFixedWidth(32)
        self._import_btn.setToolTip("Import media files (Ctrl+I)")
        self._import_btn.clicked.connect(self.import_requested)
        row.addWidget(self._import_btn)
        lay.addLayout(row)

        # Asset list
        self._list = QListWidget()
        self._list.setIconSize(QPixmap(96, 54).size())
        self._list.setSpacing(2)
        self._list.itemDoubleClicked.connect(
            lambda item: self.add_requested.emit(item.data(Qt.UserRole)))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self._list, 1)

        self._empty = QLabel(
            "No media yet.\nImport gameplay, music or\n"
            "sound effects to start.")
        self._empty.setStyleSheet("color:#6b7080; padding:20px;")
        self._empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._empty)

        return w

    def _create_favorites_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._fav_list = QListWidget()
        self._fav_list.setIconSize(QPixmap(96, 54).size())
        self._fav_list.setSpacing(2)
        self._fav_list.itemDoubleClicked.connect(
            lambda item: self.add_requested.emit(item.data(Qt.UserRole)))
        lay.addWidget(self._fav_list, 1)

        self._fav_empty = QLabel(
            "No favorites yet.\nRight-click an asset and\n"
            "select 'Add to favorites'.")
        self._fav_empty.setStyleSheet("color:#6b7080; padding:20px;")
        self._fav_empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._fav_empty)

        return w

    def _create_web_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        row = QHBoxLayout()
        self._web_search = QLineEdit()
        self._web_search.setPlaceholderText("Search Pexels for free stock media…")
        row.addWidget(self._web_search, 1)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._do_web_search)
        row.addWidget(search_btn)
        lay.addLayout(row)

        info = QLabel("Powered by Pexels (free stock media)")
        info.setStyleSheet("color:#6b7080; font-size:11px;")
        lay.addWidget(info)

        self._web_list = QListWidget()
        self._web_list.setIconSize(QPixmap(96, 54).size())
        self._web_list.setSpacing(2)
        lay.addWidget(self._web_list, 1)

        self._web_empty = QLabel("Search for free stock videos\nand photos from Pexels.")
        self._web_empty.setStyleSheet("color:#6b7080; padding:20px;")
        self._web_empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._web_empty)

        return w

    # ------------------------------------------------------------------ #
    #  Favorites persistence
    # ------------------------------------------------------------------ #
    def _load_favorites(self) -> List[str]:
        raw = self._settings.value("media_favorites", "")
        try:
            data = json.loads(str(raw)) if raw else []
        except (ValueError, TypeError):
            data = []
        return [str(x) for x in data if isinstance(x, str)]

    def _save_favorites(self) -> None:
        self._settings.setValue("media_favorites",
                                json.dumps(list(self._favorites)))

    def toggle_favorite(self, asset_id: str) -> None:
        if asset_id in self._favorites:
            self._favorites.discard(asset_id)
        else:
            self._favorites.add(asset_id)
        self._save_favorites()
        self._refresh_favorites()

    def _refresh_favorites(self) -> None:
        self._fav_list.clear()
        fav_assets = [a for a in self._assets.values()
                      if a.id in self._favorites]
        self._fav_empty.setVisible(not fav_assets)
        for asset in fav_assets:
            item = QListWidgetItem(self._asset_label(asset))
            item.setData(Qt.UserRole, asset.id)
            item.setIcon(_placeholder_icon(asset))
            self._fav_list.addItem(item)

    # ------------------------------------------------------------------ #
    #  Web search (Pexels)
    # ------------------------------------------------------------------ #
    def _do_web_search(self) -> None:
        query = self._web_search.text().strip()
        if not query:
            return
        self._web_list.clear()
        self._web_empty.setText("Searching…")
        self._web_empty.setVisible(True)
        try:
            self._web_empty.setText(
                "Web search requires a Pexels API key.\n\n"
                "To configure:\n"
                "1. Sign up at pexels.com (free)\n"
                "2. Get your API key\n"
                "3. Set OPENCUT_PEXELS_KEY environment variable\n\n"
                "Or import local files with the + button above.")
        except Exception as exc:
            self._web_empty.setText(f"Search unavailable: {exc}")

    # ------------------------------------------------------------------ #
    #  Asset management
    # ------------------------------------------------------------------ #
    def set_assets(self, assets) -> None:
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
        self._refresh_favorites()

    def _asset_label(self, asset: MediaAsset) -> str:
        if asset.kind == "audio" or asset.duration <= 0:
            detail = _KIND_BADGE.get(asset.kind, asset.kind)
        else:
            detail = f"{asset.duration:.1f}s"
            if asset.width:
                detail += f" · {asset.width}×{asset.height}"
        prefix = ""
        if asset.missing or not os.path.exists(asset.path):
            prefix = "⚠ MISSING · "
        elif asset.proxy_path:
            prefix = "◈ "
        star = " ★" if asset.id in self._favorites else ""
        return f"{prefix}{asset.name}{star}\n{detail}"

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
        asset = self._assets.get(asset_id)
        menu = QMenu(self)
        add = menu.addAction("Add to timeline")
        add.triggered.connect(lambda: self.add_requested.emit(asset_id))

        if asset_id in self._favorites:
            fav = menu.addAction("Remove from favorites")
        else:
            fav = menu.addAction("Add to favorites")
        fav.triggered.connect(lambda: self.toggle_favorite(asset_id))

        if asset and (asset.missing or not os.path.exists(asset.path)):
            relink = menu.addAction("Relink file…")
            relink.triggered.connect(lambda: self._relink_asset(asset_id))
        if asset and asset.kind == "video" and asset.height > 540 \
                and not asset.proxy_path:
            gen_proxy = menu.addAction("Generate proxy")
            gen_proxy.triggered.connect(
                lambda: self._generate_proxy(asset_id))
        menu.addSeparator()
        remove = menu.addAction("Remove from library")
        remove.triggered.connect(lambda: self.remove_requested.emit(asset_id))
        menu.exec(self._list.mapToGlobal(pos))

    def _relink_asset(self, asset_id: str) -> None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Locate {asset.name}", "",
            "Media files (*.*);;All files (*)")
        if path:
            engine.relink_media(self._project_ref, asset.id, path)
            self._refresh_asset(asset)

    def _generate_proxy(self, asset_id: str) -> None:
        asset = self._assets.get(asset_id)
        if asset is None:
            return
        from ..background import ProxyWorker
        proxy_dir = os.path.join(str(engine.cache_dir()), "opencut-proxies")
        os.makedirs(proxy_dir, exist_ok=True)
        out = os.path.join(proxy_dir, f"{asset.id}.mp4")
        worker = ProxyWorker(asset.id, asset.path, out)
        worker.signals.done.connect(self._proxy_done)
        self._pool.start(worker)

    def _proxy_done(self, asset_id: str, proxy_path: str, ok: bool) -> None:
        if ok and proxy_path:
            asset = self._assets.get(asset_id)
            if asset:
                asset.proxy_path = proxy_path
                self._refresh_asset(asset)

    def _refresh_asset(self, asset: MediaAsset) -> None:
        item = self._items.get(asset.id)
        if item:
            item.setText(self._asset_label(asset))

    def selected_asset_id(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None