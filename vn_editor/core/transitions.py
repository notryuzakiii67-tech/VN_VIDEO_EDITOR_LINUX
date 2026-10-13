"""Clip transitions.

FFmpeg's ``xfade`` filter exposes 59 real transitions on this build; every
entry below maps onto one of them (verified against the local binary), plus a
small number of genuine composites built from ``fade``/``rotate`` + ``xfade``
which are listed as such.

Semantics: a transition consumes a *handle* — the outgoing clip supplies a few
extra frames past its out-point (read from the source when available, otherwise
its last frame is held) and dissolves them into the incoming clip's opening
frames.  Nothing on the timeline moves, which is what the mobile editors do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .expr import num


@dataclass
class TransitionSpec:
    key: str
    label: str
    category: str = "Basic"
    xfade: Optional[str] = "fade"
    custom: Optional[str] = None
    keywords: str = ""


TRANSITIONS: Dict[str, TransitionSpec] = {}
ORDER: List[str] = []


def _t(key: str, label: str, category: str, xfade: Optional[str] = None,
       custom: Optional[str] = None, keywords: str = "") -> None:
    if xfade is None and custom is None:
        xfade = "fade"
    TRANSITIONS[key] = TransitionSpec(key, label, category, xfade, custom,
                                      keywords)
    ORDER.append(key)


# --------------------------------------------------------------------------- #
#  Real xfade catalogue, grouped the way a mobile editor groups them
# --------------------------------------------------------------------------- #

_t("none", "None (hard cut)", "Basic", custom="cut", keywords="cut none")

for _k, _l, _x in [
    ("fade", "Fade", "fade"),
    ("dissolve", "Dissolve", "dissolve"),
    ("fadefast", "Fade Fast", "fadefast"),
    ("fadeslow", "Fade Slow", "fadeslow"),
    ("fadeblack", "Fade to Black", "fadeblack"),
    ("fadewhite", "Fade to White", "fadewhite"),
    ("fadegrays", "Fade to Grays", "fadegrays"),
    ("distance", "Distance", "distance"),
    ("zoomin", "Zoom In", "zoomin"),
    ("pixelize", "Pixelize", "pixelize"),
    ("hblur", "Blur", "hblur"),
    ("circlecrop", "Circle Crop", "circlecrop"),
    ("rectcrop", "Rectangle Crop", "rectcrop"),
    ("radial", "Radial", "radial"),
    ("squeezeh", "Squeeze Horizontal", "squeezeh"),
    ("squeezev", "Squeeze Vertical", "squeezev"),
]:
    _t(_k, _l, "Basic", _x)

for _k, _l, _x in [
    ("wipeleft", "Wipe Left", "wipeleft"),
    ("wiperight", "Wipe Right", "wiperight"),
    ("wipeup", "Wipe Up", "wipeup"),
    ("wipedown", "Wipe Down", "wipedown"),
    ("wipetl", "Wipe from Top-Left", "wipetl"),
    ("wipetr", "Wipe from Top-Right", "wipetr"),
    ("wipebl", "Wipe from Bottom-Left", "wipebl"),
    ("wipebr", "Wipe from Bottom-Right", "wipebr"),
    ("slideleft", "Slide Left", "slideleft"),
    ("slideright", "Slide Right", "slideright"),
    ("slideup", "Slide Up", "slideup"),
    ("slidedown", "Slide Down", "slidedown"),
    ("smoothleft", "Smooth Left", "smoothleft"),
    ("smoothright", "Smooth Right", "smoothright"),
    ("smoothup", "Smooth Up", "smoothup"),
    ("smoothdown", "Smooth Down", "smoothdown"),
    ("coverleft", "Cover Left", "coverleft"),
    ("coverright", "Cover Right", "coverright"),
    ("coverup", "Cover Up", "coverup"),
    ("coverdown", "Cover Down", "coverdown"),
    ("revealleft", "Reveal Left", "revealleft"),
    ("revealright", "Reveal Right", "revealright"),
    ("revealup", "Reveal Up", "revealup"),
    ("revealdown", "Reveal Down", "revealdown"),
]:
    _t(_k, _l, "Slide & Wipe", _x)

for _k, _l, _x in [
    ("circleopen", "Circle Open", "circleopen"),
    ("circleclose", "Circle Close", "circleclose"),
    ("vertopen", "Vertical Open", "vertopen"),
    ("vertclose", "Vertical Close", "vertclose"),
    ("horzopen", "Horizontal Open", "horzopen"),
    ("horzclose", "Horizontal Close", "horzclose"),
    ("diagtl", "Diagonal Top-Left", "diagtl"),
    ("diagtr", "Diagonal Top-Right", "diagtr"),
    ("diagbl", "Diagonal Bottom-Left", "diagbl"),
    ("diagbr", "Diagonal Bottom-Right", "diagbr"),
    ("hlslice", "Horizontal Slices", "hlslice"),
    ("hrslice", "Reverse Slices", "hrslice"),
    ("vuslice", "Vertical Slices", "vuslice"),
    ("vdslice", "Downward Slices", "vdslice"),
    ("hlwind", "Wind Left", "hlwind"),
    ("hrwind", "Wind Right", "hrwind"),
    ("vuwind", "Wind Up", "vuwind"),
    ("vdwind", "Wind Down", "vdwind"),
]:
    _t(_k, _l, "Shapes", _x)


# --------------------------------------------------------------------------- #
#  Genuine composites (built from fade / rotate + xfade)
# --------------------------------------------------------------------------- #

_t("spin_dissolve", "Spin Dissolve", "Motion", custom="spin_dissolve",
   keywords="spin rotate")
_t("spin_reverse", "Spin Reverse", "Motion", custom="spin_reverse",
   keywords="spin rotate")
_t("dip_black", "Dip to Black", "Motion", custom="dip_black",
   keywords="dip black")
_t("dip_white", "Dip to White", "Motion", custom="dip_white",
   keywords="dip white")
_t("flash", "Flash", "Motion", custom="flash", keywords="flash white")
_t("shake_dissolve", "Shake Dissolve", "Motion", custom="shake_dissolve",
   keywords="shake earthquake")

TRANSITION_CATEGORIES = ["Basic", "Slide & Wipe", "Shapes", "Motion"]

#: cheap choices highlighted for slower machines
LIGHT_TRANSITIONS = {
    "none", "fade", "dissolve", "fadefast", "wipeleft", "wiperight",
    "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
    "circleopen", "circleclose", "dip_black", "dip_white", "flash",
}


def label(key: str) -> str:
    spec = TRANSITIONS.get(key)
    return spec.label if spec else key.replace("_", " ").title()


def xfade_name(key: str) -> Optional[str]:
    spec = TRANSITIONS.get(key)
    return spec.xfade if spec else "fade"


def is_custom(key: str) -> bool:
    spec = TRANSITIONS.get(key)
    return bool(spec and spec.custom and spec.custom != "cut")


def all_keys() -> List[str]:
    return list(ORDER)


def by_category() -> Dict[str, List[TransitionSpec]]:
    out: Dict[str, List[TransitionSpec]] = {}
    for k in ORDER:
        s = TRANSITIONS[k]
        out.setdefault(s.category, []).append(s)
    return out


def build(key: str, offset: float, duration: float, a: str, b: str,
          out: str, counter, width: int = 0, height: int = 0,
          fps: float = 30.0) -> Optional[str]:
    """Filtergraph fragment merging labelled streams ``a`` and ``b``.

    ``offset`` is where the transition starts, measured in stream A's timeline.
    """
    spec = TRANSITIONS.get(key or "fade")
    if spec is None:
        spec = TRANSITIONS["fade"]
    d = max(0.04, float(duration))
    off = max(0.0, float(offset))

    if spec.custom == "cut" or spec.xfade is None and spec.custom is None:
        return f"[{b}]null[{out}]"

    if spec.custom is None:
        return (f"[{a}][{b}]xfade=transition={spec.xfade}:"
                f"duration={num(d, 4)}:offset={num(off, 4)}[{out}]")

    name = spec.custom

    if name in ("dip_black", "dip_white", "flash"):
        color = {"dip_black": "black", "dip_white": "white",
                 "flash": "white"}[name]
        half = d / 2.0
        a1, b1, a2, b2 = counter(), counter(), counter(), counter()
        return (
            f"[{a}]fade=t=out:st={num(off, 4)}:d={num(half, 4)}:color={color}[{a1}];"
            f"[{b}]fade=t=in:st=0:d={num(half, 4)}:color={color}[{b1}];"
            f"[{a1}]setpts=PTS-STARTPTS[{a2}];"
            f"[{b1}]setpts=PTS-STARTPTS+{num(d, 4)}/TB[{b2}];"
            f"[{a2}][{b2}]concat=n=2:v=1:a=0[{out}]")

    if name in ("spin_dissolve", "spin_reverse"):
        sign = -1.0 if name == "spin_reverse" else 1.0
        a1, a2, b1 = counter(), counter(), counter()
        return (
            f"[{a}]rotate=a='{num(sign * 0.55, 5)}*"
            f"max(0,t-{num(off, 4)})':ow='iw':oh='ih':c=none[{a1}];"
            f"[{a1}]null[{a2}];"
            f"[{b}]rotate=a='{num(-sign * 0.55, 5)}*"
            f"max(0,{num(d, 4)}-t)':ow='iw':oh='ih':c=none[{b1}];"
            f"[{a2}][{b1}]xfade=transition=dissolve:duration={num(d, 4)}:"
            f"offset={num(off, 4)}[{out}]")

    if name == "shake_dissolve":
        # xfade needs both sides at the same size, so the shrunken frame is
        # cropped on both branches and only one of them wobbles.
        a1, b1 = counter(), counter()
        box = "crop=w='iw-8':h='ih-8'"
        return (
            f"[{a}]{box}:"
            f"x='4+4*sin(40*(t-{num(off, 4)}))':"
            f"y='4+4*cos(37*(t-{num(off, 4)}))'[{a1}];"
            f"[{b}]{box}:x=4:y=4[{b1}];"
            f"[{a1}][{b1}]xfade=transition=dissolve:duration={num(d, 4)}:"
            f"offset={num(off, 4)}[{out}]")

    return (f"[{a}][{b}]xfade=transition=fade:duration={num(d, 4)}:"
            f"offset={num(off, 4)}[{out}]")


def default_duration(key: str) -> float:
    spec = TRANSITIONS.get(key)
    if spec is None:
        return 0.5
    if spec.category == "Motion":
        return 0.7
    if spec.category == "Shapes":
        return 0.5
    return 0.5
