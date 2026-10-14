"""Reusable editor controls (sliders with live values, colour pickers,
collapsible sections, param editors driven by the effect catalogue)."""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import (QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal,
                            QTimer)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFontMetrics, QIcon,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme
from .theme import pixmap as icon_pm


# --------------------------------------------------------------------------- #
#  layout helpers
# --------------------------------------------------------------------------- #

class Section(QWidget):
    """Collapsible titled section used throughout the inspector."""

    def __init__(self, title: str, parent=None, expanded: bool = True,
                 icon: Optional[str] = None) -> None:
        super().__init__(parent)
        self._expanded = expanded
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QToolButton()
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setArrowType(Qt.NoArrow)
        self.header.setText("  " + title.upper())
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none;"
            f" color: {theme.TEXT_DIM}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1px; padding: 8px 4px 5px 2px; text-align: left; }}"
            f"QToolButton:hover {{ color: {theme.TEXT}; }}")
        if icon:
            self.header.setIcon(theme.icon(icon, theme.TEXT_MUTED, 14))
        self.header.clicked.connect(self._toggle)
        root.addWidget(self.header)

        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(2, 0, 2, 8)
        self.body_layout.setSpacing(7)
        root.addWidget(self.body)
        self.body.setVisible(expanded)

    def _toggle(self) -> None:
        self._expanded = self.header.isChecked()
        self.body.setVisible(self._expanded)

    def add(self, w: QWidget) -> QWidget:
        self.body_layout.addWidget(w)
        return w

    def add_layout(self, lay) -> None:
        self.body_layout.addLayout(lay)

    def clear(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def row(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.body_layout.addLayout(lay)
        return lay


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.BORDER_SOFT};")
    return f


def label(text: str, kind: str = "", size: Optional[int] = None,
          weight: int = 400) -> QLabel:
    lb = QLabel(text)
    if kind:
        lb.setProperty("class", kind)
    if size or weight != 400:
        lb.setFont(theme.ui_font(size or 11, weight))
    return lb


# --------------------------------------------------------------------------- #
#  slider + numeric
# --------------------------------------------------------------------------- #

class SliderField(QWidget):
    """Slider with a numeric readout; emits continuously while dragging."""

    valueChanged = Signal(float)
    editingFinished = Signal(float)

    def __init__(self, minimum: float, maximum: float, value: float,
                 decimals: int = 2, suffix: str = "", factor: float = 100.0,
                 parent=None, default: Optional[float] = None,
                 log: bool = False) -> None:
        super().__init__(parent)
        self._min, self._max = minimum, maximum
        self._decimals = decimals
        self._suffix = suffix
        self._factor = factor
        self._default = default
        self._log = log and minimum > 0
        self._value = value

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int((maximum - minimum) * factor))
        self.slider.setValue(self._to_slider(value))
        self.slider.valueChanged.connect(self._on_slide)
        self.slider.setMinimumWidth(70)
        lay.addWidget(self.slider, 1)

        self.readout = QLabel()
        self.readout.setFixedWidth(52)
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.readout.setStyleSheet(
            f"color:{theme.TEXT}; background:{theme.BG_INPUT};"
            f"border:1px solid {theme.BORDER}; border-radius:5px;"
            f"padding:2px 4px; font-size:11px;")
        lay.addWidget(self.readout)
        self._update_readout()

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)

    # -- value plumbing ----------------------------------------------------- #
    def _to_slider(self, v: float) -> int:
        if self._log:
            v = max(self._min, min(self._max, v))
            a = math.log(self._min)
            b = math.log(self._max)
            return int((math.log(v) - a) / max(1e-9, b - a)
                       * (self.slider.maximum() if hasattr(self, "slider")
                          else (self._max - self._min) * self._factor))
        return int(round((max(self._min, min(self._max, v)) - self._min)
                         * self._factor))

    def _from_slider(self, raw: int) -> float:
        if self._log:
            a = math.log(self._min)
            b = math.log(self._max)
            frac = raw / max(1, (self._max - self._min) * self._factor)
            return math.exp(a + frac * (b - a))
        return self._min + raw / self._factor

    def _on_slide(self, raw: int) -> None:
        self._value = self._from_slider(raw)
        self._update_readout()
        self.valueChanged.emit(self._value)

    def _update_readout(self) -> None:
        v = self._value
        if self._decimals == 0:
            txt = f"{int(round(v))}{self._suffix}"
        else:
            txt = f"{v:.{self._decimals}f}{self._suffix}"
        self.readout.setText(txt)

    def value(self) -> float:
        return self._value

    def set_value(self, v: float, emit: bool = False) -> None:
        self._value = max(self._min, min(self._max, v))
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(self._value))
        self.slider.blockSignals(False)
        self._update_readout()
        if emit:
            self.valueChanged.emit(self._value)

    def _ctx(self, pos) -> None:
        if self._default is None:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        act = m.addAction(f"Reset to {self._default:g}{self._suffix}")
        if m.exec(self.mapToGlobal(pos)) is act:
            self.set_value(self._default, emit=True)
            self.editingFinished.emit(self._value)

    def mouseReleaseEvent(self, ev) -> None:
        super().mouseReleaseEvent(ev)
        self.editingFinished.emit(self._value)


