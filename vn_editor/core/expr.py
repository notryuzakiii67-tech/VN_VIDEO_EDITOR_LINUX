"""Compile keyframe animations into FFmpeg *expression* strings.

FFmpeg evaluates filter options per frame when they contain expressions; the
available functions are ``if/lt/gt/lte/gte/eq/between/pow/sqrt/sin/cos/abs/
min/max/clip/mod/floor/ceil`` and the free variables differ per filter (``t``
and ``n`` for most, ``time``/``on``/``zoom`` for ``zoompan``).

We deliberately generate a *pure expression* (no lookup tables) so the result
works identically in the preview graph and the export graph.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from .model import Keyframe

MAX_KEYS_IN_EXPR = 40


def num(v: float, nd: int = 5) -> str:
    if v != v or v in (float("inf"), float("-inf")):
        return "0"
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e9:
        return str(int(round(v)))
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def _p(a: Keyframe, b: Keyframe, var: str) -> str:
    span = b.t - a.t
    if span <= 1e-9:
        return "1"
    return f"clip(({var}-{num(a.t)})/{num(span)},0,1)"


def _ease_expr(kind: str, p: str) -> str:
    """Expression for the eased progress given the raw progress ``p``."""
    if kind == "linear" or kind not in EASINGS_INLINE:
        return p
    if kind == "hold":
        return "0"
    if kind == "ease_in":
        return f"({p}*{p})"
    if kind == "ease_out":
        return f"(1-(1-{p})*(1-{p}))"
    if kind == "ease_in_out":
        return (f"if(lt({p},0.5),2*{p}*{p},"
                f"1-2*(1-{p})*(1-{p}))")
    if kind == "ease_in_cubic":
        return f"(({p})*({p})*({p}))"
    if kind == "ease_out_cubic":
        return f"(1-pow(1-{p},3))"
    if kind == "ease_in_out_cubic":
        return f"if(lt({p},0.5),4*pow({p},3),1-4*pow(1-{p},3))"
    if kind == "ease_in_quart":
        return f"pow({p},4)"
    if kind == "ease_out_quart":
        return f"(1-pow(1-{p},4))"
    if kind == "bounce_out":
        return (
            "if(lt({p},0.3636),7.5625*{p}*{p},"
            "if(lt({p},0.7273),7.5625*pow({p}-0.5455,2)+0.75,"
            "if(lt({p},0.9091),7.5625*pow({p}-0.8182,2)+0.9375,"
            "7.5625*pow({p}-0.9545,2)+0.984375)))"
        ).replace("{p}", p)
    if kind == "elastic_out":
        return (f"(if(eq({p},0),0,if(gte({p},1),1,"
                f"pow(2,-10*{p})*sin((10*{p}-0.75)*2.0943951)+1)))")
    return p


EASINGS_INLINE = {
    "linear", "hold", "ease_in", "ease_out", "ease_in_out", "ease_in_cubic",
    "ease_out_cubic", "ease_in_out_cubic", "ease_in_quart", "ease_out_quart",
    "bounce_out", "elastic_out",
}


def _downsample(keys: Sequence[Keyframe], limit: int) -> List[Keyframe]:
    ks = list(keys)
    if len(ks) <= limit:
        return ks
    step = len(ks) / limit
    out = [ks[0]]
    i = step
    while i < len(ks) - 1:
        out.append(ks[int(round(i))])
        i += step
    out.append(ks[-1])
    seen = set()
    uniq = []
    for k in out:
        key = round(k.t, 4)
        if key not in seen:
            seen.add(key)
            uniq.append(k)
    return uniq


def kf_expr(keys: Sequence[Keyframe], default: float, var: str = "t",
            limit: int = MAX_KEYS_IN_EXPR) -> str:
    """Expression giving the animated value at ``var`` (seconds)."""
    ks = _downsample(sorted(keys, key=lambda k: k.t), limit)
    if not ks:
        return num(default)
    if len(ks) == 1:
        return num(ks[0].value)
    expr = num(ks[-1].value)
    for i in range(len(ks) - 1, 0, -1):
        a, b = ks[i - 1], ks[i]
        span = b.t - a.t
        if span <= 1e-9:
            seg = num(b.value)
        else:
            p = _p(a, b, var)
            e = _ease_expr(b.easing, p)
            seg = f"({num(a.value)}+({num(b.value - a.value)})*{e})"
        expr = f"if(lt({var},{num(b.t)}),{seg},{expr})"
    return f"if(lt({var},{num(ks[0].t)}),{num(ks[0].value)},{expr})"


def kf_expr_or(default: float, keys: Sequence[Keyframe], var: str = "t") -> str:
    if keys:
        return kf_expr(keys, default, var)
    return num(default)


def is_animated(keys: Sequence[Keyframe]) -> bool:
    return bool(keys) and len(keys) >= 1


def animated_span(keys: Sequence[Keyframe], default: float) -> Tuple[float, float]:
    if not keys:
        return default, default
    vals = [k.value for k in keys] + [default]
    return min(vals), max(vals)


def scale_from_offset(size: float, offset: float) -> str:
    """Helper used by the transform compiler: keep numbers tidy."""
    return num(size + offset)


def clamp_expr(e: str, lo: float, hi: float) -> str:
    return f"clip({e},{num(lo)},{num(hi)})"


def color_to_rgb(value: str) -> Tuple[int, int, int]:
    v = (value or "#000000").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) == 8:
        v = v[:6]
    if len(v) != 6:
        return (0, 0, 0)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def color_to_ff(value: str) -> str:
    r, g, b = color_to_rgb(value)
    return f"0x{r:02X}{g:02X}{b:02X}"


def color_to_rgba8(value: str, opacity: float = 1.0) -> Tuple[int, int, int, int]:
    r, g, b = color_to_rgb(value)
    a = int(round(max(0.0, min(1.0, opacity)) * 255))
    return (r, g, b, a)


def ass_color(value: str, opacity: float = 1.0) -> str:
    """libass uses &HAABBGGRR."""
    r, g, b = color_to_rgb(value)
    a = int(round((1.0 - max(0.0, min(1.0, opacity))) * 255))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"
