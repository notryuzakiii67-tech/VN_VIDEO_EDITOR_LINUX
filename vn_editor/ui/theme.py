"""VN-style dark theme: palette, QSS and procedural vector icons.

Visual language (VN-like, original implementation):

* near-black workspace (#0d0f13) with slightly lifted panels (#16191f)
* a single saturated accent used for the playhead, selection and active tools
* the preview canvas floats on the darkest surface so the video reads as the
  brightest thing on screen
* the timeline sits under a horizontally scrolling toolbar with round tool
  buttons — VN's signature arrangement
* timeline clips are rounded, saturated fills with a lighter top edge
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

# --------------------------------------------------------------------------- #
#  Palette
# --------------------------------------------------------------------------- #

ACCENT = "#ff2f6b"
ACCENT_DIM = "#c41f52"
ACCENT_SOFT = "#ff6d99"

BG_ROOT = "#0b0d11"
BG_PANEL = "#14171d"
BG_PANEL_ALT = "#1a1e26"
BG_ELEVATED = "#22262f"
BG_INPUT = "#0f1216"
BG_HOVER = "#262b35"
BG_ACTIVE = "#313848"

BORDER = "#272c36"
BORDER_SOFT = "#1e222a"
BORDER_STRONG = "#3a4150"

TEXT = "#e9edf5"
TEXT_DIM = "#9aa3b2"
TEXT_MUTED = "#6b7383"
TEXT_ON_ACCENT = "#ffffff"

TRACK_VIDEO = "#2b4d7a"
TRACK_OVERLAY = "#5a3a7a"
TRACK_TEXT = "#2f6b52"
TRACK_AUDIO = "#7a5a2b"
TRACK_STICKER = "#7a3f5e"

CLIP_VIDEO = "#2f7fd4"
CLIP_IMAGE = "#38a169"
CLIP_AUDIO = "#c98a1b"
CLIP_TEXT = "#d94f8a"
CLIP_STICKER = "#a855f7"
CLIP_SELECTED = "#ffffff"

PLAYHEAD = "#ff3b6b"
GRID = "#20242c"
GRID_STRONG = "#2b313b"

# --------------------------------------------------------------------------- #
#  Fonts
# --------------------------------------------------------------------------- #

UI_FONT_FAMILIES = [
    "Inter", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Ubuntu", "sans-serif",
]
MONO_FAMILIES = ["JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono",
                 "monospace"]


def register_bundled_fonts() -> None:
    """Make the OFL font set available to Qt widgets (not just libass)."""
    from pathlib import Path
    d = Path(__file__).resolve().parent.parent / "resources" / "fonts"
    if not d.exists():
        return
    for f in sorted(d.glob("*.ttf")):
        try:
            QFontDatabase.addApplicationFont(str(f))
        except Exception:
            pass


def ui_font(size: int = 10, weight: int = 400) -> QFont:
    f = QFont()
    f.setFamilies(UI_FONT_FAMILIES)
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    return f


# --------------------------------------------------------------------------- #
#  Stylesheet
# --------------------------------------------------------------------------- #

_CHECK_PNG: str = ""


def check_png() -> str:
    """Path to a small white tick used by checked indicators.

    Qt style sheets cannot draw a tick, so one is rendered once and cached in
    the app cache directory.
    """
    global _CHECK_PNG
    if _CHECK_PNG and Path(_CHECK_PNG).exists():
        return _CHECK_PNG
    try:
        from ..core.project import cache_dir
        target = cache_dir() / "ui" / "check.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor("#ffffff"), 2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(3.4, 8.4), QPointF(6.6, 11.6),
                        QPointF(12.6, 4.6)])
        p.end()
        if not pm.save(str(target), "PNG"):
            return ""
        _CHECK_PNG = str(target)
        return _CHECK_PNG
    except Exception:
        return ""


def stylesheet(accent: str = ACCENT) -> str:
    a = accent
    tick = check_png()
    tick_rule = (f'    image: url("{tick}");\n' if tick else "")
    return f"""
* {{ outline: none; }}

QWidget {{
    background: {BG_ROOT};
    color: {TEXT};
    font-family: "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 12px;
}}

QMainWindow, QDialog {{ background: {BG_ROOT}; }}

