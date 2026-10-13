"""OpenCut Studio's visual theme.

An original, restrained dark theme: one accent colour, generous spacing,
readable type.  Professional power underneath, simplicity on the surface.
"""
from __future__ import annotations

ACCENT = "#4da3ff"
ACCENT_DARK = "#2b6cb8"
BG = "#1d2025"
PANEL = "#262a31"
PANEL_LIGHT = "#2f343d"
BORDER = "#3a4049"
TEXT = "#e8eaed"
TEXT_DIM = "#9aa3ad"
DANGER = "#e5534b"
OK = "#3fb950"

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QToolBar {{ background: {PANEL}; border: none; spacing: 6px; padding: 6px; }}
QMenuBar {{ background: {PANEL}; color: {TEXT}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QMenu {{ background: {PANEL_LIGHT}; border: 1px solid {BORDER}; padding: 4px; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QSplitter::handle {{ background: {BORDER}; width: 2px; height: 2px; }}

QPushButton {{
    background: {PANEL_LIGHT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_DARK}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; }}
QPushButton#accent {{
    background: {ACCENT_DARK}; border-color: {ACCENT}; font-weight: 600;
}}
QPushButton#accent:hover {{ background: {ACCENT}; color: #0b1520; }}
QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 5px;
}}
QToolButton:hover {{ background: {PANEL_LIGHT}; border-color: {BORDER}; }}
QToolButton:disabled {{ color: {TEXT_DIM}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #171a1f; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_LIGHT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DARK};
}}

QListWidget {{
    background: #171a1f; border: 1px solid {BORDER}; border-radius: 8px;
    outline: none;
}}
QListWidget::item {{ padding: 4px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {ACCENT_DARK}; }}

QSlider::groove:horizontal {{
    height: 4px; background: {BORDER}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px; margin: -5px 0; border-radius: 7px; background: {ACCENT};
}}
QSlider::sub-page:horizontal {{ background: {ACCENT_DARK}; border-radius: 2px; }}

QProgressBar {{
    background: #171a1f; border: 1px solid {BORDER}; border-radius: 6px;
    text-align: center; height: 18px;
}}
QProgressBar::chunk {{ background: {ACCENT_DARK}; border-radius: 5px; }}

QGroupBox {{
    background: #22262d;
    border: 1px solid {BORDER}; border-radius: 8px; margin-top: 10px;
    padding-top: 8px; font-weight: 600;
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {BORDER}; background: #171a1f;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT_DARK}; border-color: {ACCENT};
}}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#title {{ font-size: 15px; font-weight: 700; }}
QToolTip {{
    background: {PANEL_LIGHT}; color: {TEXT}; border: 1px solid {BORDER};
    padding: 4px 8px;
}}
QScrollBar:horizontal, QScrollBar:vertical {{
    background: #171a1f; border: none; height: 10px; width: 10px;
}}
QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-width: 30px; min-height: 30px;
}}
QScrollBar::handle:hover {{ background: {ACCENT_DARK}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
"""


def format_time(seconds: float) -> str:
    """Timeline-style time: ``m:ss.t`` (always at least ``0:00.0``)."""
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:04.1f}"


def apply_dark_palette(app) -> None:
    """Set a dark QPalette so unstyled widgets (group boxes, scroll areas,
    dialogs) match the stylesheet instead of falling back to a light
    platform palette."""
    from PySide6.QtGui import QColor, QPalette

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor("#171a1f"))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.Button, QColor(PANEL_LIGHT))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ToolTipBase, QColor(PANEL_LIGHT))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT_DARK))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    disabled = QColor(TEXT_DIM)
    pal.setColor(QPalette.Disabled, QPalette.WindowText, disabled)
    pal.setColor(QPalette.Disabled, QPalette.Text, disabled)
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
    app.setPalette(pal)
