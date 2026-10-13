"""Serialisable project model.

Everything the editor can express lives here: projects, tracks, clips,
transforms, keyframes, filters, transitions, text styling, masks and audio
properties.  The model is deliberately plain data (dataclasses + dicts) so it
serialises to JSON with no bespoke code, and so undo can snapshot it cheaply.
"""
from __future__ import annotations

import copy
import math
import time
import uuid
from dataclasses import dataclass, field, asdict, fields
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------- #
#  Aspect ratio presets (VN style)
# --------------------------------------------------------------------------- #

ASPECT_PRESETS: List[Tuple[str, int, int]] = [
    ("Original", 0, 0),
    ("9:16  Vertical", 1080, 1920),
    ("16:9  Landscape", 1920, 1080),
    ("1:1   Square", 1080, 1080),
    ("4:5   Portrait", 1080, 1350),
    ("3:4   Portrait", 1080, 1440),
    ("2:3   Portrait", 1080, 1620),
    ("4:3   Classic", 1440, 1080),
    ("21:9  Cinema", 2560, 1080),
    ("5:4   Photo", 1350, 1080),
]

RESOLUTION_PRESETS = [
    ("480p", 854, 480), ("540p", 960, 540), ("720p HD", 1280, 720),
    ("1080p Full HD", 1920, 1080), ("1440p 2K", 2560, 1440),
    ("2160p 4K", 3840, 2160),
]


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
#  Keyframes
# --------------------------------------------------------------------------- #

EASINGS = ["linear", "ease_in", "ease_out", "ease_in_out", "ease_in_cubic",
           "ease_out_cubic", "ease_in_out_cubic", "ease_in_quart",
           "ease_out_quart", "bounce_out", "elastic_out", "hold"]


@dataclass
class Keyframe:
    t: float                       # clip-local seconds
    value: float
    easing: str = "linear"

    def to_dict(self) -> Dict[str, Any]:
        return {"t": round(self.t, 4), "v": self.value, "e": self.easing}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Keyframe":
        return cls(float(d.get("t", 0.0)), float(d.get("v", 0.0)),
                   str(d.get("e", "linear")))


def ease(name: str, p: float) -> float:
    """Normalised easing curve, p in [0, 1]."""
    p = min(1.0, max(0.0, p))
    if name == "linear":
        return p
    if name == "hold":
        return 0.0
    if name == "ease_in":
        return p * p
    if name == "ease_out":
        return 1 - (1 - p) ** 2
    if name == "ease_in_out":
        return 2 * p * p if p < 0.5 else 1 - 2 * (1 - p) ** 2
    if name == "ease_in_cubic":
        return p ** 3
    if name == "ease_out_cubic":
        return 1 - (1 - p) ** 3
    if name == "ease_in_out_cubic":
        return 4 * p ** 3 if p < 0.5 else 1 - 4 * (1 - p) ** 3
    if name == "ease_in_quart":
        return p ** 4
    if name == "ease_out_quart":
        return 1 - (1 - p) ** 4
    if name == "bounce_out":
        if p < 1 / 2.75:
            return 7.5625 * p * p
        if p < 2 / 2.75:
            p -= 1.5 / 2.75
            return 7.5625 * p * p + 0.75
        if p < 2.5 / 2.75:
            p -= 2.25 / 2.75
            return 7.5625 * p * p + 0.9375
        p -= 2.625 / 2.75
        return 7.5625 * p * p + 0.984375
    if name == "elastic_out":
        if p <= 0:
            return 0.0
        if p >= 1:
            return 1.0
        return 2 ** (-10 * p) * math.sin((p * 10 - 0.75) * (2 * math.pi / 3)) + 1
    return p


def eval_keyframes(keys: List[Keyframe], t: float, default: float = 0.0) -> float:
    """Value of an animated property at clip-local time ``t``."""
    if not keys:
        return default
    ks = sorted(keys, key=lambda k: k.t)
    if t <= ks[0].t:
        return ks[0].value
    if t >= ks[-1].t:
        return ks[-1].value
    for a, b in zip(ks, ks[1:]):
        if a.t <= t <= b.t:
            span = b.t - a.t
            if span <= 1e-9:
                return b.value
            p = (t - a.t) / span
            return a.value + (b.value - a.value) * ease(b.easing, p)
    return ks[-1].value


