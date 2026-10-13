"""Text, titles, stickers and captions rendered through libass.

Why ASS instead of ``drawtext``: the static FFmpeg build has no FreeType
``drawtext`` filter at all, and libass is *better* for a video editor anyway —
real font selection, outlines, shadows, opacity, rotation and animated
position at native resolution (so text stays vector-sharp when the layer is
scaled up).

Animation strategy: the *clip keyframe system* drives text motion.  Keyframes
on x/y/scale/rotation/opacity are compiled into a chain of ASS events, one per
keyframe interval, using ``\\move`` and ``\\t`` so libass interpolates at render
resolution.  That means a text animation is a real, editable animation — the
user can open the keyframe panel and tweak it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .expr import ass_color, num
from .model import (Clip, Project, TextProps, Transform, eval_keyframes, ease,
                    Keyframe)

# --------------------------------------------------------------------------- #
#  Font management
# --------------------------------------------------------------------------- #

FONT_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"

FONT_FILES: Dict[str, str] = {
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Anton": "Anton-Regular.ttf",
    "Oswald": "Oswald.ttf",
    "Inter": "Inter.ttf",
    "Montserrat": "Montserrat.ttf",
    "Bungee": "Bungee-Regular.ttf",
    "Poppins": "Poppins-Regular.ttf",
    "Poppins Bold": "Poppins-Bold.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Righteous": "Righteous-Regular.ttf",
    "Fredoka": "Fredoka.ttf",
    "Caveat": "Caveat.ttf",
    "Teko": "Teko.ttf",
    "Titan One": "TitanOne-Regular.ttf",
    "Rubik": "Rubik.ttf",
    "Roboto": "Roboto.ttf",
    "Noto Sans": "NotoSans.ttf",
    "Space Grotesk": "SpaceGrotesk.ttf",
    "Alfa Slab One": "AlfaSlabOne-Regular.ttf",
    "Creepster": "Creepster-Regular.ttf",
    "Bowlby One": "BowlbyOne-Regular.ttf",
}

#: fonts that read well as big "POV:" style captions
CAPTION_FONTS = ["Bebas Neue", "Anton", "Archivo Black", "Oswald", "Teko",
                 "Bungee", "Alfa Slab One", "Titan One", "Bowlby One",
                 "Poppins Bold", "Montserrat", "Righteous", "Luckiest Guy"]

_SYSTEM_FONTS = ["DejaVu Sans", "Liberation Sans", "Ubuntu", "Noto Sans",
                 "FreeSans", "Arial", "Verdana", "Impact", "Comic Sans MS"]


def available_fonts() -> List[str]:
    out = list(FONT_FILES.keys())
    return out + _SYSTEM_FONTS


def font_path(name: str) -> Optional[Path]:
    fn = FONT_FILES.get(name)
    if not fn:
        return None
    p = FONT_DIR / fn
    return p if p.exists() else None


_FC_CACHE: Optional[Path] = None


def fontconfig_file() -> Path:
    """A private fontconfig file so libass sees the bundled OFL fonts.

    Written once into the app cache; never touches the user's own fontconfig.
    """
    global _FC_CACHE
    if _FC_CACHE and _FC_CACHE.exists():
        return _FC_CACHE
    from .project import cache_dir
    d = cache_dir() / "fontconfig"
    d.mkdir(parents=True, exist_ok=True)
    cache = d / "fc-cache"
    cache.mkdir(parents=True, exist_ok=True)
    conf = d / "fonts.conf"
    dirs = [str(FONT_DIR)] + [
        "/usr/share/fonts", "/usr/local/share/fonts",
        str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts"),
    ]
    body = ["<?xml version=\"1.0\"?>",
            "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">",
            "<fontconfig>"]
    for x in dirs:
        body.append(f"  <dir>{x}</dir>")
    body.append(f"  <cachedir>{cache}</cachedir>")
    for name, fn in FONT_FILES.items():
        # make sure the family name inside the TTF is what we ask for
        body.append("  <match target=\"pattern\">"
                    f"<test name=\"family\"><string>{name}</string></test>"
                    "<edit name=\"family\" mode=\"assign\" binding=\"strong\">"
                    f"<string>{name}</string></edit></match>")
    body.append("</fontconfig>")
    try:
        conf.write_text("\n".join(body), "utf-8")
    except OSError:
        pass
    _FC_CACHE = conf
    return conf


# --------------------------------------------------------------------------- #
#  ASS script building
# --------------------------------------------------------------------------- #

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: {wrap}
ScaledBorderAndShadow: yes
YCbCr Matrix: None
Title: {title}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}
"""


def ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _style_line(name: str, font: str, size: float, primary: str,
                outline_col: str, back_col: str, bold: bool, italic: bool,
                underline: bool, spacing: float, border_style: int,
                outline: float, shadow: float, align: int,
                margin_v: int = 0, margin_l: int = 0, margin_r: int = 0) -> str:
    return (f"Style: {name},{font},{num(size)},{primary},&H000000FF,"
            f"{outline_col},{back_col},{1 if bold else 0},{1 if italic else 0},"
            f"{1 if underline else 0},0,100,100,{num(spacing)},0,{border_style},"
            f"{num(outline)},{num(shadow)},{align},{margin_l},{margin_r},"
            f"{margin_v},1")


@dataclass
class TextEvent:
    """One ASS dialogue line."""
    start: float
    end: float
    style: str
    text: str
    override: str = ""
    layer: int = 0

    def line(self) -> str:
        body = f"{{{self.override}}}" if self.override else ""
        txt = self.text.replace("\n", "\\N")
        return (f"Dialogue: {self.layer},{ass_time(self.start)},"
                f"{ass_time(self.end)},{self.style},,0,0,0,,{body}{txt}")


class AssBuilder:
    def __init__(self, width: int, height: int, title: str = "VN Studio") -> None:
        self.width = max(16, int(width))
        self.height = max(16, int(height))
        self.styles: List[str] = []
        self._style_names: set = set()
        self.events: List[TextEvent] = []

    def add_style(self, name: str, **kw) -> str:
        if name in self._style_names:
            return name
        defaults = dict(font="Bebas Neue", size=self.height * 0.06,
                        primary="&H00FFFFFF", outline_col="&H00000000",
                        back_col="&H80000000", bold=False, italic=False,
                        underline=False, spacing=0.0, border_style=1,
                        outline=0.0, shadow=0.0, align=5)
        defaults.update(kw)
        self.styles.append(_style_line(name, **defaults))
        self._style_names.add(name)
        return name

    def build(self, wrap: int = 2) -> str:
        return ASS_HEADER.format(
            w=self.width, h=self.height, wrap=wrap, title="VN Studio",
            styles="\n".join(self.styles) or _style_line(
                "Default", "Bebas Neue", self.height * 0.06, "&H00FFFFFF",
                "&H00000000", "&H80000000", False, False, False, 0.0, 1,
                0.0, 0.0, 5),
            events="\n".join(e.line() for e in self.events))


# --------------------------------------------------------------------------- #
#  Animation presets -> keyframes
# --------------------------------------------------------------------------- #

