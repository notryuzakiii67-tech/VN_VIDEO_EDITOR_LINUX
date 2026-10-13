"""Playback speed, speed curves and reverse/freeze compilation.

Two distinct mechanisms:

* Constant speed  -> ``setpts=PTS/speed`` for video, ``atempo`` chain (or
  ``rubberband`` for pitch shifting) for audio.
* Speed curve     -> the clip is split into short time slices whose speed comes
  from the curve; each slice gets ``trim``+``setpts``.  Slices with the *same*
  speed are only distinguished by their source offset, so this stays exact
  where a single linear ``setpts`` would not be.

Because a varying speed makes a clip's timeline duration different from its
source duration, :func:`solve_curve_duration` finds the timeline duration that
consumes a requested amount of source media.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .expr import num
from .model import Clip, SpeedCurve

MIN_SPEED = 0.05
MAX_SPEED = 20.0

#: Curve presets offered in the Speed panel — VN's six presets plus two extra
#: ramps.  Each entry is (label, [(position 0..1, speed), …]); an empty point
#: list clears the curve.  This lives in the core so the timeline, the renderer
#: and the inspector all read the same table.
CURVE_PRESETS: List[Tuple[str, List[Tuple[float, float]]]] = [
    ("None", []),
    ("Slow Fast Slow", [(0.0, 0.5), (0.5, 2.0), (1.0, 0.5)]),
    ("Fast Slow Fast", [(0.0, 2.0), (0.5, 0.5), (1.0, 2.0)]),
    ("Slow Start", [(0.0, 0.4), (0.35, 1.0), (1.0, 1.6)]),
    ("Slow End", [(0.0, 1.6), (0.65, 1.0), (1.0, 0.4)]),
    ("Slow Middle", [(0.0, 2.0), (0.4, 0.4), (0.6, 0.4), (1.0, 2.0)]),
    ("Velocity Ramp", [(0.0, 0.6), (0.15, 0.8), (0.6, 3.0), (1.0, 3.0)]),
    ("Bullet Time", [(0.0, 1.6), (0.3, 0.35), (0.7, 0.35), (1.0, 1.8)]),
]


def curve_preset_points(name: str) -> List[Tuple[float, float]]:
    for label, pts in CURVE_PRESETS:
        if label == name:
            return list(pts)
    return []


def clamp_speed(s: float) -> float:
    return max(MIN_SPEED, min(MAX_SPEED, s))


# --------------------------------------------------------------------------- #
#  atempo chains
# --------------------------------------------------------------------------- #

def atempo_chain(speed: float, pitch_correct: bool = True,
                 pitch: float = 1.0) -> List[str]:
    """A chain of ``atempo``/``rubberband`` filters achieving ``speed``."""
    speed = clamp_speed(speed)
    filters: List[str] = []
    if abs(pitch - 1.0) > 0.001:
        from .ffmpeg import TOOLCHAIN
        if TOOLCHAIN.has_filter("rubberband"):
            # rubberband changes tempo and pitch together, which is the only
            # way to keep the two independent in one pass.
            filters.append(f"rubberband=tempo={num(speed, 5)}:"
                           f"pitch={num(pitch, 5)}")
            return filters
        # Without rubberband, resampling shifts the pitch and an ``atempo``
        # pass puts the tempo back where it belongs.  Slower than rubberband
        # but it keeps the control working on a stock FFmpeg.
        filters.append(f"asetrate=48000*{num(pitch, 5)},aresample=48000,"
                       f"asetpts=N/SR/TB")
        pitch = 1.0
        speed = clamp_speed(speed / 1.0)
    if not pitch_correct:
        filters.append(f"asetrate=48000*{num(speed, 5)},aresample=48000,atempo=1")
        return filters
    s = speed
    guard = 0
    while s > 2.0 + 1e-9 and guard < 6:
        filters.append("atempo=2.0")
        s /= 2.0
        guard += 1
    while s < 0.5 - 1e-9 and guard < 12:
        filters.append("atempo=0.5")
        s /= 0.5
        guard += 1
    if abs(s - 1.0) > 1e-4:
        filters.append(f"atempo={num(s, 6)}")
    return filters or ["anull"]


def video_setpts(speed: float) -> str:
    return f"setpts=PTS/{num(clamp_speed(speed), 6)}"


def audio_setpts(speed: float) -> str:
    return f"asetpts=PTS/{num(clamp_speed(speed), 6)}"


# --------------------------------------------------------------------------- #
#  Speed curves
# --------------------------------------------------------------------------- #

@dataclass
class SpeedSlice:
    src_start: float          # source seconds (relative to clip src_in)
    src_len: float            # source seconds consumed
    out_start: float          # clip-local timeline seconds where it lands
    out_len: float            # clip-local timeline seconds it occupies
    speed: float

    @property
    def out_end(self) -> float:
        return self.out_start + self.out_len


def curve_slices(curve: SpeedCurve, src_len: float, steps: int = 20,
                 duration: Optional[float] = None) -> List[SpeedSlice]:
    """Convert a speed curve into concrete slices over ``src_len`` media.

    If ``duration`` (timeline length) is given the slices are fitted to it,
    which is what the timeline needs; otherwise the natural duration is used.
    """
    steps = max(4, min(60, int(steps)))
    if not curve or not curve.is_ramp():
        sp = clamp_speed(curve.speed_at(0.5)) if curve else 1.0
        return [SpeedSlice(0.0, src_len, 0.0, src_len / sp, sp)]

    if duration is None:
        # integrate: src consumed = sum(out_len * speed)
        total_out = 0.0
        for i in range(steps):
            p = (i + 0.5) / steps
            total_out += (src_len / steps) / clamp_speed(curve.speed_at(p))
        duration = total_out

    slices: List[SpeedSlice] = []
    src_cursor = 0.0
    out_cursor = 0.0
    # solve for the source consumed by each output slice proportionally
    weights = []
    for i in range(steps):
        p = (i + 0.5) / steps
        weights.append(1.0 / clamp_speed(curve.speed_at(p)))
    wsum = sum(weights) or 1.0
    for i in range(steps):
        p = (i + 0.5) / steps
        out_len = duration * weights[i] / wsum
        sp = clamp_speed(curve.speed_at(p))
        src_piece = out_len * sp
        if i == steps - 1:
            src_piece = src_len - src_cursor
            out_len = duration - out_cursor
        if src_piece <= 1e-6:
            continue
        slices.append(SpeedSlice(src_cursor, src_piece, out_cursor,
                                 max(1e-4, out_len), sp))
        src_cursor += src_piece
        out_cursor += out_len
    return slices


def solve_curve_duration(curve: SpeedCurve, src_len: float,
                         steps: int = 24) -> float:
    """Timeline duration produced by running ``src_len`` media through ``curve``."""
    if not curve or not curve.is_ramp():
        sp = clamp_speed(curve.speed_at(0.5)) if curve else 1.0
        return src_len / sp
    total = 0.0
    for i in range(steps):
        p = (i + 0.5) / steps
        total += (src_len / steps) / clamp_speed(curve.speed_at(p))
    return total


def solve_src_for_duration(curve: SpeedCurve, duration: float,
                           steps: int = 24) -> float:
    """Source media needed to fill ``duration`` timeline seconds."""
    if not curve or not curve.is_ramp():
        sp = clamp_speed(curve.speed_at(0.5)) if curve else 1.0
        return duration * sp
    total = 0.0
    for i in range(steps):
        p = (i + 0.5) / steps
        total += (duration / steps) * clamp_speed(curve.speed_at(p))
    return total


# --------------------------------------------------------------------------- #
#  Graph fragments
# --------------------------------------------------------------------------- #

def video_speed_graph(clip: Clip, in_label: str, out_label: str,
                      counter, fps: float = 30.0,
                      src_in_offset: float = 0.0) -> str:
    """Video fragment handling speed, curves and reverse for one clip.

    Emits ``[in_label]...,[out_label]`` (the input pad is always explicit).
    """
    src_lab = f"[{in_label}]" if in_label else ""
    curve = clip.speed_curve
    src_len = max(0.02, source_span(clip))
    dur = max(0.02, clip.duration)

    if clip.freeze:
        # A frozen clip shows the single frame at ``freeze_at`` for its whole
        # length.  Taking that one frame and letting the layer's canvas hold it
        # (``eof_action=repeat``) is what makes it freeze: ``tpad`` clones the
        # *end* of the decoded input, which is ordinary footage, and it is
        # dropped by the following trim anyway.
        fa = max(0.0, float(clip.freeze_at))
        head = f"{src_lab}trim=start={num(fa, 5)}," if fa > 1e-4 else src_lab
        return (f"{head}trim=end_frame=1,setpts=PTS-STARTPTS[{out_label}]")

    if curve is not None and curve.is_ramp() and not clip.reverse:
        slices = curve_slices(curve, src_len, steps=20, duration=dur)
        if len(slices) < 2:
            speed = clamp_speed(curve.speed_at(0.5))
            return (f"{src_lab}setpts=(PTS-STARTPTS)/{num(speed, 6)},"
                    f"trim=duration={num(dur, 4)},setpts=PTS-STARTPTS"
                    f"[{out_label}]")
        # one input pad may only be consumed once, so split it first
        srcs = [counter() for _ in slices]
        dsts = [counter() for _ in slices]
        parts: List[str] = [
            f"{src_lab}split={len(slices)}" +
            "".join(f"[{x}]" for x in srcs)]
        for sl, s_lab, d_lab in zip(slices, srcs, dsts):
            st = src_in_offset + sl.src_start
            en = st + sl.src_len
            parts.append(
                f"[{s_lab}]trim=start={num(st, 5)}:end={num(en, 5)},"
                f"setpts=(PTS-STARTPTS)/{num(sl.speed, 6)}+"
                f"{num(sl.out_start, 5)}/TB,"
                f"fps={num(fps)}[{d_lab}]")
        parts.append("".join(f"[{x}]" for x in dsts) +
                     f"concat=n={len(slices)}:v=1:a=0,"
                     f"trim=duration={num(dur, 4)},setpts=PTS-STARTPTS"
                     f"[{out_label}]")
        return ";".join(parts)

    speed = clamp_speed(clip.speed * (curve.speed_at(0.5) if curve else 1.0))
    chain: List[str] = []
    if clip.reverse:
        chain.append("reverse")
    if abs(speed - 1.0) < 1e-4:
        chain.append("setpts=PTS-STARTPTS")
    else:
        chain.append(f"setpts=(PTS-STARTPTS)/{num(speed, 6)}")
    chain.append(f"trim=duration={num(dur, 4)}")
    chain.append("setpts=PTS-STARTPTS")
    return src_lab + ",".join(chain) + f"[{out_label}]"


def audio_speed_graph(clip: Clip, in_label: str, out_label: str,
                      counter, src_in_offset: float = 0.0) -> str:
    """Audio fragment handling speed/reverse for one clip.

    Emits ``[in_label]...,[out_label]``; ``in_label`` may be empty so callers
    can prepend their own input pad.
    """
    src_lab = f"[{in_label}]" if in_label else ""
    ap = clip.audio
    curve = clip.speed_curve
    src_len = max(0.02, source_span(clip))
    dur = max(0.02, clip.duration)

    if ap.muted or ap.speed_mode == "mute" or clip.freeze:
        # Silence, but still consuming the input pad: ``anullsrc`` is a source
        # filter, so feeding ``[n:a]`` into it produces a graph FFmpeg refuses
        # to bind ("Error binding filtergraph inputs/outputs").  A frozen clip
        # is silent here too rather than playing a 20 ms scrap of the source.
        return (f"{src_lab}volume=0,atrim=duration={num(dur, 4)},"
                f"asetpts=PTS-STARTPTS[{out_label}]")

    if curve is not None and curve.is_ramp() and not clip.reverse:
        slices = curve_slices(curve, src_len, steps=12, duration=dur)
        if len(slices) < 2:
            speed2 = clamp_speed(curve.speed_at(0.5))
            chain2 = [f"atrim=duration={num(src_len, 5)}",
                      "asetpts=PTS-STARTPTS"]
            chain2 += atempo_chain(speed2, ap.speed_mode == "pitch_correct",
                                   ap.pitch)
            chain2.append(f"atrim=duration={num(dur, 4)}")
            return src_lab + ",".join(chain2) + f"[{out_label}]"
        # an input pad can only be consumed once, so split it per slice
        srcs = [counter() for _ in slices]
        dsts = [counter() for _ in slices]
        frags: List[str] = [
            f"{src_lab}asplit={len(slices)}" + "".join(f"[{x}]" for x in srcs)]
        for sl, s_lab, d_lab in zip(slices, srcs, dsts):
            st = src_in_offset + sl.src_start
            en = st + sl.src_len
            chain = [f"atrim=start={num(st, 5)}:end={num(en, 5)}",
                     "asetpts=PTS-STARTPTS"]
            chain += atempo_chain(sl.speed, ap.speed_mode == "pitch_correct",
                                  ap.pitch)
            chain.append(f"adelay={int(sl.out_start * 1000)}|"
                         f"{int(sl.out_start * 1000)}:all=1")
            frags.append(f"[{s_lab}]" + ",".join(chain) + f"[{d_lab}]")
        frags.append("".join(f"[{x}]" for x in dsts) +
                     f"amix=inputs={len(slices)}:normalize=0:"
                     f"duration=longest[{out_label}]")
        return ";".join(frags)

    speed = clamp_speed(clip.speed * (curve.speed_at(0.5) if curve else 1.0))
    parts: List[str] = []
    if clip.reverse:
        parts.append("areverse")
    parts.append(f"atrim=duration={num(src_len, 5)}")
    parts.append("asetpts=PTS-STARTPTS")
    # ``pitch`` is a control of its own: it has to apply even when the clip
    # plays at normal speed, otherwise the slider does nothing on its own.
    if abs(speed - 1.0) > 1e-4 or abs(ap.pitch - 1.0) > 0.001:
        parts.extend(atempo_chain(speed, ap.speed_mode == "pitch_correct",
                                  ap.pitch))
    parts.append("aformat=sample_fmts=fltp:sample_rates=48000:"
                 "channel_layouts=stereo")
    parts.append(f"atrim=duration={num(dur, 4)}")
    parts.append("asetpts=PTS-STARTPTS")
    return src_lab + ",".join(parts) + f"[{out_label}]"


def source_span(clip: Clip) -> float:
    """Seconds of source media a clip consumes."""
    if clip.freeze:
        return 0.02
    if clip.speed_curve and clip.speed_curve.is_ramp():
        return solve_src_for_duration(clip.speed_curve, clip.duration)
    return clip.duration * clamp_speed(clip.speed)


def retime_duration(clip: Clip, new_source_len: float) -> float:
    """Timeline duration for consuming ``new_source_len`` of media."""
    if clip.speed_curve and clip.speed_curve.is_ramp():
        return solve_curve_duration(clip.speed_curve, new_source_len)
    return new_source_len / clamp_speed(clip.speed)
