"""Vector stickers drawn as ASS drawing commands.

Everything here is generated procedurally from primitives — no third-party art
— so the stickers ship with the app and stay sharp at any export resolution.
A sticker clip stores ``meta["sticker"]`` (key), ``meta["sticker_color"]`` and
``meta["sticker_size"]`` (fraction of canvas height); position, scale, rotation
and animation come from the normal clip transform, exactly like text.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
#  Primitives (all coordinates in a 0..100 local box, y down)
# --------------------------------------------------------------------------- #

Pt = Tuple[float, float]


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def poly(points: List[Pt], close: bool = True) -> str:
    if not points:
        return ""
    out = [f"m {_num(points[0][0])} {_num(points[0][1])}"]
    for x, y in points[1:]:
        out.append(f"l {_num(x)} {_num(y)}")
    if close:
        out.append("l " + _num(points[0][0]) + " " + _num(points[0][1]))
    return " ".join(out)


def circle(cx: float, cy: float, r: float, ccw: bool = False) -> str:
    """Circle drawn with two arcs (ASS 'b' = cubic bezier)."""
    k = 0.5523 * r
    if ccw:
        return (f"m {_num(cx)} {_num(cy - r)} "
                f"b {_num(cx - k)} {_num(cy - r)} {_num(cx - r)} {_num(cy - k)} "
                f"{_num(cx - r)} {_num(cy)} "
                f"b {_num(cx - r)} {_num(cy + k)} {_num(cx - k)} {_num(cy + r)} "
                f"{_num(cx)} {_num(cy + r)} "
                f"b {_num(cx + k)} {_num(cy + r)} {_num(cx + r)} {_num(cy + k)} "
                f"{_num(cx + r)} {_num(cy)} "
                f"b {_num(cx + r)} {_num(cy - k)} {_num(cx + k)} {_num(cy - r)} "
                f"{_num(cx)} {_num(cy - r)}")
    return (f"m {_num(cx)} {_num(cy - r)} "
            f"b {_num(cx + k)} {_num(cy - r)} {_num(cx + r)} {_num(cy - k)} "
            f"{_num(cx + r)} {_num(cy)} "
            f"b {_num(cx + r)} {_num(cy + k)} {_num(cx + k)} {_num(cy + r)} "
            f"{_num(cx)} {_num(cy + r)} "
            f"b {_num(cx - k)} {_num(cy + r)} {_num(cx - r)} {_num(cy + k)} "
            f"{_num(cx - r)} {_num(cy)} "
            f"b {_num(cx - r)} {_num(cy - k)} {_num(cx - k)} {_num(cy - r)} "
            f"{_num(cx)} {_num(cy - r)}")


def ring(cx: float, cy: float, r_out: float, r_in: float) -> str:
    return circle(cx, cy, r_out) + " " + circle(cx, cy, r_in, ccw=True)


def ellipse(cx: float, cy: float, rx: float, ry: float) -> str:
    kx, ky = 0.5523 * rx, 0.5523 * ry
    return (f"m {_num(cx)} {_num(cy - ry)} "
            f"b {_num(cx + kx)} {_num(cy - ry)} {_num(cx + rx)} {_num(cy - ky)} "
            f"{_num(cx + rx)} {_num(cy)} "
            f"b {_num(cx + rx)} {_num(cy + ky)} {_num(cx + kx)} {_num(cy + ry)} "
            f"{_num(cx)} {_num(cy + ry)} "
            f"b {_num(cx - kx)} {_num(cy + ry)} {_num(cx - rx)} {_num(cy + ky)} "
            f"{_num(cx - rx)} {_num(cy)} "
            f"b {_num(cx - rx)} {_num(cy - ky)} {_num(cx - kx)} {_num(cy - ry)} "
            f"{_num(cx)} {_num(cy - ry)}")


def rect(x: float, y: float, w: float, h: float, r: float = 0.0) -> str:
    if r <= 0.01:
        return poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
    return (f"m {_num(x + r)} {_num(y)} l {_num(x + w - r)} {_num(y)} "
            f"b {_num(x + w)} {_num(y)} {_num(x + w)} {_num(y)} "
            f"{_num(x + w)} {_num(y + r)} l {_num(x + w)} {_num(y + h - r)} "
            f"b {_num(x + w)} {_num(y + h)} {_num(x + w)} {_num(y + h)} "
            f"{_num(x + w - r)} {_num(y + h)} l {_num(x + r)} {_num(y + h)} "
            f"b {_num(x)} {_num(y + h)} {_num(x)} {_num(y + h)} "
            f"{_num(x)} {_num(y + h - r)} l {_num(x)} {_num(y + r)} "
            f"b {_num(x)} {_num(y)} {_num(x)} {_num(y)} {_num(x + r)} {_num(y)}")


def star(cx: float, cy: float, r_out: float, r_in: float, points: int = 5,
         phase: float = -90.0) -> str:
    pts: List[Pt] = []
    for i in range(points * 2):
        ang = math.radians(phase + i * 180.0 / points)
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return poly(pts)


def heart(cx: float, cy: float, s: float) -> str:
    """Heart around (cx, cy) scaled by ``s`` (roughly the half-height)."""
    return (f"m {_num(cx)} {_num(cy + s)} "
            f"b {_num(cx - s * 0.5)} {_num(cy + s * 0.3)} "
            f"{_num(cx - s * 0.95)} {_num(cy - s * 0.05)} "
            f"{_num(cx - s * 0.72)} {_num(cy - s * 0.42)} "
            f"b {_num(cx - s * 0.55)} {_num(cy - s * 0.68)} "
            f"{_num(cx - s * 0.22)} {_num(cy - s * 0.72)} "
            f"{_num(cx)} {_num(cy - s * 0.42)} "
            f"b {_num(cx + s * 0.22)} {_num(cy - s * 0.72)} "
            f"{_num(cx + s * 0.55)} {_num(cy - s * 0.68)} "
            f"{_num(cx + s * 0.72)} {_num(cy - s * 0.42)} "
            f"b {_num(cx + s * 0.95)} {_num(cy - s * 0.05)} "
            f"{_num(cx + s * 0.5)} {_num(cy + s * 0.3)} "
            f"{_num(cx)} {_num(cy + s)}")


def bolt(cx: float, cy: float, s: float) -> str:
    pts = [(cx + 0.12 * s, cy - s), (cx - 0.42 * s, cy + 0.12 * s),
           (cx - 0.02 * s, cy + 0.12 * s), (cx - 0.2 * s, cy + s),
           (cx + 0.46 * s, cy - 0.2 * s), (cx + 0.04 * s, cy - 0.2 * s)]
    return poly(pts)


def burst(cx: float, cy: float, r: float, spikes: int = 12) -> str:
    pts: List[Pt] = []
    for i in range(spikes * 2):
        ang = math.radians(-90 + i * 180.0 / spikes)
        rr = r if i % 2 == 0 else r * 0.58
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return poly(pts)


def bubble(cx: float, cy: float, w: float, h: float) -> str:
    r = h * 0.28
    return (rect(cx - w / 2, cy - h / 2, w, h, r)
            + " " + poly([(cx - w * 0.22, cy + h / 2 - 1),
                          (cx - w * 0.34, cy + h / 2 + h * 0.42),
                          (cx - w * 0.02, cy + h / 2 - 1)]))


def arrow_up(cx: float, cy: float, w: float, h: float) -> str:
    shaft_w = w * 0.34
    return poly([
        (cx, cy - h / 2),
        (cx + w / 2, cy - h * 0.06),
        (cx + shaft_w / 2, cy - h * 0.06),
        (cx + shaft_w / 2, cy + h / 2),
        (cx - shaft_w / 2, cy + h / 2),
        (cx - shaft_w / 2, cy - h * 0.06),
        (cx - w / 2, cy - h * 0.06),
    ])


def arrow_side(cx: float, cy: float, w: float, h: float, flip: bool = False) -> str:
    s = -1 if flip else 1
    shaft = h * 0.34
    pts = [(cx - s * w / 2, cy),
           (cx + s * w * 0.06, cy - h / 2),
           (cx + s * w * 0.06, cy - shaft / 2),
           (cx + s * w / 2, cy - shaft / 2),
           (cx + s * w / 2, cy + shaft / 2),
           (cx + s * w * 0.06, cy + shaft / 2),
           (cx + s * w * 0.06, cy + h / 2)]
    return poly(pts)


def arc_arrow(cx: float, cy: float, r: float, sweep: float = 250.0) -> str:
    """Curved arrow (swipe / redo indicator) drawn as a thick arc + head."""
    outer: List[Pt] = []
    inner: List[Pt] = []
    steps = 16
    thick = r * 0.3
    for i in range(steps + 1):
        a = math.radians(-30 + sweep * i / steps)
        outer.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        inner.append(((cx + (r - thick) * math.cos(a)),
                      cy + (r - thick) * math.sin(a)))
    body = poly(outer + list(reversed(inner)))
    tip = outer[-1]
    prev = outer[-2]
    ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
    head = r * 0.55
    p1 = (tip[0] + head * math.cos(ang - 1.9), tip[1] + head * math.sin(ang - 1.9))
    p2 = (tip[0] + head * math.cos(ang + 1.9), tip[1] + head * math.sin(ang + 1.9))
    return body + " " + poly([tip, p1, p2])


def crown(cx: float, cy: float, w: float, h: float) -> str:
    return poly([
        (cx - w / 2, cy + h / 2), (cx - w / 2, cy - h * 0.18),
        (cx - w * 0.25, cy + h * 0.12), (cx, cy - h / 2),
        (cx + w * 0.25, cy + h * 0.12), (cx + w / 2, cy - h * 0.18),
        (cx + w / 2, cy + h / 2),
    ])


def skull(cx: float, cy: float, s: float) -> str:
    return (circle(cx, cy - s * 0.08, s * 0.62)
            + " " + rect(cx - s * 0.3, cy + s * 0.35, s * 0.6, s * 0.42,
                         s * 0.08))


def skull_holes(cx: float, cy: float, s: float) -> str:
    return (circle(cx - s * 0.24, cy - s * 0.12, s * 0.16)
            + " " + circle(cx + s * 0.24, cy - s * 0.12, s * 0.16)
            + " " + poly([(cx - s * 0.08, cy + s * 0.16),
                          (cx + s * 0.08, cy + s * 0.16),
                          (cx, cy + s * 0.36)]))


def flame(cx: float, cy: float, s: float) -> str:
    """Tongued flame built from three tapered lobes."""
    return (f"m {_num(cx)} {_num(cy - s)} "
            f"b {_num(cx + s * 0.2)} {_num(cy - s * 0.72)} "
            f"{_num(cx + s * 0.16)} {_num(cy - s * 0.52)} "
            f"{_num(cx + s * 0.34)} {_num(cy - s * 0.36)} "
            f"b {_num(cx + s * 0.56)} {_num(cy - s * 0.16)} "
            f"{_num(cx + s * 0.62)} {_num(cy + s * 0.24)} "
            f"{_num(cx + s * 0.34)} {_num(cy + s * 0.62)} "
            f"b {_num(cx + s * 0.12)} {_num(cy + s * 0.9)} "
            f"{_num(cx - s * 0.12)} {_num(cy + s * 0.9)} "
            f"{_num(cx - s * 0.34)} {_num(cy + s * 0.62)} "
            f"b {_num(cx - s * 0.62)} {_num(cy + s * 0.24)} "
            f"{_num(cx - s * 0.56)} {_num(cy - s * 0.16)} "
            f"{_num(cx - s * 0.34)} {_num(cy - s * 0.36)} "
            f"b {_num(cx - s * 0.16)} {_num(cy - s * 0.52)} "
            f"{_num(cx - s * 0.2)} {_num(cy - s * 0.72)} "
            f"{_num(cx)} {_num(cy - s)}")


def sparkle(cx: float, cy: float, s: float) -> str:
    pts = [(cx, cy - s), (cx + s * 0.22, cy - s * 0.22), (cx + s, cy),
           (cx + s * 0.22, cy + s * 0.22), (cx, cy + s),
           (cx - s * 0.22, cy + s * 0.22), (cx - s, cy),
           (cx - s * 0.22, cy - s * 0.22)]
    return poly(pts)


def diamond(cx: float, cy: float, s: float) -> str:
    return poly([(cx, cy - s), (cx + s * 0.78, cy), (cx, cy + s),
                 (cx - s * 0.78, cy)])


def face_base(cx: float, cy: float, r: float) -> str:
    return circle(cx, cy, r)


def face_eyes(cx: float, cy: float, r: float, kind: str) -> str:
    if kind == "closed":
        return (poly([(cx - r * 0.5, cy - r * 0.1), (cx - r * 0.24, cy - r * 0.28),
                      (cx - r * 0.02, cy - r * 0.1)], close=False)
                + " " + poly([(cx + r * 0.02, cy - r * 0.1),
                              (cx + r * 0.24, cy - r * 0.28),
                              (cx + r * 0.5, cy - r * 0.1)], close=False))
    if kind == "wide":
        return (circle(cx - r * 0.32, cy - r * 0.18, r * 0.2)
                + " " + circle(cx + r * 0.32, cy - r * 0.18, r * 0.2))
    if kind == "wink":
        return (circle(cx - r * 0.32, cy - r * 0.18, r * 0.15)
                + " " + poly([(cx + r * 0.12, cy - r * 0.2),
                              (cx + r * 0.5, cy - r * 0.24)], close=False))
    if kind == "angry":
        return (circle(cx - r * 0.32, cy - r * 0.16, r * 0.14)
                + " " + circle(cx + r * 0.32, cy - r * 0.16, r * 0.14)
                + " " + poly([(cx - r * 0.56, cy - r * 0.5),
                              (cx - r * 0.1, cy - r * 0.32),
                              (cx - r * 0.55, cy - r * 0.36)])
                + " " + poly([(cx + r * 0.56, cy - r * 0.5),
                              (cx + r * 0.1, cy - r * 0.32),
                              (cx + r * 0.55, cy - r * 0.36)]))
    if kind == "cool":
        return (rect(cx - r * 0.72, cy - r * 0.36, r * 1.44, r * 0.36, r * 0.1)
                + " " + rect(cx - r * 0.78, cy - r * 0.34, r * 0.14, r * 0.2,
                             r * 0.05)
                + " " + rect(cx + r * 0.64, cy - r * 0.34, r * 0.14, r * 0.2,
                             r * 0.05))
    return (circle(cx - r * 0.32, cy - r * 0.18, r * 0.15)
            + " " + circle(cx + r * 0.32, cy - r * 0.18, r * 0.15))


def face_mouth(cx: float, cy: float, r: float, kind: str) -> str:
    if kind == "big_smile":
        return (poly([(cx - r * 0.44, cy + r * 0.2), (cx - r * 0.22, cy + r * 0.62),
                      (cx + 0.0, cy + r * 0.68), (cx + r * 0.22, cy + r * 0.62),
                      (cx + r * 0.44, cy + r * 0.2)], close=False)
                + " " + poly([(cx - r * 0.44, cy + r * 0.2),
                              (cx + r * 0.44, cy + r * 0.2)], close=False))
    if kind == "shock":
        return circle(cx, cy + r * 0.36, r * 0.22)
    if kind == "frown":
        return poly([(cx - r * 0.36, cy + r * 0.54), (cx, cy + r * 0.24),
                     (cx + r * 0.36, cy + r * 0.54)], close=False)
    if kind == "flat":
        return poly([(cx - r * 0.34, cy + r * 0.38),
                     (cx + r * 0.34, cy + r * 0.38)], close=False)
    if kind == "tongue":
        return (poly([(cx - r * 0.34, cy + r * 0.26), (cx, cy + r * 0.5),
                      (cx + r * 0.34, cy + r * 0.26)], close=False)
                + " " + ellipse(cx, cy + r * 0.5, r * 0.16, r * 0.24))
    if kind == "smirk":
        return poly([(cx - r * 0.3, cy + r * 0.44), (cx + r * 0.06, cy + r * 0.3),
                     (cx + r * 0.4, cy + r * 0.12)], close=False)
    return poly([(cx - r * 0.34, cy + r * 0.28), (cx, cy + r * 0.56),
                 (cx + r * 0.34, cy + r * 0.28)], close=False)


def hand_thumb(cx: float, cy: float, s: float) -> str:
    return (rect(cx - s * 0.45, cy - s * 0.1, s * 0.9, s * 1.0, s * 0.18)
            + " " + rect(cx - s * 0.2, cy - s * 0.85, s * 0.4, s * 0.85,
                         s * 0.18))


def point_left(cx: float, cy: float, s: float) -> str:
    return (rect(cx - s * 0.1, cy - s * 0.28, s * 0.9, s * 0.56, s * 0.14)
            + " " + poly([(cx - s * 0.1, cy - s * 0.5),
                          (cx - s * 1.0, cy),
                          (cx - s * 0.1, cy + s * 0.5)]))


def money_bag(cx: float, cy: float, s: float) -> str:
    return (f"m {_num(cx - s * 0.5)} {_num(cy - s * 0.15)} "
            f"b {_num(cx - s * 0.8)} {_num(cy + s * 0.1)} "
            f"{_num(cx - s * 0.9)} {_num(cy + s * 0.55)} "
            f"{_num(cx - s * 0.62)} {_num(cy + s * 0.85)} "
            f"b {_num(cx - s * 0.3)} {_num(cy + s * 1.1)} "
            f"{_num(cx + s * 0.3)} {_num(cy + s * 1.1)} "
            f"{_num(cx + s * 0.62)} {_num(cy + s * 0.85)} "
            f"b {_num(cx + s * 0.9)} {_num(cy + s * 0.55)} "
            f"{_num(cx + s * 0.8)} {_num(cy + s * 0.1)} "
            f"{_num(cx + s * 0.5)} {_num(cy - s * 0.15)}")


def money_tie(cx: float, cy: float, s: float) -> str:
    return rect(cx - s * 0.34, cy - s * 0.44, s * 0.68, s * 0.34, s * 0.08)


def banner(cx: float, cy: float, w: float, h: float) -> str:
    return (poly([(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                  (cx + w / 2, cy + h / 2), (cx + w * 0.2, cy + h * 0.28),
                  (cx, cy + h / 2), (cx - w * 0.2, cy + h * 0.28),
                  (cx - w / 2, cy + h / 2)])
            if False else rect(cx - w / 2, cy - h / 2, w, h, h * 0.18))


def shield(cx: float, cy: float, s: float) -> str:
    return (f"m {_num(cx - s * 0.7)} {_num(cy - s * 0.75)} "
            f"l {_num(cx + s * 0.7)} {_num(cy - s * 0.75)} "
            f"l {_num(cx + s * 0.7)} {_num(cy + s * 0.05)} "
            f"b {_num(cx + s * 0.7)} {_num(cy + s * 0.7)} "
            f"{_num(cx + s * 0.2)} {_num(cy + s * 0.95)} {_num(cx)} {_num(cy + s)} "
            f"b {_num(cx - s * 0.2)} {_num(cy + s * 0.95)} "
            f"{_num(cx - s * 0.7)} {_num(cy + s * 0.7)} {_num(cx - s * 0.7)} {_num(cy + s * 0.05)}")


def check_mark(cx: float, cy: float, s: float) -> str:
    return poly([(cx - s * 0.62, cy + s * 0.02), (cx - s * 0.3, cy + s * 0.34),
                 (cx - s * 0.53, cy + s * 0.55), (cx - s * 0.82, cy + s * 0.22)]) + \
        poly([(cx - s * 0.34, cy + s * 0.32), (cx + s * 0.2, cy - s * 0.5),
              (cx + s * 0.52, cy - s * 0.24), (cx - s * 0.12, cy + s * 0.6)])


def cross_mark(cx: float, cy: float, s: float) -> str:
    return (rect(cx - s * 0.7, cy - s * 0.16, s * 1.4, s * 0.32, s * 0.06)
            + " " + rect(cx - s * 0.16, cy - s * 0.7, s * 0.32, s * 1.4,
                         s * 0.06))


def question_mark(cx: float, cy: float, s: float) -> str:
    return (f"m {_num(cx - s * 0.42)} {_num(cy - s * 0.44)} "
            f"b {_num(cx - s * 0.42)} {_num(cy - s * 0.78)} "
            f"{_num(cx - s * 0.03)} {_num(cy - s * 0.9)} "
            f"{_num(cx + s * 0.28)} {_num(cy - s * 0.6)} "
            f"b {_num(cx + s * 0.46)} {_num(cy - s * 0.4)} "
            f"{_num(cx + s * 0.12)} {_num(cy - s * 0.16)} "
            f"{_num(cx)} {_num(cy + s * 0.42)}")


def ellipse_hl(cx: float, cy: float, rx: float, ry: float) -> str:
    return ellipse(cx, cy, rx, ry)


def underline(cx: float, cy: float, w: float, h: float) -> str:
    return rect(cx - w / 2, cy - h / 2, w, h, h / 2)


def thumbs_up(cx: float, cy: float, s: float) -> str:
    """Fist with a raised thumb."""
    return (rect(cx - s * 0.75, cy - s * 0.05, s * 0.96, s * 0.95, s * 0.16)
            + " " + rect(cx + s * 0.24, cy - s * 0.05, s * 0.4, s * 0.95,
                         s * 0.14)
            + " " + rect(cx - s * 0.5, cy - s * 0.05, s * 0.62, s * 0.24,
                         s * 0.08)
            + " " + (f"m {_num(cx + s * 0.02)} {_num(cy - s * 0.03)} "
                     f"b {_num(cx + s * 0.02)} {_num(cy - s * 0.4)} "
                     f"{_num(cx + s * 0.14)} {_num(cy - s * 0.62)} "
                     f"{_num(cx + s * 0.32)} {_num(cy - s * 0.7)} "
                     f"b {_num(cx + s * 0.5)} {_num(cy - s * 0.78)} "
                     f"{_num(cx + s * 0.62)} {_num(cy - s * 0.66)} "
                     f"{_num(cx + s * 0.5)} {_num(cy - s * 0.46)} "
                     f"b {_num(cx + s * 0.4)} {_num(cy - s * 0.3)} "
                     f"{_num(cx + s * 0.4)} {_num(cy - s * 0.16)} "
                     f"{_num(cx + s * 0.44)} {_num(cy - s * 0.03)}"))


def clap_hands(cx: float, cy: float, s: float) -> str:
    """Two hands meeting, drawn as tapered palms."""
    def palm(sign: float) -> str:
        return (f"m {_num(cx + sign * s * 0.1)} {_num(cy - s * 0.7)} "
                f"b {_num(cx + sign * s * 0.5)} {_num(cy - s * 0.6)} "
                f"{_num(cx + sign * s * 0.72)} {_num(cy - s * 0.1)} "
                f"{_num(cx + sign * s * 0.6)} {_num(cy + s * 0.6)} "
                f"b {_num(cx + sign * s * 0.52)} {_num(cy + s * 0.82)} "
                f"{_num(cx + sign * s * 0.22)} {_num(cy + s * 0.84)} "
                f"{_num(cx + sign * s * 0.14)} {_num(cy + s * 0.62)} "
                f"b {_num(cx + sign * s * 0.06)} {_num(cy + s * 0.2)} "
                f"{_num(cx + sign * s * 0.04)} {_num(cy - s * 0.3)} "
                f"{_num(cx + sign * s * 0.1)} {_num(cy - s * 0.7)}")
    return palm(-1.0) + " " + palm(1.0)


# --------------------------------------------------------------------------- #
#  Catalogue:  key -> (label, group, default colour, drawer, ink colour)
# --------------------------------------------------------------------------- #

def _face(mouth: str, eyes: str = "dot", ink: str = "#1b1e26"):
    def draw(cx, cy, s):
        return face_base(cx, cy, s * 0.86)
    def ink_layer(cx, cy, s):
        return (face_eyes(cx, cy, s * 0.86, eyes) + " "
                + face_mouth(cx, cy, s * 0.86, mouth))
    return draw, ink_layer, ink


STICKER_CATALOG: Dict[str, Tuple[str, str, str, str]] = {}


def _reg(key: str, label_txt: str, group: str, color: str, drawer: str,
         ink: Optional[str] = None) -> None:
    STICKER_CATALOG[key] = (label_txt, group, color, f"{drawer}|{ink or ''}")


def _simple(key, label_txt, group, color, layer_name, ink=None):
    _reg(key, label_txt, group, color, layer_name, ink)


# arrows & pointers
_simple("arrow_up", "Arrow up", "Arrows", "#ffffff", "arrow_up")
_simple("arrow_right", "Arrow right", "Arrows", "#ffffff", "arrow_right")
_simple("arrow_left", "Arrow left", "Arrows", "#ffffff", "arrow_left")
_simple("arrow_curved", "Swipe arrow", "Arrows", "#ffffff", "arc_arrow")
_simple("point_left", "Pointing hand", "Arrows", "#ffd166", "point_left")
_simple("underline_hl", "Underline", "Highlight", "#ff2f6b", "underline")
_simple("circle_hl", "Circled", "Highlight", "#ffd166", "circle_hl")
# shapes
_simple("star", "Star", "Frames", "#ffd166", "star")
_simple("heart", "Heart", "Frames", "#ff4d6d", "heart")
_simple("diamond", "Diamond", "Frames", "#4cc9f0", "diamond")
_simple("sparkle", "Sparkle", "Frames", "#ffe066", "sparkle")
_simple("burst", "Burst", "Frames", "#ff8c42", "burst")
# signs
_simple("question", "Question", "Signs", "#4cc9f0", "question")
_simple("exclaim", "Exclamation", "Signs", "#ff2f6b", "exclaim")
_simple("check", "Check", "Signs", "#39d98a", "check")
_simple("cross", "Cross", "Signs", "#ff4d6d", "cross")
_simple("mute_icon", "Muted", "Signs", "#9aa3b2", "mute")
_simple("shield", "Shield", "Signs", "#4cc9f0", "shield")
# objects
_simple("lightning", "Lightning", "Objects", "#ffd166", "bolt")
_simple("fire", "Fire", "Objects", "#ff7b00", "flame")
_simple("crown", "Crown", "Objects", "#ffd166", "crown")
_simple("money", "Money bag", "Objects", "#39d98a", "money_bag")
_simple("thumbs_up", "Thumbs up", "Objects", "#ffd166", "thumbs_up")
_simple("clap", "Clapping", "Objects", "#ffd166", "clap")
_simple("skull", "Skull", "Objects", "#e9edf5", "skull")
_simple("boom", "Boom", "Objects", "#ff8c42", "burst")
_simple("replay_icon", "Replay", "Objects", "#4cc9f0", "replay")
# banners
_simple("sub_banner", "Subscribe banner", "Banners", "#ff2f6b", "banner")
_simple("lower_third", "Lower third", "Banners", "#f1f5f9", "lower_third")
_simple("vs_badge", "VS badge", "Banners", "#ff2f6b", "vs")
_simple("new_icon", "NEW tag", "Banners", "#39d98a", "new")
# faces
_simple("laugh", "Laughing", "Faces", "#ffd166", "laugh")
_simple("cry", "Crying", "Faces", "#ffd166", "cry")
_simple("cool", "Cool", "Faces", "#ffd166", "cool")
_simple("shock", "Shocked", "Faces", "#ffd166", "shock")
_simple("wink", "Wink", "Faces", "#ffd166", "wink")
_simple("angry", "Angry", "Faces", "#ff6b6b", "angry")
_simple("smirk", "Smirk", "Faces", "#ffd166", "smirk")

# extra aliases used by older projects
for _alias, _target in (("arrow_curved", "arrow_curved"), ("thought", "bubble"),
                        ("speech", "bubble"), ("skull_cross", "skull")):
    pass

STICKER_GROUPS = ["Arrows", "Highlight", "Frames", "Signs", "Objects",
                  "Banners", "Faces"]


def _draw_spec(name: str):
    return STICKER_CATALOG.get(name)


FACE_SPECS = {
    "laugh": ("big_smile", "closed", "#ffd166"),
    "cry": ("frown", "closed", "#ffd166"),
    "cool": ("smirk", "cool", "#ffd166"),
    "shock": ("shock", "wide", "#ffd166"),
    "wink": ("smile", "wink", "#ffd166"),
    "angry": ("frown", "angry", "#ff6b6b"),
    "smirk": ("smirk", "dot", "#ffd166"),
}


def sticker_drawing(name: str, cx: float, cy: float, size: float
                    ) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (body_path, detail_path, detail_colour) for a sticker key.

    Coordinates are in canvas units; ``size`` is the sticker's overall height.
    """
    s = size
    w = size * 0.9

    drawers = {
        "arrow_up": lambda: arrow_up(cx, cy, w * 0.8, s * 1.15),
        "arrow_right": lambda: arrow_side(cx, cy, s * 1.15, w * 0.8),
        "arrow_left": lambda: arrow_side(cx, cy, s * 1.15, w * 0.8, True),
        "arrow_curved": lambda: arc_arrow(cx, cy, s * 0.62),
        "point_left": lambda: point_left(cx, cy, s * 0.62),
        "underline_hl": lambda: underline(cx, cy, s * 1.7, s * 0.2),
        "circle_hl": lambda: ring(cx, cy, s * 0.72, s * 0.6),
        "star": lambda: star(cx, cy, s * 0.78, s * 0.34),
        "heart": lambda: heart(cx, cy, s * 0.62),
        "diamond": lambda: diamond(cx, cy, s * 0.85),
        "sparkle": lambda: sparkle(cx, cy, s * 0.8),
        "burst": lambda: burst(cx, cy, s * 0.86),
        "question": lambda: (question_mark(cx, cy, s * 0.68)
                             + " " + circle(cx, cy + s * 0.76, s * 0.14)),
        "exclaim": lambda: (rect(cx - s * 0.13, cy - s * 0.78, s * 0.26,
                                 s * 0.92, s * 0.07)
                            + " " + circle(cx, cy + s * 0.55, s * 0.16)),
        "check": lambda: check_mark(cx, cy, s * 0.8),
        "cross": lambda: cross_mark(cx, cy, s * 0.58),
        "mute_icon": lambda: (circle(cx, cy, s * 0.64)
                              + " " + poly([(cx - s * 0.76, cy - s * 0.76),
                                            (cx + s * 0.76, cy + s * 0.76),
                                            (cx + s * 0.92, cy + s * 0.6),
                                            (cx - s * 0.6, cy - s * 0.92)])),
        "shield": lambda: shield(cx, cy, s * 0.85),
        "lightning": lambda: bolt(cx, cy, s * 0.9),
        "fire": lambda: flame(cx, cy, s * 0.78),
        "crown": lambda: crown(cx, cy, w * 0.95, s * 0.72),
        "money": lambda: (money_bag(cx, cy, s * 0.6)
                          + " " + money_tie(cx, cy, s * 0.6)),
        "thumbs_up": lambda: thumbs_up(cx, cy, s * 0.62),
        "clap": lambda: clap_hands(cx, cy, s * 0.6),
        "boom": lambda: burst(cx, cy, s * 0.86),
        "replay_icon": lambda: (ring(cx, cy, s * 0.66, s * 0.52)
                                + " " + poly([(cx + s * 0.1, cy - s * 0.86),
                                              (cx + s * 0.72, cy - s * 0.5),
                                              (cx + s * 0.12, cy - s * 0.14)])),
        "sub_banner": lambda: banner(cx, cy, s * 2.5, s * 0.66),
        "lower_third": lambda: (rect(cx - s * 1.35, cy + s * 0.02, s * 2.7,
                                     s * 0.42, s * 0.12)
                                + " " + rect(cx - s * 1.35, cy - s * 0.44,
                                             s * 1.15, s * 0.3, s * 0.08)),
        "vs_badge": lambda: burst(cx, cy, s * 0.8, spikes=8),
        "new_icon": lambda: banner(cx, cy, s * 1.5, s * 0.6),
        "skull": lambda: skull(cx, cy, s * 0.72),
    }

    detail: Optional[str] = None
    detail_color: Optional[str] = None
    if name in FACE_SPECS:
        mouth, eyes, _c = FACE_SPECS[name]
        r = s * 0.86
        path = face_base(cx, cy, r)
        detail = face_eyes(cx, cy, r, eyes) + " " + face_mouth(cx, cy, r, mouth)
        detail_color = "#1b1e26"
    elif name == "skull":
        path = skull(cx, cy, s * 0.72)
        detail = skull_holes(cx, cy, s * 0.72)
        detail_color = "#1b1e26"
    else:
        fn = drawers.get(name)
        path = fn() if fn else star(cx, cy, s * 0.7, s * 0.3)
    return path, detail, detail_color


def sticker_text_event(clip, style_name: str, px: float, py: float,
                       size: float) -> str:
    """ASS override block + drawing body for one sticker clip."""
    name = str(clip.meta.get("sticker", "star"))
    path, ink, ink_color = sticker_drawing(name, px, py, size)
    color = clip.meta.get("sticker_color")
    return path, ink, ink_color, color
