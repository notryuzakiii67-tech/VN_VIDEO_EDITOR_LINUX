"""Audio graph construction: per-clip shaping, fades, effects and the mixdown."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .expr import num
from .model import AudioProps, Clip, Project, Track
from .speedcurve import audio_speed_graph, clamp_speed

SAMPLE_RATE = 48000


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
        post.append(f"afftdn=nr={num(6 + 18 * ap.denoise, 3)}:nf=-32")
    if abs(ap.three_d) > 0.01:
        post.append(f"extrastereo=m={num(1 + ap.three_d * 1.5, 3)}")
    if ap.echo > 0.01:
        post.append(f"aecho=0.8:{num(0.5 + 0.35 * ap.echo, 3)}:"
                    f"{num(120 + 240 * ap.echo, 1)}:{num(0.25 + 0.4 * ap.echo, 3)}")
    if ap.reverb > 0.01:
        post.append(f"aecho=0.85:0.75:{num(60 + 120 * ap.reverb, 1)}:"
                    f"{num(0.2 + 0.5 * ap.reverb, 3)}")
    if ap.normalize:
        post.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    fade_in = max(0.0, ap.fade_in)
    fade_out = max(0.0, ap.fade_out)
    if fade_in > 0.001:
        post.append(f"afade=t=in:st=0:d={num(min(fade_in, clip.duration), 4)}")
    if fade_out > 0.001:
        start = max(0.0, clip.duration - fade_out)
        post.append(f"afade=t=out:st={num(start, 4)}:d={num(min(fade_out, clip.duration), 4)}")

    # 3. placement on the timeline + padding to project length
    delay_ms = int(round((clip.start + offset) * 1000))
    if delay_ms > 0:
        post.append(f"adelay={delay_ms}|{delay_ms}:all=1")
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
              master_volume: float = 1.0, normalize: bool = False) -> str:
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
                                  ai.track_volume, max_duration=duration)
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
    graph = build_mix(inputs, duration + max(0.0, 0.0), counter_iter.next,
                      master_volume=1.0)
    argv += ["-filter_complex", graph,
             "-map", "[o]", "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE),
             "-ac", "2", str(out_path)]
    return argv, inputs


def _has_audio(clip: Clip) -> bool:
    return bool(clip.meta.get("has_audio", True))


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