def keyframe_range(keys: List[Keyframe], default: float) -> Tuple[float, float]:
    if not keys:
        return default, default
    vals = [k.value for k in keys]
    return min(vals + [default]), max(vals + [default])


# --------------------------------------------------------------------------- #
#  Filters / effects
# --------------------------------------------------------------------------- #

@dataclass
class FilterInstance:
    """A single effect applied to a clip.

    ``kind`` selects the parameter schema in :mod:`vn_editor.core.effects`;
    ``params`` holds user values, ``animated`` holds keyframes per parameter.
    """
    kind: str
    params: Dict[str, float] = field(default_factory=dict)
    animated: Dict[str, List[Keyframe]] = field(default_factory=dict)
    enabled: bool = True
    id: str = field(default_factory=lambda: new_id("fx"))

    def value(self, name: str, t: float = 0.0) -> float:
        base = float(self.params.get(name, 0.0))
        keys = self.animated.get(name)
        if keys:
            return eval_keyframes(keys, t, base)
        return base

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"id": self.id, "kind": self.kind,
                             "params": dict(self.params), "enabled": self.enabled}
        if self.animated:
            d["animated"] = {k: [kf.to_dict() for kf in v]
                             for k, v in self.animated.items()}
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FilterInstance":
        return cls(kind=d.get("kind", "none"),
                   params=dict(d.get("params", {})),
                   animated={k: [Keyframe.from_dict(x) for x in v]
                             for k, v in d.get("animated", {}).items()},
                   enabled=bool(d.get("enabled", True)),
                   id=d.get("id") or new_id("fx"))


# --------------------------------------------------------------------------- #
#  Transitions
# --------------------------------------------------------------------------- #

@dataclass
class Transition:
    """Transition on the leading ('in') or trailing ('out') edge of a clip."""
    kind: str = "fade"
    duration: float = 0.5
    edge: str = "in"                     # in | out
    params: Dict[str, float] = field(default_factory=dict)
    audio: bool = True                   # crossfade the audio too

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "duration": self.duration, "edge": self.edge,
                "params": dict(self.params), "audio": self.audio}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transition":
        return cls(str(d.get("kind", "fade")), float(d.get("duration", 0.5)),
                   str(d.get("edge", "in")), dict(d.get("params", {})),
                   bool(d.get("audio", True)))


# --------------------------------------------------------------------------- #
#  Transform / mask / chroma
# --------------------------------------------------------------------------- #

@dataclass
class Transform:
    """Geometry of a clip inside the project canvas.

    ``x``/``y`` are normalised (0..1) canvas coordinates of the layer centre.
    ``scale`` is a multiplier on the "fit" size.  ``crop_*`` are normalised
    insets (0..1) applied before fitting.
    """
    x: float = 0.5
    y: float = 0.5
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    flip_h: bool = False
    flip_v: bool = False
    crop_l: float = 0.0
    crop_r: float = 0.0
    crop_t: float = 0.0
    crop_b: float = 0.0
    fit: str = "fit"                     # fit | cover | stretch
    zoom_x: float = 0.5                  # pan focus inside the layer (0..1)
    zoom_y: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transform":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class MaskProps:
    shape: str = "none"                  # none|rect|ellipse|linear|radial|heart|star|split
    x: float = 0.5
    y: float = 0.5
    width: float = 0.6
    height: float = 0.6
    rotation: float = 0.0
    feather: float = 0.02
    invert: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MaskProps":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class ChromaProps:
    enabled: bool = False
    color: str = "#00b140"
    similarity: float = 0.18
    blend: float = 0.08
    spill: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChromaProps":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class AudioProps:
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    muted: bool = False
    speed_mode: str = "pitch_correct"    # pitch_correct | pitch_shift | mute
    pitch: float = 1.0
    eq_low: float = 0.0
    eq_mid: float = 0.0
    eq_high: float = 0.0
    compressor: float = 0.0
    reverb: float = 0.0
    echo: float = 0.0
    highpass: float = 0.0
    lowpass: float = 0.0
    bass_boost: float = 0.0
    treble_boost: float = 0.0
    three_d: float = 0.0
    reverse: bool = False
    denoise: float = 0.0
    normalize: bool = False
    pan: float = 0.0                    # -1 = hard left, +1 = hard right
    fade_type: str = "linear"           # key into audio.FADE_CURVES

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AudioProps":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


# --------------------------------------------------------------------------- #
#  Text
# --------------------------------------------------------------------------- #