class RowField(QWidget):
    """Label on the left, control on the right."""

    def __init__(self, text: str, control: QWidget, parent=None,
                 wide: bool = False) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lb = label(text, size=11)
        lb.setStyleSheet(f"color:{theme.TEXT_DIM};")
        lb.setMinimumWidth(58 if not wide else 90)
        lay.addWidget(lb)
        lay.addWidget(control, 1)


class ColorButton(QPushButton):
    """Swatch button opening the colour dialog (with alpha support)."""

    colorChanged = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None,
                 allow_alpha: bool = False) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._allow_alpha = allow_alpha
        self.setFixedSize(30, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)
        self.setStyleSheet("border: 1px solid #3a4150; border-radius: 6px;")

    def color(self) -> str:
        return self._color.name(QColor.HexArgb if self._allow_alpha
                                else QColor.HexRgb)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def _pick(self) -> None:
        opts = (QColorDialog.ShowAlphaChannel if self._allow_alpha
                else QColorDialog.ColorDialogOptions())
        c = QColorDialog.getColor(self._color, self, "Choose colour",
                                  QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self._color = c
            self.update()
            self.colorChanged.emit(self.color())

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2f3a"))
        p.drawRoundedRect(r, 5, 5)
        c = QColor(self._color)
        if c.alpha() < 255:
            p.setBrush(QColor("#4a505c"))
            for i in range(0, int(r.width()), 8):
                for j in range(0, int(r.height()), 8):
                    if (i // 8 + j // 8) % 2 == 0:
                        p.drawRect(QRectF(r.x() + i, r.y() + j, 8, 8))
        p.setBrush(c)
        p.drawRoundedRect(r, 5, 5)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 5, 5)
        p.end()


class ChipGroup(QWidget):
    """A row of mutually exclusive chips (VN's tool pickers)."""

    changed = Signal(int, str)

    def __init__(self, items: Sequence[str], current: int = 0,
                 parent=None, wrap: bool = True, small: bool = False,
                 icons: Optional[Sequence[Optional[str]]] = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout
        self._buttons: List[QPushButton] = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(5)
        per_row = 4 if wrap else max(1, len(items))
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("class", "chip")
            b.setChecked(i == current)
            if icons and i < len(icons) and icons[i]:
                b.setIcon(theme.icon(icons[i] or "", theme.TEXT_DIM, 15))
                b.setIconSize(QSize(15, 15))
            if small:
                b.setStyleSheet("padding: 3px 8px; font-size: 11px;")
            b.clicked.connect(lambda _=False, idx=i: self._choose(idx))
            grid.addWidget(b, i // per_row, i % per_row)
            self._buttons.append(b)

    def _choose(self, idx: int) -> None:
        for i, b in enumerate(self._buttons):
            b.setChecked(i == idx)
        self.changed.emit(idx, self._buttons[idx].text())

    def set_current(self, idx: int) -> None:
        for i, b in enumerate(self._buttons):
            b.setChecked(i == idx)

    def count(self) -> int:
        return len(self._buttons)


class IconButton(QToolButton):
    def __init__(self, icon: str, tip: str = "", size: int = 26,
                 icon_size: int = 17, checkable: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.setIcon(theme.icon(icon, theme.TEXT_DIM, icon_size))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(tip)
        self.setFixedSize(size, size)
        self.setCheckable(checkable)
        self.setAutoRaise(True)
        self.setStyleSheet(
            f"QToolButton {{ border: 1px solid transparent; border-radius: 7px; }}"
            f"QToolButton:hover {{ background: {theme.BG_HOVER}; }}"
            f"QToolButton:checked {{ background: {theme.BG_ACTIVE};"
            f" border-color: {theme.ACCENT}; }}")

    def set_icon_name(self, name: str, color: str = theme.TEXT_DIM,
                      size: int = 17) -> None:
        self.setIcon(theme.icon(name, color, size))


# --------------------------------------------------------------------------- #
#  effect parameter editor
# --------------------------------------------------------------------------- #

class ParamEditor(QWidget):
    """Auto-builds a control per parameter from an effect spec."""

    changed = Signal(str, float)      # key, value
    commit = Signal()

    def __init__(self, spec, values: Dict[str, float], parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.values = dict(values or {})
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        self._widgets: Dict[str, QWidget] = {}
        for prm in getattr(spec, "params", []) or []:
            cur = self.values.get(prm.key, prm.default)
            if prm.kind == "choice":
                cb = QComboBox()
                for i, ch in enumerate(prm.choices or []):
                    cb.addItem(str(ch), i)
                cb.setCurrentIndex(int(max(0, min(len(prm.choices or [1]) - 1,
                                                  int(round(cur))))))
                cb.currentIndexChanged.connect(
                    lambda idx, k=prm.key: self._set(k, float(idx)))
                self._widgets[prm.key] = cb
                lay.addWidget(RowField(prm.label, cb))
            elif prm.kind == "bool":
                cbx = QCheckBox(prm.label)
                cbx.setChecked(bool(cur))
                cbx.toggled.connect(lambda on, k=prm.key: self._set(k, 1.0 if on else 0.0))
                self._widgets[prm.key] = cbx
                lay.addWidget(cbx)
            elif prm.kind == "color":
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(6)
                btn = ColorButton(self.values.get(f"{prm.key}_hex",
                                                  "#ffffff"))
                btn.colorChanged.connect(
                    lambda c, k=prm.key: self._set_hex(k, c))
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 360.0)
                spin.setValue(float(cur) % 360.0)
                spin.setSuffix("°")
                spin.setFixedWidth(70)
                spin.valueChanged.connect(lambda v, k=prm.key: self._set(k, v))
                rl.addWidget(btn)
                rl.addWidget(spin)
                self._widgets[prm.key] = row
                lay.addWidget(RowField(prm.label, row))
            else:
                steps = 100.0 if (prm.maximum - prm.minimum) <= 8 else \
                    (10.0 if (prm.maximum - prm.minimum) <= 80 else 1.0)
                f = SliderField(prm.minimum, prm.maximum, float(cur),
                                decimals=2 if steps == 100.0 else 1,
                                factor=steps, default=prm.default)
                f.valueChanged.connect(lambda v, k=prm.key: self._set(k, v))
                f.editingFinished.connect(self.commit.emit)
                self._widgets[prm.key] = f
                lay.addWidget(RowField(prm.label, f))

    def _set(self, key: str, value: float) -> None:
        self.values[key] = value
        self.changed.emit(key, value)

    def _set_hex(self, key: str, color: str) -> None:
        self.values[f"{key}_hex"] = color
        self.changed.emit(f"{key}_hex", 0.0)

    def set_values(self, values: Dict[str, float]) -> None:
        for key, w in self._widgets.items():
            if key not in values:
                continue
            v = values[key]
            w.blockSignals(True)
            if isinstance(w, SliderField):
                w.set_value(float(v))
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(int(max(0, min(w.count() - 1, int(v)))))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            w.blockSignals(False)
            self.values[key] = v


# --------------------------------------------------------------------------- #
#  preset strip
# --------------------------------------------------------------------------- #

class PresetStrip(QWidget):
    """Horizontal scrolling strip of preset thumbnails/cards."""

    picked = Signal(str)

    def __init__(self, items: Sequence[Tuple[str, str, str]],
                 parent=None, card_w: int = 76, card_h: int = 54,
                 first_text: str = "None") -> None:
        """items = (key, label, colour-or-gradient)"""
        super().__init__(parent)
        self.items = list(items)
        self.card_w, self.card_h = card_w, card_h
        self.first_text = first_text
        self.current: Optional[str] = None
        self.setMinimumHeight(card_h + 20)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items) -> None:
        self.items = list(items)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(max(200, (self.card_w + 6) * len(self.items)), self.card_h + 20)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        x = 0
        for key, text, color in self.items:
            rect = QRectF(x + 2, 2, self.card_w, self.card_h)
            selected = (self.current == key)
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            c = QColor(color)
            grad.setColorAt(0.0, c.lighter(130) if c.isValid() else QColor(theme.BG_ELEVATED))
            grad.setColorAt(1.0, c.darker(140) if c.isValid() else QColor(theme.BG_INPUT))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad) if c.isValid() else QBrush(QColor(theme.BG_INPUT)))
            p.drawRoundedRect(rect, 7, 7)
            p.setPen(QPen(QColor(theme.ACCENT) if selected
                          else QColor(0, 0, 0, 90), 2 if selected else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 7, 7)
            p.setPen(QColor("#ffffff") if c.isValid() else QColor(theme.TEXT_DIM))
            p.setFont(theme.ui_font(9, 600))
            fm = QFontMetrics(p.font())
            p.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignCenter,
                       fm.elidedText(text, Qt.ElideRight,
                                     int(rect.width() - 8)))
            x += self.card_w + 6
        p.end()

    def _hit(self, pos: QPointF) -> Optional[str]:
        x = 0
        for key, _t, _c in self.items:
            if QRectF(x + 2, 2, self.card_w, self.card_h).contains(pos):
                return key
            x += self.card_w + 6
        return None

    def mousePressEvent(self, ev) -> None:
        key = self._hit(ev.position())
        if key:
            self.current = key
            self.update()
            self.picked.emit(key)

    def wheelEvent(self, ev) -> None:
        self.scroll_offset = getattr(self, "scroll_offset", 0)
        ev.accept()


class SwatchGrid(QWidget):
    """Grid of colour swatches (degrees, LUT presets, sticker colours)."""

    picked = Signal(str)

    def __init__(self, colors: Sequence[Tuple[str, str]], parent=None,
                 columns: int = 6, cell: int = 34) -> None:
        super().__init__(parent)
        self.colors = list(colors)
        self.columns = columns
        self.cell = cell
        self.current: Optional[str] = None
        rows = max(1, (len(colors) + columns - 1) // columns)
        self.setMinimumHeight(rows * (cell + 5))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        for i, (key, color) in enumerate(self.colors):
            r, c = divmod(i, self.columns)
            rect = QRectF(c * (self.cell + 5) + 1, r * (self.cell + 5) + 1,
                          self.cell, self.cell)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(rect, 6, 6)
            p.setPen(QPen(QColor(theme.ACCENT) if self.current == key
                          else QColor(0, 0, 0, 100), 2 if self.current == key else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 6, 6)
        p.end()

    def mousePressEvent(self, ev) -> None:
        pos = ev.position()
        for i, (key, _c) in enumerate(self.colors):
            r, c = divmod(i, self.columns)
            if QRectF(c * (self.cell + 5) + 1, r * (self.cell + 5) + 1,
                      self.cell, self.cell).contains(pos):
                self.current = key
                self.update()
                self.picked.emit(key)
                return


# --------------------------------------------------------------------------- #
#  timeline-ish helpers
# --------------------------------------------------------------------------- #

class DurationInput(QWidget):
    """h:mm:ss.cs time entry used for clip start/duration."""

    changed = Signal(float)

    def __init__(self, value: float = 0.0, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit(format_time(value))
        self.edit.setFixedWidth(88)
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.editingFinished.connect(self._commit)
        lay.addWidget(self.edit)
        for text, delta in (("−1f", -1), ("+1f", 1)):
            b = QPushButton(text)
            b.setFixedWidth(38)
            b.setStyleSheet("padding: 2px; font-size: 10px;")
            b.clicked.connect(lambda _=False, d=delta: self.step(d, 30))
            lay.addWidget(b)
        self._value = value

    def value(self) -> float:
        return self._value

    def set_value(self, v: float) -> None:
        self._value = max(0.0, v)
        self.edit.setText(format_time(self._value))

    def _commit(self) -> None:
        v = parse_time(self.edit.text())
        if v is not None:
            self._value = max(0.0, v)
            self.changed.emit(self._value)
        self.set_value(self._value)

    def step(self, frames: int, fps: float) -> None:
        self._value = max(0.0, self._value + frames / max(1.0, fps))
        self.set_value(self._value)
        self.changed.emit(self._value)


def format_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    if h:
        return f"{h}:{m:02d}:{s:05.2f}"
    return f"{m:02d}:{s:05.2f}"


def parse_time(text: str) -> Optional[float]:
    parts = [p for p in text.replace(",", ".").split(":") if p.strip() != ""]
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    total = 0.0
    for v in vals:
        total = total * 60 + v
    return total if vals else None
