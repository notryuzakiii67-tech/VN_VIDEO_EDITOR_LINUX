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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import engine
from ..engine import Clip, History, Project
from ..timeline import ops

FONTS = ["Bebas Neue", "Anton", "Archivo Black", "Oswald", "Teko",
         "Bungee", "Alfa Slab One", "Titan One", "Bowlby One",
         "Montserrat", "Poppins", "Poppins Bold", "Inter", "Roboto",
         "Rubik", "Space Grotesk", "Fredoka", "Righteous", "Caveat",
         "Noto Sans", "Creepster"]


class Inspector(QWidget):
    """Properties editor bound to the current timeline selection."""

    changed = Signal()          # the project changed: refresh preview/timeline

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.history: Optional[History] = None
        self._clip_ids: List[str] = []
        self._before: Optional[dict] = None

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
        self._commit("Edit")                    # flush any pending gesture
        self.project = project
        self.history = history
        self._clip_ids = list(clip_ids)
        self._rebuild()

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
        if clip.kind == "text" and clip.text is not None:
            self._build_text_group(clip)
            self._build_transform_group(clip, text=True)
        elif clip.kind == "audio":
            self._build_audio_group(clip)
            self._build_speed_group(clip)
        else:
            self._build_transform_group(clip, text=False)
            self._build_speed_group(clip)
            asset = self.project.asset(clip.asset_id)
            if clip.kind == "video" and asset is not None and asset.has_audio:
                self._build_audio_group(clip)

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
        if clip.speed_curve is not None:
            note = QLabel("This clip uses a speed curve (Phase 2 editor); "
                          "constant-speed presets do not apply.")
            note.setObjectName("dim")
            note.setWordWrap(True)
            form.addRow(note)
        self._add(group)

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