TEXT_ANIMATIONS = [
    "none", "fade_in", "fade_out", "fade_in_out", "pop_in", "pop_out",
    "typewriter", "slide_left", "slide_right", "slide_up", "slide_down",
    "zoom_in", "zoom_out", "bounce_in", "spin_in", "blur_in", "wipe_right",
    "rise", "drop", "pulse", "shake", "wave", "glitch", "float",
]

#: Ready-made caption styles: (text, font, fill, outline, animation).  Shared by
#: the templates panel and by the tools that check the presets still render.
TEXT_PRESETS: List[Tuple[str, str, str, str, str]] = [
    ("POV: YOU ARE HIM", "Bebas Neue", "#ffffff", "#000000", "pop_in"),
    ("INSANE CLUTCH", "Anton", "#ffe14d", "#000000", "zoom_in"),
    ("GG EASY", "Archivo Black", "#39d98a", "#000000", "fade_in"),
    ("SUBSCRIBE", "Bungee", "#ff2f6b", "#ffffff", "bounce_in"),
    ("WAIT FOR IT…", "Teko", "#ffffff", "#000000", "slide_left"),
    ("NO WAY", "Titan One", "#4cc9f0", "#0b1220", "pop_in"),
    ("LET'S GO", "Poppins Bold", "#ffffff", "#ff2f6b", "slide_up"),
    ("EPIC FAIL", "Alfa Slab One", "#ff8c42", "#000000", "shake"),
    ("CLUTCH OR KICK", "Oswald", "#ffffff", "#000000", "rise"),
    ("PRO TIP", "Montserrat", "#0b1220", "#e9edf5", "fade_in"),
]


@dataclass
class TextProps:
    text: str = "Text"
    font: str = "Bebas Neue"
    size: float = 72.0                   # in project canvas pixels
    color: str = "#ffffff"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "center"                # left|center|right
    line_spacing: float = 1.15
    letter_spacing: float = 0.0
    outline_width: float = 0.0
    outline_color: str = "#000000"
    shadow: float = 0.0
    shadow_color: str = "#000000"
    bg_enabled: bool = False
    bg_color: str = "#000000"
    bg_opacity: float = 0.5
    bg_padding: float = 12.0
    bg_radius: float = 8.0
    animation: str = "none"
    anim_in: float = 0.4
    anim_out: float = 0.0
    anim_out_style: str = "none"
    glow: float = 0.0
    glow_color: str = "#ffffff"
    gradient: bool = False
    gradient_color: str = "#ff0055"
    max_width: float = 1.0               # fraction of canvas width

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TextProps":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


# --------------------------------------------------------------------------- #
#  Speed curve
# --------------------------------------------------------------------------- #

SPEED_CURVE_PRESETS: List[Tuple[str, List[Tuple[float, float]]]] = [
    ("None", []),
    ("Slow Fast Slow", [(0.0, 0.5), (0.5, 2.0), (1.0, 0.5)]),
    ("Fast Slow Fast", [(0.0, 2.0), (0.5, 0.4), (1.0, 2.0)]),
    ("Slow Motion In", [(0.0, 0.25), (1.0, 1.0)]),
    ("Slow Motion Out", [(0.0, 1.0), (1.0, 0.25)]),
    ("Fast In", [(0.0, 4.0), (1.0, 1.0)]),
    ("Fast Out", [(0.0, 1.0), (1.0, 4.0)]),
    ("Jump Cut", [(0.0, 1.0), (0.45, 6.0), (0.55, 6.0), (1.0, 1.0)]),
    ("Hero Moment", [(0.0, 2.5), (0.35, 0.25), (0.65, 0.25), (1.0, 2.5)]),
]


