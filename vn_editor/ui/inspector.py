"""Contextual inspector: the properties of whatever is selected.

Every control writes through ``ctx.commit()`` so changes land in the undo
history exactly like timeline gestures do.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import effects as fx_mod
from ..core import render as render_mod
from ..core import speedcurve as core_speedcurve
from ..core.model import (ANIM_PROPS, EASINGS, RESOLUTION_PRESETS,
                          TEXT_ANIMATIONS, Clip, FilterInstance, Keyframe,
                          MaskProps, Project, SpeedCurve, Transform)
from . import theme
from .widgets import (ChipGroup, ColorButton, DurationInput, IconButton,
                      ParamEditor, PresetStrip, RowField, Section, SliderField,
                      SwatchGrid, label, hline)

SPEED_PRESETS = [
    ("0.1x", 0.1), ("0.25x", 0.25), ("0.5x", 0.5), ("0.75x", 0.75),
    ("1x", 1.0), ("1.5x", 1.5), ("2x", 2.0), ("3x", 3.0), ("4x", 4.0),
    ("8x", 8.0),
]

#: Speed-curve presets; the table itself lives in the core so the renderer and
#: this panel can never disagree about it.
CURVE_PRESETS = list(core_speedcurve.CURVE_PRESETS)

FIT_MODES = ["fit", "cover", "stretch"]
#: Blend modes for overlay layers; the table lives in the core so the renderer
#: and this dropdown always agree.
BLEND_MODES = list(render_mod.BLEND_MODES)


class BasePage(QWidget):
    """A scrollable page of sections."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._sections: List[Section] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(self._body)
        self.body.setContentsMargins(9, 5, 9, 14)
        self.body.setSpacing(2)
        self.scroll.setWidget(self._body)
        outer.addWidget(self.scroll, 1)
        self.empty = QLabel("Select a clip to edit its properties.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color:{theme.TEXT_MUTED}; padding: 26px 18px;")
        outer.addWidget(self.empty)
        self.empty.setVisible(False)

    def section(self, title: str, icon: Optional[str] = None,
                expanded: bool = True) -> Section:
        s = Section(title, self, expanded=expanded, icon=icon)
        self.body.addWidget(s)
        self._sections.append(s)
        return s

    def finish(self) -> None:
        self.body.addStretch(1)

    def refresh(self) -> None:
        """Rebuild every control from the project state."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
#  Project / canvas
# --------------------------------------------------------------------------- #

class ProjectPage(BasePage):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Canvas", "crop")
        self.res_combo = QComboBox()
        self.res_combo.addItem("Custom", 0)
        for name, w, h in RESOLUTION_PRESETS:
            self.res_combo.addItem(f"{name}  ·  {w}×{h}", (w, h))
        self.res_combo.currentIndexChanged.connect(self._res_changed)
        s.add(RowField("Preset", self.res_combo))

        row = QHBoxLayout()
        self.w_spin = QSpinBox()
        self.w_spin.setRange(64, 7680)
        self.w_spin.setSingleStep(2)
        self.w_spin.valueChanged.connect(self._size_changed)
        self.h_spin = QSpinBox()
        self.h_spin.setRange(64, 7680)
        self.h_spin.setSingleStep(2)
        self.h_spin.valueChanged.connect(self._size_changed)
        row.addWidget(QLabel("W"))
        row.addWidget(self.w_spin, 1)
        row.addWidget(QLabel("H"))
        row.addWidget(self.h_spin, 1)
        s.add_layout(row)

        self.fps_combo = QComboBox()
        for f in (24, 25, 30, 50, 60):
            self.fps_combo.addItem(f"{f} fps", float(f))
        self.fps_combo.currentIndexChanged.connect(self._fps_changed)
        s.add(RowField("Frame rate", self.fps_combo))

        self.bg_btn = ColorButton("#000000")
        self.bg_btn.colorChanged.connect(
            lambda c: self.ctx.commit("Canvas colour",
                                      lambda p: setattr(p.settings,
                                                        "background", c)))
        s.add(RowField("Background", self.bg_btn))

        quick = QHBoxLayout()
        for text, w, h in (("9:16", 1080, 1920), ("16:9", 1920, 1080),
                           ("1:1", 1080, 1080), ("4:5", 1080, 1350)):
            b = QPushButton(text)
            b.setProperty("class", "chip")
            b.setStyleSheet("padding: 4px 8px; font-size: 11px;")
            b.clicked.connect(lambda _=False, a=w, b_=h: self._set_size(a, b_))
            quick.addWidget(b)
        s.add_layout(quick)

        s2 = self.section("Project", "file")
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._name_changed)
        s2.add(RowField("Name", self.name_edit))

        s3 = self.section("Automation", "magic")
        self.autosave_cb = QCheckBox("Autosave every 2 minutes")
        self.autosave_cb.setChecked(True)
        self.autosave_cb.toggled.connect(
            lambda on: self.ctx.set_setting("autosave", on))
        s3.add(self.autosave_cb)
        self.proxy_cb = QCheckBox("Draft quality while editing (faster)")
        self.proxy_cb.setChecked(True)
        self.proxy_cb.setToolTip(
            "Preview renders at reduced resolution. Exports are always full "
            "quality.")
        self.proxy_cb.toggled.connect(self._proxy_changed)
        s3.add(self.proxy_cb)
        self.finish()

    def _proxy_changed(self, on: bool) -> None:
        self.ctx.set_preview_quality("auto" if on else "high")

    def _res_changed(self) -> None:
        data = self.res_combo.currentData()
        if isinstance(data, tuple):
            self._set_size(*data)

    def _set_size(self, w: int, h: int) -> None:
        def apply(p: Project):
            p.settings.width, p.settings.height = int(w), int(h)
        self.ctx.commit(f"Canvas {w}×{h}", apply)

    def _size_changed(self) -> None:
        w, h = self.w_spin.value(), self.h_spin.value()
        if (w, h) == (self.ctx.project.settings.width,
                      self.ctx.project.settings.height):
            return
        self._set_size(w, h)

    def _fps_changed(self) -> None:
        fps = self.fps_combo.currentData()
        self.ctx.commit(f"{fps:g} fps",
                        lambda p: setattr(p.settings, "fps", float(fps)))

    def _name_changed(self) -> None:
        name = self.name_edit.text().strip() or "Untitled project"
        if name == self.ctx.project.name:
            return
        self.ctx.commit("Rename project",
                        lambda p: setattr(p, "name", name))

    def refresh(self) -> None:
        st = self.ctx.project.settings
        for wgt in (self.w_spin, self.h_spin, self.fps_combo,
                    self.res_combo, self.name_edit, self.bg_btn):
            wgt.blockSignals(True)
        self.w_spin.setValue(st.width)
        self.h_spin.setValue(st.height)
        idx = self.fps_combo.findData(float(st.fps))
        if idx >= 0:
            self.fps_combo.setCurrentIndex(idx)
        self.res_combo.setCurrentIndex(0)
        for i in range(self.res_combo.count()):
            if self.res_combo.itemData(i) == (st.width, st.height):
                self.res_combo.setCurrentIndex(i)
                break
        self.name_edit.setText(self.ctx.project.name)
        self.bg_btn.set_color(st.background or "#000000")
        for wgt in (self.w_spin, self.h_spin, self.fps_combo,
                    self.res_combo, self.name_edit, self.bg_btn):
            wgt.blockSignals(False)
        self.empty.setVisible(False)


# --------------------------------------------------------------------------- #
#  Transform + crop + keyframes
# --------------------------------------------------------------------------- #

class TransformPage(BasePage):
    """Position, scale, rotation, crop, opacity — with keyframe control."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)

        self._anim_rows: Dict[str, Tuple[SliderField, IconButton]] = {}

        s = self.section("Layout", "fit")
        self.fit_chips = ChipGroup(["Fit", "Cover", "Stretch"], 0, small=True)
        self.fit_chips.changed.connect(
            lambda i, _t: self._set("fit", FIT_MODES[i]))
        s.add(self.fit_chips)

        self.scale = SliderField(0.05, 5.0, 1.0, decimals=2, suffix="×",
                                 default=1.0)
        self.scale.valueChanged.connect(lambda v: self._set("scale", v))
        self.scale.editingFinished.connect(self.ctx.end_gesture)
        s.add(self._anim_row("scale", "Scale", self.scale))

        self.rot = SliderField(-180.0, 180.0, 0.0, decimals=1, suffix="°",
                               factor=10, default=0.0)
        self.rot.valueChanged.connect(lambda v: self._set("rotation", v))
        self.rot.editingFinished.connect(self.ctx.end_gesture)
        s.add(self._anim_row("rotation", "Rotation", self.rot))

        pos = QHBoxLayout()
        self.pad = PositionPad(self.ctx)
        pos.addWidget(self.pad)
        col = QVBoxLayout()
        self.x_slide = SliderField(0.0, 1.0, 0.5, decimals=3, default=0.5)
        self.x_slide.valueChanged.connect(lambda v: self._set("x", v))
        self.x_slide.editingFinished.connect(self.ctx.end_gesture)
        self.y_slide = SliderField(0.0, 1.0, 0.5, decimals=3, default=0.5)
        self.y_slide.valueChanged.connect(lambda v: self._set("y", v))
        self.y_slide.editingFinished.connect(self.ctx.end_gesture)
        col.addWidget(self._anim_row("y", "Vertical", self.y_slide))
        col.addWidget(RowField("X", self.x_slide))
        col.addWidget(RowField("Y", self.y_slide))
        pos.addLayout(col, 1)
        s.add_layout(pos)

        flips = QHBoxLayout()
        self.flip_h = IconButton("flip", "Flip horizontally", 28)
        self.flip_h.clicked.connect(lambda: self._toggle_flip("flip_h"))
        self.flip_v = IconButton("mirror", "Flip vertically", 28)
        self.flip_v.clicked.connect(lambda: self._toggle_flip("flip_v"))
        self.reset_btn = QPushButton("Reset transform")
        self.reset_btn.setProperty("class", "ghost")
        self.reset_btn.clicked.connect(self._reset)
        flips.addWidget(self.flip_h)
        flips.addWidget(self.flip_v)
        flips.addStretch(1)
        flips.addWidget(self.reset_btn)
        s.add_layout(flips)

        s2 = self.section("Crop", "crop")
        self.crop = {}
        for key, text in (("crop_l", "Left"), ("crop_r", "Right"),
                          ("crop_t", "Top"), ("crop_b", "Bottom")):
            f = SliderField(0.0, 0.95, 0.0, decimals=3, default=0.0)
            f.valueChanged.connect(lambda v, k=key: self._set(k, v))
            f.editingFinished.connect(self.ctx.end_gesture)
            self.crop[key] = f
            s2.add(RowField(text, f))
        crow = QHBoxLayout()
        for text, l, r, t, b in (("Reset", 0, 0, 0, 0), ("1:1", .25, .25, 0, 0),
                                 ("9:16", .32, .32, 0, 0),
                                 ("16:9", 0, 0, .28, .28)):
            btn = QPushButton(text)
            btn.setProperty("class", "chip")
            btn.setStyleSheet("padding: 3px 8px; font-size: 11px;")
            btn.clicked.connect(
                lambda _=False, a=l, b_=r, c=t, d=b: self._set_crop(a, b_, c, d))
            crow.addWidget(btn)
        s2.add_layout(crow)

        s3 = self.section("Opacity & blend", "opacity")
        self.opacity = SliderField(0.0, 1.0, 1.0, decimals=2, default=1.0)
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", v))
        self.opacity.editingFinished.connect(self.ctx.end_gesture)
        s3.add(self._anim_row("opacity", "Opacity", self.opacity))
        self.blend = QComboBox()
        self.blend.addItems(BLEND_MODES)
        self.blend.currentIndexChanged.connect(
            lambda: self._set_meta("blend", BLEND_MODES[self.blend.currentIndex()]))
        s3.add(RowField("Blend", self.blend))

        self.enabled_cb = QCheckBox("Clip is visible")
        self.enabled_cb.toggled.connect(self._set_enabled)
        s3.add(self.enabled_cb)

        self.finish()

    # -- helpers ------------------------------------------------------------- #
    def _anim_row(self, prop: str, text: str, field: SliderField) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        lb = label(text, size=11)
        lb.setStyleSheet(f"color:{theme.TEXT_DIM};")
        lb.setMinimumWidth(58)
        lay.addWidget(lb)
        lay.addWidget(field, 1)
        btn = IconButton("keyframe_add", f"Keyframe {prop}", 24, 15)
        btn.clicked.connect(lambda: self._add_keyframe(prop))
        lay.addWidget(btn)
        self._anim_rows[prop] = (field, btn)
        return w

    def _set(self, prop: str, value: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        t_local = max(0.0, self.ctx.playhead() - clip.start)
        self.ctx.commit(f"Set {prop}", lambda p, c=clip, pr=prop, v=value: (
            setattr(c.transform, pr, float(v))), coalesce=f"transform:{clip.id}:{prop}")
        if clip.has_animation(prop) and self.ctx.auto_keyframe():
            self.ctx.commit(f"Keyframe {prop}",
                            lambda p, c=clip, pr=prop, v=value, t=t_local:
                            c.set_keyframe(pr, t, float(v)))
        self.pad.update()
        self.ctx.request_preview_refresh()

    def _add_keyframe(self, prop: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        t_local = max(0.0, min(clip.duration, self.ctx.playhead() - clip.start))
        value = getattr(clip.transform, prop)
        self.ctx.commit(f"Keyframe {prop}",
                        lambda p: clip.set_keyframe(prop, t_local, float(value)))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _toggle_flip(self, prop: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        v = not getattr(clip.transform, prop)
        self.ctx.commit("Flip", lambda p: setattr(clip.transform, prop, v))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _reset(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Reset transform", lambda p: setattr(
            clip, "transform", Transform(fit=clip.transform.fit)))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _set_crop(self, l: float, r: float, t: float, b: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return

        def apply(p):
            clip.transform.crop_l, clip.transform.crop_r = l, r
            clip.transform.crop_t, clip.transform.crop_b = t, b
        self.ctx.commit("Crop", apply)
        self.refresh()
        self.ctx.request_preview_refresh()

    def _set_meta(self, key: str, value) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Blend mode",
                        lambda p: clip.meta.__setitem__(key, value))
        self.ctx.request_preview_refresh()

    def _set_enabled(self, on: bool) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Toggle clip", lambda p: setattr(clip, "enabled", on))
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        t = clip.transform
        for w in (self.scale, self.rot, self.x_slide, self.y_slide,
                  self.opacity, *self.crop.values()):
            w.blockSignals(True)
        self.scale.set_value(t.scale)
        self.rot.set_value(t.rotation)
        self.x_slide.set_value(t.x)
        self.y_slide.set_value(t.y)
        self.opacity.set_value(t.opacity)
        self.crop["crop_l"].set_value(t.crop_l)
        self.crop["crop_r"].set_value(t.crop_r)
        self.crop["crop_t"].set_value(t.crop_t)
        self.crop["crop_b"].set_value(t.crop_b)
        for w in (self.scale, self.rot, self.x_slide, self.y_slide,
                  self.opacity, *self.crop.values()):
            w.blockSignals(False)
        self.fit_chips.set_current(FIT_MODES.index(t.fit)
                                   if t.fit in FIT_MODES else 0)
        idx = BLEND_MODES.index(clip.meta.get("blend", "normal")) \
            if clip.meta.get("blend", "normal") in BLEND_MODES else 0
        self.blend.blockSignals(True)
        self.blend.setCurrentIndex(idx)
        self.blend.blockSignals(False)
        self.flip_h.setIcon(theme.icon("flip", theme.ACCENT if t.flip_h
                                       else theme.TEXT_DIM, 17))
        self.flip_v.setIcon(theme.icon("mirror", theme.ACCENT if t.flip_v
                                       else theme.TEXT_DIM, 17))
        self.enabled_cb.blockSignals(True)
        self.enabled_cb.setChecked(clip.enabled)
        self.enabled_cb.blockSignals(False)
        for prop, (field, btn) in self._anim_rows.items():
            has = clip.has_animation(prop)
            btn.set_icon_name("keyframe" if has else "keyframe_add",
                              theme.ACCENT if has else theme.TEXT_DIM, 15)
            at = None
            if has:
                lt = self.ctx.playhead() - clip.start
                for k in clip.animations.get(prop, []):
                    if abs(k.t - lt) < 1e-3:
                        at = k
                        break
            btn.setToolTip(f"{'Remove' if at else 'Add'} {prop} keyframe")
        self.pad.set_clip(clip)


class PositionPad(QWidget):
    """Draggable position pad mirroring the preview canvas."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.clip: Optional[Clip] = None
        self.setFixedSize(104, 150)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._dragging = False

    def set_clip(self, clip: Optional[Clip]) -> None:
        self.clip = clip
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_INPUT))
        p.drawRoundedRect(r, 7, 7)
        aspect = (self.ctx.project.settings.width
                  / max(1, self.ctx.project.settings.height))
        h = min(r.height() - 12, (r.width() - 12) / max(0.05, aspect))
        w = h * aspect
        frame = QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)
        p.setBrush(QColor("#000000"))
        p.drawRoundedRect(frame, 4, 4)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(frame, 4, 4)
        if self.clip is not None:
            t = self.clip.transform
            cx = frame.x() + t.x * frame.width()
            cy = frame.y() + t.y * frame.height()
            bx = frame.center().x() + (t.x - 0.5) * frame.width() * 1.1
            by = frame.center().y() + (t.y - 0.5) * frame.height() * 1.1
            bw = max(6.0, frame.width() * min(1.0, 1.0 / max(1.0, t.scale)))
            bh = max(6.0, frame.height() * min(1.0, 1.0 / max(1.0, t.scale)))
            box = QRectF(bx - bw / 2, by - bh / 2, bw, bh)
            p.setBrush(QColor(theme.ACCENT).lighter(120))
            p.setOpacity(0.85)
            p.drawRoundedRect(box, 3, 3)
            p.setOpacity(1.0)
            p.setPen(QPen(QColor("#ffffff"), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DashLine))
            p.drawLine(QPointF(frame.center().x(), frame.y()),
                       QPointF(frame.center().x(), frame.bottom()))
            p.drawLine(QPointF(frame.x(), frame.center().y()),
                       QPointF(frame.right(), frame.center().y()))
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(8))
        p.drawText(QRectF(r.x(), r.bottom() - 12, r.width(), 11),
                   Qt.AlignCenter, "drag to move")
        p.end()

    def _apply(self, pos: QPointF) -> None:
        if self.clip is None:
            return
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        aspect = (self.ctx.project.settings.width
                  / max(1, self.ctx.project.settings.height))
        h = min(r.height() - 12, (r.width() - 12) / max(0.05, aspect))
        w = h * aspect
        frame = QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)
        fx = (pos.x() - frame.center().x()) / max(1.0, frame.width() * 1.1) + 0.5
        fy = (pos.y() - frame.center().y()) / max(1.0, frame.height() * 1.1) + 0.5
        clip = self.clip
        x, y = max(-0.5, min(1.5, fx)), max(-0.5, min(1.5, fy))
        self.ctx.commit("Move clip", lambda p: (
            setattr(clip.transform, "x", x), setattr(clip.transform, "y", y)),
            coalesce=f"move:{clip.id}")
        self.update()
        self.ctx.request_preview_refresh()

    def mousePressEvent(self, ev) -> None:
        self._dragging = True
        self._apply(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        if self._dragging:
            self._apply(ev.position())

    def mouseReleaseEvent(self, ev) -> None:
        if self._dragging:
            self._dragging = False
            self.ctx.end_gesture()
            self.ctx.refresh_inspector()


# --------------------------------------------------------------------------- #
#  Speed
# --------------------------------------------------------------------------- #

class SpeedPage(BasePage):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Speed", "speed")
        self.chips = ChipGroup([n for n, _v in SPEED_PRESETS], -1, small=True)
        self.chips.changed.connect(self._preset)
        s.add(self.chips)
        self.slider = SliderField(0.05, 8.0, 1.0, decimals=2, suffix="×",
                                  default=1.0, log=True)
        self.slider.valueChanged.connect(self._slider)
        self.slider.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Custom", self.slider))

        s2 = self.section("Speed curve", "magic")
        self.curve_combo = QComboBox()
        for name, _pts in CURVE_PRESETS:
            self.curve_combo.addItem(name, name)
        self.curve_combo.currentIndexChanged.connect(self._curve_preset)
        s2.add(RowField("Preset", self.curve_combo))
        self.curve_editor = CurveEditor(ctx)
        s2.add(self.curve_editor)
        crow = QHBoxLayout()
        self.add_point = QPushButton("+ Point")
        self.add_point.setProperty("class", "chip")
        self.add_point.clicked.connect(self.curve_editor.add_point)
        self.clear_curve = QPushButton("Clear curve")
        self.clear_curve.setProperty("class", "chip")
        self.clear_curve.clicked.connect(lambda: self._curve_preset(0, force=True))
        crow.addWidget(self.add_point)
        crow.addWidget(self.clear_curve)
        s2.add_layout(crow)

        s3 = self.section("Time", "reverse")
        row = QHBoxLayout()
        self.reverse_cb = QCheckBox("Reverse")
        self.reverse_cb.toggled.connect(self._toggle_reverse)
        self.freeze_cb = QCheckBox("Freeze frame")
        self.freeze_cb.toggled.connect(self._toggle_freeze)
        row.addWidget(self.reverse_cb)
        row.addWidget(self.freeze_cb)
        s3.add_layout(row)
        self.freeze_at = SliderField(0.0, 1.0, 0.0, decimals=2, default=0.0)
        self.freeze_at.valueChanged.connect(self._freeze_at)
        self.freeze_at.editingFinished.connect(self.ctx.end_gesture)
        s3.add(RowField("Freeze at", self.freeze_at))
        self.interp_cb = QCheckBox("Smooth slow motion (frame interpolation)")
        self.interp_cb.setToolTip("Adds rendered in-between frames; slower to "
                                  "export but much smoother below 0.5x.")
        self.interp_cb.toggled.connect(
            lambda on: self._set_meta("interpolate", bool(on)))
        s3.add(self.interp_cb)

        s4 = self.section("Audio while speeding", "music")
        self.pitch_chips = ChipGroup(["Pitch corrected", "Shift pitch",
                                      "Mute audio"], 0, small=True)
        self.pitch_chips.changed.connect(self._pitch_mode)
        s4.add(self.pitch_chips)
        self.pitch = SliderField(0.5, 2.0, 1.0, decimals=2, suffix="×",
                                 default=1.0)
        self.pitch.valueChanged.connect(
            lambda v: self._set_audio("pitch", v))
        self.pitch.editingFinished.connect(self.ctx.end_gesture)
        s4.add(RowField("Pitch", self.pitch))
        self.finish()

    def _preset(self, idx: int, text: str) -> None:
        if idx < 0 or idx >= len(SPEED_PRESETS):
            return
        speed = SPEED_PRESETS[idx][1]
        self._apply_speed(speed)

    def _slider(self, v: float) -> None:
        self._apply_speed(v)

    def _apply_speed(self, speed: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        src_span = clip.duration * clip.speed
        new_dur = src_span / max(0.05, speed)

        def apply(p):
            clip.speed = max(0.05, speed)
            clip.duration = max(0.03, new_dur)
        self.ctx.commit(f"Speed {speed:g}×", apply,
                        coalesce=f"speed:{clip.id}")
        # keyframes are stored in clip-local time: rescale them
        if clip.animations or clip.audio_animations:
            for table in (clip.animations, clip.audio_animations):
                for prop, keys in table.items():
                    for k in keys:
                        k.t = min(clip.duration, k.t * (new_dur
                                                        / max(1e-6, src_span / clip.speed * clip.speed)))
        self.refresh()
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()

    def _curve_preset(self, idx: int, force: bool = False) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        name, pts = CURVE_PRESETS[max(0, min(len(CURVE_PRESETS) - 1, idx))]
        if not pts:
            self.ctx.commit("Clear speed curve",
                            lambda p: setattr(clip, "speed_curve", None))
        else:
            self.ctx.commit(f"Curve {name}", lambda p: setattr(
                clip, "speed_curve", SpeedCurve(list(pts), name)))
        self.refresh()
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()

    def _toggle_reverse(self, on: bool) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Reverse clip", lambda p: setattr(clip, "reverse", bool(on)))
        self.ctx.request_preview_refresh()

    def _toggle_freeze(self, on: bool) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Freeze frame", lambda p: setattr(clip, "freeze", bool(on)))
        self.ctx.request_preview_refresh()

    def _freeze_at(self, v: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        clip.freeze_at = v
        self.ctx.commit("Freeze position",
                        lambda p: setattr(clip, "freeze_at", float(v)),
                        coalesce=f"freeze:{clip.id}")
        self.ctx.request_preview_refresh()

    def _pitch_mode(self, idx: int, _t: str) -> None:
        mode = ["pitch_correct", "pitch_shift", "mute"][max(0, min(2, idx))]
        self._set_audio("speed_mode", mode)

    def _set_audio(self, key: str, value) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit(f"Audio {key}",
                        lambda p: setattr(clip.audio, key, value),
                        coalesce=f"audio:{clip.id}:{key}")
        self.ctx.request_preview_refresh()

    def _set_meta(self, key: str, value) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Clip option",
                        lambda p: clip.meta.__setitem__(key, value))
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        self.slider.blockSignals(True)
        self.slider.set_value(clip.speed)
        self.slider.blockSignals(False)
        self.chips.set_current(-1)
        for i, (_n, v) in enumerate(SPEED_PRESETS):
            if abs(v - clip.speed) < 1e-6:
                self.chips.set_current(i)
                break
        name = clip.speed_curve.preset if clip.speed_curve else "None"
        idx = self.curve_combo.findData(name)
        self.curve_combo.blockSignals(True)
        self.curve_combo.setCurrentIndex(max(0, idx))
        self.curve_combo.blockSignals(False)
        self.curve_editor.set_clip(clip)
        for w in (self.reverse_cb, self.freeze_cb, self.interp_cb):
            w.blockSignals(True)
        self.reverse_cb.setChecked(clip.reverse)
        self.freeze_cb.setChecked(clip.freeze)
        self.interp_cb.setChecked(bool(clip.meta.get("interpolate")))
        for w in (self.reverse_cb, self.freeze_cb, self.interp_cb):
            w.blockSignals(False)
        self.freeze_at.blockSignals(True)
        self.freeze_at.set_value(clip.freeze_at)
        self.freeze_at.blockSignals(False)
        mode = clip.audio.speed_mode
        self.pitch_chips.set_current({"pitch_correct": 0, "pitch_shift": 1,
                                      "mute": 2}.get(mode, 0))
        self.pitch.blockSignals(True)
        self.pitch.set_value(clip.audio.pitch)
        self.pitch.blockSignals(False)


class CurveEditor(QWidget):
    """Editable speed-over-time curve (drag points, double-click to add)."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.clip: Optional[Clip] = None
        self.dragging: Optional[int] = None
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    def set_clip(self, clip: Optional[Clip]) -> None:
        self.clip = clip
        self.update()

    def _points(self) -> List[Tuple[float, float]]:
        if self.clip is None or not self.clip.speed_curve:
            return [(0.0, 1.0), (1.0, 1.0)]
        pts = sorted(self.clip.speed_curve.points)
        return pts or [(0.0, 1.0), (1.0, 1.0)]

    def _plot(self) -> QRectF:
        return QRectF(self.rect()).adjusted(8, 8, -8, -14)

    def _to_px(self, p: float, s: float) -> QPointF:
        r = self._plot()
        x = r.x() + p * r.width()
        y = r.bottom() - (min(4.0, max(0.0, s)) / 4.0) * r.height()
        return QPointF(x, y)

    def _from_px(self, pos: QPointF) -> Tuple[float, float]:
        r = self._plot()
        p = (pos.x() - r.x()) / max(1.0, r.width())
        s = (r.bottom() - pos.y()) / max(1.0, r.height()) * 4.0
        return max(0.0, min(1.0, p)), max(0.1, min(4.0, s))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG_INPUT))
        p.drawRoundedRect(r, 7, 7)
        plot = self._plot()
        p.setPen(QPen(QColor(theme.GRID), 1))
        for frac in (0.25, 0.5, 0.75):
            y = plot.y() + plot.height() * frac
            p.drawLine(QPointF(plot.x(), y), QPointF(plot.right(), y))
            p.setPen(QColor(theme.TEXT_MUTED))
            p.setFont(theme.ui_font(8))
            p.drawText(QRectF(plot.x() + 2, y - 10, 40, 10),
                       Qt.AlignLeft, f"{4.0 * (1 - frac):.1f}x")
            p.setPen(QPen(QColor(theme.GRID), 1))
        pts = self._points()
        px = [self._to_px(a, b) for a, b in pts]
        if self.clip and self.clip.speed_curve:
            poly = [QPointF(plot.x(), plot.bottom())]
            poly += px
            poly.append(QPointF(plot.right(), plot.bottom()))
            p.setBrush(QColor(theme.ACCENT).lighter(105))
            p.setOpacity(0.22)
            p.drawPolygon(poly)
            p.setOpacity(1.0)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        for a, b in zip(px, px[1:]):
            p.drawLine(a, b)
        for pt in px:
            p.setBrush(QColor("#ffffff"))
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawEllipse(pt, 4, 4)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(theme.ui_font(8))
        p.drawText(QRectF(plot.x(), plot.bottom() + 1, 30, 12), Qt.AlignLeft,
                   "start")
        p.drawText(QRectF(plot.right() - 30, plot.bottom() + 1, 30, 12),
                   Qt.AlignRight, "end")
        p.end()

    def _hit(self, pos: QPointF) -> Optional[int]:
        for i, (a, b) in enumerate(self._points()):
            if (self._to_px(a, b) - pos).manhattanLength() < 12:
                return i
        return None

    def mousePressEvent(self, ev) -> None:
        if self.clip is None:
            return
        self.dragging = self._hit(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        if self.dragging is None or self.clip is None:
            return
        p, s = self._from_px(ev.position())
        curve = self.clip.speed_curve or SpeedCurve([], "Custom")
        pts = sorted(curve.points) or [(0.0, 1.0), (1.0, 1.0)]
        pts[self.dragging] = (p, s)
        curve.points = pts
        curve.preset = "Custom"
        self.clip.speed_curve = curve
        self.ctx.commit("Speed curve", lambda pr: None,
                        coalesce=f"curve:{self.clip.id}")
        self.update()
        self.ctx.request_preview_refresh()

    def mouseReleaseEvent(self, ev) -> None:
        if self.dragging is not None:
            self.dragging = None
            self.ctx.end_gesture()

    def mouseDoubleClickEvent(self, ev) -> None:
        if self.clip is None:
            return
        p, s = self._from_px(ev.position())
        curve = self.clip.speed_curve or SpeedCurve([], "Custom")
        pts = sorted(curve.points) or [(0.0, 1.0), (1.0, 1.0)]
        pts.append((p, s))
        curve.points = sorted(pts)
        curve.preset = "Custom"
        self.clip.speed_curve = curve
        self.ctx.commit("Add curve point", lambda pr: None)
        self.update()
        self.ctx.request_preview_refresh()

    def add_point(self) -> None:
        if self.clip is None:
            return
        curve = self.clip.speed_curve or SpeedCurve([(0.0, 1.0), (1.0, 1.0)],
                                                    "Custom")
        pts = sorted(curve.points) or [(0.0, 1.0), (1.0, 1.0)]
        mid = len(pts) // 2
        p = (pts[mid - 1][0] + pts[mid][0]) / 2 if mid else 0.5
        s = (pts[mid - 1][1] + pts[mid][1]) / 2 if mid else 1.5
        pts.append((p, s))
        curve.points = sorted(pts)
        curve.preset = "Custom"
        self.clip.speed_curve = curve
        self.ctx.commit("Add curve point", lambda pr: None)
        self.update()
        self.ctx.request_preview_refresh()


# --------------------------------------------------------------------------- #
#  Filters / effects / colour
# --------------------------------------------------------------------------- #

class FilterPage(BasePage):
    """25 colour grades with intensity, plus manual colour tools."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Grades", "filter")
        self.strip = PresetStrip([
            (name, name, _grade_color(name)) for name in fx_mod.GRADES
        ], card_w=62, card_h=44, first_text="Original")
        self.strip.picked.connect(self._pick)
        s.add(self.strip)
        self.intensity = SliderField(0.0, 1.0, 1.0, decimals=2, default=1.0)
        self.intensity.valueChanged.connect(self._intensity)
        self.intensity.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Strength", self.intensity))

        s2 = self.section("Adjust", "magic")
        self.manual: Dict[str, SliderField] = {}
        manual_defs = [
            ("brightness", -1.0, 1.0, 0.0), ("contrast", 0.0, 3.0, 1.0),
            ("saturation", 0.0, 3.0, 1.0), ("temperature", -1.0, 1.0, 0.0),
            ("tint", -1.0, 1.0, 0.0), ("vibrance", -2.0, 2.0, 0.0),
            ("fade", 0.0, 1.0, 0.0), ("sharpen", 0.0, 3.0, 0.0),
            ("blur", 0.0, 30.0, 0.0),
        ]
        for key, lo, hi, dv in manual_defs:
            f = SliderField(lo, hi, dv, decimals=2, default=dv)
            f.valueChanged.connect(lambda v, k=key: self._manual(k, v))
            f.editingFinished.connect(self.ctx.end_gesture)
            self.manual[key] = f
            s2.add(RowField(key.title(), f))
        crow = QHBoxLayout()
        clear = QPushButton("Reset adjustments")
        clear.setProperty("class", "chip")
        clear.clicked.connect(self._clear_manual)
        crow.addWidget(clear)
        s2.add_layout(crow)

        s3 = self.section("Colour balance", "magic")
        self.balance_grid = QGridLayout()
        self.balance: Dict[str, SliderField] = {}
        for row, band in enumerate(("shadows", "midtones", "highlights")):
            for col, ch in enumerate(("r", "g", "b")):
                f = SliderField(-1.0, 1.0, 0.0, decimals=2, default=0.0)
                key = f"{band}_{ch}"
                f.setToolTip(f"{band.title()} · {ch.upper()}")
                f.valueChanged.connect(lambda v, k=key: self._balance(k, v))
                f.editingFinished.connect(self.ctx.end_gesture)
                self.balance[key] = f
                self.balance_grid.addWidget(f, row, col)
        s3.add_layout(self.balance_grid)
        self.finish()

    def _grade_fx(self):
        clip = self.ctx.selected_clip()
        if clip is None:
            return None
        return clip.find_filter("grade")

    def _pick(self, name: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        existing = clip.find_filter("grade")
        params = {"intensity": self.intensity.value(),
                  "name": name, "grade": float(list(fx_mod.GRADES).index(name))}

        def apply(p):
            if existing:
                existing.params.update(params)
            else:
                clip.add_filter("grade", dict(params))
        self.ctx.commit(f"Grade {name}", apply)
        self.ctx.refresh_inspector()
        self.ctx.request_preview_refresh()

    def _intensity(self, v: float) -> None:
        clip = self.ctx.selected_clip()
        fx = self._grade_fx() if clip else None
        if not fx:
            return
        self.ctx.commit("Grade strength",
                        lambda p: fx.params.__setitem__("intensity", float(v)),
                        coalesce=f"grade:{clip.id}")

    def _manual(self, key: str, value: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        existing = clip.find_filter("basic") or clip.find_filter("vibrance") \
            or clip.find_filter("temperature")

        def apply(p):
            f = clip.find_filter("basic")
            if key in ("brightness", "contrast", "saturation", "fade"):
                if f is None:
                    f = clip.add_filter("basic", {})
                f.params[key] = float(value)
            elif key == "vibrance":
                if f is None:
                    f = clip.add_filter("vibrance", {})
                f.params["amount"] = float(value)
                f.kind = "vibrance"
            elif key in ("temperature", "tint"):
                t = clip.find_filter("temperature")
                if t is None:
                    t = clip.add_filter("temperature", {})
                t.params[key if key == "temperature" else "tint"] = float(value)
            elif key == "sharpen":
                sh = clip.find_filter("sharpen")
                if sh is None:
                    sh = clip.add_filter("sharpen", {})
                sh.params["amount"] = float(value)
            elif key == "blur":
                bl = clip.find_filter("blur")
                if bl is None:
                    bl = clip.add_filter("blur", {})
                bl.params["sigma"] = float(value)
        self.ctx.commit(f"Adjust {key}", apply,
                        coalesce=f"adjust:{clip.id}:{key}")
        self.ctx.request_preview_refresh()

    def _balance(self, key: str, value: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        band, ch = key.split("_")

        def apply(p):
            f = clip.find_filter("colorbalance")
            if f is None:
                f = clip.add_filter("colorbalance", {})
            f.params[f"{band}_{ch}"] = float(value)
        self.ctx.commit(f"Balance {key}", apply,
                        coalesce=f"bal:{clip.id}:{key}")
        self.ctx.request_preview_refresh()

    def _clear_manual(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Clear adjustments",
                        lambda p: clip.filters.__setitem__(
                            slice(None),
                            [f for f in clip.filters if f.kind in
                             ("grade", "lut")]))
        self.ctx.refresh_inspector()
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        grade = clip.find_filter("grade")
        self.strip.current = grade.params.get("name") if grade else "Original"
        self.strip.update()
        if grade:
            self.intensity.blockSignals(True)
            self.intensity.set_value(grade.params.get("intensity", 1.0))
            self.intensity.blockSignals(False)
        vals: Dict[str, float] = {}
        basic = clip.find_filter("basic")
        if basic:
            vals.update({k: v for k, v in basic.params.items()
                         if k in self.manual})
        vib = clip.find_filter("vibrance")
        if vib:
            vals["vibrance"] = vib.params.get("amount", 0.0)
        temp = clip.find_filter("temperature")
        if temp:
            vals["temperature"] = temp.params.get("temperature", 0.0)
            vals["tint"] = temp.params.get("tint", 0.0)
        sharp = clip.find_filter("sharpen")
        if sharp:
            vals["sharpen"] = sharp.params.get("amount", 0.0)
        blur = clip.find_filter("blur")
        if blur:
            vals["blur"] = blur.params.get("sigma", 0.0)
        for key, f in self.manual.items():
            f.blockSignals(True)
            f.set_value(vals.get(key, f._default or 0.0))
            f.blockSignals(False)
        bal = clip.find_filter("colorbalance")
        for key, f in self.balance.items():
            f.blockSignals(True)
            f.set_value((bal.params.get(key, 0.0) if bal else 0.0))
            f.blockSignals(False)


def _grade_color(name: str) -> str:
    table = {
        "Original": "#3b4252", "Vivid": "#ff4d6d", "Bright": "#ffd166",
        "Warm": "#ff8c42", "Cool": "#4cc9f0", "Golden Hour": "#ffb703",
        "Teal & Orange": "#2a9d8f", "Cinematic": "#264653", "Matte": "#8d99ae",
        "Fade": "#a5a58d", "Vintage": "#cb997e", "Retro Film": "#b08968",
        "Noir": "#495057", "Mono Contrast": "#6c757d", "Nordic": "#adb5bd",
        "Pastel": "#e8a0bf", "Peach": "#ffb4a2", "Cyberpunk": "#7209b7",
        "Anime": "#f72585", "Sky Blue": "#90e0ef", "Dramatic": "#212529",
        "Fresh": "#80ed99", "Sunset": "#ff7b00", "Silver": "#ced4da",
        "Neon Pop": "#ff006e",
    }
    return table.get(name, "#4b5563")


class EffectsPage(BasePage):
    """Add/remove/configure effects from the catalogue."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Add effect", "magic")
        self.cat_combo = QComboBox()
        cats = fx_mod.categories()
        self.cat_combo.addItem("All", "All")
        for name in cats:
            self.cat_combo.addItem(name, name)
        self.cat_combo.currentIndexChanged.connect(self._rebuild_add_list)
        s.add(RowField("Category", self.cat_combo))
        self.add_strip = PresetStrip([], card_w=70, card_h=40)
        self.add_strip.picked.connect(self._add_effect)
        s.add(self.add_strip)

        self.stack_section = self.section("Applied", "layers")
        self.param_box = QWidget()
        self.param_layout = QVBoxLayout(self.param_box)
        self.param_layout.setContentsMargins(0, 0, 0, 0)
        self.param_layout.setSpacing(6)
        self.stack_section.add(self.param_box)
        self._editor: Optional[ParamEditor] = None
        self._editing_fx: Optional[str] = None
        self._rebuild_add_list()
        self.finish()

    def _rebuild_add_list(self) -> None:
        cat = self.cat_combo.currentData()
        cats = fx_mod.categories()
        keys: List[str] = []
        if cat == "All":
            for v in cats.values():
                keys += v
        else:
            keys = cats.get(cat, [])
        self.add_strip.set_items([
            (k, fx_mod.effect_label(k), "#3b4252") for k in keys])
        self.add_strip.update()

    def _add_effect(self, key: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        params = fx_mod.default_params(key)
        self.ctx.commit(f"Add {fx_mod.effect_label(key)}",
                        lambda p: clip.add_filter(key, params))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _remove(self, fx_id: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Remove effect",
                        lambda p: clip.remove_filter(fx_id))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _select(self, fx_id: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        fx = next((f for f in clip.filters if f.id == fx_id), None)
        if fx is None:
            return
        self._editing_fx = fx_id
        while self.param_layout.count():
            item = self.param_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        spec = fx_mod.EFFECTS.get(fx.kind)
        if spec is None:
            return
        self._editor = ParamEditor(spec, fx.params)
        self._editor.changed.connect(lambda k, v: self._param_changed(fx.kind, k, v))
        self._editor.commit.connect(self.ctx.end_gesture)
        self.param_layout.addWidget(self._editor)
        self.ctx.request_preview_refresh()

    def _param_changed(self, kind: str, key: str, value: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None or self._editor is None:
            return
        fx = next((f for f in clip.filters if f.id == self._editing_fx), None)
        if fx is None:
            return
        if key.endswith("_hex"):
            fx.params[key] = value
            fx.params[key[:-4]] = _hex_to_degrees(value)
        else:
            fx.params[key] = float(value)
        self.ctx.commit(f"{fx_mod.effect_label(kind)} · {key}",
                        lambda p: None, coalesce=f"fx:{fx.id}:{key}")
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        while self.param_layout.count():
            item = self.param_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._editor = None
        if not clip.filters:
            self.param_layout.addWidget(label("No effects on this clip yet.",
                                              "muted", 11))
            return
        for fx in clip.filters:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(5)
            name = QPushButton(fx_mod.effect_label(fx.kind))
            name.setCheckable(True)
            name.setChecked(fx.id == self._editing_fx)
            name.setStyleSheet("text-align: left; padding: 5px 8px;")
            name.clicked.connect(lambda _=False, i=fx.id: self._select(i))
            lay.addWidget(name, 1)
            eye = IconButton("eye", "Enable / bypass", 24, 15,
                             checkable=True)
            eye.setChecked(fx.enabled)
            eye.toggled.connect(
                lambda on, i=fx.id, f=fx: self._toggle(f, on))
            lay.addWidget(eye)
            up = IconButton("chevron_up", "Move up", 24, 14)
            up.clicked.connect(lambda _=False, i=fx.id: self._move(i, -1))
            lay.addWidget(up)
            down = IconButton("chevron_down", "Move down", 24, 14)
            down.clicked.connect(lambda _=False, i=fx.id: self._move(i, 1))
            lay.addWidget(down)
            trash = IconButton("delete", "Remove", 24, 15)
            trash.clicked.connect(lambda _=False, i=fx.id: self._remove(i))
            lay.addWidget(trash)
            self.param_layout.addWidget(row)
        if self._editing_fx is None and clip.filters:
            self._editing_fx = clip.filters[0].id
        for fx in clip.filters:
            if fx.id == self._editing_fx:
                spec = fx_mod.EFFECTS.get(fx.kind)
                if spec is not None:
                    self._editor = ParamEditor(spec, fx.params)
                    self._editor.changed.connect(
                        lambda k, v, kind=fx.kind: self._param_changed(kind, k, v))
                    self._editor.commit.connect(self.ctx.end_gesture)
                    self.param_layout.addWidget(self._editor)

    def _toggle(self, fx, on: bool) -> None:
        self.ctx.commit("Toggle effect", lambda p: setattr(fx, "enabled", on))
        self.ctx.request_preview_refresh()

    def _move(self, fx_id: str, delta: int) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        idx = next((i for i, f in enumerate(clip.filters) if f.id == fx_id), -1)
        if idx < 0:
            return
        new = max(0, min(len(clip.filters) - 1, idx + delta))
        if new == idx:
            return

        def apply(p):
            f = clip.filters.pop(idx)
            clip.filters.insert(new, f)
        self.ctx.commit("Reorder effect", apply)
        self.refresh()
        self.ctx.request_preview_refresh()


def _hex_to_degrees(hex_color: str) -> float:
    c = QColor(hex_color)
    if not c.isValid():
        return 0.0
    import colorsys
    h, _l, _s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
    return h * 360.0


# --------------------------------------------------------------------------- #
#  Text
# --------------------------------------------------------------------------- #

class TextPage(BasePage):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Content", "text")
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Type your caption…")
        self.text_edit.setFixedHeight(66)
        self.text_edit.textChanged.connect(self._text_changed)
        s.add(self.text_edit)

        preset_row = QHBoxLayout()
        for style, label_txt in (("POV: YOU ARE HIM", "POV"),
                                 ("INSANE CLUTCH", "Meme"),
                                 ("GG EASY", "Simple"),
                                 ("SUBSCRIBE", "CTA")):
            b = QPushButton(label_txt)
            b.setProperty("class", "chip")
            b.setStyleSheet("padding: 3px 8px; font-size: 11px;")
            b.clicked.connect(lambda _=False, t=style: self._set_text(t))
            preset_row.addWidget(b)
        s.add_layout(preset_row)

        s2 = self.section("Type", "text")
        from ..core.ass import FONT_FILES
        self.font_combo = QComboBox()
        for fname in sorted(FONT_FILES):
            self.font_combo.addItem(fname, fname)
        self.font_combo.currentIndexChanged.connect(self._font)
        s2.add(RowField("Font", self.font_combo))
        self.size = SliderField(8.0, 400.0, 72.0, decimals=0, suffix="px",
                                factor=1.0, default=72.0)
        self.size.valueChanged.connect(lambda v: self._set_text_prop("size", v))
        self.size.editingFinished.connect(self.ctx.end_gesture)
        s2.add(RowField("Size", self.size))
        self.autofit_cb = QCheckBox("Auto-shrink to fit frame width")
        self.autofit_cb.setChecked(True)
        self.autofit_cb.setToolTip(
            "Long captions are scaled down so they never run off screen, "
            "exactly like VN.")
        self.autofit_cb.toggled.connect(self._toggle_autofit)
        s2.add(self.autofit_cb)
        self.maxw = SliderField(0.2, 1.0, 1.0, decimals=2, default=1.0)
        self.maxw.valueChanged.connect(lambda v: self._set_text_prop("max_width", v))
        self.maxw.editingFinished.connect(self.ctx.end_gesture)
        s2.add(RowField("Wrap width", self.maxw))
        style_row = QHBoxLayout()
        self.bold = IconButton("text", "Bold", 28, 16, checkable=True)
        self.bold.toggled.connect(lambda on: self._set_text_prop("bold", on))
        self.italic = IconButton("list", "Italic", 28, 16, checkable=True)
        self.italic.toggled.connect(lambda on: self._set_text_prop("italic", on))
        self.underline = IconButton("minus", "Underline", 28, 16, checkable=True)
        self.underline.toggled.connect(
            lambda on: self._set_text_prop("underline", on))
        style_row.addWidget(self.bold)
        style_row.addWidget(self.italic)
        style_row.addWidget(self.underline)
        style_row.addStretch(1)
        s2.add_layout(style_row)
        self.align_chips = ChipGroup(["Left", "Center", "Right"], 1, small=True)
        self.align_chips.changed.connect(
            lambda i, _t: self._set_text_prop("align",
                                              ["left", "center", "right"][i]))
        s2.add(self.align_chips)
        self.letter = SliderField(-10.0, 30.0, 0.0, decimals=1, default=0.0)
        self.letter.valueChanged.connect(
            lambda v: self._set_text_prop("letter_spacing", v))
        self.letter.editingFinished.connect(self.ctx.end_gesture)
        s2.add(RowField("Tracking", self.letter))
        self.line_sp = SliderField(0.6, 2.5, 1.15, decimals=2, default=1.15)
        self.line_sp.valueChanged.connect(
            lambda v: self._set_text_prop("line_spacing", v))
        self.line_sp.editingFinished.connect(self.ctx.end_gesture)
        s2.add(RowField("Line height", self.line_sp))

        s3 = self.section("Colour & outline", "filter")
        self.color_btn = ColorButton("#ffffff")
        self.color_btn.colorChanged.connect(
            lambda c: self._set_text_prop("color", c))
        s3.add(RowField("Fill", self.color_btn))
        self.outline_btn = ColorButton("#000000")
        self.outline_btn.colorChanged.connect(
            lambda c: self._set_text_prop("outline_color", c))
        s3.add(RowField("Outline", self.outline_btn))
        self.outline_w = SliderField(0.0, 30.0, 4.0, decimals=1, default=4.0)
        self.outline_w.valueChanged.connect(
            lambda v: self._set_text_prop("outline_width", v))
        self.outline_w.editingFinished.connect(self.ctx.end_gesture)
        s3.add(RowField("Outline size", self.outline_w))
        self.shadow_btn = ColorButton("#000000")
        self.shadow_btn.colorChanged.connect(
            lambda c: self._set_text_prop("shadow_color", c))
        s3.add(RowField("Shadow", self.shadow_btn))
        self.shadow = SliderField(0.0, 20.0, 0.0, decimals=1, default=0.0)
        self.shadow.valueChanged.connect(
            lambda v: self._set_text_prop("shadow", v))
        self.shadow.editingFinished.connect(self.ctx.end_gesture)
        s3.add(RowField("Shadow blur", self.shadow))
        self.bg_cb = QCheckBox("Background box")
        self.bg_cb.toggled.connect(lambda on: self._set_text_prop("bg_enabled", on))
        s3.add(self.bg_cb)
        self.bg_btn = ColorButton("#000000")
        self.bg_btn.colorChanged.connect(
            lambda c: self._set_text_prop("bg_color", c))
        s3.add(RowField("Box colour", self.bg_btn))

        s4 = self.section("Animation", "magic")
        self.anim_combo = QComboBox()
        for a in TEXT_ANIMATIONS:
            self.anim_combo.addItem(a.replace("_", " ").title(), a)
        self.anim_combo.currentIndexChanged.connect(self._anim)
        s4.add(RowField("In / out", self.anim_combo))
        self.anim_in = SliderField(0.05, 3.0, 0.4, decimals=2, suffix="s",
                                   default=0.4)
        self.anim_in.valueChanged.connect(
            lambda v: self._set_text_prop("anim_in", v))
        self.anim_in.editingFinished.connect(self.ctx.end_gesture)
        s4.add(RowField("Duration", self.anim_in))
        self.finish()

    # -- actions -------------------------------------------------------------- #
    def _apply(self, fn, desc: str, coalesce: str = "") -> None:
        clip = self.ctx.selected_clip()
        if clip is None or clip.text is None:
            return
        self.ctx.commit(desc, lambda p: fn(clip.text), coalesce=coalesce)
        self.ctx.request_preview_refresh()

    def _text_changed(self) -> None:
        text = self.text_edit.toPlainText()
        self._apply(lambda tp: setattr(tp, "text", text), "Edit text",
                    coalesce="text:content")
        self.ctx.refresh_timeline()

    def _set_text(self, text: str) -> None:
        self.text_edit.setPlainText(text)
        self._apply(lambda tp: setattr(tp, "text", text), "Edit text")

    def _set_text_prop(self, key: str, value) -> None:
        self._apply(lambda tp: setattr(tp, key, value), f"Text {key}",
                    coalesce=f"text:{key}")

    def _toggle_autofit(self, on: bool) -> None:
        self._apply(lambda tp: setattr(tp, "max_width", 1.0 if on else 0.98),
                    "Auto-fit")

    def _font(self) -> None:
        name = self.font_combo.currentData()
        self._apply(lambda tp: setattr(tp, "font", name), "Font")

    def _anim(self) -> None:
        from ..core.ass import apply_text_animation
        name = self.anim_combo.currentData()
        clip = self.ctx.selected_clip()
        if clip is None or clip.text is None:
            return

        def apply(p):
            clip.text.animation = name
            if name in ("none", "fade_in", "fade_out", "fade_in_out",
                        "typewriter"):
                clip.text.animation = name
            else:
                apply_text_animation(clip, name, clip.duration,
                                     clip.text.anim_in)
        self.ctx.commit(f"Text animation {name}", apply)
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None or clip.text is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        tp = clip.text
        widgets = [self.text_edit, self.font_combo, self.size, self.maxw,
                   self.bold, self.italic, self.underline, self.letter,
                   self.line_sp, self.outline_w, self.shadow, self.bg_cb,
                   self.anim_combo, self.anim_in, self.autofit_cb]
        for w in widgets:
            w.blockSignals(True)
        self.text_edit.setPlainText(tp.text)
        idx = self.font_combo.findData(tp.font)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        self.size.set_value(tp.size)
        self.maxw.set_value(tp.max_width)
        self.bold.setChecked(tp.bold)
        self.italic.setChecked(tp.italic)
        self.underline.setChecked(tp.underline)
        self.letter.set_value(tp.letter_spacing)
        self.line_sp.set_value(tp.line_spacing)
        self.outline_w.set_value(tp.outline_width)
        self.shadow.set_value(tp.shadow)
        self.bg_cb.setChecked(tp.bg_enabled)
        ai = self.anim_combo.findData(tp.animation)
        if ai >= 0:
            self.anim_combo.setCurrentIndex(ai)
        self.anim_in.set_value(tp.anim_in)
        self.autofit_cb.setChecked(tp.max_width >= 0.999)
        for w in widgets:
            w.blockSignals(False)
        self.color_btn.set_color(tp.color)
        self.outline_btn.set_color(tp.outline_color)
        self.shadow_btn.set_color(tp.shadow_color)
        self.bg_btn.set_color(tp.bg_color)
        self.align_chips.set_current({"left": 0, "center": 1,
                                      "right": 2}.get(tp.align, 1))


# --------------------------------------------------------------------------- #
#  Mask + chroma
# --------------------------------------------------------------------------- #

class MaskPage(BasePage):
    SHAPES = list(render_mod.MASK_SHAPES)

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Mask shape", "mask")
        self.shape_chips = ChipGroup([x.title() for x in self.SHAPES], 0,
                                     small=True)
        self.shape_chips.changed.connect(self._shape)
        s.add(self.shape_chips)
        self.x = SliderField(0.0, 1.0, 0.5, decimals=3, default=0.5)
        self.y = SliderField(0.0, 1.0, 0.5, decimals=3, default=0.5)
        self.w = SliderField(0.05, 1.5, 0.6, decimals=3, default=0.6)
        self.h = SliderField(0.05, 1.5, 0.6, decimals=3, default=0.6)
        self.rot = SliderField(-180, 180, 0.0, decimals=0, suffix="°",
                               factor=1.0, default=0.0)
        self.feather = SliderField(0.0, 0.5, 0.02, decimals=3, default=0.02)
        self.invert = QCheckBox("Invert mask")
        for key, f in (("x", self.x), ("y", self.y), ("width", self.w),
                       ("height", self.h), ("rotation", self.rot),
                       ("feather", self.feather)):
            f.valueChanged.connect(lambda v, k=key: self._set_mask(k, v))
            f.editingFinished.connect(self.ctx.end_gesture)
            s.add(RowField(key.title(), f))
        self.invert.toggled.connect(
            lambda on: self._set_mask("invert", bool(on)))
        s.add(self.invert)

        s2 = self.section("Chroma key", "chroma")
        self.key_cb = QCheckBox("Remove this colour")
        self.key_cb.toggled.connect(self._toggle_chroma)
        s2.add(self.key_cb)
        self.key_color = ColorButton("#00b140")
        self.key_color.colorChanged.connect(
            lambda c: self._set_chroma("color", c))
        s2.add(RowField("Key colour", self.key_color))
        row = QHBoxLayout()
        for name, c in (("Green", "#00b140"), ("Blue", "#0047ab"),
                        ("Magenta", "#ff00ff"), ("White", "#ffffff")):
            b = QPushButton(name)
            b.setProperty("class", "chip")
            b.setStyleSheet("padding: 3px 8px; font-size: 11px;")
            b.clicked.connect(lambda _=False, col=c: self._set_chroma("color", col))
            row.addWidget(b)
        s2.add_layout(row)
        for key, lo, hi, dv in (("similarity", 0.0, 0.8, 0.18),
                                ("blend", 0.0, 0.5, 0.08),
                                ("spill", 0.0, 1.0, 0.0)):
            f = SliderField(lo, hi, dv, decimals=2, default=dv)
            f.valueChanged.connect(lambda v, k=key: self._set_chroma(k, v))
            f.editingFinished.connect(self.ctx.end_gesture)
            s2.add(RowField(key.title(), f))
            setattr(self, f"chroma_{key}", f)
        self.finish()

    def _shape(self, idx: int, _t: str) -> None:
        self._set_mask("shape", self.SHAPES[max(0, min(len(self.SHAPES) - 1,
                                                       idx))])

    def _set_mask(self, key: str, value) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit(f"Mask {key}",
                        lambda p: setattr(clip.mask, key, value),
                        coalesce=f"mask:{clip.id}:{key}")
        self.ctx.request_preview_refresh()

    def _toggle_chroma(self, on: bool) -> None:
        self._set_chroma("enabled", bool(on))

    def _set_chroma(self, key: str, value) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit(f"Chroma {key}",
                        lambda p: setattr(clip.chroma, key, value),
                        coalesce=f"chroma:{clip.id}:{key}")
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        m = clip.mask
        self.shape_chips.set_current(self.SHAPES.index(m.shape)
                                     if m.shape in self.SHAPES else 0)
        for key, f in (("x", self.x), ("y", self.y), ("width", self.w),
                       ("height", self.h), ("rotation", self.rot),
                       ("feather", self.feather)):
            f.blockSignals(True)
            f.set_value(getattr(m, key))
            f.blockSignals(False)
        self.invert.blockSignals(True)
        self.invert.setChecked(m.invert)
        self.invert.blockSignals(False)
        key_cb = self.key_cb
        key_cb.blockSignals(True)
        key_cb.setChecked(clip.chroma.enabled)
        key_cb.blockSignals(False)
        self.key_color.set_color(clip.chroma.color)
        for key in ("similarity", "blend", "spill"):
            f = getattr(self, f"chroma_{key}")
            f.blockSignals(True)
            f.set_value(getattr(clip.chroma, key))
            f.blockSignals(False)


# --------------------------------------------------------------------------- #
#  Keyframes
# --------------------------------------------------------------------------- #

class KeyframePage(BasePage):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Keyframes", "keyframe")
        self.prop_chips = ChipGroup([p.title() for p in ANIM_PROPS], 2,
                                    small=True)
        self.prop_chips.changed.connect(self._prop_changed)
        s.add(self.prop_chips)
        self.value = SliderField(0.0, 3.0, 1.0, decimals=3, default=1.0)
        self.value.valueChanged.connect(self._value_changed)
        self.value.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Value", self.value))
        row = QHBoxLayout()
        add = QPushButton(" Add keyframe")
        add.setIcon(theme.icon("keyframe_add", theme.TEXT_ON_ACCENT, 15))
        add.setProperty("class", "primary")
        add.clicked.connect(self._add)
        rm = QPushButton("Delete")
        rm.clicked.connect(self._delete)
        row.addWidget(add)
        row.addWidget(rm)
        s.add_layout(row)
        self.easing = QComboBox()
        for e in EASINGS:
            self.easing.addItem(e.replace("_", " ").title(), e)
        self.easing.currentIndexChanged.connect(self._easing)
        s.add(RowField("Easing", self.easing))
        self.list_box = QWidget()
        self.list_layout = QVBoxLayout(self.list_box)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(3)
        s.add(self.list_box)

        s2 = self.section("Motion presets", "magic")
        presets = [("Zoom in", [("scale", 0.0, 1.0), ("scale", 1.0, 1.6)]),
                   ("Zoom out", [("scale", 0.0, 1.6), ("scale", 1.0, 1.0)]),
                   ("Pan left", [("x", 0.0, 0.62), ("x", 1.0, 0.38)]),
                   ("Pan right", [("x", 0.0, 0.38), ("x", 1.0, 0.62)]),
                   ("Rise", [("y", 0.0, 0.62), ("y", 1.0, 0.42)]),
                   ("Drop", [("y", 0.0, 0.42), ("y", 1.0, 0.62)]),
                   ("Spin", [("rotation", 0.0, -12.0), ("rotation", 1.0, 12.0)]),
                   ("Fade in", [("opacity", 0.0, 0.0), ("opacity", 0.25, 1.0)]),
                   ("Punch", [("scale", 0.0, 1.0), ("scale", 0.12, 1.25),
                              ("scale", 0.3, 1.0)])]
        self.preset_strip = PresetStrip(
            [(name, name, "#3b4252") for name, _k in presets], card_w=68,
            card_h=38)
        self._presets = dict(presets)
        self.preset_strip.picked.connect(self._apply_preset)
        s2.add(self.preset_strip)
        self.finish()

    def _prop(self) -> str:
        return ANIM_PROPS[max(0, min(len(ANIM_PROPS) - 1,
                                     self.prop_chips._buttons.index(
                                         next(b for b in self.prop_chips._buttons
                                              if b.isChecked()))
                                     if any(b.isChecked() for b in
                                            self.prop_chips._buttons) else 2))]

    def _current_prop(self) -> str:
        for i, b in enumerate(self.prop_chips._buttons):
            if b.isChecked():
                return ANIM_PROPS[i]
        return "scale"

    def _prop_changed(self, idx: int, _t: str) -> None:
        self.refresh()

    def _value_changed(self, v: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        prop = self._current_prop()
        t_local = max(0.0, min(clip.duration, self.ctx.playhead() - clip.start))
        self.ctx.commit(f"Keyframe {prop}",
                        lambda p: clip.set_keyframe(prop, t_local, float(v)),
                        coalesce=f"kf:{clip.id}:{prop}")
        self.ctx.request_preview_refresh()

    def _add(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        prop = self._current_prop()
        t_local = max(0.0, min(clip.duration, self.ctx.playhead() - clip.start))
        base = getattr(clip.transform, prop)
        self.ctx.commit("Add keyframe",
                        lambda p: clip.set_keyframe(prop, t_local, float(base)))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _delete(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        prop = self._current_prop()
        sel = self.ctx.selected_keyframe()
        t_local = (sel[2] if sel and sel[0] == clip.id
                   else max(0.0, min(clip.duration,
                                     self.ctx.playhead() - clip.start)))
        self.ctx.commit("Delete keyframe",
                        lambda p: clip.remove_keyframe(prop, t_local))
        self.refresh()
        self.ctx.request_preview_refresh()

    def _easing(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        name = self.easing.currentData()
        prop = self._current_prop()

        def apply(p):
            for k in clip.animations.get(prop, []):
                k.easing = name
        self.ctx.commit("Easing", apply)
        self.ctx.request_preview_refresh()

    def _apply_preset(self, name: str) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        keys = self._presets.get(name, [])
        dur = max(0.2, clip.duration)

        def apply(p):
            for prop, p0, value in keys:
                clip.set_keyframe(prop, round(p0 * dur, 4), float(value))
        self.ctx.commit(f"Motion {name}", apply)
        self.refresh()
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        prop = self._current_prop()
        lt = max(0.0, self.ctx.playhead() - clip.start)
        self.value.blockSignals(True)
        cur = clip.prop_at(prop, lt)
        lo, hi = {"scale": (0.0, 5.0), "rotation": (-360.0, 360.0),
                  "opacity": (0.0, 1.0), "x": (-1.0, 2.0),
                  "y": (-1.0, 2.0)}.get(prop, (0.0, 3.0))
        self.value._min, self.value._max = lo, hi
        self.value.slider.setRange(0, int((hi - lo) * self.value._factor))
        self.value.set_value(cur)
        self.value.blockSignals(False)
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        rows = 0
        for p in clip.animated_props():
            for k in clip.animations.get(p, []):
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(4)
                txt = f"{p} · {k.t:.2f}s → {k.value:.3f}"
                b = QPushButton(txt)
                b.setStyleSheet("text-align: left; padding: 3px 7px;"
                                "font-size: 11px;")
                b.clicked.connect(
                    lambda _=False, t=k.t: self._goto(clip, t))
                lay.addWidget(b, 1)
                tr = IconButton("delete", "Remove", 22, 13)
                tr.clicked.connect(
                    lambda _=False, pp=p, t=k.t: self._remove(pp, t))
                lay.addWidget(tr)
                self.list_layout.addWidget(row)
                rows += 1
        if not rows:
            self.list_layout.addWidget(label("No keyframes yet.", "muted", 11))


    def _goto(self, clip, t_local: float) -> None:
        self.ctx.seek(clip.start + t_local)

    def _remove(self, prop: str, t_local: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit("Remove keyframe",
                        lambda p: clip.remove_keyframe(prop, t_local))
        self.refresh()
        self.ctx.refresh_timeline()
        self.ctx.request_preview_refresh()


# --------------------------------------------------------------------------- #
#  Audio (clip level)
# --------------------------------------------------------------------------- #

class AudioPage(BasePage):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        s = self.section("Level", "volume")
        self.volume = SliderField(0.0, 2.0, 1.0, decimals=2, default=1.0)
        self.volume.valueChanged.connect(lambda v: self._set("volume", v))
        self.volume.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Volume", self.volume))
        self.mute = QCheckBox("Mute")
        self.mute.toggled.connect(lambda on: self._set("muted", bool(on)))
        s.add(self.mute)
        self.pan = SliderField(-1.0, 1.0, 0.0, decimals=2, default=0.0)
        self.pan.valueChanged.connect(lambda v: self._set("pan", v))
        self.pan.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Pan  L / R", self.pan))
        self.fade_in = SliderField(0.0, 5.0, 0.0, decimals=2, suffix="s",
                                   default=0.0)
        self.fade_in.valueChanged.connect(lambda v: self._set("fade_in", v))
        self.fade_in.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Fade in", self.fade_in))
        self.fade_out = SliderField(0.0, 5.0, 0.0, decimals=2, suffix="s",
                                    default=0.0)
        self.fade_out.valueChanged.connect(lambda v: self._set("fade_out", v))
        self.fade_out.editingFinished.connect(self.ctx.end_gesture)
        s.add(RowField("Fade out", self.fade_out))
        frow = QHBoxLayout()
        for text, fi, fo in (("No fades", 0.0, 0.0), ("Soft", 0.4, 0.4),
                             ("J-cut", 1.2, 0.05), ("L-cut", 0.05, 1.2)):
            b = QPushButton(text)
            b.setProperty("class", "chip")
            b.setStyleSheet("padding: 3px 8px; font-size: 11px;")
            b.clicked.connect(lambda _=False, a=fi, c=fo: self._fades(a, c))
            frow.addWidget(b)
        s.add_layout(frow)

        self.fade_curve_row = QHBoxLayout()
        self.fade_curve_buttons = {}
        for key, name in (("linear", "Linear"), ("smooth", "Smooth"),
                          ("gentle", "Gentle"), ("quick", "Quick")):
            b = QPushButton(name)
            b.setProperty("class", "chip")
            b.setCheckable(True)
            b.setStyleSheet("padding: 3px 8px; font-size: 11px;")
            b.clicked.connect(lambda _=False, k=key: self._set_curve(k))
            self.fade_curve_row.addWidget(b)
            self.fade_curve_buttons[key] = b
        s.add(RowField("Fade curve", self._wrap(self.fade_curve_row)))

        s2 = self.section("Tone", "music")
        for key, lo, hi, dv in (("bass_boost", 0.0, 20.0, 0.0),
                                ("treble_boost", 0.0, 20.0, 0.0),
                                ("highpass", 0.0, 800.0, 0.0),
                                ("lowpass", 500.0, 20000.0, 20000.0),
                                ("compressor", 0.0, 1.0, 0.0),
                                ("denoise", 0.0, 1.0, 0.0),
                                ("echo", 0.0, 1.0, 0.0),
                                ("reverb", 0.0, 1.0, 0.0),
                                ("three_d", 0.0, 1.0, 0.0)):
            f = SliderField(lo, hi, dv, decimals=2, default=dv)
            f.valueChanged.connect(lambda v, k=key: self._set(k, v))
            f.editingFinished.connect(self.ctx.end_gesture)
            s2.add(RowField(key.replace("_", " ").title(), f))
            setattr(self, f"p_{key}", f)
        self.normalize = QCheckBox("Normalise loudness")
        self.normalize.toggled.connect(lambda on: self._set("normalize", bool(on)))
        s2.add(self.normalize)
        self.reverse = QCheckBox("Reverse audio")
        self.reverse.toggled.connect(lambda on: self._set("reverse", bool(on)))
        s2.add(self.reverse)
        self.finish()

    def _set(self, key: str, value) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return
        self.ctx.commit(f"Audio {key}",
                        lambda p: setattr(clip.audio, key, value),
                        coalesce=f"aud:{clip.id}:{key}")
        self.ctx.request_preview_refresh()

    def _wrap(self, layout):
        """Put a layout in a plain container so RowField can host it."""
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    def _set_curve(self, key: str) -> None:
        self._set("fade_type", key)
        for k, b in self.fade_curve_buttons.items():
            b.setChecked(k == key)

    def _fades(self, fade_in: float, fade_out: float) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            return

        def apply(p):
            clip.audio.fade_in, clip.audio.fade_out = fade_in, fade_out
        self.ctx.commit("Audio fades", apply)
        self.refresh()
        self.ctx.request_preview_refresh()

    def refresh(self) -> None:
        clip = self.ctx.selected_clip()
        if clip is None:
            self.empty.setVisible(True)
            self._body.setVisible(False)
            return
        self.empty.setVisible(False)
        self._body.setVisible(True)
        a = clip.audio
        for key, w in (("volume", self.volume), ("pan", self.pan),
                       ("fade_in", self.fade_in), ("fade_out", self.fade_out)):
            w.blockSignals(True)
            w.set_value(getattr(a, key))
            w.blockSignals(False)
        for key in ("bass_boost", "treble_boost", "highpass", "lowpass",
                    "compressor", "denoise", "echo", "reverb", "three_d"):
            w = getattr(self, f"p_{key}")
            w.blockSignals(True)
            w.set_value(getattr(a, key))
            w.blockSignals(False)
        for key, b in self.fade_curve_buttons.items():
            b.setChecked(key == getattr(a, "fade_type", "linear"))
        for cb, val in ((self.mute, a.muted), (self.normalize, a.normalize),
                        (self.reverse, a.reverse)):
            cb.blockSignals(True)
            cb.setChecked(val)
            cb.blockSignals(False)


# --------------------------------------------------------------------------- #
#  Inspector container
# --------------------------------------------------------------------------- #

PAGE_BY_KIND = {
    "video": ["Layout", "Speed", "Grade", "Effects", "Audio", "Mask",
              "Keyframes"],
    "image": ["Layout", "Grade", "Effects", "Motion"],
    "audio": ["Audio clip", "Speed"],
    "text": ["Text", "Layout", "Motion"],
    "sticker": ["Layout", "Motion"],
    "shape": ["Layout", "Motion"],
}


class Inspector(QWidget):
    """Tabs across the top, contextual page below."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setMinimumWidth(268)
        self.setMaximumWidth(400)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setStyleSheet(
            f"QTabBar {{ background: {theme.BG_PANEL}; }}"
            f"QTabBar::tab {{ background: transparent; color: {theme.TEXT_MUTED};"
            f" padding: 8px 11px; font-weight: 600; font-size: 11px;"
            f" border-bottom: 2px solid transparent; }}"
            f"QTabBar::tab:selected {{ color: {theme.TEXT};"
            f" border-bottom: 2px solid {theme.ACCENT}; }}"
            f"QTabBar::tab:hover {{ color: {theme.TEXT}; }}")
        root.addWidget(self.tabs)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.pages: Dict[str, BasePage] = {}
        self._add_page("Project", ProjectPage(ctx, self))
        self._add_page("Layout", TransformPage(ctx, self))
        self._add_page("Speed", SpeedPage(ctx, self))
        self._add_page("Grade", FilterPage(ctx, self))
        self._add_page("Effects", EffectsPage(ctx, self))
        self._add_page("Text", TextPage(ctx, self))
        self._add_page("Mask", MaskPage(ctx, self))
        self._add_page("Motion", KeyframePage(ctx, self))
        self._add_page("Audio", AudioPage(ctx, self))
        self.tabs.currentChanged.connect(self._tab_changed)
        self._kind = ""
        self.show_for(None)

    def _add_page(self, title: str, page: BasePage) -> None:
        idx = self.tabs.addTab(title)
        self.pages[title] = page
        self.stack.addWidget(page)

    def _tab_changed(self, idx: int) -> None:
        title = self.tabs.tabText(idx)
        page = self.pages.get(title)
        if page:
            page.refresh()

    def show_tab(self, title: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == title:
                self.tabs.setCurrentIndex(i)
                return
        # fall back to the closest available tab
        aliases = {"Audio clip": "Audio", "Layout": "Layout", "Motion":
                   "Keyframes", "Grade": "Grade"}
        alt = aliases.get(title)
        if alt and alt in self.pages:
            self.show_tab(alt)

    def show_for(self, clip: Optional[Clip]) -> None:
        if clip is None:
            wanted = ["Project", "Layout", "Speed", "Grade", "Effects",
                      "Mask", "Motion"]
        elif clip.kind == "text":
            wanted = ["Text", "Layout", "Motion", "Effects"]
        elif clip.kind == "audio":
            wanted = ["Audio", "Speed"]
        else:
            wanted = ["Layout", "Speed", "Grade", "Effects", "Audio", "Mask",
                      "Motion"]
        # keep the user's tab if it is still available
        current = self.tabs.tabText(self.tabs.currentIndex())
        self._rebuild_tabs(wanted, keep=current)
        self.refresh_all()

    def _rebuild_tabs(self, wanted: List[str], keep: str = "") -> None:
        if self._kind == ",".join(wanted):
            return
        self._kind = ",".join(wanted)
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self._titles = list(wanted)
        for t in wanted:
            self.tabs.addTab(t)
        self.tabs.blockSignals(False)
        idx = wanted.index(keep) if keep in wanted else 0
        self.tabs.setCurrentIndex(idx)
        page = self.pages.get(wanted[idx])
        if page:
            page.refresh()

    def refresh_all(self) -> None:
        page = self.pages.get(self.tabs.tabText(self.tabs.currentIndex()))
        if page:
            page.refresh()


from PySide6.QtWidgets import QTabBar  # noqa: E402  (kept last: widget registry)