ANIM_PRESETS: Dict[str, Dict[str, Any]] = {
    "none": {},
    "fade_in": {"opacity": [(0.0, 0.0), (0.4, 1.0)]},
    "fade_out": {"opacity": [(0.0, 1.0), (0.4, 0.0)]},
    "fade_in_out": {"opacity": [(0.0, 0.0), (0.35, 1.0), (0.75, 1.0), (1.0, 0.0)]},
    "pop_in": {"scale": [(0.0, 0.4), (0.5, 1.12), (0.75, 1.0)], "opacity": [(0.0, 0.0), (0.2, 1.0)]},
    "pop_out": {"scale": [(0.0, 1.0), (0.35, 1.12), (1.0, 0.4)], "opacity": [(0.7, 1.0), (1.0, 0.0)]},
    "zoom_in": {"scale": [(0.0, 0.1), (0.6, 1.0)], "opacity": [(0.0, 0.0), (0.3, 1.0)]},
    "zoom_out": {"scale": [(0.0, 1.9), (0.6, 1.0)], "opacity": [(0.0, 0.0), (0.3, 1.0)]},
    "slide_left": {"x": [(0.0, 1.35), (0.5, 0.5)], "opacity": [(0.0, 0.0), (0.15, 1.0)]},
    "slide_right": {"x": [(0.0, -0.35), (0.5, 0.5)], "opacity": [(0.0, 0.0), (0.15, 1.0)]},
    "slide_up": {"y": [(0.0, 1.3), (0.5, 0.5)], "opacity": [(0.0, 0.0), (0.15, 1.0)]},
    "slide_down": {"y": [(0.0, -0.3), (0.5, 0.5)], "opacity": [(0.0, 0.0), (0.15, 1.0)]},
    "rise": {"y": [(0.0, 0.78), (1.0, 0.55)], "opacity": [(0.0, 0.0), (0.25, 1.0), (0.85, 1.0), (1.0, 0.0)]},
    "drop": {"y": [(0.0, 0.15), (0.6, 0.5)]},
    "spin_in": {"rotation": [(0.0, -180.0), (0.7, 0.0)], "scale": [(0.0, 0.3), (0.7, 1.0)]},
    "bounce_in": {"y": [(0.0, -0.2), (0.45, 0.58), (0.7, 0.46), (1.0, 0.5)]},
    "blur_in": {"opacity": [(0.0, 0.0), (0.5, 1.0)]},
    "pulse": {"scale": [(0.0, 1.0), (0.5, 1.1), (1.0, 1.0)]},
    "shake": {"x": [(0.0, 0.5), (0.1, 0.478), (0.2, 0.524), (0.3, 0.486),
                    (0.4, 0.516), (0.5, 0.492), (0.6, 0.512), (0.7, 0.494),
                    (0.85, 0.506), (1.0, 0.5)]},
    "wave": {"rotation": [(0.0, -8.0), (0.25, 8.0), (0.5, -8.0), (0.75, 8.0), (1.0, -8.0)]},
    "float": {"y": [(0.0, 0.5), (0.5, 0.465), (1.0, 0.5)]},
    "glitch": {"x": [(0.0, 0.5), (0.06, 0.47), (0.12, 0.53), (0.18, 0.5),
                     (0.5, 0.5), (0.56, 0.53), (0.62, 0.47), (0.7, 0.5)],
               "opacity": [(0.0, 1.0), (0.1, 0.35), (0.2, 1.0), (0.55, 0.4), (0.65, 1.0)]},
    "typewriter": {},
    "wipe_right": {"opacity": [(0.0, 0.0), (0.5, 1.0)]},
}


#: presets that need special handling (not pure keyframes)
SPECIAL_ANIMS = {"typewriter"}


def apply_text_animation(clip: Clip, animation: str, duration: float,
                         anim_in: float = 0.4, easing: str = "ease_in_out") -> None:
    """Turn a preset name into real keyframes on the clip.

    Writing keyframes (instead of a black-box effect) means the animation stays
    fully editable in the keyframe panel — matching VN's behaviour where adding
    an animation preset just pre-fills the animation curve.
    """
    preset = ANIM_PRESETS.get(animation or "none", {})
    if not preset:
        clip.meta["text_anim"] = animation if animation in SPECIAL_ANIMS else "none"
        return
    dur = max(0.05, duration)
    for prop, pairs in preset.items():
        base = getattr(clip.transform, prop, 0.0)
        keys: List[Keyframe] = []
        for frac, value in pairs:
            t = frac * dur
            if prop in ("x", "y") and value not in (0.0, 1.0):
                # keep the preset's *delta* relative to the user's placement
                if abs(value - 0.5) > 1e-6:
                    value = base + (value - 0.5)
            keys.append(Keyframe(round(t, 4), float(value), easing))
        if keys:
            clip.animations[prop] = keys
    clip.meta["text_anim"] = animation


def clear_text_animation(clip: Clip) -> None:
    for p in ("x", "y", "scale", "rotation", "opacity"):
        clip.animations.pop(p, None)
    clip.meta.pop("text_anim", None)


# --------------------------------------------------------------------------- #
#  Style / override generation
# --------------------------------------------------------------------------- #

def _align_code(align: str) -> int:
    return {"left": 4, "center": 5, "right": 6}.get(align, 5)