@dataclass
class SpeedCurve:
    """Piecewise-linear speed over normalised clip progress."""
    points: List[Tuple[float, float]] = field(default_factory=list)
    preset: str = "None"

    def speed_at(self, p: float) -> float:
        if not self.points:
            return 1.0
        pts = sorted(self.points, key=lambda x: x[0])
        if p <= pts[0][0]:
            return max(0.05, pts[0][1])
        if p >= pts[-1][0]:
            return max(0.05, pts[-1][1])
        for a, b in zip(pts, pts[1:]):
            if a[0] <= p <= b[0]:
                span = b[0] - a[0]
                if span <= 1e-9:
                    return max(0.05, b[1])
                f = (p - a[0]) / span
                return max(0.05, a[1] + (b[1] - a[1]) * f)
        return 1.0

    def segments(self, steps: int = 24) -> List[Tuple[float, float, float]]:
        """[(p_start, p_end, average_speed)] over ``steps`` slices."""
        out: List[Tuple[float, float, float]] = []
        for i in range(steps):
            p0 = i / steps
            p1 = (i + 1) / steps
            mid = (p0 + p1) / 2
            out.append((p0, p1, self.speed_at(mid)))
        return out

    def average(self, steps: int = 48) -> float:
        segs = self.segments(steps)
        return sum(s for _, _, s in segs) / max(1, len(segs))

    def is_ramp(self) -> bool:
        if len(self.points) < 2:
            return False
        speeds = [s for _, s in self.points]
        return (max(speeds) - min(speeds)) > 1e-6

    def to_dict(self) -> Dict[str, Any]:
        return {"preset": self.preset,
                "points": [[round(a, 4), round(b, 4)] for a, b in self.points]}

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Optional["SpeedCurve"]:
        if not d:
            return None
        pts = [(float(a), float(b)) for a, b in d.get("points", [])]
        if not pts:
            return None
        return cls(pts, str(d.get("preset", "Custom")))


# --------------------------------------------------------------------------- #
#  Clip
# --------------------------------------------------------------------------- #

CLIP_KINDS = ("video", "image", "audio", "text", "sticker", "shape", "adjustment")

ANIM_PROPS = ("x", "y", "scale", "rotation", "opacity")