QToolTip {{
    background: {BG_ELEVATED}; color: {TEXT};
    border: 1px solid {BORDER_STRONG}; border-radius: 6px; padding: 5px 8px;
}}

QFrame[class="panel"] {{
    background: {BG_PANEL};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
}}
QFrame[class="panelFlat"] {{
    background: {BG_PANEL};
    border: none;
    border-radius: 0px;
}}
QFrame[class="card"] {{
    background: {BG_PANEL_ALT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
}}
QFrame[class="separator"] {{ background: {BORDER_SOFT}; max-height: 1px; }}

QLabel {{ background: transparent; }}
QLabel[class="h1"] {{ font-size: 17px; font-weight: 700; }}
QLabel[class="h2"] {{ font-size: 14px; font-weight: 600; }}
QLabel[class="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[class="dim"] {{ color: {TEXT_DIM}; }}
QLabel[class="sectionTitle"] {{
    color: {TEXT_DIM}; font-size: 11px; font-weight: 700;
    letter-spacing: 1px; text-transform: uppercase;
}}
QLabel[class="accent"] {{ color: {a}; font-weight: 600; }}

/* ---- buttons ---- */
QPushButton {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 12px;
    color: {TEXT};
    min-height: 16px;
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {BG_ACTIVE}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; background: {BG_PANEL_ALT};
                        border-color: {BORDER_SOFT}; }}
QPushButton[class="primary"] {{
    background: {a}; border: none; color: {TEXT_ON_ACCENT}; font-weight: 600;
}}
QPushButton[class="primary"]:hover {{ background: {ACCENT_SOFT}; }}
QPushButton[class="primary"]:disabled {{ background: {ACCENT_DIM}; color: #d9c2cd; }}
QPushButton[class="ghost"] {{ background: transparent; border: none; padding: 4px 8px; }}
QPushButton[class="ghost"]:hover {{ background: {BG_HOVER}; border-radius: 6px; }}
QPushButton[class="tool"] {{
    background: transparent; border: 1px solid transparent; border-radius: 9px;
    padding: 4px; color: {TEXT_DIM};
}}
QPushButton[class="tool"]:hover {{ background: {BG_HOVER}; color: {TEXT}; }}
QPushButton[class="tool"]:checked {{
    background: {BG_ACTIVE}; border-color: {a}; color: {TEXT};
}}
QPushButton[class="chip"] {{
    background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 13px;
    padding: 5px 12px; color: {TEXT_DIM};
}}
QPushButton[class="chip"]:hover {{ border-color: {BORDER_STRONG}; color: {TEXT}; }}
QPushButton[class="chip"]:checked {{
    background: {a}; border-color: {a}; color: white; font-weight: 600;
}}
QPushButton[class="danger"] {{ background: #7a2338; border-color: #a12a48; }}
QPushButton[class="danger"]:hover {{ background: #94304a; }}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 5px 8px;
    color: {TEXT};
    selection-background-color: {a};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {a}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {TEXT_DIM};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {BG_ELEVATED}; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 4px; selection-background-color: {a};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: {BG_PANEL_ALT}; border: none; width: 15px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-bottom: 4px solid {TEXT_DIM};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid {TEXT_DIM};
}}

/* ---- sliders ---- */
QSlider::groove:horizontal {{
    height: 4px; background: {BG_INPUT}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: #ffffff; width: 13px; height: 13px; margin: -5px 0;
    border-radius: 7px; border: 2px solid {a};
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_SOFT}; }}
QSlider::groove:vertical {{ width: 4px; background: {BG_INPUT}; border-radius: 2px; }}
QSlider::sub-page:vertical {{ background: {BG_INPUT}; }}
QSlider::handle:vertical {{
    background: #ffffff; height: 12px; width: 12px; margin: 0 -5px;
    border-radius: 6px; border: 2px solid {a};
}}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #333a46; border-radius: 5px; min-height: 26px;
}}
QScrollBar::handle:vertical:hover {{ background: #444d5d; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: #333a46; border-radius: 5px; min-width: 26px;
}}
QScrollBar::handle:horizontal:hover {{ background: #444d5d; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_MUTED}; padding: 7px 13px;
    border-radius: 7px; margin-right: 2px; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT_DIM}; background: {BG_PANEL_ALT}; }}
QTabBar::tab:selected {{ color: {TEXT}; background: {BG_ELEVATED}; }}

/* ---- lists / trees ---- */
QListWidget, QListView, QTreeWidget, QTreeView, QTableWidget {{
    background: transparent; border: none; outline: none;
    alternate-background-color: {BG_PANEL_ALT};
}}
QListWidget::item, QListView::item {{ padding: 5px 6px; border-radius: 7px; }}
QListWidget::item:hover, QListView::item:hover {{ background: {BG_PANEL_ALT}; }}
QListWidget::item:selected, QListView::item:selected {{
    background: {BG_ACTIVE}; color: {TEXT};
}}
QHeaderView::section {{
    background: {BG_PANEL}; color: {TEXT_DIM}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 5px 8px; font-weight: 600;
}}

/* ---- misc ---- */
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {BORDER_STRONG}; background: {BG_INPUT};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked {{
    background: {a}; border-color: {a};
{tick_rule}}}
QRadioButton::indicator:checked {{ background: {a}; border-color: {a}; }}
QCheckBox::indicator:disabled {{ background: {BG_PANEL_ALT}; }}
QGroupBox {{
    border: 1px solid {BORDER_SOFT}; border-radius: 9px; margin-top: 9px;
    padding-top: 9px; font-weight: 600; color: {TEXT_DIM};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
QProgressBar {{
    background: {BG_INPUT}; border: none; border-radius: 5px;
    height: 8px; text-align: center; color: {TEXT_DIM};
}}
QProgressBar::chunk {{ background: {a}; border-radius: 5px; }}
QMenu {{
    background: {BG_ELEVATED}; border: 1px solid {BORDER_STRONG};
    border-radius: 9px; padding: 5px;
}}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {a}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QSplitter::handle {{ background: {BORDER_SOFT}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:hover {{ background: {a}; }}
QStatusBar {{ background: {BG_PANEL}; color: {TEXT_MUTED}; border-top: 1px solid {BORDER_SOFT}; }}
QStatusBar::item {{ border: none; }}
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {BG_PANEL}; padding: 6px 10px; border-bottom: 1px solid {BORDER_SOFT};
    font-weight: 600; color: {TEXT_DIM};
}}
QToolBar {{ background: {BG_PANEL}; border: none; spacing: 4px; padding: 4px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 6px 4px; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QDialog QLabel {{ background: transparent; }}
"""


# --------------------------------------------------------------------------- #
#  Procedural icons
# --------------------------------------------------------------------------- #

def _painter_on(size: int) -> Tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    return pm, p


def _stroke(p: QPainter, color: QColor, width: float) -> None:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _fill(p: QPainter, color: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(color))


def _poly(pts, w: float, h: float, cx=0.5, cy=0.5):
    return QPolygonF([QPointF((x - cx) * w + w / 2, (y - cy) * h + h / 2)
                      for x, y in pts])


ICON_DRAWERS: Dict[str, callable] = {}


def icon(name: str, color: str = TEXT_DIM, size: int = 22,
         color2: Optional[str] = None) -> QIcon:
    drawer = ICON_DRAWERS.get(name)
    pm, p = _painter_on(size)
    if drawer is None:
        _stroke(p, QColor(color), 1.6)
        p.drawRect(QRectF(size * 0.25, size * 0.25, size * 0.5, size * 0.5))
    else:
        drawer(p, size, QColor(color), QColor(color2 or color))
    p.end()
    return QIcon(pm)


def pixmap(name: str, color: str = TEXT_DIM, size: int = 22,
           color2: Optional[str] = None) -> QPixmap:
    drawer = ICON_DRAWERS.get(name)
    pm, p = _painter_on(size)
    if drawer:
        drawer(p, size, QColor(color), QColor(color2 or color))
    p.end()
    return pm


def _d(name):
    def deco(fn):
        ICON_DRAWERS[name] = fn
        return fn
    return deco


# -- media / transport ------------------------------------------------------ #

@_d("play")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.36, 0.24), (0.78, 0.5), (0.36, 0.76)], s, s))


@_d("pause")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawRoundedRect(QRectF(s * .3, s * .26, s * .14, s * .48), 2, 2)
    p.drawRoundedRect(QRectF(s * .54, s * .26, s * .14, s * .48), 2, 2)


@_d("stop")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawRoundedRect(QRectF(s * .3, s * .3, s * .4, s * .4), 3, 3)


@_d("skip_start")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.72, 0.24), (0.3, 0.5), (0.72, 0.76)], s, s))
    p.drawRect(QRectF(s * .24, s * .26, s * .08, s * .48))


@_d("skip_end")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.28, 0.24), (0.7, 0.5), (0.28, 0.76)], s, s))
    p.drawRect(QRectF(s * .68, s * .26, s * .08, s * .48))


@_d("frame_prev")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.66, 0.26), (0.32, 0.5), (0.66, 0.74)], s, s))
    p.drawRect(QRectF(s * .24, s * .28, s * .06, s * .44))


@_d("frame_next")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.34, 0.26), (0.68, 0.5), (0.34, 0.74)], s, s))
    p.drawRect(QRectF(s * .7, s * .28, s * .06, s * .44))


@_d("loop")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.075)
    r = QRectF(s * .24, s * .28, s * .52, s * .44)
    p.drawArc(r, 40 * 16, 280 * 16)
    _fill(p, c)
    p.drawPolygon(_poly([(0.72, 0.2), (0.86, 0.34), (0.66, 0.38)], s, s))


@_d("fullscreen")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.075)
    m = s * 0.24
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = m if sx > 0 else s - m
        y = m if sy > 0 else s - m
        p.drawLine(QPointF(x, y), QPointF(x + sx * s * .18, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + sy * s * .18))


@_d("volume")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.3, 0.38), (0.42, 0.38), (0.56, 0.26),
                         (0.56, 0.74), (0.42, 0.62), (0.3, 0.62)], s, s))
    _stroke(p, c, s * 0.06)
    p.drawArc(QRectF(s * .48, s * .34, s * .28, s * .32), -60 * 16, 120 * 16)


@_d("mute")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.28, 0.38), (0.4, 0.38), (0.54, 0.26),
                         (0.54, 0.74), (0.4, 0.62), (0.28, 0.62)], s, s))
    _stroke(p, c, s * 0.065)
    p.drawLine(QPointF(s * .62, s * .38), QPointF(s * .8, s * .62))
    p.drawLine(QPointF(s * .8, s * .38), QPointF(s * .62, s * .62))


# -- editing ---------------------------------------------------------------- #

@_d("split")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .5, s * .18), QPointF(s * .5, s * .82))
    _fill(p, c)
    p.drawPolygon(_poly([(0.2, 0.4), (0.38, 0.5), (0.2, 0.6)], s, s))
    p.drawPolygon(_poly([(0.8, 0.4), (0.62, 0.5), (0.8, 0.6)], s, s))


@_d("trim")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawRoundedRect(QRectF(s * .3, s * .34, s * .4, s * .32), 3, 3)
    _fill(p, c)
    p.drawRect(QRectF(s * .2, s * .28, s * .06, s * .44))
    p.drawRect(QRectF(s * .74, s * .28, s * .06, s * .44))


@_d("delete")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .26, s * .32), QPointF(s * .74, s * .32))
    p.drawLine(QPointF(s * .42, s * .24), QPointF(s * .58, s * .24))
    p.drawRoundedRect(QRectF(s * .34, s * .32, s * .32, s * .44), 3, 3)


@_d("copy")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawRoundedRect(QRectF(s * .3, s * .3, s * .4, s * .4), 3, 3)
    p.drawRoundedRect(QRectF(s * .22, s * .22, s * .4, s * .4), 3, 3)


@_d("undo")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.075)
    p.drawArc(QRectF(s * .24, s * .28, s * .52, s * .44), 150 * 16, 210 * 16)
    _fill(p, c)
    p.drawPolygon(_poly([(0.16, 0.4), (0.34, 0.28), (0.36, 0.5)], s, s))


@_d("redo")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.075)
    p.drawArc(QRectF(s * .24, s * .28, s * .52, s * .44), -180 * 16, 210 * 16)
    _fill(p, c)
    p.drawPolygon(_poly([(0.84, 0.4), (0.66, 0.28), (0.64, 0.5)], s, s))


@_d("plus")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.085)
    p.drawLine(QPointF(s * .5, s * .26), QPointF(s * .5, s * .74))
    p.drawLine(QPointF(s * .26, s * .5), QPointF(s * .74, s * .5))


@_d("minus")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.085)
    p.drawLine(QPointF(s * .26, s * .5), QPointF(s * .74, s * .5))


@_d("close")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.08)
    p.drawLine(QPointF(s * .3, s * .3), QPointF(s * .7, s * .7))
    p.drawLine(QPointF(s * .7, s * .3), QPointF(s * .3, s * .7))


@_d("check")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.09)
    p.drawPolyline(_poly([(0.26, 0.52), (0.44, 0.68), (0.74, 0.34)], s, s))


@_d("chevron_down")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.08)
    p.drawPolyline(_poly([(0.32, 0.42), (0.5, 0.6), (0.68, 0.42)], s, s))


@_d("chevron_up")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.08)
    p.drawPolyline(_poly([(0.32, 0.58), (0.5, 0.4), (0.68, 0.58)], s, s))


@_d("chevron_left")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.08)
    p.drawPolyline(_poly([(0.58, 0.3), (0.4, 0.5), (0.58, 0.7)], s, s))


@_d("chevron_right")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.08)
    p.drawPolyline(_poly([(0.42, 0.3), (0.6, 0.5), (0.42, 0.7)], s, s))


@_d("keyframe")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.5, 0.24), (0.76, 0.5), (0.5, 0.76), (0.24, 0.5)],
                        s, s))


@_d("keyframe_outline")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawPolygon(_poly([(0.5, 0.24), (0.76, 0.5), (0.5, 0.76), (0.24, 0.5)],
                        s, s))


@_d("keyframe_add")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawPolygon(_poly([(0.5, 0.24), (0.76, 0.5), (0.5, 0.76), (0.24, 0.5)],
                        s, s))
    _stroke(p, c2, s * 0.075)
    p.drawLine(QPointF(s * .5, s * .36), QPointF(s * .5, s * .64))
    p.drawLine(QPointF(s * .36, s * .5), QPointF(s * .64, s * .5))


@_d("speed")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    poly = QPainterPath()
    poly.moveTo(s * .2, s * .72)
    poly.cubicTo(s * .42, s * .72, s * .4, s * .3, s * .8, s * .3)
    p.drawPath(poly)
    _fill(p, c2)
    p.drawEllipse(QPointF(s * .2, s * .72), s * .055, s * .055)
    p.drawEllipse(QPointF(s * .8, s * .3), s * .055, s * .055)


@_d("transition")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.18, 0.3), (0.46, 0.5), (0.18, 0.7)], s, s))
    _fill(p, c2)
    p.drawPolygon(_poly([(0.82, 0.3), (0.54, 0.5), (0.82, 0.7)], s, s))


@_d("filter")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawEllipse(QRectF(s * .24, s * .24, s * .3, s * .3))
    p.drawEllipse(QRectF(s * .46, s * .46, s * .3, s * .3))
    p.drawLine(QPointF(s * .4, s * .4), QPointF(s * .6, s * .6))


@_d("text")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.075)
    p.drawLine(QPointF(s * .24, s * .3), QPointF(s * .76, s * .3))
    p.drawLine(QPointF(s * .5, s * .3), QPointF(s * .5, s * .74))


@_d("sticker")
def _(p, s, c, c2):
    _fill(p, c)
    pts = []
    import math
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = 0.4 if i % 2 == 0 else 0.18
        pts.append((0.5 + r * math.cos(ang), 0.5 + r * math.sin(ang)))
    p.drawPolygon(_poly(pts, s, s))


@_d("music")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .62, s * .24), QPointF(s * .62, s * .66))
    p.drawLine(QPointF(s * .62, s * .24), QPointF(s * .78, s * .3))
    _fill(p, c)
    p.drawEllipse(QPointF(s * .5, s * .68), s * .12, s * .09)
    p.drawEllipse(QPointF(s * .72, s * .74), s * .1, s * .075)


@_d("mic")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawRoundedRect(QRectF(s * .4, s * .22, s * .2, s * .34), s * .1, s * .1)
    p.drawArc(QRectF(s * .3, s * .4, s * .4, s * .3), 180 * 16, 180 * 16)
    p.drawLine(QPointF(s * .5, s * .7), QPointF(s * .5, s * .8))


@_d("crop")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .3, s * .2), QPointF(s * .3, s * .7))
    p.drawLine(QPointF(s * .3, s * .7), QPointF(s * .8, s * .7))
    p.drawLine(QPointF(s * .24, s * .3), QPointF(s * .7, s * .3))
    p.drawLine(QPointF(s * .7, s * .3), QPointF(s * .7, s * .8))


@_d("rotate")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawArc(QRectF(s * .26, s * .26, s * .48, s * .48), 30 * 16, 280 * 16)
    _fill(p, c)
    p.drawPolygon(_poly([(0.72, 0.16), (0.86, 0.32), (0.66, 0.36)], s, s))


@_d("flip")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .5, s * .18), QPointF(s * .5, s * .82))
    _fill(p, c)
    p.drawPolygon(_poly([(0.4, 0.32), (0.4, 0.68), (0.16, 0.5)], s, s))
    _stroke(p, c, s * 0.06)
    p.drawPolygon(_poly([(0.6, 0.32), (0.6, 0.68), (0.84, 0.5)], s, s))


@_d("mask")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawEllipse(QRectF(s * .24, s * .24, s * .52, s * .52))
    _fill(p, c2)
    p.drawRect(QRectF(s * .5, s * .24, s * .26, s * .52))


@_d("chroma")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawEllipse(QRectF(s * .22, s * .22, s * .4, s * .4))
    _fill(p, c2)
    p.drawEllipse(QRectF(s * .38, s * .38, s * .4, s * .4))


@_d("opacity")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawEllipse(QRectF(s * .24, s * .24, s * .52, s * .52))
    _fill(p, c)
    path = QPainterPath()
    path.moveTo(s * .5, s * .24)
    path.arcTo(QRectF(s * .24, s * .24, s * .52, s * .52), 90, -180)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPath(path)


@_d("scale")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawRect(QRectF(s * .22, s * .22, s * .56, s * .56))
    _fill(p, c2)
    p.drawRect(QRectF(s * .5, s * .5, s * .3, s * .3))


@_d("move")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .5, s * .22), QPointF(s * .5, s * .78))
    p.drawLine(QPointF(s * .22, s * .5), QPointF(s * .78, s * .5))
    _fill(p, c)
    for pts in ([(0.5, 0.16), (0.42, 0.28), (0.58, 0.28)],
                [(0.5, 0.84), (0.42, 0.72), (0.58, 0.72)],
                [(0.16, 0.5), (0.28, 0.42), (0.28, 0.58)],
                [(0.84, 0.5), (0.72, 0.42), (0.72, 0.58)]):
        p.drawPolygon(_poly(pts, s, s))


@_d("fit")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawRect(QRectF(s * .2, s * .28, s * .6, s * .44))
    _fill(p, c2)
    p.drawRect(QRectF(s * .32, s * .36, s * .36, s * .28))


@_d("layers")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    for i, y in enumerate((0.3, 0.48, 0.66)):
        p.drawPolygon(_poly([(0.5, y - 0.12), (0.8, y), (0.5, y + 0.12),
                             (0.2, y)], s, s))


@_d("add_track")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawRoundedRect(QRectF(s * .18, s * .3, s * .5, s * .18), 3, 3)
    p.drawRoundedRect(QRectF(s * .18, s * .54, s * .5, s * .18), 3, 3)
    _fill(p, c2)
    p.drawEllipse(QPointF(s * .78, s * .4), s * .08, s * .08)
    p.drawEllipse(QPointF(s * .78, s * .64), s * .08, s * .08)


@_d("lock")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawArc(QRectF(s * .34, s * .26, s * .32, s * .32), 0, 180 * 16)
    _fill(p, c)
    p.drawRoundedRect(QRectF(s * .28, s * .5, s * .44, s * .3), 3, 3)


@_d("eye")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    path = QPainterPath()
    path.moveTo(s * .2, s * .5)
    path.quadTo(s * .5, s * .18, s * .8, s * .5)
    path.quadTo(s * .5, s * .82, s * .2, s * .5)
    p.drawPath(path)
    _fill(p, c)
    p.drawEllipse(QPointF(s * .5, s * .5), s * .09, s * .09)


@_d("settings")
def _(p, s, c, c2):
    import math
    _stroke(p, c, s * 0.065)
    p.drawEllipse(QRectF(s * .36, s * .36, s * .28, s * .28))
    for i in range(8):
        ang = i * math.pi / 4
        x0 = s * .5 + math.cos(ang) * s * .26
        y0 = s * .5 + math.sin(ang) * s * .26
        x1 = s * .5 + math.cos(ang) * s * .38
        y1 = s * .5 + math.sin(ang) * s * .38
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))


@_d("export")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .5, s * .24), QPointF(s * .5, s * .58))
    _fill(p, c2)
    p.drawPolygon(_poly([(0.5, 0.16), (0.36, 0.32), (0.64, 0.32)], s, s))
    _stroke(p, c, s * 0.07)
    p.drawPolyline(_poly([(0.28, 0.56), (0.28, 0.78), (0.72, 0.78),
                          (0.72, 0.56)], s, s))


@_d("save")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawRoundedRect(QRectF(s * .24, s * .24, s * .52, s * .52), 3, 3)
    _fill(p, c2)
    p.drawRect(QRectF(s * .34, s * .24, s * .32, s * .2))
    p.drawRect(QRectF(s * .32, s * .54, s * .36, s * .22))


@_d("folder")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    path = QPainterPath()
    path.moveTo(s * .2, s * .72)
    path.lineTo(s * .2, s * .34)
    path.lineTo(s * .42, s * .34)
    path.lineTo(s * .48, s * .42)
    path.lineTo(s * .8, s * .42)
    path.lineTo(s * .8, s * .72)
    path.closeSubpath()
    p.drawPath(path)


@_d("file")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    path = QPainterPath()
    path.moveTo(s * .28, s * .2)
    path.lineTo(s * .6, s * .2)
    path.lineTo(s * .74, s * .36)
    path.lineTo(s * .74, s * .8)
    path.lineTo(s * .28, s * .8)
    path.closeSubpath()
    p.drawPath(path)


@_d("grid")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.055)
    for i in (0.32, 0.5, 0.68):
        p.drawLine(QPointF(s * .2, s * i), QPointF(s * .8, s * i))
        p.drawLine(QPointF(s * i, s * .2), QPointF(s * i, s * .8))
    p.drawRect(QRectF(s * .2, s * .2, s * .6, s * .6))


@_d("list")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    for i in (0.3, 0.44, 0.58, 0.72):
        p.drawLine(QPointF(s * .34, s * i), QPointF(s * .78, s * i))
        p.drawPoint(QPointF(s * .24, s * i))


@_d("aspect")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawRect(QRectF(s * .18, s * .32, s * .34, s * .36))
    p.drawRect(QRectF(s * .46, s * .24, s * .36, s * .52))


@_d("captions")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.06)
    p.drawRoundedRect(QRectF(s * .18, s * .3, s * .64, s * .4), 4, 4)
    _fill(p, c)
    p.drawRect(QRectF(s * .28, s * .42, s * .2, s * .05))
    p.drawRect(QRectF(s * .28, s * .54, s * .34, s * .05))


@_d("magic")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawLine(QPointF(s * .32, s * .7), QPointF(s * .7, s * .3))
    _fill(p, c2)
    p.drawPolygon(_poly([(0.82, 0.18), (0.86, 0.28), (0.96, 0.32),
                         (0.86, 0.36), (0.82, 0.46), (0.78, 0.36),
                         (0.68, 0.32), (0.78, 0.28)], s, s))
    p.drawPolygon(_poly([(0.6, 0.62), (0.64, 0.68), (0.7, 0.7),
                         (0.64, 0.72), (0.6, 0.78), (0.56, 0.72),
                         (0.5, 0.7), (0.56, 0.68)], s, s))


@_d("template")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.06)
    p.drawRect(QRectF(s * .18, s * .2, s * .64, s * .6))
    _fill(p, c2)
    p.drawRect(QRectF(s * .24, s * .26, s * .26, s * .22))
    p.drawRect(QRectF(s * .54, s * .26, s * .22, s * .22))
    p.drawRect(QRectF(s * .24, s * .54, s * .52, s * .2))


@_d("marker")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.3, 0.2), (0.72, 0.2), (0.72, 0.5), (0.5, 0.38),
                         (0.3, 0.5)], s, s))
    _stroke(p, c, s * 0.06)
    p.drawLine(QPointF(s * .5, s * .42), QPointF(s * .5, s * .82))


@_d("beat")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawPolygon(_poly([(0.2, 0.5), (0.34, 0.32), (0.45, 0.68),
                         (0.56, 0.38), (0.66, 0.56), (0.8, 0.5)], s, s))


@_d("heart")
def _(p, s, c, c2):
    _fill(p, c)
    path = QPainterPath()
    path.moveTo(s * .5, s * .74)
    path.cubicTo(s * .08, s * .46, s * .2, s * .18, s * .5, s * .36)
    path.cubicTo(s * .8, s * .18, s * .92, s * .46, s * .5, s * .74)
    p.drawPath(path)


@_d("info")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawEllipse(QRectF(s * .22, s * .22, s * .56, s * .56))
    _fill(p, c)
    p.drawEllipse(QPointF(s * .5, s * .36), s * .045, s * .045)
    p.drawRect(QRectF(s * .465, s * .46, s * .07, s * .2))


@_d("warn")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawPolygon(_poly([(0.5, 0.2), (0.84, 0.78), (0.16, 0.78)], s, s))
    _fill(p, c)
    p.drawRect(QRectF(s * .465, s * .42, s * .07, s * .16))
    p.drawEllipse(QPointF(s * .5, s * .68), s * .04, s * .04)


@_d("refresh")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawArc(QRectF(s * .24, s * .24, s * .52, s * .52), 60 * 16, 250 * 16)
    _fill(p, c)
    p.drawPolygon(_poly([(0.76, 0.18), (0.88, 0.36), (0.68, 0.4)], s, s))


@_d("zoom_in")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawEllipse(QRectF(s * .2, s * .2, s * .44, s * .44))
    p.drawLine(QPointF(s * .58, s * .58), QPointF(s * .8, s * .8))
    p.drawLine(QPointF(s * .42, s * .3), QPointF(s * .42, s * .54))
    p.drawLine(QPointF(s * .3, s * .42), QPointF(s * .54, s * .42))


@_d("zoom_out")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawEllipse(QRectF(s * .2, s * .2, s * .44, s * .44))
    p.drawLine(QPointF(s * .58, s * .58), QPointF(s * .8, s * .8))
    p.drawLine(QPointF(s * .3, s * .42), QPointF(s * .54, s * .42))


@_d("record")
def _(p, s, c, c2):
    _fill(p, c)
    p.drawEllipse(QPointF(s * .5, s * .5), s * .26, s * .26)


@_d("reverse")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.07)
    p.drawArc(QRectF(s * .22, s * .22, s * .56, s * .56), 40 * 16, 280 * 16)
    _fill(p, c)
    p.drawPolygon(_poly([(0.2, 0.3), (0.4, 0.24), (0.34, 0.46)], s, s))


@_d("freeze")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    for ang in (0, 60, 120):
        import math
        a = math.radians(ang)
        dx, dy = math.cos(a) * s * .28, math.sin(a) * s * .28
        p.drawLine(QPointF(s * .5 - dx, s * .5 - dy),
                   QPointF(s * .5 + dx, s * .5 + dy))
    p.drawEllipse(QRectF(s * .4, s * .4, s * .2, s * .2))


@_d("pip")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawRoundedRect(QRectF(s * .18, s * .24, s * .64, s * .52), 3, 3)
    _fill(p, c2)
    p.drawRoundedRect(QRectF(s * .52, s * .52, s * .26, s * .2), 2, 2)


@_d("mirror")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.065)
    p.drawPolygon(_poly([(0.42, 0.28), (0.42, 0.72), (0.18, 0.5)], s, s))
    _fill(p, c)
    p.drawPolygon(_poly([(0.58, 0.28), (0.58, 0.72), (0.82, 0.5)], s, s))


@_d("horizon")
def _(p, s, c, c2):
    _stroke(p, c, s * 0.06)
    p.drawLine(QPointF(s * .2, s * .62), QPointF(s * .8, s * .62))
    p.drawArc(QRectF(s * .3, s * .3, s * .4, s * .4), 0, 180 * 16)
