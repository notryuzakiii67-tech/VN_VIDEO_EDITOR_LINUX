"""Audio graph construction: per-clip shaping, fades, effects and the mixdown."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .expr import num
from .model import AudioProps, Clip, Project, Track
from .speedcurve import audio_speed_graph, clamp_speed

SAMPLE_RATE = 48000

#: Fade shapes offered in the inspector.  The values are FFmpeg ``afade``
#: curve names; the keys are what ``AudioProps.fade_type`` stores.  Measured
#: mid-fade gain (tone at -24.1 dB RMS, 1 s fade, 0-0.3 s window):
#: linear -39.3, smooth -35.6, gentle -52.0, quick -25.9 dB.
FADE_CURVES = {
    "linear": "tri",
    "smooth": "qsin",
    "gentle": "qua",
    "quick": "log",
}


# --------------------------------------------------------------------------- #
#  Per-clip chain
# --------------------------------------------------------------------------- #

def clip_audio_filters(clip: Clip, counter, src_in: float,
                       track_volume: float = 1.0,
                       offset: float = 0.0,
                       max_duration: float = 0.0) -> str:
    """Full audio filtergraph for one clip, ending in an output label."""
    ap = clip.audio
    label = counter()
    parts: List[str] = []

    # 1. speed / reverse / trim
    inner = audio_speed_graph(clip, "", label, counter, src_in)
    # audio_speed_graph emits a complete fragment ending in [label]
    fragment = inner

    # 2. gain envelope + fades + tone shaping appended to that fragment
    post: List[str] = []

    vol = max(0.0, ap.volume) * max(0.0, track_volume)
    env = clip.audio_animations.get("volume")
    if env:
        from .expr import kf_expr
        expr = kf_expr(env, vol, "t")
        post.append(f"volume=volume='{expr}':eval=frame")
    elif abs(vol - 1.0) > 1e-4:
        post.append(f"volume={num(vol, 5)}")

    if ap.highpass > 0:
        post.append(f"highpass=f={num(max(20.0, ap.highpass))}")
    if ap.lowpass > 0:
        post.append(f"lowpass=f={num(max(200.0, ap.lowpass))}")
    if abs(ap.eq_low) > 0.01:
        post.append(f"equalizer=f=120:width_type=o:width=1.2:g={num(ap.eq_low)}")
    if abs(ap.eq_mid) > 0.01:
        post.append(f"equalizer=f=1200:width_type=o:width=1.0:g={num(ap.eq_mid)}")
    if abs(ap.eq_high) > 0.01:
        post.append(f"equalizer=f=6000:width_type=o:width=1.0:g={num(ap.eq_high)}")
    if abs(ap.bass_boost) > 0.01:
        post.append(f"equalizer=f=90:width_type=o:width=1.0:g={num(ap.bass_boost)}")
    if abs(ap.treble_boost) > 0.01:
        post.append(f"equalizer=f=9000:width_type=o:width=1.5:g={num(ap.treble_boost)}")
    if ap.compressor > 0.01:
        post.append(f"acompressor=threshold={num(-6 - 24 * max(0.0, min(1.0, ap.compressor)))}dB:"
                    f"ratio={num(1 + 9 * max(0.0, min(1.0, ap.compressor)), 3)}:"
                    f"attack=8:release=180")
    if ap.denoise > 0.01:
        # ``nf`` (the assumed noise floor) has to sit near the level of the
        # noise itself: pinned at -32 dB it left almost all hiss in place
        # (measured: -0.7 dB).  Tracking the slider from -36 dB to -14 dB takes
        # the floor down by ~12 dB at full strength on a -19 dB hiss.
        d = max(0.0, min(1.0, ap.denoise))
        # ``nf`` is capped at -20 dB by FFmpeg, and asking for more makes the
        # whole graph fail to build.
        post.append(f"afftdn=nr={num(6 + 42 * d, 3)}:"
                    f"nf={num(min(-20.0, -36 + 22 * d), 3)}")
    if abs(ap.three_d) > 0.01:
        post.append(f"extrastereo=m={num(1 + ap.three_d * 1.5, 3)}")
    if ap.echo > 0.01:
        # in_gain stays at 1.0: adding an echo must not duck the direct sound.
        # Two taps, the second twice as far out as the first, so it reads as a
        # repeat rather than a single blur.
        e = max(0.0, min(1.0, ap.echo))
        d1 = int(120 + 240 * e)
        post.append(f"aecho=1.0:{num(0.7 + 0.2 * e, 3)}:"
                    f"{d1}|{d1 * 2}:"
                    f"{num(0.45 + 0.35 * e, 3)}|{num(0.25 + 0.3 * e, 3)}")
    if ap.reverb > 0.01:
        r = max(0.0, min(1.0, ap.reverb))
        d1 = int(50 + 90 * r)
        post.append(f"aecho=1.0:{num(0.6 + 0.25 * r, 3)}:"
                    f"{d1}|{d1 * 2}|{d1 * 3}:"
                    f"{num(0.4 + 0.3 * r, 3)}|{num(0.3 + 0.25 * r, 3)}|"
                    f"{num(0.2 + 0.2 * r, 3)}")
    if ap.normalize:
        post.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    if abs(ap.pan) > 0.005:
        # Stereo balance.  The source is forced to stereo first so the two
        # channel expressions are always valid, and the far channel is the one
        # that moves - the near channel keeps its level, which is what a
        # balance control is supposed to do.
        pan = max(-1.0, min(1.0, ap.pan))
        gl = 1.0 if pan < 0 else 1.0 - pan
        gr = 1.0 if pan > 0 else 1.0 + pan
        post.append("aformat=channel_layouts=stereo")
        post.append(f"pan=stereo|c0={num(gl, 4)}*c0|c1={num(gr, 4)}*c1")

    curve = FADE_CURVES.get(ap.fade_type, "tri")
    fade_in = max(0.0, ap.fade_in)
    fade_out = max(0.0, ap.fade_out)
    if fade_in > 0.001:
        post.append(f"afade=t=in:st=0:d={num(min(fade_in, clip.duration), 4)}:"
                    f"curve={curve}")
    if fade_out > 0.001:
        start = max(0.0, clip.duration - fade_out)
        post.append(f"afade=t=out:st={num(start, 4)}:"
                    f"d={num(min(fade_out, clip.duration), 4)}:curve={curve}")

    # 3. placement on the timeline + padding to project length
    delay_ms = int(round((clip.start + offset) * 1000))
    if delay_ms < 0:
        # The graph opens inside this clip (the preview pipe starts at the
        # playhead): the part before the window is dropped, because a negative
        # ``adelay`` is not something FFmpeg will build.  The clip's own fades
        # and envelopes have already been applied above, on its own clock.
        post.append(f"atrim=start={num(-delay_ms / 1000.0, 5)}")
        post.append("asetpts=PTS-STARTPTS")
        delay_ms = 0
    post.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    if max_duration > 0:
        post.append(f"apad")
        post.append(f"atrim=duration={num(max_duration, 4)}")

    if post:
        # splice the post filters before the terminal label
        if fragment.endswith(f"[{label}]"):
            fragment = fragment[:-len(f"[{label}]")] + "," + ",".join(post) + f"[{label}]"
        else:
            fragment = fragment + "," + ",".join(post)
    if delay_ms > 0:
        # Placement by *samples*, not by timestamp.  ``adelay`` only shifts
        # frame timestamps, and ``amix`` aligns its inputs by frame/sample
        # count, ignoring those timestamps — so with ``adelay`` a clip that
        # starts at 3 s was mixed as if it started at 0.  Measured on FFmpeg
        # 7.0.2: a branch traced with ashowinfo carries its content at
        # 3.0-6.0 s right up to the amix input, yet the mix output contains
        # it at 0-3 s.  Prepending real silence makes the sample counts line
        # up with the timeline, which is the only alignment amix honours.
        content = counter()
        silence = counter()
        body = fragment[:-len(f"[{label}]")] if fragment.endswith(f"[{label}]") \
            else fragment
        delay = num(delay_ms / 1000.0, 4)
        fragment = (
            f"{body}[{content}];"
            f"anullsrc=r={SAMPLE_RATE}:cl=stereo,"
            f"atrim=duration={delay},asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:"
            f"channel_layouts=stereo[{silence}];"
            f"[{silence}][{content}]concat=n=2:v=0:a=1[{label}]")
    return fragment


# --------------------------------------------------------------------------- #
#  Track / project mixdown
# --------------------------------------------------------------------------- #

@dataclass
class AudioInput:
    """One ffmpeg input feeding the audio mix."""
    clip: Clip
    index: int
    src_in: float
    track_volume: float


def build_mix(inputs: Sequence[AudioInput], duration: float, counter,
              master_volume: float = 1.0, normalize: bool = False,
              start: float = 0.0) -> str:
    """Mix all audio inputs into one stereo stream labelled ``[o]``."""
    duration = max(0.05, duration)
    if not inputs:
        lab = counter()
        return (f"anullsrc=r={SAMPLE_RATE}:cl=stereo,"
                f"atrim=duration={num(duration, 4)}[{lab}]")

    frags: List[str] = []
    labels: List[str] = []
    for ai in inputs:
        frag = clip_audio_filters(ai.clip, counter, ai.src_in,
                                  ai.track_volume, offset=-start,
                                  max_duration=duration)
        labels.append(ai.label if hasattr(ai, "label") else
                      frag.rsplit("[", 1)[1].rstrip("]"))
        frags.append(f"[{ai.index}:a]{frag}")
    labs = "".join(f"[{l}]" for l in labels)
    out = counter()
    mix = (f"{labs}amix=inputs={len(labels)}:duration=longest:"
           f"normalize=0:dropout_transition=0")
    if abs(master_volume - 1.0) > 1e-4:
        mix += f",volume={num(master_volume, 5)}"
    if normalize:
        mix += ",loudnorm=I=-14:TP=-1.0:LRA=11"
    mix += (",alimiter=limit=0.97:level=disabled,"
            "aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo[{out}]")
    return ";".join(frags) + ";" + mix


def extract_audio_cmd(src: str, out: str, start: float = 0.0,
                      duration: float = 0.0, rate: int = SAMPLE_RATE) -> List[str]:
    args = ["-y", "-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.4f}"]
    args += ["-i", str(src)]
    if duration > 0:
        args += ["-t", f"{duration:.4f}"]
    args += ["-vn", "-ac", "2", "-ar", str(rate), "-c:a", "pcm_s16le", str(out)]
    return args


def mixdown_wav(project: Project, out_path: str, duration: float,
                start: float = 0.0) -> Tuple[List[str], List[AudioInput]]:
    """Build an argv list + input table for exporting the audio stem."""
    inputs: List[AudioInput] = []
    argv: List[str] = []
    index = 0
    solo = any(t.solo for t in project.tracks if t.kind == "audio")
    counter_iter = _Counter()

    for track in project.tracks:
        if track.kind == "audio" and (track.muted or (solo and not track.solo)):
            continue
        for clip in sorted(track.clips, key=lambda c: c.start):
            if clip.kind in ("text", "sticker", "shape", "adjustment"):
                continue
            if not clip.enabled or not clip.path:
                continue
            if clip.kind == "audio" or (clip.kind == "video" and _has_audio(clip)):
                if clip.audio.muted:
                    continue
                if not (clip.end > start and clip.start < start + duration):
                    continue
                argv += _input_args(clip, start)
                inputs.append(AudioInput(clip, index,
                                         _source_in(clip, start), track.volume))
                index += 1
    if not inputs:
        return [], []
    graph = build_mix(inputs, duration, counter_iter.next,
                      master_volume=1.0, start=start)
    argv += ["-filter_complex", graph,
             "-map", "[o]", "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE),
             "-ac", "2", str(out_path)]
    return argv, inputs


def clip_has_audio(clip: Clip) -> bool:
    """Whether a clip contributes an audio stream.

    A clip imported through the UI carries ``meta["has_audio"]``.  When that flag
    is missing — an older project, a clip pasted without its metadata, media
    replaced after import — the file itself is probed.  Guessing "yes" here is
    what used to make a silent export fail outright: the mix referenced an audio
    stream the input never had.
    """
    if clip.kind == "audio":
        return True
    if not clip.path:
        return False
    try:
        from .probe import probe as _probe
        info = _probe(str(clip.path), use_cache=True)
    except Exception:                              # noqa: BLE001
        info = None
    # A file the probe could actually read settles the question, even against a
    # stale flag (media replaced after import, project written elsewhere).
    usable = (info is not None
              and (bool(getattr(info, "has_audio", False))
                   or bool(getattr(info, "has_video", False))
                   or float(getattr(info, "duration", 0.0) or 0.0) > 0.001))
    if usable:
        return bool(getattr(info, "has_audio", False))
    flag = clip.meta.get("has_audio")
    return bool(flag) if flag is not None else False


_has_audio = clip_has_audio


def _source_in(clip: Clip, start: float) -> float:
    if clip.start >= start:
        return clip.src_in
    return clip.src_in + (start - clip.start)


def _input_args(clip: Clip, start: float) -> List[str]:
    ss = 0.0
    if clip.start > 0 and clip.start >= start:
        ss = max(0.0, clip.src_in)
    elif clip.start < start:
        ss = max(0.0, clip.src_in)
    args: List[str] = []
    if ss > 0.001:
        args += ["-ss", f"{ss:.5f}"]
    args += ["-i", str(clip.path)]
    return args


class _Counter:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"a{self._n}"

    __call__ = next


# --------------------------------------------------------------------------- #
#  Voice-over
# --------------------------------------------------------------------------- #

def voiceover_args(device: str = "default", duration: float = 0.0,
                   out: str = "/tmp/vo.wav") -> List[str]:
    """Argument list for recording from ALSA/PulseAudio."""
    args = ["-y", "-v", "error", "-f", "pulse" if _has_pulse() else "alsa",
            "-i", device]
    if duration > 0:
        args += ["-t", f"{duration:.3f}"]
    args += ["-ac", "2", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)]
    return args


def _has_pulse() -> bool:
    import shutil
    return shutil.which("pactl") is not None or shutil.which("pulseaudio") is not None


# --------------------------------------------------------------------------- #
#  Master volume envelope
# --------------------------------------------------------------------------- #

def volume_curve_filter(points: Sequence[Tuple[float, float]],
                        duration: float) -> str:
    """Piecewise-linear master volume from (time, gain) pairs."""
    if not points:
        return "anull"
    pts = sorted(points)
    expr = num(pts[-1][1])
    for i in range(len(pts) - 1, 0, -1):
        a, b = pts[i - 1], pts[i]
        span = max(1e-4, b[0] - a[0])
        p = f"clip((t-{num(a[0])})/{num(span)},0,1)"
        seg = f"({num(a[1])}+({num(b[1] - a[1])})*{p})"
        expr = f"if(lt(t,{num(b[0])}),{seg},{expr})"
    return f"volume=volume='min(1,{expr})':eval=frame"