@dataclass
class Clip:
    kind: str = "video"
    track_id: str = ""
    start: float = 0.0                  # timeline seconds
    duration: float = 5.0               # timeline seconds
    src_in: float = 0.0                 # offset into source media (seconds)
    speed: float = 1.0
    id: str = field(default_factory=lambda: new_id("clip"))
    name: str = ""
    asset_id: str = ""
    path: str = ""                      # resolved at render time
    reverse: bool = False
    freeze: bool = False
    freeze_at: float = 0.0
    speed_curve: Optional[SpeedCurve] = None
    transform: Transform = field(default_factory=Transform)
    animations: Dict[str, List[Keyframe]] = field(default_factory=dict)
    filters: List[FilterInstance] = field(default_factory=list)
    transition_in: Optional[Transition] = None
    transition_out: Optional[Transition] = None
    audio: AudioProps = field(default_factory=AudioProps)
    audio_animations: Dict[str, List[Keyframe]] = field(default_factory=dict)
    text: Optional[TextProps] = None
    mask: MaskProps = field(default_factory=MaskProps)
    chroma: ChromaProps = field(default_factory=ChromaProps)
    blend: str = "normal"
    enabled: bool = True
    locked: bool = False
    color: str = ""                     # timeline tint (labels)
    volume_env: List[Tuple[float, float]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    # -- geometry ---------------------------------------------------------- #
    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def src_duration(self) -> float:
        """Source media consumed, in source seconds."""
        if self.kind in ("text", "sticker", "shape", "adjustment"):
            return self.duration
        return self.duration * self.speed

    def contains(self, t: float) -> bool:
        return self.start - 1e-9 <= t <= self.end + 1e-9

    def local_time(self, timeline_t: float) -> float:
        return max(0.0, min(self.duration, timeline_t - self.start))

    def src_time(self, timeline_t: float) -> float:
        """Source timestamp for a timeline moment."""
        local = self.local_time(timeline_t)
        if self.freeze:
            return self.src_in + self.freeze_at
        if self.speed_curve and self.speed_curve.is_ramp():
            return self.src_in + self._ramped_offset(local)
        return self.src_in + local * self.speed

    def _ramped_offset(self, local: float) -> float:
        """Source seconds consumed after ``local`` timeline seconds of a ramp."""
        if self.duration <= 0:
            return 0.0
        segs = 60
        total = 0.0
        step = self.duration / segs
        elapsed = 0.0
        for i in range(segs):
            p = (i + 0.5) / segs
            seg_len = min(step, local - elapsed)
            if seg_len <= 0:
                break
            total += seg_len * self.speed_curve.speed_at(p)
            elapsed += seg_len
        return total

    def anim(self, prop: str, t_local: float) -> Optional[float]:
        keys = self.animations.get(prop)
        if not keys:
            return None
        return eval_keyframes(keys, t_local)

    def has_animation(self, prop: str) -> bool:
        return bool(self.animations.get(prop))

    def prop_at(self, prop: str, t_local: float) -> float:
        base = getattr(self.transform, prop, 0.0)
        v = self.anim(prop, t_local)
        return base if v is None else v

    def keyframes(self, prop: str) -> List[Keyframe]:
        return self.animations.setdefault(prop, [])

    def set_keyframe(self, prop: str, t_local: float, value: float,
                     easing: str = "ease_in_out") -> Keyframe:
        keys = self.animations.setdefault(prop, [])
        for k in keys:
            if abs(k.t - t_local) < 1e-3:
                k.value = value
                k.easing = easing
                return k
        kf = Keyframe(round(t_local, 4), value, easing)
        keys.append(kf)
        keys.sort(key=lambda k: k.t)
        return kf

    def remove_keyframe(self, prop: str, t_local: float) -> bool:
        keys = self.animations.get(prop)
        if not keys:
            return False
        before = len(keys)
        self.animations[prop] = [k for k in keys if abs(k.t - t_local) > 1e-3]
        if not self.animations[prop]:
            del self.animations[prop]
        return len(self.animations.get(prop, [])) != before

    def clear_animation(self, prop: str) -> None:
        self.animations.pop(prop, None)

    def animated_props(self) -> List[str]:
        return sorted(self.animations.keys())

    # -- effects ----------------------------------------------------------- #
    def add_filter(self, kind: str, params: Optional[Dict[str, float]] = None) -> FilterInstance:
        fx = FilterInstance(kind, dict(params or {}))
        self.filters.append(fx)
        return fx

    def find_filter(self, kind: str) -> Optional[FilterInstance]:
        for fx in self.filters:
            if fx.kind == kind:
                return fx
        return None

    def remove_filter(self, fx_id: str) -> None:
        self.filters = [f for f in self.filters if f.id != fx_id]

    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.kind == "text" and self.text:
            first = self.text.text.strip().splitlines()[0] if self.text.text.strip() else "Text"
            return first[:28]
        if self.path:
            import os
            return os.path.basename(self.path)[:32]
        return self.kind.title()

    # -- serialisation ----------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id, "kind": self.kind, "track": self.track_id,
            "start": round(self.start, 5), "duration": round(self.duration, 5),
            "src_in": round(self.src_in, 5), "speed": self.speed,
            "name": self.name, "asset_id": self.asset_id, "path": self.path,
            "reverse": self.reverse, "freeze": self.freeze,
            "freeze_at": self.freeze_at,
            "transform": self.transform.to_dict(),
            "blend": self.blend, "enabled": self.enabled, "locked": self.locked,
            "color": self.color,
        }
        if self.speed_curve:
            d["speed_curve"] = self.speed_curve.to_dict()
        if self.animations:
            d["animations"] = {k: [kf.to_dict() for kf in v]
                               for k, v in self.animations.items()}
        if self.filters:
            d["filters"] = [f.to_dict() for f in self.filters]
        if self.transition_in:
            d["transition_in"] = self.transition_in.to_dict()
        if self.transition_out:
            d["transition_out"] = self.transition_out.to_dict()
        d["audio"] = self.audio.to_dict()
        if self.audio_animations:
            d["audio_animations"] = {k: [kf.to_dict() for kf in v]
                                     for k, v in self.audio_animations.items()}
        if self.text:
            d["text"] = self.text.to_dict()
        if self.mask.shape != "none":
            d["mask"] = self.mask.to_dict()
        if self.chroma.enabled:
            d["chroma"] = self.chroma.to_dict()
        if self.volume_env:
            d["volume_env"] = [[round(a, 4), round(b, 4)] for a, b in self.volume_env]
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Clip":
        c = cls(kind=str(d.get("kind", "video")))
        c.id = d.get("id") or new_id("clip")
        c.track_id = d.get("track", "")
        c.start = float(d.get("start", 0.0))
        c.duration = max(0.02, float(d.get("duration", 1.0)))
        c.src_in = float(d.get("src_in", 0.0))
        c.speed = max(0.05, float(d.get("speed", 1.0)))
        c.name = d.get("name", "")
        c.asset_id = d.get("asset_id", "")
        c.path = d.get("path", "")
        c.reverse = bool(d.get("reverse", False))
        c.freeze = bool(d.get("freeze", False))
        c.freeze_at = float(d.get("freeze_at", 0.0))
        c.speed_curve = SpeedCurve.from_dict(d.get("speed_curve"))
        c.transform = Transform.from_dict(d.get("transform", {}))
        c.animations = {k: [Keyframe.from_dict(x) for x in v]
                        for k, v in d.get("animations", {}).items()}
        c.filters = [FilterInstance.from_dict(x) for x in d.get("filters", [])]
        if d.get("transition_in"):
            c.transition_in = Transition.from_dict(d["transition_in"])
        if d.get("transition_out"):
            c.transition_out = Transition.from_dict(d["transition_out"])
        c.audio = AudioProps.from_dict(d.get("audio", {}))
        c.audio_animations = {k: [Keyframe.from_dict(x) for x in v]
                              for k, v in d.get("audio_animations", {}).items()}
        if d.get("text"):
            c.text = TextProps.from_dict(d["text"])
        c.mask = MaskProps.from_dict(d.get("mask", {}))
        c.chroma = ChromaProps.from_dict(d.get("chroma", {}))
        c.blend = str(d.get("blend", "normal"))
        c.enabled = bool(d.get("enabled", True))
        c.locked = bool(d.get("locked", False))
        c.color = d.get("color", "")
        c.volume_env = [(float(a), float(b)) for a, b in d.get("volume_env", [])]
        c.meta = dict(d.get("meta", {}))
        return c

    def clone(self, offset: float = 0.0) -> "Clip":
        d = self.to_dict()
        d["id"] = new_id("clip")
        c = Clip.from_dict(d)
        c.start += offset
        return c


