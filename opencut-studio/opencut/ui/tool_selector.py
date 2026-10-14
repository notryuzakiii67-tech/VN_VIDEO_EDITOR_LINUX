"""Right-side contextual toolbar (compact, icon-driven).

Provides quick access to editing tools when a clip is selected.
Tools: Speed, Audio, Text, Transform, Effects, Animation, Canvas.
Each tool shows an inspector panel below the toolbar when active.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, ACCENT_DARK, BG, BORDER, PANEL, PANEL_LIGHT, TEXT, TEXT_DIM


class ToolButton(QToolButton):
    """Compact tool button with icon text and tooltip."""

    def __init__(self, label: str, icon_text: str, tooltip: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setText(icon_text)
        self.setToolTip(f"{label}: {tooltip}")
        self.setFixedSize(44, 44)
        self.setCheckable(True)
        self.setObjectName("toolBtn")


class ToolSelector(QWidget):
    """Vertical toolbar on the right with inspector panels underneath."""

    tool_changed = Signal(str)  # tool name when selection changes

    TOOLS = [
        ("speed",    "⚡", "Speed",    "Adjust playback speed"),
        ("audio",    "🔊", "Audio",    "Volume, pan, EQ"),
        ("text",     "T",  "Text",     "Title, caption, style"),
        ("transform","↗",  "Transform","Position, scale, rotate"),
        ("effects",  "✨", "Effects",  "Filters, color, glow"),
        ("animation","🎬", "Animation","Keyframes, easing"),
        ("canvas",   "🖼", "Canvas",   "Background, aspect ratio"),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_tool: Optional[str] = None
        self._buttons: dict[str, ToolButton] = {}
        self._panels: dict[str, QWidget] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QWidget()
        toolbar.setStyleSheet(f"background:{PANEL};")
        toolbar.setFixedWidth(52)
        tlay = QVBoxLayout(toolbar)
        tlay.setContentsMargins(4, 8, 4, 8)
        tlay.setSpacing(2)

        title = QLabel("Tools")
        title.setStyleSheet(f"color:{TEXT_DIM}; font-size:10px; font-weight:600;")
        title.setAlignment(Qt.AlignCenter)
        tlay.addWidget(title)

        for name, icon, label, tip in self.TOOLS:
            btn = ToolButton(label, icon, tip)
            btn.clicked.connect(lambda checked, n=name: self._select_tool(n))
            tlay.addWidget(btn)
            self._buttons[name] = btn

        tlay.addStretch(1)
        layout.addWidget(toolbar)

        # Stacked panels (hidden by default, shown when tool is selected)
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{BG};")
        self._stack.setVisible(False)

        # Create placeholder panels for each tool
        for name, _, label, tip in self.TOOLS:
            panel = self._create_tool_panel(name, label, tip)
            self._panels[name] = panel
            self._stack.addWidget(panel)

        layout.addWidget(self._stack, 1)

    def _create_tool_panel(self, name: str, label: str,
                           tip: str) -> QWidget:
        """Create an inspector panel for a tool.

        Returns a scrollable panel with header and placeholder content.
        The actual inspector content is injected later from main_window.
        """
        outer = QWidget()
        vlay = QVBoxLayout(outer)
        vlay.setContentsMargins(8, 8, 8, 8)
        vlay.setSpacing(6)

        # Header with close button
        hdr = QWidget()
        hdr.setStyleSheet(f"background:{PANEL}; border-radius:6px;")
        hlay = QVBoxLayout(hdr)
        hlay.setContentsMargins(8, 6, 8, 6)
        title = QLabel(f"{label} — {tip}")
        title.setStyleSheet(f"color:{TEXT}; font-weight:600; font-size:13px;")
        hlay.addWidget(title)
        vlay.addWidget(hdr)

        # Scrollable content area (populated by main_window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background:transparent; border:none;")
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        scroll.setWidget(content)
        vlay.addWidget(scroll, 1)

        # Store references for external population
        outer.setProperty("tool_name", name)
        outer.setProperty("content_widget", content)
        outer.setProperty("scroll_area", scroll)
        return outer

    def _select_tool(self, name: str) -> None:
        if self._active_tool == name:
            # Deselect
            self._active_tool = None
            self._stack.setVisible(False)
            for btn in self._buttons.values():
                btn.setChecked(False)
            self.tool_changed.emit("")
            return

        self._active_tool = name
        self._stack.setVisible(True)
        panel = self._panels[name]
        self._stack.setCurrentWidget(panel)
        for n, btn in self._buttons.items():
            btn.setChecked(n == name)
        self.tool_changed.emit(name)

    def get_content_widget(self, tool_name: str) -> Optional[QWidget]:
        """Get the content widget for a tool panel (for external population)."""
        panel = self._panels.get(tool_name)
        if panel:
            return panel.property("content_widget")
        return None

    def active_tool(self) -> Optional[str]:
        return self._active_tool

    def set_active(self, name: str) -> None:
        if name in self._buttons:
            self._select_tool(name)