def _motion_override(t0: float, t1: float, clip: Clip,
                     props: Sequence[str], width: int, height: int) -> str:
    """Override block animating ``props`` between clip-local t0..t1.

    ASS coordinates live in PlayRes space (the canvas pixel grid), so the
    normalised transform values are multiplied by the canvas size here.
    """
    span = max(1e-3, t1 - t0)
    parts: List[str] = []
    tr = clip.transform

    def at(prop: str, t: float) -> float:
        base = getattr(tr, prop, 0.0)
        v = clip.anim(prop, t)
        return base if v is None else v

    # position
    if "x" in props or "y" in props:
        x0, x1 = at("x", t0), at("x", t1)
        y0, y1 = at("y", t0), at("y", t1)
        parts.append(f"\\move({num(x0 * width, 4)},{num(y0 * height, 4)},"
                     f"{num(x1 * width, 4)},{num(y1 * height, 4)},0,"
                     f"{int(span * 1000)})")
    else:
        parts.append(f"\\pos({num(at('x', t0) * width, 4)},"
                     f"{num(at('y', t0) * height, 4)})")

    def accel(prop: str) -> str:
        keys = clip.animations.get(prop)
        if not keys:
            return ""
        for k in keys:
            if t0 - 1e-6 <= k.t <= t1 + 1e-6:
                a = ease(k.easing, 0.5) - 0.5
                if abs(a) > 0.08:
                    return f",{num(max(0.2, min(5.0, 1.0 + a * 3.2)), 3)}"
        return ""

    # scale
    if "scale" in props:
        s0, s1 = at("scale", t0), at("scale", t1)
        parts.append(f"\\fscx{num(s0 * 100, 4)}\\fscy{num(s0 * 100, 4)}")
        if abs(s1 - s0) > 1e-4:
            parts.append(f"\\t(0,{int(span * 1000)}{accel('scale')},"
                         f"\\fscx{num(s1 * 100, 4)}\\fscy{num(s1 * 100, 4)})")
    # rotation
    if "rotation" in props:
        r0, r1 = at("rotation", t0), at("rotation", t1)
        parts.append(f"\\frz{num(r0, 4)}")
        if abs(r1 - r0) > 1e-4:
            parts.append(f"\\t(0,{int(span * 1000)}{accel('rotation')},\\frz{num(r1, 4)})")
    # opacity
    if "opacity" in props:
        o0, o1 = at("opacity", t0), at("opacity", t1)
        parts.append(f"\\alpha{ass_color('#000000', o0)[2:]}")
        if abs(o1 - o0) > 1e-4:
            parts.append(f"\\t(0,{int(span * 1000)}{accel('opacity')},"
                         f"\\alpha{ass_color('#000000', o1)[2:]})")
    return "".join(parts)


def _base_override(clip: Clip, style: str, t0: float) -> str:
    tp: TextProps = clip.text or TextProps()
    tr = clip.transform
    parts: List[str] = []
    if tp.bold:
        parts.append("\\b1")
    if tp.italic:
        parts.append("\\i1")
    if tp.underline:
        parts.append("\\u1")
    if tp.glow > 0:
        parts.append(f"\\blur{num(tp.glow * 0.6, 3)}")
    if tp.bg_enabled:
        parts.append(f"\\4a{ass_color('#000000', tp.bg_opacity)[2:]}")
    return "".join(parts)


def _union_times(clip: Clip, props: Sequence[str]) -> List[float]:
    ts = {0.0, clip.duration}
    for p in props:
        for k in clip.animations.get(p, []):
            ts.add(max(0.0, min(clip.duration, k.t)))
    return sorted(ts)


# --------------------------------------------------------------------------- #
#  Public: add a text clip to the builder
# --------------------------------------------------------------------------- #