# --------------------------------------------------------------------------- #
#  Track
# --------------------------------------------------------------------------- #

TRACK_KINDS = ("video", "overlay", "text", "audio", "sticker")


@dataclass
class Track:
    kind: str = "video"
    name: str = ""
    id: str = field(default_factory=lambda: new_id("trk"))
    height: int = 74
    muted: bool = False
    locked: bool = False
    hidden: bool = False
    solo: bool = False
    volume: float = 1.0
    expanded: bool = True
    clips: List[Clip] = field(default_factory=list)

    def sort(self) -> None:
        self.clips.sort(key=lambda c: (c.start, c.id))

    def clip_at(self, t: float, ignore_locked: bool = False) -> Optional[Clip]:
        for c in self.clips:
            if c.contains(t) and (not ignore_locked or not c.locked):
                return c
        return None

    def clips_in(self, a: float, b: float) -> List[Clip]:
        return [c for c in self.clips if c.end > a and c.start < b]

    @property
    def end(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    def to_dict(self) -> Dict[str, Any]:
        self.sort()
        return {"id": self.id, "kind": self.kind, "name": self.name,
                "height": self.height, "muted": self.muted,
                "locked": self.locked, "hidden": self.hidden,
                "solo": self.solo, "volume": self.volume,
                "clips": [c.to_dict() for c in self.clips]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Track":
        t = cls(kind=str(d.get("kind", "video")))
        t.id = d.get("id") or new_id("trk")
        t.name = d.get("name", "")
        t.height = int(d.get("height", 74))
        t.muted = bool(d.get("muted", False))
        t.locked = bool(d.get("locked", False))
        t.hidden = bool(d.get("hidden", False))
        t.solo = bool(d.get("solo", False))
        t.volume = float(d.get("volume", 1.0))
        t.clips = [Clip.from_dict(x) for x in d.get("clips", [])]
        for c in t.clips:
            c.track_id = t.id
        t.sort()
        return t

    def label(self) -> str:
        if self.name:
            return self.name
        return {"video": "Video", "overlay": "Overlay", "text": "Text",
                "audio": "Audio", "sticker": "Sticker"}.get(self.kind, self.kind.title())


# --------------------------------------------------------------------------- #
#  Media asset
# --------------------------------------------------------------------------- #

@dataclass
class MediaAsset:
    path: str
    id: str = field(default_factory=lambda: new_id("asset"))
    name: str = ""
    kind: str = "video"
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    rotation: int = 0
    has_audio: bool = False
    sample_rate: int = 0
    channels: int = 0
    size_bytes: int = 0
    added: float = field(default_factory=time.time)
    proxy_path: str = ""
    peaks: List[float] = field(default_factory=list)
    missing: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "path": self.path, "name": self.name, "kind": self.kind,
            "duration": self.duration, "width": self.width, "height": self.height,
            "fps": self.fps, "rotation": self.rotation,
            "has_audio": self.has_audio, "sample_rate": self.sample_rate,
            "channels": self.channels, "size_bytes": self.size_bytes,
            "added": self.added, "proxy_path": self.proxy_path,
            "missing": self.missing,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MediaAsset":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_info(cls, info, path: str) -> "MediaAsset":
        import os
        return cls(path=path, name=os.path.basename(path), kind=info.kind,
                   duration=info.duration, width=info.width, height=info.height,
                   fps=info.fps or 30.0, rotation=info.rotation,
                   has_audio=info.has_audio, sample_rate=info.sample_rate,
                   channels=info.channels, size_bytes=info.size_bytes)


# --------------------------------------------------------------------------- #
#  Project
# --------------------------------------------------------------------------- #

EXPORT_PRESETS: List[Tuple[str, str, int, str]] = [
    ("1080p", "1080p", 12000, "high"),
    ("720p", "720p", 8000, "high"),
    ("480p", "480p", 4000, "medium"),
    ("4K", "2160p", 40000, "high"),
    ("Source", "source", 0, "high"),
]


@dataclass
class ProjectSettings:
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    sample_rate: int = 48000
    background: str = "#000000"
    background_path: str = ""
    ratio_name: str = "9:16  Vertical"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProjectSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


@dataclass
class Project:
    name: str = "Untitled project"
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    tracks: List[Track] = field(default_factory=list)
    assets: List[MediaAsset] = field(default_factory=list)
    markers: List[Dict[str, Any]] = field(default_factory=list)
    beats: List[float] = field(default_factory=list)
    beat_asset_id: str = ""
    id: str = field(default_factory=lambda: new_id("proj"))
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    editor_state: Dict[str, Any] = field(default_factory=dict)
    version: int = 1

    # -- construction ------------------------------------------------------ #
    @classmethod
    def create_default(cls, name: str = "Untitled project",
                       width: int = 1080, height: int = 1920,
                       fps: float = 30.0) -> "Project":
        p = cls(name=name)
        p.settings = ProjectSettings(width=width, height=height, fps=fps)
        p.ensure_tracks()
        return p

    def ensure_tracks(self) -> None:
        if not self.tracks:
            self.tracks = [
                Track(kind="video", name="Video 1", height=80),
                Track(kind="overlay", name="Overlay 1", height=66),
                Track(kind="text", name="Text 1", height=58),
                Track(kind="audio", name="Audio 1", height=62),
            ]

    # -- lookups ----------------------------------------------------------- #
    def track(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.id == track_id:
                return t
        return None

    def clip(self, clip_id: str) -> Optional[Clip]:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return c
        return None

    def clip_track(self, clip_id: str) -> Optional[Track]:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return t
        return None

    def asset(self, asset_id: str) -> Optional[MediaAsset]:
        for a in self.assets:
            if a.id == asset_id:
                return a
        return None

    def asset_by_path(self, path: str) -> Optional[MediaAsset]:
        for a in self.assets:
            if a.path == path:
                return a
        return None

    def all_clips(self) -> List[Clip]:
        out: List[Clip] = []
        for t in self.tracks:
            out.extend(t.clips)
        return out

    def clips_at(self, t: float) -> List[Clip]:
        out = []
        for tr in self.tracks:
            if tr.hidden:
                continue
            c = tr.clip_at(t)
            if c:
                out.append(c)
        return out

    def video_tracks(self) -> List[Track]:
        return [t for t in self.tracks if t.kind in ("video", "overlay", "sticker")]

    def audio_tracks(self) -> List[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    def text_tracks(self) -> List[Track]:
        return [t for t in self.tracks if t.kind == "text"]

    # -- timing ------------------------------------------------------------ #
    @property
    def duration(self) -> float:
        d = 0.0
        for t in self.tracks:
            d = max(d, t.end)
        return d

    def content_end(self) -> float:
        return self.duration

    # -- editing helpers ---------------------------------------------------- #
    def add_track(self, kind: str = "overlay", name: str = "",
                  index: Optional[int] = None) -> Track:
        base = {"video": "Video", "overlay": "Overlay", "text": "Text",
                "audio": "Audio", "sticker": "Sticker"}.get(kind, "Track")
        n = len([t for t in self.tracks if t.kind == kind]) + 1
        height = {"video": 80, "overlay": 66, "text": 58, "audio": 62,
                  "sticker": 62}.get(kind, 70)
        tr = Track(kind=kind, name=name or f"{base} {n}", height=height)
        if index is None:
            # video first, then overlay, then sticker, then text, then audio
            order = {"video": 0, "overlay": 1, "sticker": 2, "text": 3, "audio": 4}
            pos = len(self.tracks)
            for i, t in enumerate(self.tracks):
                if order.get(t.kind, 9) > order.get(kind, 9):
                    pos = i
                    break
            self.tracks.insert(pos, tr)
        else:
            self.tracks.insert(max(0, min(index, len(self.tracks))), tr)
        return tr

    def remove_track(self, track_id: str) -> bool:
        before = len(self.tracks)
        self.tracks = [t for t in self.tracks if t.id != track_id]
        return len(self.tracks) != before

    def add_clip(self, clip: Clip, track: Optional[Track] = None) -> Clip:
        if track is None:
            track = self.track(clip.track_id)
        if track is None:
            track = self.ensure_track_for(clip.kind)
        clip.track_id = track.id
        track.clips.append(clip)
        track.sort()
        return clip

    def ensure_track_for(self, kind: str) -> Track:
        want = {"video": "video", "image": "overlay", "audio": "audio",
                "text": "text", "sticker": "sticker", "shape": "overlay",
                "adjustment": "overlay"}.get(kind, "overlay")
        for t in self.tracks:
            if t.kind == want:
                return t
        return self.add_track(want)

    def remove_clip(self, clip_id: str) -> Optional[Clip]:
        for t in self.tracks:
            for i, c in enumerate(t.clips):
                if c.id == clip_id:
                    return t.clips.pop(i)
        return None

    def move_clip(self, clip_id: str, new_start: float,
                  new_track_id: Optional[str] = None) -> None:
        clip = self.clip(clip_id)
        if clip is None:
            return
        if new_track_id and new_track_id != clip.track_id:
            old = self.track(clip.track_id)
            dest = self.track(new_track_id)
            if old and dest:
                old.clips = [c for c in old.clips if c.id != clip_id]
                clip.track_id = dest.id
                dest.clips.append(clip)
                dest.sort()
        clip.start = max(0.0, new_start)

    def resolve_overlaps(self, track_id: str, keep_id: str = "") -> None:
        """Push clips forward so a track never has two clips on one frame."""
        tr = self.track(track_id)
        if tr is None:
            return
        tr.sort()
        cursor = 0.0
        for c in tr.clips:
            if c.id == keep_id:
                cursor = c.end
                continue
            if c.start < cursor - 1e-6:
                c.start = cursor
            cursor = max(cursor, c.end)
        tr.sort()

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": "vn-editor-project",
            "version": self.version,
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "modified": self.modified,
            "settings": self.settings.to_dict(),
            "assets": [a.to_dict() for a in self.assets],
            "tracks": [t.to_dict() for t in self.tracks],
            "markers": self.markers,
            "beats": self.beats,
            "beat_asset_id": self.beat_asset_id,
            "editor_state": self.editor_state,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Project":
        p = cls(name=d.get("name", "Untitled project"))
        p.id = d.get("id") or new_id("proj")
        p.settings = ProjectSettings.from_dict(d.get("settings", {}))
        p.assets = [MediaAsset.from_dict(a) for a in d.get("assets", [])]
        p.tracks = [Track.from_dict(t) for t in d.get("tracks", [])]
        p.markers = list(d.get("markers", []))
        p.beats = [float(x) for x in d.get("beats", [])]
        p.beat_asset_id = d.get("beat_asset_id", "")
        p.editor_state = dict(d.get("editor_state", {}))
        p.created = float(d.get("created", time.time()))
        p.modified = float(d.get("modified", time.time()))
        p.version = int(d.get("version", 1))
        p.ensure_tracks()
        return p

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.to_dict())

    @classmethod
    def restore(cls, snap: Dict[str, Any]) -> "Project":
        return cls.from_dict(copy.deepcopy(snap))


def clip_z_order(project: Project) -> List[Track]:
    """Tracks from back (drawn first) to front (drawn last)."""
    order = {"video": 0, "overlay": 1, "sticker": 2, "text": 3}
    return sorted([t for t in project.tracks if t.kind != "audio"],
                  key=lambda t: order.get(t.kind, 5))
