"""Editing toolbar (between main content and timeline).

Compact horizontal bar with editing actions:
Split, Delete, Duplicate, Trim, Extract Audio, Mirror, Flip, Reverse,
Freeze Frame, Speed adjustment.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QWidget,
)

from .theme import ACCENT, ACCENT_DARK, BG, BORDER, PANEL, PANEL_LIGHT, TEXT, TEXT_DIM


class EditToolbar(QWidget):
    """Compact editing toolbar with clip manipulation actions."""

    split_requested = Signal()
    delete_requested = Signal()
    duplicate_requested = Signal()
    extract_audio_requested = Signal()
    mirror_requested = Signal()
    flip_requested = Signal()
    reverse_requested = Signal()
    freeze_requested = Signal()
    speed_changed = Signal(float)  # 0.25..4.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"background:{PANEL}; border-top:1px solid {BORDER};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # Primary actions (icon-buttons)
        def btn(text: str, tip: str, signal: Signal, accent: bool = False):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedHeight(28)
            b.setStyleSheet(f"""
                QPushButton {{
                    background:{ACCENT_DARK if accent else PANEL_LIGHT};
                    border:1px solid {ACCENT if accent else BORDER};
                    border-radius:4px; padding:2px 8px; font-size:12px;
                    {'font-weight:600;' if accent else ''}
                }}
                QPushButton:hover {{ border-color:{ACCENT}; }}
                QPushButton:disabled {{ color:{TEXT_DIM}; }}
            """)
            b.clicked.connect(signal.emit)
            layout.addWidget(b)
            return b

        self._split_btn = btn("✂ Split", "Split at playhead (Ctrl+K)",
                              self.split_requested)
        self._dup_btn = btn("⧉ Duplicate", "Duplicate selection (Ctrl+D)",
                            self.duplicate_requested)
        self._del_btn = btn("🗑 Delete", "Delete selection (Del)",
                            self.delete_requested)

        # Separator
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{BORDER};")
        layout.addWidget(sep)

        # Transform actions
        self._mirror_btn = btn("↔ Mirror", "Mirror horizontally",
                               self.mirror_requested)
        self._flip_btn = btn("↕ Flip", "Flip vertically",
                             self.flip_requested)
        self._reverse_btn = btn("⟲ Reverse", "Reverse playback",
                                self.reverse_requested)
        self._freeze_btn = btn("⏸ Freeze", "Freeze frame at playhead",
                               self.freeze_requested)
        self._extract_btn = btn("🎵 Extract", "Extract audio track",
                                self.extract_audio_requested)

        # Separator
        sep2 = QWidget()
        sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background:{BORDER};")
        layout.addWidget(sep2)

        # Speed control
        speed_label = QLabel("Speed:")
        speed_label.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px;")
        layout.addWidget(speed_label)
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(25, 400)  # 0.25x to 4.0x
        self._speed_slider.setValue(100)
        self._speed_slider.setFixedWidth(100)
        self._speed_slider.valueChanged.connect(self._on_speed)
        layout.addWidget(self._speed_slider)
        self._speed_label = QLabel("1.00×")
        self._speed_label.setStyleSheet(f"color:{TEXT}; font-size:11px;")
        self._speed_label.setFixedWidth(36)
        layout.addWidget(self._speed_label)

        layout.addStretch(1)

    def _on_speed(self, val: int) -> None:
        speed = val / 100.0
        self._speed_label.setText(f"{speed:.2f}×")
        self.speed_changed.emit(speed)

    def set_enabled_state(self, has_selection: bool) -> None:
        """Enable/disable buttons based on whether a clip is selected."""
        for btn in (self._split_btn, self._dup_btn, self._del_btn,
                    self._mirror_btn, self._flip_btn, self._reverse_btn,
                    self._freeze_btn, self._extract_btn):
            btn.setEnabled(has_selection)