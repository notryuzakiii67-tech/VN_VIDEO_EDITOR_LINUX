"""Filter / effect catalogue.

Each effect knows how to turn its parameters into FFmpeg filtergraph
fragments.  Parameters that FFmpeg can evaluate per frame (``eq``'s
brightness/contrast/gamma/saturation are ``<string>`` options with timeline
support) are emitted as *expressions* so they can be keyframed; parameters that
are numeric-only are animated with ``sendcmd`` when needed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .expr import color_to_ff, kf_expr, num
from .model import FilterInstance, Keyframe, eval_keyframes


@dataclass
class Param:
    key: str
    label: str
    default: float
    lo: float = 0.0
    hi: float = 1.0
    step: float = 0.01
    animatable: bool = False
    kind: str = "float"              # float | int | bool | color | choice | file
    choices: List[str] = field(default_factory=list)


@dataclass
class EffectSpec:
    key: str
    label: str
    category: str = "Basic"
    params: List[Param] = field(default_factory=list)
    build: Optional[Callable[[Dict[str, float], Dict[str, List[Keyframe]], float], List[str]]] = None
    description: str = ""
    needs: Tuple[str, ...] = ()

    def defaults(self) -> Dict[str, float]:
        return {p.key: p.default for p in self.params}


EFFECTS: Dict[str, EffectSpec] = {}
GRADES: Dict[str, Dict[str, float]] = {}
GRADE_ORDER: List[str] = []


def _register(spec: EffectSpec) -> EffectSpec:
    EFFECTS[spec.key] = spec
    return spec


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #

def _opt(value: str) -> str:
    """Wrap an expression for a filter option value."""
    return f"'{value}'"


def _pval(fx: Dict[str, float], key: str, d: float) -> float:
    try:
        return float(fx.get(key, d))
    except (TypeError, ValueError):
        return d


def _expr_for(key: str, fx: Dict[str, float], anims: Dict[str, List[Keyframe]],
              default: float, var: str = "t") -> str:
    keys = anims.get(key)
    if keys:
        return kf_expr(keys, default, var)
    return num(_pval(fx, key, default))


# --------------------------------------------------------------------------- #
#  Basic colour / tone  (built on eq, which supports expressions)
# --------------------------------------------------------------------------- #

def _build_basic(fx, anims, dur) -> List[str]:
    bright = _expr_for("brightness", fx, anims, 0.0)
    contrast = _expr_for("contrast", fx, anims, 1.0)
    sat = _expr_for("saturation", fx, anims, 1.0)
    gamma = _expr_for("gamma", fx, anims, 1.0)
    parts = [f"eq=brightness={_opt(bright)}:contrast={_opt(contrast)}:"
             f"saturation={_opt(sat)}:gamma={_opt(gamma)}"]
    return parts


_register(EffectSpec(
    "basic", "Brightness / Contrast", "Basic",
    [Param("brightness", "Brightness", 0.0, -1.0, 1.0, 0.01, True),
     Param("contrast", "Contrast", 1.0, 0.0, 3.0, 0.01, True),
     Param("saturation", "Saturation", 1.0, 0.0, 3.0, 0.01, True),
     Param("gamma", "Gamma", 1.0, 0.1, 4.0, 0.01, True)],
    _build_basic, "Tone and colour: the foundation of VN's Adjust panel"))

_register(EffectSpec(
    "brightness", "Brightness", "Basic",
    [Param("brightness", "Brightness", 0.0, -1.0, 1.0, 0.01, True)],
    _build_basic, "Simple brightness lift"))

_register(EffectSpec(
    "contrast", "Contrast", "Basic",
    [Param("contrast", "Contrast", 1.0, 0.0, 3.0, 0.01, True)],
    _build_basic, "Add or remove punch"))

_register(EffectSpec(
    "saturation", "Saturation", "Basic",
    [Param("saturation", "Saturation", 1.0, 0.0, 3.0, 0.01, True)],
    _build_basic, "Colour intensity, 0 gives black and white"))

_register(EffectSpec(
    "exposure", "Exposure", "Basic",
    [Param("exposure", "Exposure", 0.0, -1.0, 1.0, 0.01, True),
     Param("contrast", "Contrast", 1.0, 0.0, 3.0, 0.01, True)],
    lambda fx, a, d: _build_basic({"brightness": _pval(fx, "exposure", 0.0) * 0.6,
                                   "contrast": _pval(fx, "contrast", 1.0)},
                                  {"brightness": a.get("exposure", [])}, d),
    "Exposure with gamma compensation"))


def _build_temperature(fx, anims, dur) -> List[str]:
    t = _pval(fx, "temperature", 0.0)
    if abs(t) < 1e-4:
        return []
    temp = 6500 + t * 2500
    temp = max(1500.0, min(15000.0, temp))
    return [f"colortemperature=temperature={num(temp)}:mix=1"]


_register(EffectSpec(
    "temperature", "Temperature", "Basic",
    [Param("temperature", "Warm / Cool", 0.0, -1.0, 1.0, 0.01)],
    _build_temperature, "Warm and cool white balance"))

_register(EffectSpec(
    "tint", "Tint", "Basic",
    [Param("tint", "Magenta / Green", 0.0, -1.0, 1.0, 0.01)],
    lambda fx, a, d: (
        [f"colorbalance=gm={num(-_pval(fx, 'tint', 0.0) * 0.25)}:"
         f"rm={num(_pval(fx, 'tint', 0.0) * 0.12)}:bm={num(_pval(fx, 'tint', 0.0) * 0.12)}"]
        if abs(_pval(fx, "tint", 0.0)) > 1e-4 else []),
    "Green / magenta tint"))

_register(EffectSpec(
    "hue", "Hue", "Basic",
    [Param("hue", "Hue shift", 0.0, -180.0, 180.0, 1.0),
     Param("saturation", "Saturation", 1.0, 0.0, 3.0, 0.01, True)],
    lambda fx, a, d: [f"hue=h={num(_pval(fx, 'hue', 0.0))}"
                      f":s={_opt(_expr_for('saturation', fx, a, 1.0))}"],
    "Rotate hue and adjust saturation"))


def _build_levels(fx, anims, dur) -> List[str]:
    b = _pval(fx, "black", 0.0)
    w = _pval(fx, "white", 1.0)
    g = _pval(fx, "gamma", 1.0)
    return [f"colorlevels=rimin={num(max(0.0, min(0.9, b)))}:"
            f"gimin={num(max(0.0, min(0.9, b)))}:"
            f"bimin={num(max(0.0, min(0.9, b)))}:"
            f"rimax={num(max(0.1, min(1.0, w)))}:"
            f"gimax={num(max(0.1, min(1.0, w)))}:"
            f"bimax={num(max(0.1, min(1.0, w)))}:"
            f"gamma={num(max(0.1, g))}"]


_register(EffectSpec(
    "levels", "Levels", "Basic",
    [Param("black", "Black point", 0.0, 0.0, 0.6, 0.005),
     Param("white", "White point", 1.0, 0.4, 1.0, 0.005),
     Param("gamma", "Midtones", 1.0, 0.2, 3.0, 0.01)],
    _build_levels, "Remap the tonal range"))


def _build_curves(fx, anims, dur) -> List[str]:
    s = _pval(fx, "shadows", 0.0)
    h = _pval(fx, "highlights", 0.0)
    m = _pval(fx, "mid", 0.0)
    pts: List[str] = []
    for i in range(0, 6):
        x = i / 5.0
        y = x
        base = x
        y += s * (1 - base) * 0.35 * (1 - x)
        y += h * base * 0.35 * (1 - (1 - x))
        y += m * math.sin(math.pi * x) * 0.3
        y = max(0.0, min(1.0, y))
        pts.append(f"{num(x)}/{num(y)}")
    curve = " ".join(pts)
    return [f"curves=all={_opt(curve)}"]


_register(EffectSpec(
    "curves", "Highlights / Shadows", "Basic",
    [Param("shadows", "Shadows", 0.0, -1.0, 1.0, 0.01),
     Param("mid", "Midtones", 0.0, -1.0, 1.0, 0.01),
     Param("highlights", "Highlights", 0.0, -1.0, 1.0, 0.01)],
    _build_curves, "Recover highlights and lift shadows"))

def _build_bw(fx, anims, dur) -> List[str]:
    amt = max(0.0, min(1.0, _pval(fx, "amount", 1.0)))
    return [f"eq=saturation={num(1.0 - amt)}"]


_register(EffectSpec(
    "bw", "Black & White", "Colour",
    [Param("amount", "Amount", 1.0, 0.0, 1.0, 0.01)],
    _build_bw, "Monochrome conversion"))

_register(EffectSpec(
    "sepia", "Sepia", "Colour",
    [Param("amount", "Strength", 1.0, 0.0, 1.0, 0.01)],
    lambda fx, a, d: [
        f"colorchannelmixer="
        f"rr={num(0.393 * _pval(fx, 'amount', 1.0) + (1 - _pval(fx, 'amount', 1.0)))}:"
        f"rg={num(0.769 * _pval(fx, 'amount', 1.0))}:"
        f"rb={num(0.189 * _pval(fx, 'amount', 1.0))}:"
        f"gr={num(0.349 * _pval(fx, 'amount', 1.0))}:"
        f"gg={num(0.686 * _pval(fx, 'amount', 1.0) + (1 - _pval(fx, 'amount', 1.0)))}:"
        f"gb={num(0.168 * _pval(fx, 'amount', 1.0))}:"
        f"br={num(0.272 * _pval(fx, 'amount', 1.0))}:"
        f"bg={num(0.534 * _pval(fx, 'amount', 1.0))}:"
        f"bb={num(0.131 * _pval(fx, 'amount', 1.0) + (1 - _pval(fx, 'amount', 1.0)))}"],
    "Classic sepia tone"))

_register(EffectSpec(
    "negate", "Invert", "Colour",
    [Param("amount", "Amount", 1.0, 0.0, 1.0, 0.01)],
    lambda fx, a, d: [f"negate=negate_alpha=0"],
    "Negative image"))

_register(EffectSpec(
    "vibrance", "Vibrance", "Colour",
    [Param("amount", "Vibrance", 0.0, -1.0, 1.0, 0.01)],
    lambda fx, a, d: [f"vibrance=intensity={num(_pval(fx, 'amount', 0.0))}"],
    "Smart saturation that protects skin tones"))

_register(EffectSpec(
    "colorbalance", "Colour Balance", "Colour",
    [Param("rs", "Shadows R", 0.0, -1.0, 1.0, 0.01),
     Param("gs", "Shadows G", 0.0, -1.0, 1.0, 0.01),
     Param("bs", "Shadows B", 0.0, -1.0, 1.0, 0.01),
     Param("rm", "Midtones R", 0.0, -1.0, 1.0, 0.01),
     Param("gm", "Midtones G", 0.0, -1.0, 1.0, 0.01),
     Param("bm", "Midtones B", 0.0, -1.0, 1.0, 0.01),
     Param("rh", "Highlights R", 0.0, -1.0, 1.0, 0.01),
     Param("gh", "Highlights G", 0.0, -1.0, 1.0, 0.01),
     Param("bh", "Highlights B", 0.0, -1.0, 1.0, 0.01)],
    lambda fx, a, d: [
        "colorbalance=" + ":".join(
            f"{k}={num(_pval(fx, k, 0.0))}"
            for k in ("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh"))
        if any(abs(_pval(fx, k, 0.0)) > 1e-4
               for k in ("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh"))
        else "null"],
    "Three-way colour wheels"))

_register(EffectSpec(
    "colorize", "Colour Wash", "Colour",
    [Param("hue", "Hue", 0.0, 0.0, 360.0, 1.0),
     Param("saturation", "Saturation", 0.5, 0.0, 1.0, 0.01),
     Param("lightness", "Lightness", 0.0, -1.0, 1.0, 0.01),
     Param("mix", "Mix", 0.5, 0.0, 1.0, 0.01)],
    lambda fx, a, d: [f"colorize=hue={num(_pval(fx, 'hue', 0.0))}:"
                      f"saturation={num(_pval(fx, 'saturation', 0.5))}:"
                      f"lightness={num(_pval(fx, 'lightness', 0.0))}:"
                      f"mix={num(_pval(fx, 'mix', 0.5))}"],
    "Tint the image with a single colour"))


# --------------------------------------------------------------------------- #
#  Blur / sharpen / grain
# --------------------------------------------------------------------------- #

def _build_blur(fx, anims, dur) -> List[str]:
    sigma = _pval(fx, "sigma", 4.0)
    return [f"gblur=sigma={num(max(0.0, sigma))}:steps=2"]


_register(EffectSpec(
    "blur", "Blur", "Blur & Sharp",
    [Param("sigma", "Blur", 4.0, 0.0, 60.0, 0.1)],
    _build_blur, "Gaussian blur"))

_register(EffectSpec(
    "mosaic", "Mosaic / Pixelate", "Blur & Sharp",
    [Param("size", "Pixel size", 12.0, 2.0, 120.0, 1.0)],
    lambda fx, a, d: [f"pixelize=w={int(max(2, _pval(fx, 'size', 12.0)))}:"
                      f"h={int(max(2, _pval(fx, 'size', 12.0)))}"],
    "Pixelate for censorship or transitions"))

_register(EffectSpec(
    "sharpen", "Sharpen", "Blur & Sharp",
    [Param("amount", "Amount", 0.6, 0.0, 3.0, 0.01)],
    lambda fx, a, d: [f"unsharp=luma_msize_x=5:luma_msize_y=5:"
                      f"luma_amount={num(_pval(fx, 'amount', 0.6))}"],
    "Unsharp mask"))

_register(EffectSpec(
    "denoise", "Denoise", "Blur & Sharp",
    [Param("amount", "Strength", 4.0, 0.0, 20.0, 0.1)],
    lambda fx, a, d: [f"hqdn3d={num(_pval(fx, 'amount', 4.0))}:"
                      f"{num(_pval(fx, 'amount', 4.0) * 0.75)}:"
                      f"{num(_pval(fx, 'amount', 4.0) * 1.5)}:"
                      f"{num(_pval(fx, 'amount', 4.0) * 1.5)}"],
    "Temporal + spatial denoiser"))

_register(EffectSpec(
    "grain", "Film Grain", "Blur & Sharp",
    [Param("amount", "Amount", 12.0, 0.0, 60.0, 1.0)],
    lambda fx, a, d: [f"noise=alls={int(_pval(fx, 'amount', 12.0))}:allf=t+u"],
    "Adds organic film grain"))

_register(EffectSpec(
    "vignette", "Vignette", "Light",
    [Param("angle", "Angle", 0.5, 0.0, 1.6, 0.01)],
    lambda fx, a, d: [f"vignette=angle={num(max(0.0, _pval(fx, 'angle', 0.5)))}"],
    "Darkened corners"))

_register(EffectSpec(
    "glow", "Glow / Bloom", "Light",
    [Param("amount", "Amount", 0.5, 0.0, 1.0, 0.01),
     Param("radius", "Radius", 18.0, 1.0, 80.0, 0.5)],
    None, "Soft light bloom"))
EFFECTS["glow"].build = None    # handled specially (multi-branch)

_register(EffectSpec(
    "soft_focus", "Soft Focus", "Light",
    [Param("amount", "Softness", 6.0, 0.0, 40.0, 0.1),
     Param("contrast", "Contrast", 1.0, 0.4, 1.6, 0.01)],
    lambda fx, a, d: [f"gblur=sigma={num(_pval(fx, 'amount', 6.0))}:steps=2",
                      f"eq=contrast={num(_pval(fx, 'contrast', 1.0))}"],
    "Dreamy diffusion"))


# --------------------------------------------------------------------------- #
#  Stylised looks
# --------------------------------------------------------------------------- #

_register(EffectSpec(
    "rgb_split", "RGB Split", "Stylise",
    [Param("offset", "Offset", 6.0, 0.0, 60.0, 0.5),
     Param("angle", "Angle", 0.0, 0.0, 360.0, 1.0)],
    lambda fx, a, d: [
        f"rgbashift=rh={int(_pval(fx, 'offset', 6.0))}:"
        f"bh={int(-_pval(fx, 'offset', 6.0))}"],
    "Chromatic aberration / glitch fringe"))

_register(EffectSpec(
    "vhs", "VHS", "Stylise",
    [Param("amount", "Amount", 0.6, 0.0, 1.0, 0.01)],
    None, "Analog tape look"))
EFFECTS["vhs"].build = None

_register(EffectSpec(
    "old_film", "Old Film", "Stylise",
    [Param("amount", "Amount", 0.7, 0.0, 1.0, 0.01)],
    None, "Faded archival look"))
EFFECTS["old_film"].build = None

def _build_posterize(fx, anims, dur) -> List[str]:
    levels = max(2.0, _pval(fx, "levels", 8.0))
    step = 255.0 / levels
    expr = "floor(val/" + num(step) + ")*" + num(step)
    return ["lutrgb=r='" + expr + "':g='" + expr + "':b='" + expr + "'"]


_register(EffectSpec(
    "posterize", "Posterize", "Stylise",
    [Param("levels", "Levels", 8.0, 2.0, 32.0, 1.0)],
    _build_posterize, "Reduce colour depth"))


def _build_edge(fx, anims, dur) -> List[str]:
    """Edge detectors.  Only ``edgedetect`` takes low/high thresholds; the
    Sobel/Prewitt/Kirsch variants accept scale/delta instead."""
    mode = int(_pval(fx, "mode", 0))
    amount = max(0.05, min(4.0, _pval(fx, "amount", 1.0)))
    if mode <= 0:
        return [f"edgedetect=low=0.06:high=0.24:mode=wires"]
    name = ["edgedetect", "sobel", "prewitt", "kirsch"][max(0, min(3, mode))]
    return [f"{name}=scale={num(amount)}:delta=0"]


_register(EffectSpec(
    "edge", "Edge Detect", "Stylise",
    [Param("mode", "Detector", 1.0, 0.0, 3.0, 1.0, kind="choice",
           choices=["Edge", "Sobel", "Prewitt", "Kirsch"]),
     Param("amount", "Intensity", 1.0, 0.05, 4.0, 0.05)],
    _build_edge, "Outline / sketch look"))

_register(EffectSpec(
    "scanline", "Scanlines / CRT", "Stylise",
    [Param("amount", "Amount", 0.5, 0.0, 1.0, 0.01)],
    None, "CRT screen simulation"))
EFFECTS["scanline"].build = None

_register(EffectSpec(
    "mirror", "Mirror", "Stylise",
    [Param("mode", "Mode", 0.0, 0.0, 3.0, 1.0, kind="choice",
           choices=["Left", "Right", "Top", "Bottom"])],
    None, "Mirror one half onto the other"))
EFFECTS["mirror"].build = None

_register(EffectSpec(
    "zoom_blur", "Echo / Motion Blur", "Stylise",
    [Param("frames", "Echoes", 4.0, 2.0, 12.0, 1.0),
     Param("decay", "Decay", 0.6, 0.0, 1.0, 0.01)],
    lambda fx, a, d: [
        "tmix=frames=" + str(int(max(2, min(12, _pval(fx, "frames", 4.0))))) +
        ":weights='" + " ".join(
            num(_pval(fx, "decay", 0.6) ** i)
            for i in range(int(max(2, min(12, _pval(fx, "frames", 4.0)))))) + "'"],
    "Frame-blend echo / smear"))

_register(EffectSpec(
    "chroma_shift", "Colour Shift", "Stylise",
    [Param("shift", "Shift", 4.0, -40.0, 40.0, 0.5)],
    lambda fx, a, d: [f"chromashift=cbh={int(_pval(fx, 'shift', 4.0))}:"
                      f"crh={int(-_pval(fx, 'shift', 4.0))}"],
    "Shift chroma planes"))

_register(EffectSpec(
    "heat", "Thermal", "Stylise",
    [Param("mix", "Mix", 0.7, 0.0, 1.0, 0.01)],
    lambda fx, a, d: ["pseudocolor=preset=turbo"],
    "False-colour thermal look"))

_register(EffectSpec(
    "neon", "Neon", "Stylise",
    [Param("amount", "Amount", 0.8, 0.0, 1.0, 0.01)],
    None, "Glowing neon outlines"))
EFFECTS["neon"].build = None

_register(EffectSpec(
    "cartoon", "Cartoon", "Stylise",
    [Param("levels", "Flatness", 6.0, 2.0, 16.0, 1.0)],
    None, "Cel-shaded look"))
EFFECTS["cartoon"].build = None

_register(EffectSpec(
    "stabilize", "Stabilise", "Repair",
    [Param("shakiness", "Shakiness", 5.0, 1.0, 10.0, 1.0),
     Param("smoothing", "Smoothing", 15.0, 2.0, 60.0, 1.0)],
    None, "Two-pass video stabilisation"))
EFFECTS["stabilize"].build = None

_register(EffectSpec(
    "deinterlace", "Deinterlace", "Repair",
    [Param("mode", "Mode", 0.0, 0.0, 2.0, 1.0, kind="choice",
           choices=["Auto", "Bob", "Weave"])],
    lambda fx, a, d: ["yadif=mode=0:parity=-1:deint=0"],
    "Remove interlacing artefacts"))

_register(EffectSpec(
    "lut", "LUT (.cube)", "Colour",
    [Param("file", "LUT file", 0.0, kind="file"),
     Param("intensity", "Intensity", 1.0, 0.0, 1.0, 0.01)],
    None, "Load a .cube 3D LUT"))
EFFECTS["lut"].build = None


# --------------------------------------------------------------------------- #
#  Named colour grades (VN's "Filters" tab)
# --------------------------------------------------------------------------- #

GRADE_DEFS: List[Tuple[str, Dict[str, float]]] = [
    ("Original",        {}),
    ("Vivid",           {"contrast": 1.14, "saturation": 1.35, "brightness": 0.02}),
    ("Bright",          {"brightness": 0.09, "contrast": 1.06, "gamma": 1.05}),
    ("Warm",            {"temperature": 0.35, "saturation": 1.12, "brightness": 0.03}),
    ("Cool",            {"temperature": -0.35, "saturation": 1.05}),
    ("Golden Hour",     {"temperature": 0.55, "brightness": 0.05, "saturation": 1.2,
                         "gamma": 1.06}),
    ("Teal & Orange",   {"rs": 0.18, "bh": 0.2, "rm": 0.05, "gm": -0.04, "bm": 0.07,
                         "contrast": 1.1, "saturation": 1.15}),
    ("Cinematic",       {"contrast": 1.16, "saturation": 0.88, "shadows": -0.15,
                         "highlights": 0.1, "bs": 0.09}),
    ("Matte",           {"black": 0.06, "contrast": 0.9, "saturation": 0.95}),
    ("Fade",            {"black": 0.09, "white": 0.94, "saturation": 0.86,
                         "brightness": 0.04}),
    ("Vintage",         {"temperature": 0.3, "saturation": 0.82, "black": 0.05,
                         "contrast": 0.94, "vignette": 0.35}),
    ("Retro Film",      {"temperature": 0.22, "saturation": 0.8, "black": 0.06,
                         "grain": 10.0, "vignette": 0.4}),
    ("Noir",            {"saturation": 0.0, "contrast": 1.35, "brightness": -0.03}),
    ("Mono Contrast",   {"saturation": 0.0, "contrast": 1.5, "gamma": 1.1}),
    ("Nordic",          {"temperature": -0.3, "saturation": 0.78, "contrast": 1.08,
                         "brightness": 0.04}),
    ("Pastel",          {"saturation": 0.75, "brightness": 0.1, "contrast": 0.9}),
    ("Peach",           {"temperature": 0.4, "saturation": 1.08, "brightness": 0.06,
                         "rm": 0.06, "bm": -0.03}),
    ("Cyberpunk",       {"bs": 0.25, "bm": 0.16, "rm": 0.12, "contrast": 1.2,
                         "saturation": 1.3, "vibrance": 0.3}),
    ("Anime",           {"saturation": 1.45, "contrast": 1.12, "vibrance": 0.4,
                         "sharpen": 0.8}),
    ("Sky Blue",        {"temperature": -0.45, "saturation": 1.12, "brightness": 0.05}),
    ("Dramatic",        {"contrast": 1.35, "shadows": -0.25, "highlights": 0.14,
                         "saturation": 0.92}),
    ("Fresh",           {"saturation": 1.18, "brightness": 0.05, "gamma": 1.04,
                         "temperature": -0.12}),
    ("Sunset",          {"temperature": 0.6, "rh": 0.1, "saturation": 1.25,
                         "brightness": 0.03}),
    ("Silver",          {"saturation": 0.25, "contrast": 1.2, "bs": 0.06}),
    ("Neon Pop",        {"saturation": 1.6, "contrast": 1.15, "vibrance": 0.5}),
]

for _name, _params in GRADE_DEFS:
    GRADES[_name] = dict(_params)
    GRADE_ORDER.append(_name)


def build_grade(name: str, intensity: float = 1.0) -> List[str]:
    """Filter fragments for a named look."""
    params = GRADES.get(name or "Original")
    if not params or intensity <= 0.0:
        return []
    k = max(0.0, min(2.0, intensity))
    frags: List[str] = []
    eq_parts: List[str] = []
    if params.get("brightness"):
        eq_parts.append(f"brightness={num(params['brightness'] * k)}")
    if params.get("contrast"):
        eq_parts.append(f"contrast={num(1.0 + (params['contrast'] - 1.0) * k)}")
    if params.get("saturation") is not None and "saturation" in params:
        eq_parts.append(f"saturation={num(1.0 + (params['saturation'] - 1.0) * k)}")
    if params.get("gamma"):
        eq_parts.append(f"gamma={num(1.0 + (params['gamma'] - 1.0) * k)}")
    if eq_parts:
        frags.append("eq=" + ":".join(eq_parts))
    if params.get("temperature"):
        temp = 6500 + params["temperature"] * k * 2500
        frags.append(f"colortemperature=temperature={num(max(1500, min(15000, temp)))}")
    if params.get("vibrance"):
        frags.append(f"vibrance=intensity={num(params['vibrance'] * k)}")
    cb_keys = ("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh")
    cb = [f"{key}={num(params[key] * k)}" for key in cb_keys if params.get(key)]
    if cb:
        frags.append("colorbalance=" + ":".join(cb))
    if params.get("black") or params.get("white"):
        frags.append(f"colorlevels=rimin={num(params.get('black', 0.0) * k)}:"
                     f"gimin={num(params.get('black', 0.0) * k)}:"
                     f"bimin={num(params.get('black', 0.0) * k)}:"
                     f"rimax={num(1.0 - (1.0 - params.get('white', 1.0)) * k)}:"
                     f"gimax={num(1.0 - (1.0 - params.get('white', 1.0)) * k)}:"
                     f"bimax={num(1.0 - (1.0 - params.get('white', 1.0)) * k)}")
    if params.get("shadows") or params.get("highlights"):
        frags.append(_build_curves({"shadows": params.get("shadows", 0.0) * k,
                                    "highlights": params.get("highlights", 0.0) * k,
                                    "mid": 0.0}, {}, 0)[0])
    if params.get("grain"):
        frags.append(f"noise=alls={int(params['grain'] * k)}:allf=t+u")
    if params.get("vignette"):
        frags.append(f"vignette=angle={num(0.3 + params['vignette'] * k)}")
    if params.get("sharpen"):
        frags.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:"
                     f"luma_amount={num(params['sharpen'] * k)}")
    return frags


# --------------------------------------------------------------------------- #
#  Multi-branch effects (need split/blend)
# --------------------------------------------------------------------------- #

MULTIBRANCH = {"glow", "vhs", "old_film", "neon", "cartoon", "scanline", "mirror"}


def build_multibranch(fx: FilterInstance, in_label: str, out_label: str,
                      counter, dur: float) -> str:
    """Filtergraph fragment for effects that need split/blend compositing."""
    kind = fx.kind
    p = fx.params

    def c(n=1):
        return [counter() for _ in range(n)]

    if kind == "glow":
        amount = max(0.0, min(1.0, _pval(p, "amount", 0.5)))
        radius = max(1.0, _pval(p, "radius", 18.0))
        if amount <= 0.001:
            return f"[{in_label}]null[{out_label}]"
        a1, a2, ab = c(3)
        return (f"[{in_label}]split=2[{a1}][{a2}];"
                f"[{a2}]gblur=sigma={num(radius)}:steps=2[{ab}];"
                f"[{a1}][{ab}]blend=all_mode=screen:all_opacity={num(amount)}"
                f"[{out_label}]")

    if kind == "vhs":
        amt = max(0.0, min(1.0, _pval(p, "amount", 0.6)))
        return (f"[{in_label}]rgbashift=rh={int(6 * amt)}:bh={int(-5 * amt)},"
                f"chromashift=cbh={int(3 * amt)},"
                f"eq=saturation={num(1 - 0.25 * amt)}:contrast={num(1 + 0.1 * amt)},"
                f"curves=all='0/0.04 0.5/0.52 1/0.97',"
                f"noise=alls={int(18 * amt)}:allf=t,"
                f"vignette=angle={num(0.3 + 0.3 * amt)}[{out_label}]")

    if kind == "old_film":
        amt = max(0.0, min(1.0, _pval(p, "amount", 0.7)))
        inv = 1.0 - amt
        return (f"[{in_label}]colorchannelmixer="
                f"rr={num(0.5 + 0.3 * inv)}:rg={num(0.35 * amt)}:"
                f"gg={num(0.45 + 0.3 * inv)}:gb={num(0.1 * amt)}:"
                f"bb={num(0.4 + 0.3 * inv)}:br={num(0.2 * amt)},"
                f"eq=contrast={num(1 - 0.18 * amt)}:brightness={num(0.05 * amt)},"
                f"noise=alls={int(24 * amt)}:allf=t+u,"
                f"vignette=angle={num(0.35 + 0.4 * amt)}[{out_label}]")

    if kind == "neon":
        amt = max(0.0, min(1.0, _pval(p, "amount", 0.8)))
        a1, a2, ae, ab = c(4)
        return (f"[{in_label}]split=2[{a1}][{a2}];"
                f"[{a2}]edgedetect=low=0.05:high=0.22[{ae}];"
                f"[{ae}]gblur=sigma={num(2 + 4 * amt)}[{ab}];"
                f"[{a1}][{ab}]blend=all_mode=screen:all_opacity={num(0.7 * amt)}"
                f"[{out_label}]")

    if kind == "cartoon":
        q = 255.0 / max(2.0, _pval(p, "levels", 6.0))
        return (f"[{in_label}]lutrgb=r='floor(val/{num(q)})*{num(q)}':"
                f"g='floor(val/{num(q)})*{num(q)}':"
                f"b='floor(val/{num(q)})*{num(q)}',"
                f"unsharp=luma_msize_x=7:luma_msize_y=7:luma_amount=1.2[{out_label}]")

    if kind == "scanline":
        amt = max(0.0, min(1.0, _pval(p, "amount", 0.5)))
        k1 = num(0.18 * amt)
        return (f"[{in_label}]geq=r='r(X,Y)*(1-{k1}*mod(Y,2))':"
                f"g='g(X,Y)*(1-{k1}*mod(Y,2))':"
                f"b='b(X,Y)*(1-{k1}*mod(Y,2))'[{out_label}]")

    if kind == "mirror":
        mode = int(_pval(p, "mode", 0))
        a1, a2, a3, a4 = c(4) if False else ("ma", "mb", "mc", "md")
        if mode == 0:
            return (f"[{in_label}]split=2[{a1}][{a2}];"
                    f"[{a1}]crop=iw/2:ih:0:0[{a3}];"
                    f"[{a2}]crop=iw/2:ih:0:0,hflip[{a4}];"
                    f"[{a3}][{a4}]hstack[{out_label}]")
        if mode == 1:
            return (f"[{in_label}]split=2[{a1}][{a2}];"
                    f"[{a1}]crop=iw/2:ih:iw/2:0[{a3}];"
                    f"[{a2}]crop=iw/2:ih:iw/2:0,hflip[{a4}];"
                    f"[{a4}][{a3}]hstack[{out_label}]")
        if mode == 2:
            return (f"[{in_label}]split=2[{a1}][{a2}];"
                    f"[{a1}]crop=iw:ih/2:0:0[{a3}];"
                    f"[{a2}]crop=iw:ih/2:0:0,vflip[{a4}];"
                    f"[{a3}][{a4}]vstack[{out_label}]")
        return (f"[{in_label}]split=2[{a1}][{a2}];"
                f"[{a1}]crop=iw:ih/2:0:ih/2[{a3}];"
                f"[{a2}]crop=iw:ih/2:0:ih/2,vflip[{a4}];"
                f"[{a4}][{a3}]vstack[{out_label}]")

    return f"[{in_label}]null[{out_label}]"


def build_effect_chain(clip, counter) -> List[str]:
    """Filter fragments for every effect on a clip (excluding multi-branch)."""
    frags: List[str] = []
    for fx in clip.filters:
        if not fx.enabled:
            continue
        if fx.kind == "grade":
            intensity = float(fx.params.get("intensity", 1.0))
            frags.extend(build_grade(str(fx.params.get("name", "Original")), intensity))
            continue
        spec = EFFECTS.get(fx.kind)
        if spec is None or fx.kind in MULTIBRANCH:
            continue
        if fx.kind == "lut":
            path = fx.params.get("file")
            if not path:
                continue
            from .ffmpeg import escape_filter_path
            intensity = float(fx.params.get("intensity", 1.0))
            if intensity >= 0.999:
                frags.append(f"lut3d=file={escape_filter_path(str(path))}")
            else:
                frags.append(f"lut3d=file={escape_filter_path(str(path))}:"
                             f"interp=tetrahedral")
            continue
        if fx.kind == "stabilize":
            continue
        if spec.build is None:
            continue
        try:
            frags.extend(spec.build(fx.params, fx.animated, clip.duration))
        except Exception:
            continue
    return [f for f in frags if f and f != "null"]


def has_multibranch(clip) -> bool:
    return any(fx.enabled and fx.kind in MULTIBRANCH for fx in clip.filters)


def effect_params(kind: str) -> List[Param]:
    spec = EFFECTS.get(kind)
    return list(spec.params) if spec else []


def effect_label(kind: str) -> str:
    spec = EFFECTS.get(kind)
    return spec.label if spec else kind.replace("_", " ").title()


def categories() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for key, spec in EFFECTS.items():
        if spec.build is None and key not in MULTIBRANCH:
            continue
        out.setdefault(spec.category, []).append(key)
    return out


def default_params(kind: str) -> Dict[str, float]:
    spec = EFFECTS.get(kind)
    return spec.defaults() if spec else {}
