"""Context-sensitive properties panel (right side of the workspace).

Shows the controls that matter for what is selected:

* **nothing selected** → project settings (name, format, frame rate);
* **video / image clip** → transform (position, scale, rotation,
  opacity, fit), speed, and the clip's audio controls;
* **text clip** → wording, font, size, colour, outline, shadow, position;
* **audio clip** → volume, fades, mute, pan, speed.

Editing model: every gesture is one undo step.  Sliders and spin boxes
apply live (so the preview follows) and the undo snapshot is pushed when
the gesture ends; discrete controls apply and push immediately.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import engine
from ..engine import Clip, History, Project
from ..timeline import ops, titles

FONTS = ["Bebas Neue", "Anton", "Archivo Black", "Oswald", "Teko",
         "Bungee", "Alfa Slab One", "Titan One", "Bowlby One",
         "Montserrat", "Poppins", "Poppins Bold", "Inter", "Roboto",
         "Rubik", "Space Grotesk", "Fredoka", "Righteous", "Caveat",
         "Noto Sans", "Creepster"]


class Inspector(QWidget):
    """Properties editor bound to the current timeline selection."""

    changed = Signal()          # the project changed: refresh preview/timeline
    seek_requested = Signal(float)   # jump the playhead (keyframe list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.history: Optional[History] = None
        self._clip_ids: List[str] = []
        self._before: Optional[dict] = None
        self._playhead = 0.0
        self._kf_prop = "scale"
        self._kf: dict = {}
        self._user_title_presets: List[dict] = []
        # Set by the main window: called with (name, preset) to persist
        # a user title preset (QSettings lives in the main window).
        self.preset_saver = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Properties")
        title.setObjectName("title")
        title.setContentsMargins(8, 8, 8, 0)
        outer.addWidget(title)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._body = QWidget()
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(600)
        self._text_timer.timeout.connect(lambda: self._commit("Edit text"))

    # ------------------------------------------------------------------ #
    #  Undo session handling
    # ------------------------------------------------------------------ #
    def _begin(self) -> None:
        if self.history is not None and self._before is None:
            self._before = self.history.snapshot()

    def _commit(self, label: str) -> None:
        if self.history is None or self._before is None:
            return
        after = self.history.snapshot()
        if after != self._before:
            self.history.push(
                engine.SnapshotCommand(label, self._before, after),
                _already_applied=True)
        self._before = None

    def _apply(self, label: str, mutate: Callable[[Project], None]) -> None:
        """Apply a discrete edit as a single undo step."""
        if self.project is None or self.history is None:
            return
        self._begin()
        try:
            mutate(self.project)
        except Exception:
            self._before = None       # nothing recorded for a failed edit
            raise
        self._commit(label)
        self.changed.emit()

    def _apply_live(self, mutate: Callable[[Project], None]) -> None:
        """Apply during a gesture (undo is pushed by ``_commit``)."""
        if self.project is None:
            return
        self._begin()
        mutate(self.project)
        self.changed.emit()

    # ------------------------------------------------------------------ #
    def set_selection(self, project: Optional[Project],
                      history: Optional[History], clip_ids: List[str]) -> None:
        if self._before is not None:
            # A live gesture (slider/spin drag) is in flight: rebuilding
            # now would destroy the very control being dragged.
            self.project = project
            self.history = history
            self._clip_ids = list(clip_ids)
            return
        self._commit("Edit")                    # flush any pending gesture
        self.project = project
        self.history = history
        self._clip_ids = list(clip_ids)
        self._rebuild()

    def set_playhead(self, t: float) -> None:
        """Follow the playhead: the keyframe panel evaluates at it."""
        self._playhead = float(t)
        if self._kf:
            self._refresh_keyframes()

    def set_user_title_presets(self, presets: List[dict]) -> None:
        self._user_title_presets = [dict(p) for p in presets]

    def refresh(self) -> None:
        self._rebuild()

    def _clip(self) -> Optional[Clip]:
        if self.project is None or len(self._clip_ids) != 1:
            return None
        return self.project.clip(self._clip_ids[0])

    def _clear_body(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # hide + unparent immediately: a pending deleteLater must
                # never leave the old page painting over the new one
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _add(self, widget: QWidget) -> QWidget:
        self._layout.insertWidget(self._layout.count() - 1, widget)
        return widget

    def _rebuild(self) -> None:
        self._clear_body()
        self._kf = {}
        if self.project is None:
            self._add(QLabel("No project open"))
            return
        clip = self._clip()
        if len(self._clip_ids) > 1:
            label = QLabel(f"{len(self._clip_ids)} clips selected.\n"
                           "Select a single clip to edit its properties.")
            label.setObjectName("dim")
            label.setWordWrap(True)
            self._add(label)
            return
        if clip is None:
            self._build_project_page()
            return
        header = QLabel(f"{clip.name or clip.kind}  ·  {clip.kind}")
        header.setStyleSheet("font-weight:700;")
        self._add(header)
        timing = QLabel(
            f"Starts {clip.start:.2f}s · lasts {clip.duration:.2f}s"
            + (f" · source from {clip.src_in:.2f}s"
               if clip.kind in ("video", "audio") else ""))
        timing.setObjectName("dim")
        timing.setWordWrap(True)
        self._add(timing)
        asset = self.project.asset(clip.asset_id)
        has_audio = clip.kind == "audio" or (
            clip.kind == "video" and asset is not None and asset.has_audio)
        if clip.kind == "text" and clip.text is not None:
            self._build_text_group(clip)
            self._build_title_group(clip)
            self._build_text_animation_group(clip)
            self._build_transform_group(clip, text=True)
            self._build_keyframes_group(clip, include_volume=False)
            self._build_transition_group(clip)
        elif clip.kind == "audio":
            self._build_audio_group(clip)
            self._build_audio_processing_group(clip)
            self._build_keyframes_group(clip, include_volume=True,
                                        transform_props=False)
            self._build_transition_group(clip)
            self._build_speed_group(clip)
        else:
            self._build_transform_group(clip, text=False)
            self._build_colour_group(clip)
            self._build_compositing_group(clip)
            self._build_effects_group(clip)
            self._build_keyframes_group(clip, include_volume=has_audio)
            self._build_transition_group(clip)
            self._build_speed_group(clip)
            if has_audio:
                self._build_audio_group(clip)
                self._build_audio_processing_group(clip)

    # ------------------------------------------------------------------ #
    #  Control builders
    # ------------------------------------------------------------------ #
    def _slider_row(self, form: QFormLayout, label: str, value: float,
                    lo: float, hi: float, scale: float,
                    get: Callable[[Clip], float],
                    set_: Callable[[Clip, float], None],
                    undo_label: str, suffix: str = "") -> QSlider:
        clip = self._clip()
        slider = QSlider(Qt.Horizontal)
        slider.setRange(int(lo * scale), int(hi * scale))
        slider.setValue(int(round(value * scale)))
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2 if scale >= 100 else 1)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setFixedWidth(86)
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(slider, 1)
        lay.addWidget(spin)

        def from_slider(v: int) -> None:
            spin.blockSignals(True)
            spin.setValue(v / scale)
            spin.blockSignals(False)
            c = self._clip()
            if c is not None:
                self._apply_live(lambda p, c=c: set_(c, v / scale))

        def from_spin(v: float) -> None:
            slider.blockSignals(True)
            slider.setValue(int(round(v * scale)))
            slider.blockSignals(False)
            c = self._clip()
            if c is not None:
                self._apply_live(lambda p, c=c: set_(c, v))

        slider.valueChanged.connect(from_slider)
        spin.valueChanged.connect(from_spin)
        slider.sliderReleased.connect(lambda: self._commit(undo_label))
        spin.editingFinished.connect(lambda: self._commit(undo_label))
        form.addRow(label, row)
        return slider

    def _color_row(self, form: QFormLayout, label: str, current: str,
                   apply_color: Callable[[Clip, str], None],
                   undo_label: str) -> QPushButton:
        button = QPushButton(current)
        button.setStyleSheet(f"background:{current}; color:#ffffff;")

        def pick() -> None:
            clip = self._clip()
            if clip is None:
                return
            color = QColorDialog.getColor(QColor(current), self, label)
            if not color.isValid():
                return
            hexv = color.name()
            button.setText(hexv)
            button.setStyleSheet(f"background:{hexv}; color:#ffffff;")
            self._apply(undo_label,
                        lambda p, c=clip: apply_color(c, hexv))

        button.clicked.connect(pick)
        form.addRow(label, button)
        return button

    # ------------------------------------------------------------------ #
    #  Project page
    # ------------------------------------------------------------------ #
    def _build_project_page(self) -> None:
        project = self.project
        assert project is not None
        group = QGroupBox("Project")
        form = QFormLayout(group)
        name = QLineEdit(project.name)
        name.editingFinished.connect(
            lambda: self._apply("Rename project",
                                lambda p: setattr(p, "name", name.text())))
        form.addRow("Name", name)
        s = project.settings
        size = QLabel(f"{s.width} × {s.height} · {s.ratio_name}")
        size.setObjectName("dim")
        form.addRow("Format", size)
        fps = QDoubleSpinBox()
        fps.setRange(1, 120)
        fps.setValue(float(s.fps))
        fps.setSuffix(" fps")
        fps.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p: setattr(p.settings, "fps", float(v))))
        fps.editingFinished.connect(lambda: self._commit("Change frame rate"))
        form.addRow("Frame rate", fps)
        self._add(group)
        hint = QLabel(
            "Tip: select a clip on the timeline to edit its transform, "
            "speed, text or audio.  With nothing selected you are editing "
            "the project itself.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        self._add(hint)

    # ------------------------------------------------------------------ #
    #  Transform
    # ------------------------------------------------------------------ #
    def _build_transform_group(self, clip: Clip, text: bool) -> None:
        group = QGroupBox("Text position" if text else "Transform")
        form = QFormLayout(group)
        t = clip.transform
        self._slider_row(
            form, "Position X", t.x * 100, 0, 100, 1,
            lambda c: c.transform.x,
            lambda c, v: setattr(c.transform, "x", v / 100.0),
            "Move clip", "%")
        self._slider_row(
            form, "Position Y", t.y * 100, 0, 100, 1,
            lambda c: c.transform.y,
            lambda c, v: setattr(c.transform, "y", v / 100.0),
            "Move clip", "%")
        if not text:
            self._slider_row(
                form, "Scale", t.scale * 100, 5, 400, 1,
                lambda c: c.transform.scale,
                lambda c, v: setattr(c.transform, "scale", v / 100.0),
                "Scale clip", "%")
            self._slider_row(
                form, "Rotation", t.rotation, -180, 180, 1,
                lambda c: c.transform.rotation,
                lambda c, v: setattr(c.transform, "rotation", v),
                "Rotate clip", "°")
            fit = QComboBox()
            fit.addItems(["fit", "cover", "stretch"])
            fit.setCurrentText(t.fit)
            fit.currentTextChanged.connect(
                lambda v: self._apply(
                    "Change fit",
                    lambda p: setattr(self._clip().transform, "fit", v)))  # type: ignore
            form.addRow("Fit", fit)
        self._slider_row(
            form, "Opacity", t.opacity * 100, 0, 100, 1,
            lambda c: c.transform.opacity,
            lambda c, v: setattr(c.transform, "opacity", v / 100.0),
            "Change opacity", "%")
        self._add(group)

    # ------------------------------------------------------------------ #
    #  Speed
    # ------------------------------------------------------------------ #
    def _build_speed_group(self, clip: Clip) -> None:
        group = QGroupBox("Speed")
        form = QFormLayout(group)
        if clip.speed_curve is None:
            self._slider_row(
                form, "Speed", clip.speed * 100, 10, 400, 1,
                lambda c: c.speed,
                lambda c, v: ops.set_clip_speed(self.project, c, v / 100.0),  # type: ignore
                "Change speed", "%")
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            for factor in (0.25, 0.5, 1.0, 1.5, 2.0, 4.0):
                btn = QPushButton(f"{factor:g}×")
                btn.clicked.connect(
                    lambda _=False, f=factor: self._set_speed_preset(f))
                lay.addWidget(btn)
            form.addRow("Presets", row)
        else:
            note = QLabel(
                f"Speed curve: {clip.speed_curve.preset}. Constant-speed "
                "controls do not apply while a curve is active; choose "
                "“None” below to remove it.")
            note.setObjectName("dim")
            note.setWordWrap(True)
            form.addRow(note)
        curve = QComboBox()
        curve.addItems(engine.speed_curve_presets())
        current = clip.speed_curve.preset if clip.speed_curve else "None"
        if current in engine.speed_curve_presets():
            curve.setCurrentText(current)
        else:
            curve.setCurrentText("None")
        apply_curve = QPushButton("Apply")

        def apply_curve_clicked() -> None:
            c = self._clip()
            if c is None:
                return
            label = curve.currentText()
            if label == "None":
                self._apply("Remove speed curve",
                            lambda p, c=c: ops.clear_speed_curve(c))
            else:
                self._apply("Apply speed curve",
                            lambda p, c=c, label=label:
                            ops.apply_speed_curve(c, label))

        apply_curve.clicked.connect(apply_curve_clicked)
        crow = QWidget()
        clay = QHBoxLayout(crow)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.addWidget(curve, 1)
        clay.addWidget(apply_curve)
        form.addRow("Speed curve", crow)
        custom = QPushButton("Custom curve…")
        custom.clicked.connect(lambda: self._edit_speed_curve(self._clip()))
        form.addRow(custom)
        if clip.kind in ("video", "audio"):
            reverse = QCheckBox("Play in reverse")
            reverse.setChecked(bool(clip.reverse))
            reverse.toggled.connect(
                lambda v: self._apply(
                    "Reverse clip" if v else "Play forwards",
                    lambda p, v=v: ops.set_reverse(self._clip(), bool(v))))  # type: ignore
            form.addRow(reverse)
        self._add(group)

    def _edit_speed_curve(self, clip: Optional[Clip]) -> None:
        if clip is None:
            return
        current = (list(clip.speed_curve.points) if clip.speed_curve
                   else [(0.0, 1.0), (1.0, 1.0)])
        dialog = SpeedCurveDialog(current, self)
        if dialog.exec() == QDialog.Accepted:
            points = dialog.points()
            self._apply("Edit speed curve",
                        lambda p, c=clip: ops.set_speed_curve_points(c,
                                                                     points))

    def _set_speed_preset(self, factor: float) -> None:
        clip = self._clip()
        if clip is None:
            return
        try:
            self._apply("Change speed",
                        lambda p, c=clip: ops.set_clip_speed(p, c, factor))
        except ValueError:
            pass        # speed-curve clip: the group shows an explanation

    # ------------------------------------------------------------------ #
    #  Audio
    # ------------------------------------------------------------------ #
    def _build_audio_group(self, clip: Clip) -> None:
        group = QGroupBox("Audio")
        form = QFormLayout(group)
        a = clip.audio
        self._slider_row(
            form, "Volume", a.volume * 100, 0, 200, 1,
            lambda c: c.audio.volume,
            lambda c, v: setattr(c.audio, "volume", v / 100.0),
            "Change volume", "%")
        self._slider_row(
            form, "Pan", a.pan * 100, -100, 100, 1,
            lambda c: c.audio.pan,
            lambda c, v: setattr(c.audio, "pan", v / 100.0),
            "Change pan", "")
        for label, attr in (("Fade in", "fade_in"), ("Fade out", "fade_out")):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 30.0)
            spin.setSingleStep(0.1)
            spin.setDecimals(2)
            spin.setSuffix(" s")
            spin.setValue(float(getattr(a, attr)))
            spin.valueChanged.connect(
                lambda v, at=attr: self._apply_live(
                    lambda p, at=at, v=v: setattr(self._clip().audio, at, float(v))))  # type: ignore
            spin.editingFinished.connect(lambda: self._commit("Change fade"))
            form.addRow(label, spin)
        fade_type = QComboBox()
        for key in engine.FADE_TYPES:
            fade_type.addItem(_FADE_LABELS.get(str(key), str(key)), str(key))
        idx = fade_type.findData(str(a.fade_type))
        fade_type.setCurrentIndex(idx if idx >= 0 else 0)
        fade_type.currentIndexChanged.connect(
            lambda _i: self._apply(
                "Change fade curve",
                lambda p: ops.set_fade_type(
                    self._clip(), fade_type.currentData())))  # type: ignore
        form.addRow("Fade curve", fade_type)
        mute = QCheckBox("Mute this clip")
        mute.setChecked(a.muted)
        mute.toggled.connect(
            lambda v: self._apply(
                "Mute clip" if v else "Unmute clip",
                lambda p, v=v: setattr(self._clip().audio, "muted", bool(v))))  # type: ignore
        form.addRow(mute)
        self._add(group)

    # ------------------------------------------------------------------ #
    #  Text
    # ------------------------------------------------------------------ #
    def _build_text_group(self, clip: Clip) -> None:
        props = clip.text
        assert props is not None
        group = QGroupBox("Text")
        form = QFormLayout(group)
        edit = QPlainTextEdit(props.text)
        edit.setFixedHeight(64)

        def text_changed() -> None:
            c = self._clip()
            if c is not None and c.text is not None:
                self._apply_live(
                    lambda p, c=c: (setattr(c.text, "text", edit.toPlainText()),
                                    setattr(c, "name",
                                            edit.toPlainText().splitlines()[0][:24]
                                            if edit.toPlainText() else "Text")))
            self._text_timer.start()

        edit.textChanged.connect(text_changed)
        form.addRow("Wording", edit)

        font = QComboBox()
        font.addItems(FONTS)
        if props.font in FONTS:
            font.setCurrentText(props.font)
        font.currentTextChanged.connect(
            lambda v: self._apply(
                "Change font",
                lambda p, v=v: setattr(self._clip().text, "font", v)))  # type: ignore
        form.addRow("Font", font)

        size = QSpinBox()
        size.setRange(8, 600)
        size.setValue(int(round(props.size)))
        size.setSuffix(" px")
        size.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p, v=v: setattr(self._clip().text, "size", float(v))))  # type: ignore
        size.editingFinished.connect(lambda: self._commit("Change text size"))
        form.addRow("Size", size)

        self._color_row(form, "Colour", props.color,
                        lambda c, v: setattr(c.text, "color", v),
                        "Change text colour")
        outline = QDoubleSpinBox()
        outline.setRange(0.0, 40.0)
        outline.setDecimals(1)
        outline.setValue(float(props.outline_width))
        outline.setSuffix(" px")
        outline.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p, v=v: setattr(self._clip().text, "outline_width", float(v))))  # type: ignore
        outline.editingFinished.connect(lambda: self._commit("Change outline"))
        form.addRow("Outline width", outline)
        self._color_row(form, "Outline colour", props.outline_color,
                        lambda c, v: setattr(c.text, "outline_color", v),
                        "Change outline colour")
        shadow = QDoubleSpinBox()
        shadow.setRange(0.0, 40.0)
        shadow.setDecimals(1)
        shadow.setValue(float(props.shadow))
        shadow.setSuffix(" px")
        shadow.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p, v=v: setattr(self._clip().text, "shadow", float(v))))  # type: ignore
        shadow.editingFinished.connect(lambda: self._commit("Change shadow"))
        form.addRow("Shadow", shadow)

        align = QComboBox()
        align.addItems(["left", "center", "right"])
        align.setCurrentText(props.align)
        align.currentTextChanged.connect(
            lambda v: self._apply(
                "Change alignment",
                lambda p, v=v: setattr(self._clip().text, "align", v)))  # type: ignore
        form.addRow("Align", align)

        bold = QCheckBox("Bold")
        bold.setChecked(props.bold)
        bold.toggled.connect(
            lambda v: self._apply(
                "Toggle bold",
                lambda p, v=v: setattr(self._clip().text, "bold", bool(v))))  # type: ignore
        italic = QCheckBox("Italic")
        italic.setChecked(props.italic)
        italic.toggled.connect(
            lambda v: self._apply(
                "Toggle italic",
                lambda p, v=v: setattr(self._clip().text, "italic", bool(v))))  # type: ignore
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(bold)
        lay.addWidget(italic)
        lay.addStretch(1)
        form.addRow("Style", row)
        self._add(group)

    # ------------------------------------------------------------------ #
    #  Phase 4: Effects, Colour grading, Compositing, Audio processing
    # ------------------------------------------------------------------ #
    def _build_colour_group(self, clip: Clip) -> None:
        """Quick colour grading sliders: brightness, contrast, saturation, temperature."""
        group = QGroupBox("Colour")
        form = QFormLayout(group)
        # Find or create a basic effect on this clip
        basic = self._find_or_add_effect(clip, "basic")
        defaults = engine.effect_defaults("basic")
        for key, label, lo, hi in [
            ("brightness", "Brightness", -1.0, 1.0),
            ("contrast", "Contrast", 0.0, 3.0),
            ("saturation", "Saturation", 0.0, 3.0),
            ("gamma", "Gamma", 0.1, 3.0),
        ]:
            defval = defaults.get(key, 0.0 if key == "brightness" else 1.0)
            self._slider_row(
                form, label,
                basic.params.get(key, defval),
                lo, hi, 100,
                lambda c, k=key, d=defval: self._get_fx_param(c, "basic", k, d),
                lambda c, v, k=key: self._set_effect_param(c, "basic", k, v),
                f"Change {label.lower()}")
        # Temperature (separate effect)
        temp = self._find_or_add_effect(clip, "temperature")
        temp_val = temp.params.get("temperature", 0.0) if temp else 0.0
        self._slider_row(
            form, "Temperature", temp_val, -1.0, 1.0, 100,
            lambda c: self._get_fx_param(c, "temperature", "temperature", 0.0),
            lambda c, v: self._set_effect_param(c, "temperature", "temperature", v),
            "Change temperature")
        self._add(group)

    def _build_compositing_group(self, clip: Clip) -> None:
        """Blend mode, mask, and chroma key controls."""
        group = QGroupBox("Compositing")
        form = QFormLayout(group)
        # Blend mode
        blend = QComboBox()
        for mode in engine.BLEND_MODES:
            blend.addItem(mode.replace("_", " ").title(), mode)
        idx = blend.findData(clip.blend)
        blend.setCurrentIndex(max(0, idx))
        blend.currentIndexChanged.connect(
            lambda _i: self._apply(
                "Change blend mode",
                lambda p: setattr(self._clip(), "blend", blend.currentData())))
        form.addRow("Blend mode", blend)
        # Mask shape
        mask_shape = QComboBox()
        for shape in engine.MASK_SHAPES:
            mask_shape.addItem(shape.title() if shape != "none" else "None", shape)
        idx = mask_shape.findData(clip.mask.shape)
        mask_shape.setCurrentIndex(max(0, idx))
        mask_shape.currentIndexChanged.connect(
            lambda _i: self._apply(
                "Change mask",
                lambda p: setattr(self._clip().mask, "shape", mask_shape.currentData())))
        form.addRow("Mask", mask_shape)
        # Mask feather
        self._slider_row(
            form, "Mask feather", clip.mask.feather * 100, 0, 100, 1,
            lambda c: c.mask.feather,
            lambda c, v: setattr(c.mask, "feather", v / 100.0),
            "Change mask feather", "%")
        # Mask size
        self._slider_row(
            form, "Mask width", clip.mask.width * 100, 0, 100, 1,
            lambda c: c.mask.width * 100,
            lambda c, v: setattr(c.mask, "width", v / 100.0),
            "Change mask width", "%")
        self._slider_row(
            form, "Mask height", clip.mask.height * 100, 0, 100, 1,
            lambda c: c.mask.height * 100,
            lambda c, v: setattr(c.mask, "height", v / 100.0),
            "Change mask height", "%")
        mask_invert = QCheckBox("Invert mask")
        mask_invert.setChecked(clip.mask.invert)
        mask_invert.toggled.connect(
            lambda v: self._apply(
                "Invert mask",
                lambda p, v=v: setattr(self._clip().mask, "invert", bool(v))))
        form.addRow(mask_invert)
        # Chroma key
        chroma_enabled = QCheckBox("Chroma key (green/blue screen)")
        chroma_enabled.setChecked(clip.chroma.enabled)
        chroma_enabled.toggled.connect(
            lambda v: self._apply(
                "Toggle chroma key",
                lambda p, v=v: setattr(self._clip().chroma, "enabled", bool(v))))
        form.addRow(chroma_enabled)
        self._slider_row(
            form, "Key similarity", clip.chroma.similarity * 100, 0, 100, 1,
            lambda c: c.chroma.similarity,
            lambda c, v: setattr(c.chroma, "similarity", v / 100.0),
            "Change chroma similarity", "%")
        self._slider_row(
            form, "Key blend", clip.chroma.blend * 100, 0, 100, 1,
            lambda c: c.chroma.blend,
            lambda c, v: setattr(c.chroma, "blend", v / 100.0),
            "Change chroma blend", "%")
        self._add(group)

    def _build_effects_group(self, clip: Clip) -> None:
        """Effect stack: add, remove, enable/disable, reorder effects."""
        group = QGroupBox("Effects")
        lay = QVBoxLayout(group)
        # Current effects list
        if clip.filters:
            for fx in clip.filters:
                row = QHBoxLayout()
                lbl = QLabel(engine.effect_label(fx.kind))
                lbl.setStyleSheet("font-weight:600;")
                row.addWidget(lbl)
                # Enable/disable toggle
                en = QCheckBox()
                en.setChecked(fx.enabled)
                en.setToolTip("Enable/disable this effect")
                en.toggled.connect(
                    lambda v, fx_id=fx.id: self._toggle_effect(fx_id, v))
                row.addWidget(en)
                # Remove button
                rm = QPushButton("✕")
                rm.setFixedWidth(24)
                rm.setToolTip("Remove this effect")
                rm.clicked.connect(
                    lambda _, fx_id=fx.id: self._remove_effect(fx_id))
                row.addWidget(rm)
                lay.addLayout(row)
                # Parameters
                params = engine.effect_params(fx.kind)
                for p in params:
                    if p.kind == "float":
                        val = fx.params.get(p.key, p.default)
                        self._slider_row_inline(
                            lay, p.label, val, p.lo, p.hi,
                            max(1, int(1.0 / p.step)) if p.step else 100,
                            fx.id, p.key, p.default)
        # Add effect button
        add_btn = QPushButton("+ Add effect")
        add_btn.setToolTip("Add a video effect to this clip")
        add_btn.clicked.connect(lambda: self._add_effect_dialog(clip))
        lay.addWidget(add_btn)
        self._add(group)

    def _build_audio_processing_group(self, clip: Clip) -> None:
        """Audio processing: EQ, compressor, denoise, echo, reverb."""
        group = QGroupBox("Audio processing")
        form = QFormLayout(group)
        a = clip.audio
        # EQ
        for label, attr in [("EQ Low", "eq_low"), ("EQ Mid", "eq_mid"),
                            ("EQ High", "eq_high")]:
            self._slider_row(
                form, label, getattr(a, attr), -12, 12, 1,
                lambda c, at=attr: getattr(c.audio, at),
                lambda c, v, at=attr: setattr(c.audio, at, v),
                f"Change {label.lower()}", " dB")
        # Bass/Treble boost
        for label, attr in [("Bass boost", "bass_boost"),
                            ("Treble boost", "treble_boost")]:
            self._slider_row(
                form, label, getattr(a, attr) * 100, 0, 100, 1,
                lambda c, at=attr: getattr(c.audio, at) * 100,
                lambda c, v, at=attr: setattr(c.audio, at, v / 100.0),
                f"Change {label.lower()}", "%")
        # Compressor
        self._slider_row(
            form, "Compressor", a.compressor * 100, 0, 100, 1,
            lambda c: c.audio.compressor * 100,
            lambda c, v: setattr(c.audio, "compressor", v / 100.0),
            "Change compressor", "%")
        # Denoise
        self._slider_row(
            form, "Denoise", a.denoise * 100, 0, 100, 1,
            lambda c: c.audio.denoise * 100,
            lambda c, v: setattr(c.audio, "denoise", v / 100.0),
            "Change denoise", "%")
        # Echo
        self._slider_row(
            form, "Echo", a.echo * 100, 0, 100, 1,
            lambda c: c.audio.echo * 100,
            lambda c, v: setattr(c.audio, "echo", v / 100.0),
            "Change echo", "%")
        # Reverb
        self._slider_row(
            form, "Reverb", a.reverb * 100, 0, 100, 1,
            lambda c: c.audio.reverb * 100,
            lambda c, v: setattr(c.audio, "reverb", v / 100.0),
            "Change reverb", "%")
        # Highpass/Lowpass
        for label, attr in [("Highpass", "highpass"), ("Lowpass", "lowpass")]:
            self._slider_row(
                form, label, getattr(a, attr), 0, 20000, 1,
                lambda c, at=attr: getattr(c.audio, at),
                lambda c, v, at=attr: setattr(c.audio, at, v),
                f"Change {label.lower()}", " Hz")
        # Normalize
        norm = QCheckBox("Normalize audio")
        norm.setChecked(a.normalize)
        norm.toggled.connect(
            lambda v: self._apply(
                "Toggle normalize",
                lambda p, v=v: setattr(self._clip().audio, "normalize", bool(v))))
        form.addRow(norm)
        self._add(group)

    # -- Phase 4 helpers -- #
    def _get_fx_param(self, clip: Clip, kind: str, key: str, default: float) -> float:
        for f in clip.filters:
            if f.kind == kind:
                return f.params.get(key, default)
        return default

    def _find_or_add_effect(self, clip: Clip, kind: str):
        """Find an existing effect of this kind, or create one."""
        for fx in clip.filters:
            if fx.kind == kind and fx.enabled:
                return fx
        # Create with defaults
        from vn_editor.core.model import FilterInstance
        defaults = engine.effect_defaults(kind)
        fx = FilterInstance(kind=kind, params=dict(defaults))
        clip.filters.append(fx)
        return fx

    def _set_effect_param(self, clip: Clip, kind: str, key: str, value: float) -> None:
        """Set a parameter on an effect of the given kind."""
        fx = None
        for f in clip.filters:
            if f.kind == kind:
                fx = f
                break
        if fx is None:
            return
        fx.params[key] = value

    def _toggle_effect(self, fx_id: str, enabled: bool) -> None:
        def _do(p):
            c = self._clip()
            if c:
                for f in c.filters:
                    if f.id == fx_id:
                        f.enabled = enabled
        self._apply("Toggle effect" if not enabled else "Enable effect", _do)

    def _enable_filter(self, fx_id: str) -> None:
        c = self._clip()
        if c:
            for f in c.filters:
                if f.id == fx_id:
                    f.enabled = True

    def _remove_effect(self, fx_id: str) -> None:
        self._apply(
            "Remove effect",
            lambda p: self._clip().remove_filter(fx_id))

    def _add_effect_dialog(self, clip: Clip) -> None:
        """Show a dialog to add an effect from the catalogue."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Add effect")
        dlg.setMinimumWidth(300)
        lay = QVBoxLayout(dlg)
        cat_combo = QComboBox()
        cats = engine.effect_categories()
        for cat in cats:
            cat_combo.addItem(cat, cat)
        lay.addWidget(cat_combo)
        fx_combo = QComboBox()
        lay.addWidget(fx_combo)
        def update_effects(idx):
            fx_combo.clear()
            cat = cat_combo.currentData()
            for kind in cats.get(cat, []):
                fx_combo.addItem(engine.effect_label(kind), kind)
        cat_combo.currentIndexChanged.connect(update_effects)
        update_effects(0)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted and fx_combo.currentData():
            kind = fx_combo.currentData()
            from vn_editor.core.model import FilterInstance
            defaults = engine.effect_defaults(kind)
            fx = FilterInstance(kind=kind, params=dict(defaults))
            self._apply(
                f"Add {engine.effect_label(kind)}",
                lambda p: self._clip().filters.append(fx))
            self._rebuild()

    def _slider_row_inline(self, layout, label, value, lo, hi, scale,
                           fx_id, param_key, default):
        """Inline slider for effect parameters (not QFormLayout-based)."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setFixedWidth(80)
        h.addWidget(lbl)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(int(lo * scale), int(hi * scale))
        slider.setValue(int(round(value * scale)))
        h.addWidget(slider, 1)
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setValue(value)
        spin.setFixedWidth(70)
        h.addWidget(spin)
        slider.valueChanged.connect(lambda v: (spin.blockSignals(True),
            spin.setValue(v / scale), spin.blockSignals(False),
            self._set_effect_param_live(fx_id, param_key, v / scale)))
        spin.valueChanged.connect(lambda v: (slider.blockSignals(True),
            slider.setValue(int(v * scale)), slider.blockSignals(False),
            self._set_effect_param_live(fx_id, param_key, v)))
        spin.editingFinished.connect(lambda: self._commit(f"Change {label.lower()}"))
        layout.addWidget(row)

    def _set_effect_param_live(self, fx_id, key, value):
        c = self._clip()
        if c:
            for f in c.filters:
                if f.id == fx_id:
                    self._begin()
                    f.params[key] = value
                    return
        props = clip.text
        assert props is not None
        group = QGroupBox("Text")
        form = QFormLayout(group)
        edit = QPlainTextEdit(props.text)
        edit.setFixedHeight(64)

        def text_changed() -> None:
            c = self._clip()
            if c is not None and c.text is not None:
                self._apply_live(
                    lambda p, c=c: (setattr(c.text, "text", edit.toPlainText()),
                                    setattr(c, "name",
                                            edit.toPlainText().splitlines()[0][:24]
                                            if edit.toPlainText() else "Text")))
            self._text_timer.start()

        edit.textChanged.connect(text_changed)
        form.addRow("Wording", edit)

        font = QComboBox()
        font.addItems(FONTS)
        if props.font in FONTS:
            font.setCurrentText(props.font)
        font.currentTextChanged.connect(
            lambda v: self._apply(
                "Change font",
                lambda p, v=v: setattr(self._clip().text, "font", v)))  # type: ignore
        form.addRow("Font", font)

        size = QSpinBox()
        size.setRange(8, 600)
        size.setValue(int(round(props.size)))
        size.setSuffix(" px")
        size.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p, v=v: setattr(self._clip().text, "size", float(v))))  # type: ignore
        size.editingFinished.connect(lambda: self._commit("Change text size"))
        form.addRow("Size", size)

        self._color_row(form, "Colour", props.color,
                        lambda c, v: setattr(c.text, "color", v),
                        "Change text colour")
        outline = QDoubleSpinBox()
        outline.setRange(0.0, 40.0)
        outline.setDecimals(1)
        outline.setValue(float(props.outline_width))
        outline.setSuffix(" px")
        outline.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p, v=v: setattr(self._clip().text, "outline_width", float(v))))  # type: ignore
        outline.editingFinished.connect(lambda: self._commit("Change outline"))
        form.addRow("Outline width", outline)
        self._color_row(form, "Outline colour", props.outline_color,
                        lambda c, v: setattr(c.text, "outline_color", v),
                        "Change outline colour")
        shadow = QDoubleSpinBox()
        shadow.setRange(0.0, 40.0)
        shadow.setDecimals(1)
        shadow.setValue(float(props.shadow))
        shadow.setSuffix(" px")
        shadow.valueChanged.connect(
            lambda v: self._apply_live(
                lambda p, v=v: setattr(self._clip().text, "shadow", float(v))))  # type: ignore
        shadow.editingFinished.connect(lambda: self._commit("Change shadow"))
        form.addRow("Shadow", shadow)

        align = QComboBox()
        align.addItems(["left", "center", "right"])
        align.setCurrentText(props.align)
        align.currentTextChanged.connect(
            lambda v: self._apply(
                "Change alignment",
                lambda p, v=v: setattr(self._clip().text, "align", v)))  # type: ignore
        form.addRow("Align", align)

        bold = QCheckBox("Bold")
        bold.setChecked(props.bold)
        bold.toggled.connect(
            lambda v: self._apply(
                "Toggle bold",
                lambda p, v=v: setattr(self._clip().text, "bold", bool(v))))  # type: ignore
        italic = QCheckBox("Italic")
        italic.setChecked(props.italic)
        italic.toggled.connect(
            lambda v: self._apply(
                "Toggle italic",
                lambda p, v=v: setattr(self._clip().text, "italic", bool(v))))  # type: ignore
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(bold)
        lay.addWidget(italic)
        lay.addStretch(1)
        form.addRow("Style", row)
        self._add(group)


    # ------------------------------------------------------------------ #
    #  Keyframes
    # ------------------------------------------------------------------ #
    def _kf_local(self, clip: Clip) -> float:
        return min(max(self._playhead - clip.start, 0.0),
                   max(0.0, clip.duration))

    def _kf_at_playhead(self, clip: Clip, prop: str):
        local = self._kf_local(clip)
        for k in ops.keyframes(clip, prop):
            if abs(k.t - local) <= 1e-3:
                return k
        return None

    def _build_keyframes_group(self, clip: Clip, include_volume: bool,
                               transform_props: bool = True) -> None:
        group = QGroupBox("Animation (keyframes)")
        v = QVBoxLayout(group)
        props_avail = (["x", "y", "scale", "rotation", "opacity"]
                       if transform_props else [])
        if include_volume:
            props_avail.append("volume")
        if self._kf_prop not in props_avail:
            self._kf_prop = props_avail[0]
        prop_combo = QComboBox()
        for prop in props_avail:
            prop_combo.addItem(_KF_PROPS[prop][0], prop)
        prop_combo.setCurrentIndex(props_avail.index(self._kf_prop))
        prop_combo.currentIndexChanged.connect(self._kf_prop_changed)
        v.addWidget(prop_combo)

        value = QDoubleSpinBox()
        value.valueChanged.connect(self._kf_value_changed)
        value.editingFinished.connect(lambda: self._commit("Edit keyframe"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Value at playhead"))
        row.addWidget(value, 1)
        v.addLayout(row)
        time_label = QLabel("")
        time_label.setObjectName("dim")
        v.addWidget(time_label)

        ease = QComboBox()
        for key in engine.EASINGS:
            ease.addItem(_easing_label(key), key)
        ease.setCurrentIndex(max(0, ease.findData("ease_in_out")))
        ease.currentIndexChanged.connect(self._kf_easing_changed)
        add_btn = QPushButton("◆ Add / update keyframe")
        add_btn.clicked.connect(self._kf_add)
        del_btn = QPushButton("Delete keyframe here")
        del_btn.clicked.connect(self._kf_delete)
        row2 = QHBoxLayout()
        row2.addWidget(add_btn, 1)
        row2.addWidget(del_btn)
        v.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Easing"))
        row3.addWidget(ease, 1)
        v.addLayout(row3)

        lst = QListWidget()
        lst.setMaximumHeight(96)
        lst.itemClicked.connect(self._kf_list_clicked)
        v.addWidget(lst)
        clear_btn = QPushButton("Clear all keyframes for this property")
        clear_btn.clicked.connect(self._kf_clear)
        v.addWidget(clear_btn)
        hint = QLabel(
            "Values are evaluated at the playhead. Add a keyframe, move "
            "the playhead, change the value — preview and export both "
            "animate between keyframes. Click a timeline diamond to "
            "jump to a keyframe.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self._kf = {"prop": prop_combo, "value": value, "ease": ease,
                    "list": lst, "add": add_btn, "del": del_btn,
                    "time": time_label}
        self._refresh_keyframes()
        self._add(group)

    def _refresh_keyframes(self) -> None:
        if not self._kf:
            return
        clip = self._clip()
        if clip is None:
            return
        prop = self._kf_prop
        _label, lo, hi, factor, suffix, decimals = _KF_PROPS[prop]
        local = self._kf_local(clip)
        spin = self._kf["value"]
        spin.blockSignals(True)
        spin.setRange(lo * factor, hi * factor)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(ops.evaluate(clip, prop, local) * factor)
        spin.blockSignals(False)
        kf = self._kf_at_playhead(clip, prop)
        self._kf["del"].setEnabled(kf is not None)
        ease = self._kf["ease"]
        ease.blockSignals(True)
        if kf is not None:
            idx = ease.findData(kf.easing)
            if idx >= 0:
                ease.setCurrentIndex(idx)
        ease.blockSignals(False)
        self._kf["time"].setText(
            f"Playhead at {local:.2f}s of {clip.duration:.2f}s"
            + ("  ·  ◆ keyframe here" if kf is not None else ""))
        lst = self._kf["list"]
        lst.blockSignals(True)
        lst.clear()
        for k in ops.keyframes(clip, prop):
            item = QListWidgetItem(
                f"{k.t:.2f}s  →  {k.value * factor:.1f}{suffix}"
                f"  ·  {_easing_label(k.easing)}")
            item.setData(Qt.UserRole, k.t)
            lst.addItem(item)
            if kf is not None and abs(k.t - kf.t) <= 1e-9:
                lst.setCurrentItem(item)
        lst.blockSignals(False)

    def _kf_prop_changed(self) -> None:
        prop = self._kf["prop"].currentData()
        if prop:
            self._kf_prop = str(prop)
        self._refresh_keyframes()

    def _kf_value_changed(self, display_value: float) -> None:
        clip = self._clip()
        if clip is None:
            return
        prop = self._kf_prop
        stored = display_value / _KF_PROPS[prop][3]

        def mutate(p: Project, c: Clip = clip) -> None:
            kf = self._kf_at_playhead(c, prop)
            if kf is not None:
                kf.value = stored
            elif prop == "volume":
                c.audio.volume = stored
            else:
                setattr(c.transform, prop, stored)

        self._apply_live(mutate)

    def _kf_add(self) -> None:
        clip = self._clip()
        if clip is None:
            return
        prop = self._kf_prop
        stored = self._kf["value"].value() / _KF_PROPS[prop][3]
        easing = str(self._kf["ease"].currentData() or "ease_in_out")
        self._apply("Add keyframe",
                    lambda p, c=clip: ops.add_keyframe(
                        c, prop, self._kf_local(c), value=stored,
                        easing=easing))

    def _kf_delete(self) -> None:
        clip = self._clip()
        if clip is None:
            return
        kf = self._kf_at_playhead(clip, self._kf_prop)
        if kf is None:
            return
        self._apply("Delete keyframe",
                    lambda p, c=clip, t=kf.t: ops.remove_keyframe(
                        c, self._kf_prop, t))

    def _kf_easing_changed(self) -> None:
        clip = self._clip()
        if clip is None:
            return
        kf = self._kf_at_playhead(clip, self._kf_prop)
        if kf is None:
            return                      # applies to the next added keyframe
        easing = str(self._kf["ease"].currentData() or kf.easing)
        self._apply("Change easing",
                    lambda p, k=kf, e=easing: setattr(k, "easing", e))

    def _kf_clear(self) -> None:
        clip = self._clip()
        if clip is None:
            return
        self._apply("Clear keyframes",
                    lambda p, c=clip: ops.clear_keyframes(c, self._kf_prop))

    def _kf_list_clicked(self, item: QListWidgetItem) -> None:
        clip = self._clip()
        if clip is None:
            return
        self.seek_requested.emit(clip.start + float(item.data(Qt.UserRole)))

    # ------------------------------------------------------------------ #
    #  Transitions
    # ------------------------------------------------------------------ #
    def _build_transition_group(self, clip: Clip) -> None:
        group = QGroupBox("Transition in")
        v = QVBoxLayout(group)
        assert self.project is not None
        prev = ops.previous_clip(self.project, clip)
        effective = ops.effective_transition(self.project, clip)
        current = QLabel(
            f"Current: {engine.transition_label(effective.kind)}"
            f" · {effective.duration:.2f}s" if effective is not None
            else "Current: none")
        current.setObjectName("dim")
        v.addWidget(current)
        if prev is None:
            hint = QLabel(
                "This is the first clip on its track. Transitions happen "
                "at the join between two clips — trim or move another "
                "clip against this one first.")
            hint.setObjectName("dim")
            hint.setWordWrap(True)
            v.addWidget(hint)
            self._add(group)
            return
        kind = QComboBox()
        for category, specs in engine.transition_catalogue().items():
            for spec in specs:
                kind.addItem(f"{category} — {spec.label}", spec.key)
        if effective is not None:
            idx = kind.findData(effective.kind)
            if idx >= 0:
                kind.setCurrentIndex(idx)
        duration = QDoubleSpinBox()
        duration.setRange(0.05, max(0.05, min(clip.duration, prev.duration)))
        duration.setSingleStep(0.05)
        duration.setDecimals(2)
        duration.setSuffix(" s")
        duration.setValue(
            effective.duration if effective is not None
            else engine.transition_default_duration(
                str(kind.currentData() or "fade")))
        kind.currentIndexChanged.connect(
            lambda _i: duration.setValue(
                engine.transition_default_duration(
                    str(kind.currentData() or "fade"))))
        row = QHBoxLayout()
        row.addWidget(kind, 1)
        row.addWidget(duration)
        v.addLayout(row)
        row2 = QHBoxLayout()
        apply_btn = QPushButton("Apply transition")
        apply_btn.clicked.connect(
            lambda: self._apply(
                "Set transition",
                lambda p, c=clip: ops.set_transition(
                    p, c, str(kind.currentData()), float(duration.value()))))
        remove_btn = QPushButton("Remove")
        remove_btn.setEnabled(effective is not None)
        remove_btn.clicked.connect(
            lambda: self._apply(
                "Remove transition",
                lambda p, c=clip: ops.remove_transition(p, c)))
        row2.addWidget(apply_btn, 1)
        row2.addWidget(remove_btn)
        v.addLayout(row2)
        hint = QLabel(
            "The transition renders across the join with the previous "
            "clip (picture, plus an audio crossfade when both clips have "
            "sound) — exactly what you see in the preview.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self._add(group)

    # ------------------------------------------------------------------ #
    #  Text animation + title styles (text clips)
    # ------------------------------------------------------------------ #
    def _build_text_animation_group(self, clip: Clip) -> None:
        group = QGroupBox("Text animation")
        v = QVBoxLayout(group)
        combo = QComboBox()
        for name in engine.text_animation_names():
            combo.addItem(name.replace("_", " ").capitalize(), name)
        current = clip.meta.get("text_anim", "none")
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        entrance = QDoubleSpinBox()
        entrance.setRange(0.05, 3.0)
        entrance.setSingleStep(0.05)
        entrance.setDecimals(2)
        entrance.setSuffix(" s")
        entrance.setValue(float(clip.text.anim_in or 0.4))  # type: ignore
        row = QHBoxLayout()
        row.addWidget(combo, 1)
        row.addWidget(QLabel("Entrance"))
        row.addWidget(entrance)
        v.addLayout(row)
        row2 = QHBoxLayout()
        apply_btn = QPushButton("Apply animation")
        clear_btn = QPushButton("Clear")

        def apply_clicked() -> None:
            c = self._clip()
            if c is None:
                return
            name = str(combo.currentData() or "none")
            if name == "none":
                self._apply("Clear text animation",
                            lambda p, c=c: ops.clear_text_animation(c))
            else:
                self._apply("Apply text animation",
                            lambda p, c=c: ops.apply_text_animation(
                                c, name, float(entrance.value())))

        apply_btn.clicked.connect(apply_clicked)
        clear_btn.clicked.connect(
            lambda: self._apply(
                "Clear text animation",
                lambda p: ops.clear_text_animation(self._clip())))  # type: ignore
        row2.addWidget(apply_btn, 1)
        row2.addWidget(clear_btn)
        v.addLayout(row2)
        hint = QLabel(
            "Presets are converted into ordinary keyframes, so you can "
            "fine-tune them in the Animation panel above.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self._add(group)

    def _build_title_group(self, clip: Clip) -> None:
        group = QGroupBox("Title style")
        v = QVBoxLayout(group)
        combo = QComboBox()
        for preset in titles.title_presets():
            combo.addItem(f"Built-in — {preset['label']}", preset)
        for preset in self._user_title_presets:
            combo.addItem(f"My preset — {preset['label']}", preset)
        apply_btn = QPushButton("Apply style to this clip")

        def apply_clicked() -> None:
            c = self._clip()
            preset = combo.currentData()
            if c is None or not isinstance(preset, dict):
                return
            self._apply("Apply title style",
                        lambda p, c=c, preset=preset:
                        titles.apply_preset_to_clip(p, c, preset))

        apply_btn.clicked.connect(apply_clicked)
        row = QHBoxLayout()
        row.addWidget(combo, 1)
        row.addWidget(apply_btn)
        v.addLayout(row)
        save_btn = QPushButton("Save current style as my preset…")

        def save_clicked() -> None:
            c = self._clip()
            if c is None or self.project is None or self.preset_saver is None:
                return
            name, ok = QInputDialog.getText(
                self, "Save title preset", "Preset name:",
                text=(c.name or "My title"))
            if not ok or not name.strip():
                return
            preset = titles.preset_from_clip(self.project, c, name.strip())
            self.preset_saver(name.strip(), preset)

        save_btn.clicked.connect(save_clicked)
        v.addWidget(save_btn)
        hint = QLabel(
            "Styles are size-relative, so they survive switching the "
            "project between 16:9 and 9:16.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self._add(group)


# --------------------------------------------------------------------------- #
#  Phase 2 property metadata
# --------------------------------------------------------------------------- #
#: keyframeable property -> (label, stored min, stored max, display factor,
#: suffix, decimals).  Values are stored in the model's native units and
#: shown scaled (percent for x/y/scale/opacity/volume).
_KF_PROPS = {
    "x": ("Position X", 0.0, 1.0, 100.0, "%", 1),
    "y": ("Position Y", 0.0, 1.0, 100.0, "%", 1),
    "scale": ("Scale", 0.05, 4.0, 100.0, "%", 1),
    "rotation": ("Rotation", -180.0, 180.0, 1.0, "°", 1),
    "opacity": ("Opacity", 0.0, 1.0, 100.0, "%", 1),
    "volume": ("Volume", 0.0, 2.0, 100.0, "%", 1),
}
_FADE_LABELS = {"linear": "Linear", "smooth": "Smooth",
                "gentle": "Gentle", "quick": "Quick"}


def _easing_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


# --------------------------------------------------------------------------- #
#  Speed-curve point editor (dialog)
# --------------------------------------------------------------------------- #
class _CurveCanvas(QWidget):
    """Tiny speed-curve editor: drag points, double-click adds, right-
    click deletes.  Points are (progress 0..1, speed 0.05..4 shown)."""

    changed = Signal()

    def __init__(self, points, parent=None) -> None:
        super().__init__(parent)
        self.points = sorted([list(p) for p in points]) or [[0.0, 1.0],
                                                            [1.0, 1.0]]
        self._drag_idx: Optional[int] = None
        self.setMinimumSize(380, 190)
        self.setMouseTracking(True)

    # geometry: plot area with margins
    def _plot(self):
        w, h = self.width(), self.height()
        return 34, 10, max(10, w - 44), max(10, h - 34)

    def _to_px(self, prog: float, speed: float):
        x0, y0, pw, ph = self._plot()
        return (x0 + prog * pw,
                y0 + ph - (min(speed, 4.0) / 4.0) * ph)

    def _from_px(self, x: float, y: float):
        x0, y0, pw, ph = self._plot()
        prog = min(1.0, max(0.0, (x - x0) / pw))
        speed = min(4.0, max(0.05, (1.0 - (y - y0) / ph) * 4.0))
        return prog, speed

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#1b1e24"))
        x0, y0, pw, ph = self._plot()
        p.setPen(QPen(QColor("#333a44"), 1))
        for i in range(5):
            gx = x0 + pw * i / 4
            gy = y0 + ph * i / 4
            p.drawLine(int(gx), y0, int(gx), y0 + ph)
            p.drawLine(x0, int(gy), x0 + pw, int(gy))
        # speed = 1 reference line
        _, one_y = self._to_px(0.0, 1.0)
        p.setPen(QPen(QColor("#ffcf4d"), 1, Qt.DashLine))
        p.drawLine(x0, int(one_y), x0 + pw, int(one_y))
        p.setFont(QFont("Sans", 7))
        p.setPen(QColor("#9aa3ad"))
        p.drawText(2, int(one_y) + 3, "1x")
        p.drawText(2, y0 + 9, "4x")
        # curve
        pts = [QPointF(*self._to_px(pr, sp)) for pr, sp in self.points]
        p.setPen(QPen(QColor("#7cc7ff"), 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        p.setBrush(QColor("#ffd75e"))
        p.setPen(QPen(QColor("#20242b"), 1))
        for pt in pts:
            p.drawEllipse(pt, 4.5, 4.5)
        p.end()

    def _nearest(self, x: float, y: float) -> Optional[int]:

        best, best_d = None, 14.0
        for i, (pr, sp) in enumerate(self.points):
            px, py = self._to_px(pr, sp)
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        idx = self._nearest(pos.x(), pos.y())
        if event.button() == Qt.RightButton:
            if idx is not None and len(self.points) > 2:
                del self.points[idx]
                self.changed.emit()
                self.update()
            return
        if idx is None:
            prog, speed = self._from_px(pos.x(), pos.y())
            self.points.append([prog, speed])
            self.points.sort()
            idx = self.points.index([prog, speed])
            self.changed.emit()
        self._drag_idx = idx
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_idx is None:
            return
        pos = event.position()
        prog, speed = self._from_px(pos.x(), pos.y())
        i = self._drag_idx
        # keep point order stable while dragging
        lo = self.points[i - 1][0] + 0.01 if i > 0 else 0.0
        hi = self.points[i + 1][0] - 0.01 if i < len(self.points) - 1 else 1.0
        self.points[i] = [min(max(prog, lo), max(lo, hi)), speed]
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_idx = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        prog, speed = self._from_px(pos.x(), pos.y())
        self.points.append([prog, speed])
        self.points.sort()
        self.changed.emit()
        self.update()


class SpeedCurveDialog(QDialog):
    """Edit a clip's custom speed curve (points + preset starting points)."""

    def __init__(self, points, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom speed curve")
        layout = QVBoxLayout(self)
        hint = QLabel(
            "The curve maps clip progress (left→right) to playback speed. "
            "Drag points, double-click to add one, right-click to delete. "
            "The yellow dashed line is normal (1x) speed.")
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("Start from preset"))
        self._preset = QComboBox()
        self._preset.addItems(engine.speed_curve_presets())
        self._preset.currentTextChanged.connect(self._load_preset)
        row.addWidget(self._preset, 1)
        layout.addLayout(row)
        self._canvas = _CurveCanvas(points, self)
        layout.addWidget(self._canvas)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_preset(self, label: str) -> None:
        points = engine.speed_curve_points(label)
        if points:
            self._canvas.points = sorted([list(p) for p in points])
            self._canvas.update()

    def points(self):
        return [(float(p), float(s)) for p, s in self._canvas.points]