def add_text_clip(builder: AssBuilder, clip: Clip, style_name: str,
                  timeline_offset: float = 0.0) -> None:
    """Emit ASS events for one text clip."""
    tp: TextProps = clip.text or TextProps()
    props = [p for p in ("x", "y", "scale", "rotation", "opacity")
             if clip.animations.get(p)]
    animated = bool(props)
    anim = clip.meta.get("text_anim", "none")

    if anim == "typewriter":
        _add_typewriter(builder, clip, style_name, timeline_offset, tp)
        return

    text = tp.text if tp.text else " "
    if not animated:
        ov = _base_override(clip, style_name, 0.0)
        ov += (f"\\pos({num(clip.transform.x * builder.width, 4)},"
               f"{num(clip.transform.y * builder.height, 4)})")
        ov += f"\\fscx{num(clip.transform.scale * 100, 3)}\\fscy{num(clip.transform.scale * 100, 3)}"
        if clip.transform.rotation:
            ov += f"\\frz{num(clip.transform.rotation, 3)}"
        if clip.transform.opacity < 0.999:
            ov += f"\\alpha{ass_color('#000000', clip.transform.opacity)[2:]}"
        if tp.animation in ("fade_in", "fade_in_out") and not clip.animations.get("opacity"):
            ov += f"\\fad({int(tp.anim_in * 1000)},{int(tp.anim_out * 1000)})"
        builder.events.append(TextEvent(clip.start + timeline_offset,
                                        clip.end + timeline_offset,
                                        style_name, text, ov))
        return

    times = _union_times(clip, props)
    for a, b in zip(times, times[1:]):
        if b - a < 1e-4:
            continue
        ov = _base_override(clip, style_name, a)
        ov += _motion_override(a, b, clip, props, builder.width, builder.height)
        builder.events.append(TextEvent(
            clip.start + timeline_offset + a,
            clip.start + timeline_offset + b + 0.01,
            style_name, text, ov))
    # a tiny tail event so the last frame is drawn
    if times:
        ov = _base_override(clip, style_name, times[-1])
        ov += _motion_override(times[-1], clip.duration, clip, props,
                               builder.width, builder.height)
        builder.events.append(TextEvent(
            clip.start + timeline_offset + times[-1],
            clip.end + timeline_offset, style_name, text, ov))


def _add_typewriter(builder: AssBuilder, clip: Clip, style_name: str,
                    timeline_offset: float, tp: TextProps) -> None:
    text = tp.text or " "
    chars = max(1, len(text))
    steps = min(chars, 60)
    step_dur = clip.duration / steps
    for i in range(steps):
        shown = int(round((i + 1) * chars / steps))
        piece = text[:shown]
        ov = (f"\\pos({num(clip.transform.x * builder.width, 4)},"
              f"{num(clip.transform.y * builder.height, 4)})")
        if clip.transform.scale != 1.0:
            ov += (f"\\fscx{num(clip.transform.scale * 100, 3)}"
                   f"\\fscy{num(clip.transform.scale * 100, 3)}")
        builder.events.append(TextEvent(
            clip.start + timeline_offset + i * step_dur,
            clip.start + timeline_offset + (i + 1) * step_dur + 0.02,
            style_name, piece, ov))


def text_style_for(builder: AssBuilder, clip: Clip, index: int) -> str:
    """Create (or reuse) the ASS style for a text clip.

    The font size is auto-fitted to the clip's ``max_width`` fraction of the
    canvas using real TrueType metrics, so long captions never spill outside
    the frame — the same behaviour VN has.
    """
    tp: TextProps = clip.text or TextProps()
    name = f"T{index}"
    border_style = 3 if tp.bg_enabled else 1
    outline = tp.outline_width
    if tp.bg_enabled:
        outline = max(1.0, tp.bg_padding * 0.6)

    size = max(4.0, float(tp.size))
    avail = max(64.0, builder.width * max(0.2, min(1.0, tp.max_width)))
    avail -= max(0.0, outline) * 2.2 + max(0.0, tp.shadow) * 2.0
    try:
        from .fonts import fit_size, libass_scale
        size = fit_size(tp.text or " ", tp.font, size, avail,
                        tp.letter_spacing, min_size=builder.height * 0.012)
        # libass measures Fontsize as the font's ascender-descender span, while
        # TextProps.size is an em size: convert so the preview and the export
        # agree with the inspector.
        size *= libass_scale(tp.font)
    except Exception:
        pass

    builder.add_style(
        name,
        font=tp.font,
        size=size,
        primary=ass_color(tp.color, 1.0),
        outline_col=ass_color(tp.outline_color, 1.0),
        back_col=ass_color(tp.shadow_color, 0.85),
        bold=tp.bold, italic=tp.italic, underline=tp.underline,
        spacing=tp.letter_spacing,
        border_style=border_style,
        outline=outline,
        shadow=tp.shadow,
        align=_align_code(tp.align),
    )
    clip.meta["effective_size"] = size
    return name


# --------------------------------------------------------------------------- #
#  Stickers
# --------------------------------------------------------------------------- #

