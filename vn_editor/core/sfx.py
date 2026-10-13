"""Procedurally synthesised sound effects.

Everything here is generated from oscillators, noise and filters at first run,
so the editor ships with a usable SFX library without embedding (or licensing)
any third-party audio.  The meme-classic effects people reach for in
Minecraft/Shorts edits — the descending "fahhh", the "thud", the "get out" —
are approximated with a formant/brass model, and if ``espeak-ng`` is installed
the "get out" line is upgraded to a real synthetic voice (still generated
locally, still royalty free).

Additional slots let a user drop their *own* files into the SFX folder; the
library then prefers the user's file (see :func:`user_override`).
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore

SR = 44100


# --------------------------------------------------------------------------- #
#  DSP helpers
# --------------------------------------------------------------------------- #

def _t(n: int) -> "np.ndarray":
    return np.arange(n, dtype=np.float64) / SR


def _noise(n: int, seed: int = 0) -> "np.ndarray":
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n) * 0.5


def _sine(freq, n: int) -> "np.ndarray":
    t = _t(n)
    if hasattr(freq, "__len__"):
        ph = 2 * math.pi * np.cumsum(np.asarray(freq, dtype=np.float64)) / SR
        return np.sin(ph)
    return np.sin(2 * math.pi * freq * t)


def _saw(freq, n: int) -> "np.ndarray":
    t = _t(n)
    if hasattr(freq, "__len__"):
        ph = np.cumsum(np.asarray(freq, dtype=np.float64)) / SR
        return 2.0 * (ph - np.floor(ph + 0.5))
    return 2.0 * ((freq * t) - np.floor(freq * t + 0.5))


def _square(freq, n: int) -> "np.ndarray":
    return np.sign(_sine(freq, n))


def _lin(a: float, b: float, n: int) -> "np.ndarray":
    return np.linspace(a, b, n)


def _exp_env(n: int, attack: float = 0.005, decay: float = 0.3,
             power: float = 2.0) -> "np.ndarray":
    t = _t(n)
    a = max(1e-4, attack)
    env = np.clip(t / a, 0, 1) * np.exp(-np.maximum(0.0, t - a) / max(1e-4, decay / power))
    return env


def _adsr(n: int, a: float, d: float, s: float, r: float) -> "np.ndarray":
    t = _t(n)
    total = n / SR
    out = np.zeros(n)
    ai = int(min(n, a * SR))
    di = int(min(n - ai, d * SR))
    si = max(0, n - ai - di - int(r * SR))
    ri = n - ai - di - si
    if ai > 0:
        out[:ai] = np.linspace(0, 1, ai)
    if di > 0:
        out[ai:ai + di] = np.linspace(1, s, di)
    if si > 0:
        out[ai + di:ai + di + si] = s
    if ri > 0:
        start = out[ai + di + si - 1] if ai + di + si > 0 else s
        out[ai + di + si:] = np.linspace(start, 0, ri)
    return out


def _biquad(x: "np.ndarray", b0: float, b1: float, b2: float,
            a1: float, a2: float) -> "np.ndarray":
    n = len(x)
    y = np.zeros(n)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(n):
        v = b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, x[i]
        y2, y1 = y1, v
        y[i] = v
    return y


def _lowpass(x: "np.ndarray", cutoff: float, q: float = 0.707) -> "np.ndarray":
    w0 = 2 * math.pi * max(20.0, min(cutoff, SR * 0.45)) / SR
    alpha = math.sin(w0) / (2 * q)
    b0 = (1 - math.cos(w0)) / 2
    b1 = 1 - math.cos(w0)
    b2 = b0
    a0 = 1 + alpha
    a1 = -2 * math.cos(w0)
    a2 = 1 - alpha
    return _biquad(x, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _highpass(x: "np.ndarray", cutoff: float, q: float = 0.707) -> "np.ndarray":
    w0 = 2 * math.pi * max(20.0, min(cutoff, SR * 0.45)) / SR
    alpha = math.sin(w0) / (2 * q)
    b0 = (1 + math.cos(w0)) / 2
    b1 = -(1 + math.cos(w0))
    b2 = b0
    a0 = 1 + alpha
    a1 = -2 * math.cos(w0)
    a2 = 1 - alpha
    return _biquad(x, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _bandpass(x: "np.ndarray", f0: float, q: float = 4.0) -> "np.ndarray":
    w0 = 2 * math.pi * max(30.0, min(f0, SR * 0.45)) / SR
    alpha = math.sin(w0) / (2 * q)
    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1 + alpha
    a1 = -2 * math.cos(w0)
    a2 = 1 - alpha
    return _biquad(x, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _soft_clip(x: "np.ndarray", drive: float = 1.4) -> "np.ndarray":
    return np.tanh(x * drive)


def _normalize(x: "np.ndarray", peak: float = 0.92) -> "np.ndarray":
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    if m < 1e-9:
        return x
    return x * (peak / m)


def _reverb(x: "np.ndarray", amount: float = 0.35, delay: float = 0.075,
            taps: int = 6) -> "np.ndarray":
    out = x.copy()
    d = int(delay * SR)
    for i in range(1, taps + 1):
        g = amount * (0.72 ** i)
        if d * i >= len(x):
            break
        out[d * i:] += x[:len(x) - d * i] * g
    return out


def _resample(x: "np.ndarray", factor: float) -> "np.ndarray":
    if factor <= 1.001:
        return x
    n = int(len(x) / factor)
    idx = np.linspace(0, len(x) - 1, max(1, n))
    return np.interp(idx, np.arange(len(x)), x)


def _mix(*parts: "np.ndarray") -> "np.ndarray":
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:len(p)] += p
    return out


def _delay_line(x, seconds, gain=0.6, repeats=3):
    d = int(seconds * SR)
    out = x.copy()
    for i in range(1, repeats + 1):
        g = gain ** i
        if d * i >= len(out):
            break
        out[d * i:] += x[:len(out) - d * i] * g
    return out


def _write_wav(path: Path, data: "np.ndarray", sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(data, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


# --------------------------------------------------------------------------- #
#  Voice / formant model (for the meme "voice" effects)
# --------------------------------------------------------------------------- #

#: F1/F2/F3 formants for the vowels used by the built-in phrases
FORMANTS = {
    "a":  (800, 1150, 2900),
    "e":  (400, 2000, 2550),
    "i":  (350, 2100, 2700),
    "o":  (450, 800, 2830),
    "u":  (325, 700, 2530),
    "uh": (600, 1200, 2500),
    "ah": (730, 1090, 2440),
    "ow": (500, 900, 2600),
    "ee": (300, 2200, 3000),
}


def _glottal(n: int, f0_curve: "np.ndarray", buzz: float = 0.55) -> "np.ndarray":
    """Simple glottal pulse train with jitter."""
    ph = np.cumsum(f0_curve) / SR
    frac = ph - np.floor(ph)
    pulse = np.exp(-frac * 7.0) * 2 - 0.35
    jitter = np.sin(2 * math.pi * 5.3 * _t(n)) * 0.02
    return pulse + jitter + buzz * np.sin(2 * math.pi * 2 * ph)


def _say(phrase: List[Tuple[str, float, float]], f0_start: float = 150.0,
         f0_end: float = 105.0, bend: float = 0.0) -> "np.ndarray":
    """Render a phrase of (vowel, duration, amplitude) with formant filtering."""
    parts: List["np.ndarray"] = []
    total = sum(d for _, d, _ in phrase)
    for idx, (vowel, dur, amp) in enumerate(phrase):
        n = int(dur * SR)
        if n <= 8:
            continue
        pos = sum(d for _, d, _ in phrase[:idx]) / max(1e-6, total)
        f0 = f0_start + (f0_end - f0_start) * pos + bend * math.sin(pos * 3.1)
        f0_curve = _lin(f0 * 1.03, f0 * 0.97, n)
        src = _glottal(n, f0_curve)
        f1, f2, f3 = FORMANTS.get(vowel, FORMANTS["ah"])
        shaped = (_bandpass(src, f1, 5.0) * 1.0
                  + _bandpass(src, f2, 7.0) * 0.55
                  + _bandpass(src, f3, 9.0) * 0.25
                  + src * 0.12)
        env = _adsr(n, 0.02, 0.06, 0.85, min(0.12, dur * 0.5)) * amp
        parts.append(shaped * env)
    if not parts:
        return np.zeros(64)
    gap = np.zeros(int(0.02 * SR))
    out: List["np.ndarray"] = []
    for i, p in enumerate(parts):
        out.append(p)
        if i != len(parts) - 1:
            out.append(gap)
    return np.concatenate(out)


# --------------------------------------------------------------------------- #
#  Generators
# --------------------------------------------------------------------------- #

def _gen_fahhh(dur: float = 1.30, start_f: float = 330.0, end_f: float = 96.0,
               drive: float = 1.6, seed: int = 1) -> "np.ndarray":
    """"Fahhh" — a brassy descending blat with vibrato and a formant tilt."""
    n = int(dur * SR)
    sweep = np.exp(np.linspace(math.log(start_f), math.log(end_f), n))
    vibrato = 1.0 + 0.035 * np.sin(2 * math.pi * 6.5 * _t(n))
    f = sweep * vibrato
    osc = (_saw(f, n) * 0.55 + _saw(f * 1.005, n) * 0.30 + _saw(f * 0.5, n) * 0.35)
    osc = _bandpass(osc, 620, 0.9) + _bandpass(osc, 1500, 1.2) * 0.4
    lp = _lowpass(osc, 2600.0)
    lp = _lowpass(lp, 1200.0)
    env = _exp_env(n, 0.03, dur * 0.85, 1.6)
    env *= np.clip(_t(n) / 0.04, 0, 1)
    body = lp * env * drive
    body = _soft_clip(body, 1.2)
    tail = _reverb(body, 0.22, 0.06, 4)
    return _normalize(body * 0.85 + tail * 0.35)


def _gen_thud(dur: float = 0.34, f0: float = 78.0, seed: int = 2) -> "np.ndarray":
    n = int(dur * SR)
    freq = _lin(f0 * 1.7, f0 * 0.72, n)
    body = _sine(freq, n)
    body *= _exp_env(n, 0.002, dur * 0.42, 1.9)
    click = _noise(n, seed) * np.exp(-_t(n) * 260) * 0.6
    click = _bandpass(click, 2400, 1.1)
    rumble = _lowpass(_noise(n, seed + 1), 260) * np.exp(-_t(n) * 12) * 0.5
    out = _soft_clip(body * 1.25 + click + rumble, 1.15)
    return _normalize(out)


def _gen_deep_thud(dur: float = 0.75, f0: float = 46.0) -> "np.ndarray":
    base = _gen_thud(dur, f0, seed=7)
    sub = _sine(_lin(f0 * 1.4, f0 * 0.6, int(dur * SR)), int(dur * SR))
    sub *= _exp_env(int(dur * SR), 0.005, dur * 0.5, 1.7)
    return _normalize(base * 0.85 + sub * 0.5)


def _gen_boom(dur: float = 1.6, start: float = 220.0, end: float = 30.0,
              seed: int = 3) -> "np.ndarray":
    n = int(dur * SR)
    sweep = np.exp(np.linspace(math.log(start), math.log(end), n))
    body = _sine(sweep, n) * _exp_env(n, 0.004, dur * 0.28, 1.5)
    crack = _noise(n, seed) * np.exp(-_t(n) * 30)
    crack = _lowpass(crack, 3200)
    tail = _reverb(crack * 0.4, 0.35, 0.11, 7)
    return _normalize(body * 1.0 + crack * 0.55 + tail * 0.5)


def _gen_vine_boom(seed: int = 4) -> "np.ndarray":
    n = int(1.35 * SR)
    sweep = np.exp(np.linspace(math.log(180.0), math.log(40.0), n))
    body = (_sine(sweep, n) * 0.9 + _sine(sweep * 2, n) * 0.2)
    body *= _exp_env(n, 0.004, 0.55, 1.8)
    air = _lowpass(_noise(n, seed), 900) * _exp_env(n, 0.01, 0.35, 1.5) * 0.4
    return _normalize(_reverb(body + air, 0.3, 0.09, 5) * 0.9)


def _gen_get_out(echo: bool = False) -> "np.ndarray":
    """'Get out!' as an aggressive formant-synth shout."""
    phrase = [("e", 0.13, 0.55), ("uh", 0.06, 0.32),
              ("ow", 0.24, 0.95), ("t", 0.05, 0.30)]
    voice = _say(phrase, f0_start=196.0, f0_end=132.0, bend=9.0)
    n = len(voice)
    # consonant transients: a hard 'g' plosive and a 't' stop
    g = _noise(int(0.03 * SR), 11) * np.exp(-_t(int(0.03 * SR)) * 90)
    g = _bandpass(g, 1800, 1.0) * 0.55
    tstop = _noise(int(0.035 * SR), 12) * np.exp(-_t(int(0.035 * SR)) * 55)
    tstop = _bandpass(tstop, 3800, 1.4) * 0.5
    voice[:len(g)] += g
    off = int(0.42 * SR)
    if off + len(tstop) < n:
        voice[off:off + len(tstop)] += tstop
    voice = _soft_clip(voice * 1.6, 1.35)
    voice = _highpass(voice, 110)
    out = _reverb(voice, 0.25, 0.08, 5) if echo else _reverb(voice, 0.14, 0.05, 3)
    return _normalize(voice * 0.9 + out * 0.45)


def _gen_bruh() -> "np.ndarray":
    voice = _say([("u", 0.22, 0.9), ("uh", 0.30, 0.8)], f0_start=118.0,
                 f0_end=84.0, bend=4.0)
    voice = _lowpass(voice, 2600)
    return _normalize(_soft_clip(voice * 1.5, 1.2) * 0.9
                      + _reverb(voice, 0.2, 0.07, 4) * 0.35)


def _gen_oof(seed: int = 5) -> "np.ndarray":
    voice = _say([("u", 0.09, 0.9), ("uh", 0.13, 0.7)], f0_start=150.0,
                 f0_end=96.0)
    thud = _gen_thud(0.22, 92.0, seed)
    return _normalize(_mix(voice * 0.9, thud * 0.6))


def _gen_laugh() -> "np.ndarray":
    parts = []
    for i in range(5):
        v = _say([("a", 0.085, 0.85), ("uh", 0.05, 0.5)],
                 f0_start=210 - i * 6, f0_end=150 - i * 5)
        parts.append(v)
        parts.append(np.zeros(int(0.045 * SR)))
    return _normalize(np.concatenate(parts) * 0.95)


def _gen_scream() -> "np.ndarray":
    n = int(0.9 * SR)
    f = _lin(420, 900, n) * (1 + 0.05 * np.sin(2 * math.pi * 7 * _t(n)))
    src = _saw(f, n) * 0.6 + _noise(n, 21) * 0.25
    src = _bandpass(src, 1100, 1.2) + _bandpass(src, 2600, 1.5) * 0.4
    env = _adsr(n, 0.05, 0.2, 0.8, 0.4)
    return _normalize(_soft_clip(src * env * 1.6, 1.3) * 0.95)


def _gen_record_scratch(seed: int = 6) -> "np.ndarray":
    n = int(0.42 * SR)
    x = _noise(n, seed)
    env = (np.exp(-np.maximum(0, _t(n) - 0.02) * 14)
           * np.clip(_t(n) / 0.004, 0, 1))
    x = _bandpass(x, 1800, 0.8) * env
    x += _bandpass(_noise(n, seed + 5), 600, 0.9) * env * 0.6
    return _normalize(x)


def _gen_pop(seed: int = 8) -> "np.ndarray":
    n = int(0.12 * SR)
    f = _lin(900, 240, n)
    x = _sine(f, n) * _exp_env(n, 0.001, 0.03, 1.6)
    x += _lowpass(_noise(n, seed), 2000) * np.exp(-_t(n) * 90) * 0.3
    return _normalize(x)


def _gen_ding() -> "np.ndarray":
    n = int(1.1 * SR)
    x = (_sine(1568.0, n) * 1.0 + _sine(2349.0, n) * 0.5 + _sine(3136.0, n) * 0.28)
    x *= _exp_env(n, 0.002, 0.55, 1.4)
    return _normalize(x)


def _gen_bell() -> "np.ndarray":
    n = int(2.0 * SR)
    partials = [1.0, 2.01, 2.98, 4.16, 5.43, 6.79]
    gains = [1.0, 0.6, 0.4, 0.25, 0.16, 0.1]
    x = np.zeros(n)
    for p, g in zip(partials, gains):
        x += _sine(440.0 * p, n) * g * np.exp(-_t(n) * (1.2 + p * 0.5))
    return _normalize(x)


def _gen_metal_pipe(seed: int = 9) -> "np.ndarray":
    """Inharmonic metallic fall — the 'metal pipe' meme sound, re-synthesised."""
    n = int(1.5 * SR)
    x = np.zeros(n)
    base = np.exp(np.linspace(math.log(900.0), math.log(210.0), n))
    for k, g in enumerate([1.0, 1.41, 1.93, 2.68, 3.31, 4.12], start=1):
        f = base * k
        x += _sine(f, n) * (g / k) * np.exp(-_t(n) * (1.6 + 0.35 * k))
    strike = _highpass(_noise(n, seed), 2500) * np.exp(-_t(n) * 120) * 0.5
    return _normalize(x + strike)


def _gen_whoosh(seed: int = 10) -> "np.ndarray":
    n = int(0.55 * SR)
    x = _noise(n, seed)
    sweep = np.exp(np.linspace(math.log(180.0), math.log(5200.0), n))
    x = _bandpass(x, 1200, 0.7)
    env = np.sin(math.pi * np.clip(_t(n) / 0.55, 0, 1)) ** 1.4
    # sweep the band with a time-varying low-pass chain approximation
    shaped = np.zeros(n)
    chunk = 2048
    for i in range(0, n, chunk):
        j = min(n, i + chunk)
        cut = float(sweep[min(n - 1, i)])
        shaped[i:j] = _lowpass(_highpass(x[i:j], max(80.0, cut * 0.25)), cut)
    return _normalize(shaped * env)


def _gen_riser(seed: int = 11) -> "np.ndarray":
    n = int(1.8 * SR)
    env = np.clip(_t(n) / 1.8, 0, 1) ** 2
    noise = _bandpass(_noise(n, seed), 3000, 0.6) * env
    tone = _sine(np.exp(np.linspace(math.log(220.0), math.log(1760.0), n)), n)
    tone *= env * 0.4
    return _normalize(noise + tone)


def _gen_sub_drop() -> "np.ndarray":
    n = int(1.2 * SR)
    f = np.exp(np.linspace(math.log(320.0), math.log(32.0), n))
    x = _sine(f, n) * _exp_env(n, 0.01, 0.5, 1.6)
    return _normalize(x)


def _gen_kick(seed: int = 13) -> "np.ndarray":
    return _gen_thud(0.28, 62.0, seed)


def _gen_snare(seed: int = 14) -> "np.ndarray":
    n = int(0.28 * SR)
    body = _sine(_lin(320, 170, n), n) * np.exp(-_t(n) * 30)
    noise = _highpass(_noise(n, seed), 1400) * np.exp(-_t(n) * 22)
    return _normalize(body * 0.5 + noise * 0.8)


def _gen_hihat(seed: int = 15, dur: float = 0.11) -> "np.ndarray":
    n = int(dur * SR)
    x = _highpass(_noise(n, seed), 7000) * np.exp(-_t(n) * 55)
    return _normalize(x) * 0.8


def _gen_clap(seed: int = 16) -> "np.ndarray":
    n = int(0.36 * SR)
    out = np.zeros(n)
    for i, off in enumerate([0.0, 0.011, 0.021, 0.033]):
        s = int(off * SR)
        seg = _bandpass(_noise(n - s, seed + i), 1500, 0.9)
        out[s:] += seg * np.exp(-np.arange(n - s) / SR * 28)
    return _normalize(out)


def _gen_drum_roll() -> "np.ndarray":
    parts = []
    for i in range(16):
        snare = _gen_snare(20 + i) * (0.45 + i * 0.035)
        parts.append(snare)
        gap = int((0.085 - i * 0.003) * SR)
        if gap > 0:
            parts.append(np.zeros(gap))
    return _normalize(np.concatenate(parts)) * 0.9


def _gen_airhorn() -> "np.ndarray":
    parts = []
    for _ in range(3):
        n = int(0.26 * SR)
        f = 466.0
        x = (_saw(f, n) * 0.5 + _saw(f * 1.01, n) * 0.4 + _saw(f * 0.5, n) * 0.3)
        x = _bandpass(x, 1400, 0.8) + _bandpass(x, 2600, 1.2) * 0.4
        x *= _adsr(n, 0.012, 0.03, 0.85, 0.06)
        parts.append(_soft_clip(x * 1.5, 1.3))
        parts.append(np.zeros(int(0.06 * SR)))
    return _normalize(np.concatenate(parts))


def _gen_buzzer() -> "np.ndarray":
    n = int(0.55 * SR)
    x = _square(110.0, n) * 0.6 + _square(112.0, n) * 0.4
    x = _bandpass(x, 900, 1.0)
    x *= _adsr(n, 0.005, 0.03, 0.9, 0.08)
    return _normalize(x)


def _gen_tada() -> "np.ndarray":
    seq = [(523.25, 0.13), (659.25, 0.13), (783.99, 0.42)]
    parts = []
    for f, d in seq:
        n = int(d * SR)
        x = (_sine(f, n) + _sine(f * 2, n) * 0.4 + _sine(f * 3, n) * 0.15)
        x *= _adsr(n, 0.01, 0.05, 0.7, d * 0.5)
        parts.append(x)
    return _normalize(np.concatenate(parts))


def _gen_glitch(seed: int = 17) -> "np.ndarray":
    n = int(0.4 * SR)
    out = np.zeros(n)
    rng = np.random.default_rng(seed)
    for i in range(12):
        s = int(rng.uniform(0, 0.34) * SR)
        ln = int(rng.uniform(0.008, 0.05) * SR)
        ln = min(ln, n - s)
        if ln <= 2:
            continue
        tone = _square(rng.uniform(200, 4000), ln)
        out[s:s + ln] += tone * rng.uniform(0.3, 0.9)
    out += _noise(n, seed + 3) * 0.1
    return _normalize(_soft_clip(out, 1.2))


def _gen_applause(seed: int = 18) -> "np.ndarray":
    n = int(2.4 * SR)
    out = np.zeros(n)
    rng = np.random.default_rng(seed)
    for _ in range(700):
        s = int(abs(rng.normal(0.9, 0.4)) * SR) % max(1, n - 2000)
        ln = 1400
        out[s:s + ln] += _bandpass(_noise(ln, int(rng.integers(0, 9999))), 2200, 0.8) * \
            rng.uniform(0.05, 0.3) * np.exp(-np.arange(ln) / SR * 60)
    env = np.clip(_t(n) / 0.25, 0, 1) * np.exp(-np.maximum(0, _t(n) - 0.6) * 1.6)
    return _normalize(out * env)


def _gen_suspense() -> "np.ndarray":
    n = int(3.0 * SR)
    x = (_sine(55.0, n) * 0.6 + _sine(82.5, n) * 0.3)
    x += _bandpass(_noise(n, 19), 300, 0.7) * 0.25
    trem = 1.0 + 0.25 * np.sin(2 * math.pi * 4.5 * _t(n))
    env = np.clip(_t(n) / 0.8, 0, 1) * np.clip((3.0 - _t(n)) / 1.0, 0, 1)
    return _normalize(x * trem * env * 0.8)


def _gen_anvil(seed: int = 22) -> "np.ndarray":
    """Metallic clang in the spirit of a game 'anvil' hit."""
    n = int(1.4 * SR)
    x = np.zeros(n)
    base = 620.0
    for k, g in enumerate([1.0, 1.71, 2.43, 3.17, 4.05, 5.31], start=1):
        f = base * k
        x += _sine(f, n) * (g / (k ** 1.3)) * np.exp(-_t(n) * (2.2 + 0.5 * k))
    x += _highpass(_noise(n, seed), 3000) * np.exp(-_t(n) * 90) * 0.4
    return _normalize(x)


def _gen_block(seed: int = 23) -> "np.ndarray":
    n = int(0.22 * SR)
    body = _lowpass(_noise(n, seed), 900) * _exp_env(n, 0.002, 0.05, 1.6)
    tone = _sine(_lin(360, 210, n), n) * np.exp(-_t(n) * 40)
    return _normalize(body * 0.7 + tone * 0.6)


def _gen_xp(seed: int = 24) -> "np.ndarray":
    parts = []
    for i, f in enumerate([1046.5, 1318.5, 1568.0]):
        n = int(0.12 * SR)
        x = (_sine(f, n) + _sine(f * 2, n) * 0.3) * _adsr(n, 0.004, 0.03, 0.6, 0.05)
        parts.append(x)
        parts.append(np.zeros(int(0.035 * SR)))
    return _normalize(np.concatenate(parts))


def _gen_villager(seed: int = 25) -> "np.ndarray":
    voice = _say([("uh", 0.16, 0.7), ("e", 0.20, 0.8)], f0_start=210.0,
                 f0_end=170.0, bend=12.0)
    return _normalize(_soft_clip(voice * 1.4, 1.2))


def _gen_creeper(seed: int = 26) -> "np.ndarray":
    n = int(1.1 * SR)
    x = _highpass(_noise(n, seed), 2500)
    env = np.clip(_t(n) / 0.15, 0, 1) * np.exp(-np.maximum(0, _t(n) - 0.3) * 3)
    return _normalize(x * env) * 0.85


def _gen_explosion(seed: int = 27) -> "np.ndarray":
    n = int(1.5 * SR)
    x = _lowpass(_noise(n, seed), 700) * np.exp(-_t(n) * 6)
    sub = _sine(_lin(90, 28, n), n) * np.exp(-_t(n) * 4) * 0.8
    crack = _highpass(_noise(n, seed + 1), 2500) * np.exp(-_t(n) * 40) * 0.5
    return _normalize(_reverb(x + sub + crack, 0.3, 0.1, 5) * 0.95)


def _gen_door() -> "np.ndarray":
    n = int(0.9 * SR)
    creak = _bandpass(_noise(n, 29), 900, 1.4)
    freq = np.sin(np.linspace(0, 40 * math.pi, n)) 
    creak *= (0.3 + 0.7 * (freq * 0.5 + 0.5)) * np.exp(-_t(n) * 3)
    slam = _lowpass(_noise(n, 30), 400) * np.exp(-_t(n) * 30) * 0.9
    return _normalize(creak * 0.5 + slam)


def _gen_level_up() -> "np.ndarray":
    parts = []
    for f in [523.25, 659.25, 783.99, 1046.5]:
        n = int(0.13 * SR)
        x = (_sine(f, n) * 0.8 + _sine(f * 2, n) * 0.25)
        x *= _adsr(n, 0.006, 0.04, 0.7, 0.05)
        parts.append(x)
    return _normalize(np.concatenate(parts))


def _gen_portal() -> "np.ndarray":
    n = int(1.6 * SR)
    x = _bandpass(_noise(n, 31), 700, 0.6)
    mod = 0.6 + 0.4 * np.sin(2 * math.pi * 3.2 * _t(n))
    env = np.clip(_t(n) / 0.3, 0, 1) * np.exp(-np.maximum(0, _t(n) - 0.9) * 1.6)
    tone = _sine(_lin(180, 300, n), n) * 0.4
    return _normalize(x * mod * env + tone * env)


def _gen_woosh_ui() -> "np.ndarray":
    return _gen_whoosh(33)


# --------------------------------------------------------------------------- #
#  Catalogue
# --------------------------------------------------------------------------- #

@dataclass
class SfxSpec:
    key: str
    label: str
    category: str
    generator: Callable[[], "np.ndarray"]
    duration: float = 1.0
    tags: Tuple[str, ...] = ()
    keywords: str = ""


CATALOG: Dict[str, SfxSpec] = {}
ORDER: List[str] = []


def _add(key: str, label: str, category: str, gen, dur: float = 1.0,
         tags: Tuple[str, ...] = (), keywords: str = "") -> None:
    CATALOG[key] = SfxSpec(key, label, category, gen, dur, tags, keywords)
    ORDER.append(key)


# -- the ones the user asked for, first ------------------------------------- #
_add("fahhh", "Fahhh", "Meme", lambda: _gen_fahhh(1.30), 1.30,
     ("meme", "fail", "funny"), "fahhh fah fail sad trombone descending")
_add("fahhh_long", "Fahhh (long)", "Meme",
     lambda: _gen_fahhh(2.20, 340.0, 80.0, 1.7), 2.20,
     ("meme", "fail"), "fahhh long fail")
_add("fahhh_reverb", "Fahhh (reverb)", "Meme",
     lambda: _reverb(_gen_fahhh(1.30), 0.4, 0.12, 6), 1.90,
     ("meme", "fail"), "fahhh echo reverb")
_add("thud", "Thud", "Impact",
     lambda: _gen_thud(0.34, 78.0), 0.34,
     ("meme", "hit", "impact"), "thud thump hit bump fall")
_add("thud_deep", "Thud (deep)", "Impact",
     lambda: _gen_deep_thud(0.75, 46.0), 0.75,
     ("meme", "hit"), "deep thud boom")
_add("vine_boom", "Vine Boom", "Impact", _gen_vine_boom, 1.35,
     ("meme", "impact"), "vine boom bass drop")
_add("boom", "Boom", "Impact", lambda: _gen_boom(1.6), 1.6,
     ("impact", "explosion"), "boom explosion")
_add("get_out", "Get Out", "Meme", lambda: _gen_get_out(False), 0.85,
     ("meme", "voice"), "get out go away angry voice")
_add("get_out_echo", "Get Out (echo)", "Meme", lambda: _gen_get_out(True), 1.2,
     ("meme", "voice"), "get out echo reverb")
_add("bruh", "Bruh", "Meme", _gen_bruh, 0.7, ("meme", "voice"),
     "bruh disappointed voice")
_add("oof", "Oof", "Meme", _gen_oof, 0.4, ("meme", "voice"), "oof hurt")
_add("laugh", "Laugh", "Meme", _gen_laugh, 0.85, ("meme",), "laugh haha funny")
_add("scream", "Scream", "Meme", _gen_scream, 0.9, ("meme", "voice"),
     "scream yell")
_add("metal_pipe", "Metal Pipe", "Impact", _gen_metal_pipe, 1.5,
     ("meme", "impact"), "metal pipe fall clang")

# -- impacts / drums -------------------------------------------------------- #
_add("kick", "Kick Drum", "Impact", _gen_kick, 0.28, ("music", "drum"), "kick")
_add("snare", "Snare", "Impact", _gen_snare, 0.28, ("music", "drum"), "snare")
_add("hihat", "Hi-Hat", "Impact", _gen_hihat, 0.11, ("music", "drum"), "hat")
_add("clap", "Clap", "Impact", _gen_clap, 0.36, ("music", "drum"), "clap")
_add("drum_roll", "Drum Roll", "Impact", _gen_drum_roll, 1.4, ("music",),
     "drum roll snare")
_add("sub_drop", "Sub Drop", "Impact", _gen_sub_drop, 1.2, ("impact",),
     "sub drop bass")

# -- UI / notification ------------------------------------------------------ #
_add("pop", "Pop", "UI", _gen_pop, 0.12, ("ui",), "pop bubble")
_add("ding", "Ding", "UI", _gen_ding, 1.1, ("ui", "notify"), "ding bell notify")
_add("bell", "Bell", "UI", _gen_bell, 2.0, ("ui",), "bell chime")
_add("correct", "Correct", "UI", _gen_tada, 0.9, ("ui", "quiz"), "correct tada")
_add("wrong", "Wrong Buzzer", "UI", _gen_buzzer, 0.55, ("ui", "quiz"),
     "wrong buzzer error")
_add("whoosh", "Whoosh", "UI", _gen_whoosh, 0.55, ("ui", "transition"),
     "whoosh swipe transition")
_add("riser", "Riser", "UI", _gen_riser, 1.8, ("transition",), "riser tension")
_add("record_scratch", "Record Scratch", "UI", _gen_record_scratch, 0.42,
     ("meme",), "record scratch stop")
_add("glitch", "Glitch", "UI", _gen_glitch, 0.4, ("ui",), "glitch digital")
_add("airhorn", "Air Horn", "UI", _gen_airhorn, 1.0, ("meme",), "air horn dj")
_add("applause", "Applause", "Crowd", _gen_applause, 2.4, ("crowd",),
     "applause clap crowd")
_add("suspense", "Suspense", "Cinematic", _gen_suspense, 3.0, ("cinematic",),
     "suspense tension drone")

# -- Minecraft-flavoured ---------------------------------------------------- #
_add("mc_anvil", "Anvil Clang", "Minecraft", _gen_anvil, 1.4, ("minecraft",),
     "minecraft anvil clang metal")
_add("mc_block", "Block Place", "Minecraft", _gen_block, 0.22, ("minecraft",),
     "minecraft block place dig stone")
_add("mc_xp", "XP Orb", "Minecraft", _gen_xp, 0.55, ("minecraft",),
     "minecraft xp experience orb level")
_add("mc_level_up", "Level Up", "Minecraft", _gen_level_up, 0.6,
     ("minecraft",), "minecraft level up enchant")
_add("mc_villager", "Villager Hmm", "Minecraft", _gen_villager, 0.45,
     ("minecraft", "voice"), "minecraft villager hmm trade")
_add("mc_creeper", "Creeper Hiss", "Minecraft", _gen_creeper, 1.1,
     ("minecraft",), "minecraft creeper hiss sss")
_add("mc_explosion", "Explosion", "Minecraft", _gen_explosion, 1.5,
     ("minecraft", "impact"), "minecraft explosion tnt boom")
_add("mc_door", "Door", "Minecraft", _gen_door, 0.9, ("minecraft",),
     "minecraft door open close wood")
_add("mc_portal", "Nether Portal", "Minecraft", _gen_portal, 1.6,
     ("minecraft",), "minecraft portal nether whoosh")
_add("mc_click", "Button Click", "Minecraft", _gen_woosh_ui, 0.55,
     ("minecraft", "ui"), "minecraft button click ui")


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in text.lower()).strip("_")


# --------------------------------------------------------------------------- #
#  Generation + lookup
# --------------------------------------------------------------------------- #

def library_dir() -> Path:
    from .project import data_dir
    d = data_dir() / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sfx_path(key: str) -> Path:
    spec = CATALOG.get(key)
    name = f"{key}.wav" if spec else f"{_slug(key)}.wav"
    return library_dir() / name


def user_override(key: str) -> Optional[Path]:
    """A user-supplied file (``fahhh.mp3`` etc.) wins over the generated one."""
    d = library_dir() / "user"
    if not d.exists():
        return None
    for ext in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".opus"):
        p = d / f"{key}{ext}"
        if p.exists():
            return p
    return None


def generate(key: str, force: bool = False) -> Optional[Path]:
    spec = CATALOG.get(key)
    if spec is None or np is None:
        return None
    path = sfx_path(key)
    if path.exists() and not force:
        return path
    try:
        data = spec.generator()
        return _write_wav(path, data)
    except Exception:
        return None


def ensure_library(progress: Optional[Callable[[int, int, str], None]] = None,
                   force: bool = False) -> Dict[str, Path]:
    """Generate every missing effect.  Fast: the whole set is a few seconds."""
    out: Dict[str, Path] = {}
    total = len(ORDER)
    for i, key in enumerate(ORDER):
        if progress:
            progress(i + 1, total, CATALOG[key].label)
        p = generate(key, force=force)
        if p:
            out[key] = p
    return out


def resolve(key: str) -> Optional[str]:
    """Path to use for a sound-effect key (user override first)."""
    if key in _USER_PATHS:
        return str(_USER_PATHS[key])
    ov = user_override(key)
    if ov:
        return str(ov)
    p = generate(key)
    return str(p) if p else None


AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".opus")
USER_PREFIX = "usr_"
_USER_PATHS: Dict[str, Path] = {}


def import_user_audio(files: Sequence[str]) -> List[str]:
    """Copy files into the user sound folder and expose them as catalogue items.

    Returns the catalogue keys of everything that was imported.  Imported
    sounds behave exactly like the built-in ones: they show up in the library,
    can be previewed and dropped on the timeline.
    """
    d = library_dir() / "user"
    d.mkdir(parents=True, exist_ok=True)
    keys: List[str] = []
    for f in files:
        try:
            src = Path(f)
            if src.suffix.lower() not in AUDIO_EXTS:
                continue
            target = d / src.name
            if src.resolve() != target.resolve():
                shutil.copy2(src, target)
            key = register_user_file(target)
            if key:
                keys.append(key)
        except Exception:
            continue
    return keys


def register_user_file(path: Path) -> Optional[str]:
    """Add a single file already inside ``<library>/user`` to the catalogue."""
    try:
        if path.suffix.lower() not in AUDIO_EXTS:
            return None
        key = USER_PREFIX + _slug(path.stem)
        if not key.strip(USER_PREFIX):
            return None
        _USER_PATHS[key] = path
        if key not in CATALOG:
            spec = SfxSpec(key, path.stem.replace("_", " ").title(),
                           "My sounds", lambda: None, 1.0, ("custom",),
                           f"custom imported {path.stem} {path.suffix[1:]}")
            CATALOG[key] = spec
            ORDER.append(key)
        return key
    except Exception:
        return None


def scan_user_library() -> List[str]:
    """Register every audio file in the user folder (called at startup)."""
    d = library_dir() / "user"
    keys: List[str] = []
    if not d.exists():
        return keys
    for f in sorted(d.iterdir()):
        if f.is_file():
            key = register_user_file(f)
            if key:
                keys.append(key)
    return keys


def search(text: str) -> List[SfxSpec]:
    t = (text or "").strip().lower()
    if not t:
        return [CATALOG[k] for k in ORDER]
    out = []
    for k in ORDER:
        s = CATALOG[k]
        hay = f"{s.label} {s.keywords} {' '.join(s.tags)} {s.category}".lower()
        if t in hay:
            out.append(s)
    return out


def categories() -> List[str]:
    seen: List[str] = []
    for k in ORDER:
        c = CATALOG[k].category
        if c not in seen:
            seen.append(c)
    return seen


# --------------------------------------------------------------------------- #
#  Speech via a local TTS engine (optional quality upgrade)
# --------------------------------------------------------------------------- #

TTS_ENGINES = [
    ("espeak-ng", ["-w", "{out}", "-s", "135", "-p", "28", "-v", "en-us"]),
    ("espeak", ["-w", "{out}", "-s", "135", "-p", "28"]),
    ("pico2wave", ["-w", "{out}", "-l", "en-US"]),
]


def available_tts() -> Optional[str]:
    for name, _ in TTS_ENGINES:
        if shutil.which(name):
            return name
    return None


def synthesize_speech(text: str, out_path: str, pitch: float = 1.0,
                      rate: float = 1.0) -> bool:
    """Local TTS -> WAV, then pitch/rate shaped with FFmpeg.  No cloud calls."""
    engine = available_tts()
    if not engine:
        return False
    args_tmpl = next(a for n, a in TTS_ENGINES if n == engine)
    tmp = str(Path(out_path).with_suffix(".raw.wav"))
    cmd = [engine] + [a.format(out=tmp) for a in args_tmpl] + [text]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
    except Exception:
        return False
    if not os.path.exists(tmp):
        return False
    from . import ffmpeg as _ff
    filters = []
    if abs(rate - 1.0) > 0.01:
        filters.append(f"atempo={max(0.5, min(2.0, rate)):.4f}")
    if abs(pitch - 1.0) > 0.01:
        filters.append(f"asetrate=44100*{pitch:.4f},aresample=44100,"
                       f"atempo=1")
    cmd2 = ["-y", "-v", "error", "-i", tmp]
    if filters:
        cmd2 += ["-af", ",".join(filters)]
    cmd2 += ["-ac", "1", "-ar", "44100", out_path]
    r = _ff.ffmpeg(*cmd2, timeout=60)
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return r.ok and os.path.exists(out_path)


def upgrade_voice_effects() -> int:
    """Replace the synthesised voice SFX with local-TTS renders when possible."""
    if not available_tts():
        return 0
    done = 0
    plans = {
        "get_out": ("Get out!", -0.28, 0.92),
        "get_out_echo": ("Get out!", -0.28, 0.92),
        "bruh": ("Bruh.", -0.32, 0.85),
        "oof": ("Oof!", -0.2, 1.0),
    }
    for key, (phrase, pitch_semis, rate) in plans.items():
        pitch = 2 ** (pitch_semis / 12.0)
        target = sfx_path(key)
        if synthesize_speech(phrase, str(target), pitch=pitch, rate=rate):
            done += 1
    return done