BUILTIN_STICKERS = [
    "arrow_up", "arrow_right", "arrow_curved", "speech", "thought", "burst",
    "star", "heart", "fire", "skull", "lightning", "question", "exclaim",
    "check", "cross", "circle_hl", "underline_hl", "crown", "money", "cry",
    "laugh", "cool", "shock", "wink", "angry", "diamond", "sparkle", "boom",
    "thumbs_up", "skull_cross", "sub_banner", "lower_third", "vs_badge",
    "mute_icon", "replay_icon", "new_icon", "clap", "point_left",
]


def sticker_dir() -> Path:
    from .project import data_dir
    d = data_dir() / "stickers"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
#  Captions
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  Stickers (vector, drawn through the same libass pass as text)
# --------------------------------------------------------------------------- #

STICKER_ANIMATIONS = {
    "none": "",
    "pop": lambda d: _pop_override(d),
    "pop_in": lambda d: _pop_override(d),
    "fade": lambda d: f"\\fad({int(d * 500)},{int(d * 500)})",
    "spin": lambda d: (f"\\frz-40\\t(0,{int(d * 1000)},"
                       f"\\frz0)"),
    "slide_left": lambda d: f"\\move(",
    "slide_right": lambda d: f"\\move(",
    "slide_up": lambda d: f"\\move(",
    "slide_down": lambda d: f"\\move(",
    "pulse": lambda d: (f"\\t(0,{int(d * 500)},\\fscx112\\fscy112)"
                        f"\\t({int(d * 500)},{int(d * 1000)},"
                        f"\\fscx100\\fscy100)"),
}


def _pop_override(d: float) -> str:
    a = int(round(d * 1000 * 0.55))
    b = int(round(d * 1000))
    return (f"\\fscx28\\fscy28\\t(0,{a},\\fscx107\\fscy107)"
            f"\\t({a},{b},\\fscx100\\fscy100)")


def _sticker_default_color(name: str) -> str:
    try:
        from .stickers import STICKER_CATALOG
        return STICKER_CATALOG.get(name, ("", "", "#ffffff", ""))[2]
    except Exception:
        return "#ffffff"


def _drawing_bbox(path: str) -> Tuple[float, float, float, float]:
    """Bounding box of an ASS drawing path."""
    xs: List[float] = []
    ys: List[float] = []
    tokens = path.replace("m", " m ").replace("l", " l ").replace(
        "b", " b ").replace("c", " c ").split()
    i = 0
    while i < len(tokens):
        t = tokens[i]
        try:
            if t in ("m", "l"):
                xs.append(float(tokens[i + 1]))
                ys.append(float(tokens[i + 2]))
                i += 3
            elif t == "b":
                for k in range(3):
                    xs.append(float(tokens[i + 1 + k * 2]))
                    ys.append(float(tokens[i + 2 + k * 2]))
                i += 7
            else:
                i += 1
        except (IndexError, ValueError):
            break
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _path_bbox(path: str) -> Tuple[float, float, float, float]:
    """Bounding box of an ASS drawing path.

    Control points count as much as on-curve points here: libass sizes a
    drawing from its recorded points, so using the same points keeps the
    emitted box in step with the one libass lays out.
    """
    xs: List[float] = []
    ys: List[float] = []
    tokens = path.replace("m", " m ").replace("l", " l ").replace(
        "b", " b ").replace("c", " c ").split()
    i = 0
    while i < len(tokens):
        t = tokens[i]
        try:
            if t in ("m", "l"):
                xs.append(float(tokens[i + 1]))
                ys.append(float(tokens[i + 2]))
                i += 3
            elif t == "b":
                for k in range(3):
                    xs.append(float(tokens[i + 1 + k * 2]))
                    ys.append(float(tokens[i + 2 + k * 2]))
                i += 7
            else:
                i += 1
        except (IndexError, ValueError):
            break
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _path_visual_bbox(path: str) -> Tuple[float, float, float, float]:
    """Bounding box sampled along the curves (the shape as it is drawn)."""
    xs: List[float] = []
    ys: List[float] = []
    cx = cy = 0.0
    tokens = path.replace("m", " m ").replace("l", " l ").replace(
        "b", " b ").replace("c", " c ").split()
    i = 0
    while i < len(tokens):
        t = tokens[i]
        try:
            if t in ("m", "l"):
                cx, cy = float(tokens[i + 1]), float(tokens[i + 2])
                xs.append(cx)
                ys.append(cy)
                i += 3
            elif t == "b":
                p1 = (float(tokens[i + 1]), float(tokens[i + 2]))
                p2 = (float(tokens[i + 3]), float(tokens[i + 4]))
                p3 = (float(tokens[i + 5]), float(tokens[i + 6]))
                for k in range(17):
                    u = k / 16.0
                    v = 1.0 - u
                    xs.append(v * v * v * cx + 3 * v * v * u * p1[0]
                              + 3 * v * u * u * p2[0] + u * u * u * p3[0])
                    ys.append(v * v * v * cy + 3 * v * v * u * p1[1]
                              + 3 * v * u * u * p2[1] + u * u * u * p3[1])
                cx, cy = p3
                i += 7
            else:
                i += 1
        except (IndexError, ValueError):
            break
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _transform_path(path: str, sx: float, sy: float, dx: float,
                    dy: float) -> str:
    """Scale then translate an ASS drawing path (affine, so beziers are safe)."""
    out: List[str] = []
    tokens = path.replace("m", " m ").replace("l", " l ").replace(
        "b", " b ").replace("c", " c ").split()
    i = 0
    while i < len(tokens):
        t = tokens[i]
        try:
            if t in ("m", "l"):
                out.append(f"{t} {num(float(tokens[i + 1]) * sx + dx, 2)} "
                           f"{num(float(tokens[i + 2]) * sy + dy, 2)}")
                i += 3
            elif t == "b":
                cs = []
                for k in range(3):
                    cs.append(num(float(tokens[i + 1 + k * 2]) * sx + dx, 2))
                    cs.append(num(float(tokens[i + 2 + k * 2]) * sy + dy, 2))
                out.append("b " + " ".join(cs))
                i += 7
            elif t == "c":
                out.append("c")
                i += 1
            else:
                out.append(t)
                i += 1
        except (IndexError, ValueError):
            break
    return " ".join(out)


# Calibrated against libass 0.17: a path normalised into the box
# (0, 0, size, size) lands with that box centred on ``\pos`` plus this offset,
# expressed as a fraction of ``size``.
STICKER_BOX_OFFSET = (0.5, 0.5)


def add_sticker_clip(builder: AssBuilder, clip: Clip, index: int,
                     timeline_offset: float = 0.0) -> None:
    """Draw one vector sticker as an ASS event.

    libass centres a drawing's bounding box (measured over every recorded point,
    bezier handles included) on ``\\pos``.  Each layer is therefore translated
    so that its own box centre lands on the clip's transform position, which
    keeps the body and the detail layer aligned without extra geometry.
    """
    from .stickers import sticker_drawing
    style_name = f"K{index}"
    canvas_w, canvas_h = float(builder.width), float(builder.height)
    base_size = float(clip.meta.get("sticker_size", 0.18)) * canvas_h
    size = base_size * max(0.02, float(clip.transform.scale))
    cx = float(clip.transform.x) * canvas_w
    cy = float(clip.transform.y) * canvas_h
    name = str(clip.meta.get("sticker", "star"))
    path, ink, ink_color = sticker_drawing(name, 0.0, 0.0, size)
    if not path.strip():
        return

    # libass draws a path at ``coord + pos - path_bbox_size/2``, verified
    # against libass 0.17.  Both layers therefore share one affine transform
    # (so their relative geometry is preserved) and each gets its own ``\pos``
    # derived from its own bounding box.
    x0, y0, x1, y1 = _path_bbox(path)
    bw, bh = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    k = size / max(bw, bh)
    base_dx = (size - bw * k) / 2.0 - x0 * k
    base_dy = (size - bh * k) / 2.0 - y0 * k
    # Control hulls bulge away from the drawn curve on some shapes.  Since
    # libass centres the *hull* box on \pos, the shared transform is nudged so
    # that the drawn shape is centred on the hull box centre instead.
    fitted = _transform_path(path, k, k, base_dx, base_dy)
    hx0, hy0, hx1, hy1 = _path_bbox(fitted)
    vx0, vy0, vx1, vy1 = _path_visual_bbox(fitted)
    base_dx += (hx0 + hx1) / 2.0 - (vx0 + vx1) / 2.0
    base_dy += (hy0 + hy1) / 2.0 - (vy0 + vy1) / 2.0

    def place(raw: str) -> Tuple[str, float, float]:
        out = _transform_path(raw, k, k, base_dx, base_dy)
        if raw is path or not ink:
            # the body defines the visual centre; nudge both layers so the
            # drawn shape (not its control hull) sits centred in the box
            pass
        nx0, ny0, _nx1, _ny1 = _path_bbox(out)
        return out, cx - nx0, cy - ny0

    body, body_px, body_py = place(path)
    detail, ink_px, ink_py = place(ink) if ink else ("", 0.0, 0.0)

    color = (clip.meta.get("sticker_color")
             or _sticker_default_color(name) or "#ffffff")
    outline_w = max(0.0, float(clip.meta.get("sticker_outline",
                                             max(1.2, size * 0.03))))
    builder.add_style(
        style_name,
        font="Bebas Neue", size=max(8.0, size),
        primary=ass_color(color, 1.0),
        outline_col=ass_color(clip.meta.get("sticker_outline_color",
                                            "#000000"), 1.0),
        back_col=ass_color("#000000", 0.75),
        bold=False, outline=outline_w,
        shadow=max(0.0, float(clip.meta.get("sticker_shadow", 0.0))),
        align=5, margin_l=0, margin_r=0, margin_v=0,
    )

    ov = f"\\an5\\pos({num(body_px, 2)},{num(body_py, 2)})"
    rot = float(clip.transform.rotation)
    if abs(rot) > 1e-3:
        ov += f"\\frz{num(rot, 2)}"
    anim = str(clip.meta.get("sticker_anim", "pop_in"))
    adur = max(0.05, float(clip.meta.get("sticker_anim_duration", 0.4)))
    spec = STICKER_ANIMATIONS.get(anim)
    if callable(spec):
        if anim.startswith("slide_"):
            dist = size * 1.2
            deltas = {"slide_left": (-dist, 0), "slide_right": (dist, 0),
                      "slide_up": (0, dist), "slide_down": (0, -dist)}
            ddx, ddy = deltas[anim]
            ov += (f"\\move({num(body_px + ddx, 2)},{num(body_py + ddy, 2)},"
                   f"{num(body_px, 2)},{num(body_py, 2)},0,"
                   f"{int(adur * 1000)})")
        else:
            ov += spec(adur)
    if clip.transform.opacity < 0.999:
        ov += f"\\alpha&H{int((1 - clip.transform.opacity) * 255):02X}&"

    builder.events.append(TextEvent(clip.start + timeline_offset,
                                    clip.end + timeline_offset, style_name,
                                    "{" + ov + "\\p1}" + body + "{\\p0}"))
    if detail and ink_color:
        # Detail strokes (eyes, mouths) are open paths: an invisible fill plus a
        # visible outline is how ASS draws a line.
        ink_style = f"{style_name}i"
        builder.add_style(
            ink_style, font="Bebas Neue", size=max(8.0, size),
            primary=ass_color(ink_color, 0.0),
            outline_col=ass_color(ink_color, 1.0),
            back_col=ass_color(ink_color, 0.0),
            bold=False, outline=max(1.0, size * 0.038), shadow=0.0, align=5,
            margin_l=0, margin_r=0, margin_v=0,
        )
        ov_ink = (f"\\an5\\pos({num(ink_px, 2)},{num(ink_py, 2)})"
                  + ov[len(f"\\an5\\pos({num(body_px, 2)},{num(body_py, 2)})"):])
        builder.events.append(TextEvent(clip.start + timeline_offset,
                                        clip.end + timeline_offset, ink_style,
                                        "{" + ov_ink + "\\p1}" + detail +
                                        "{\\p0}"))


def sticker_clips(project) -> List[Clip]:
    """Sticker clips that are drawn as vectors (no file on disk)."""
    out: List[Clip] = []
    for track in project.tracks:
        if track.hidden or track.kind not in ("sticker", "overlay", "text"):
            continue
        for c in track.clips:
            if c.kind == "sticker" and c.enabled and not c.path:
                out.append(c)
    out.sort(key=lambda c: c.start)
    return out


def add_caption_events(builder: AssBuilder, captions: Sequence[Dict[str, Any]],
                       style_name: str, offset: float = 0.0) -> None:
    for cap in captions:
        start = float(cap.get("start", 0.0)) + offset
        end = float(cap.get("end", start + 1.0)) + offset
        text = str(cap.get("text", "")).strip()
        if not text or end <= start:
            continue
        builder.events.append(TextEvent(start, end, style_name, text))
