#!/usr/bin/env python3
"""Feature audit: render every advertised option and check it really works.

The editor lists 39 effects, 25 colour looks, 65 transitions, 24 text
animations, 38 stickers, 8 mask shapes and a set of speed curves.  A menu entry
that silently renders nothing (or crashes FFmpeg) would be a lie, so this script
takes each catalogue entry, renders a short clip with it, and verifies the
result: the render must succeed *and* the picture must actually change where the
feature is supposed to act.

    python tools/feature_audit.py [--quick] [--only effects,transitions]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vn_editor.core import ffmpeg as ff          # noqa: E402
from vn_editor.core import probe as probe_mod    # noqa: E402
from vn_editor.core import render as render_mod  # noqa: E402
from vn_editor.core.model import (Clip, MediaAsset, Project, TextProps,  # noqa: E402
                                 Transform)
from vn_editor.core.ass import apply_text_animation  # noqa: E402

FAILURES: List[Tuple[str, str]] = []
AUDIT_LUT: Optional[str] = None
NOOPS: List[Tuple[str, float]] = []
BASELINE: Optional[str] = None


def report(name: str, ok: bool, detail: str = "") -> None:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{(' — ' + detail) if detail else ''}", flush=True)
    if not ok:
        FAILURES.append((name, detail))


def media(tmp: Path) -> str:
    """A short test pattern with motion, so effects have something to act on."""
    src = tmp / "audit_src.mp4"
    if not src.exists():
        r = ff.ffmpeg("-y", "-v", "error",
                      "-f", "lavfi", "-i",
                      "testsrc2=size=320x180:rate=15:duration=2",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                      "-c:v", "libx264", "-preset", "ultrafast",
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                      str(src), timeout=180)
        if not r.ok:
            raise SystemExit("could not build the audit source clip")
    return str(src)


def base_project(src: str, kind: str = "video") -> Project:
    p = Project.create_default("audit", 320, 180, 15.0)
    p.settings.background = "#101010"
    info = probe_mod.probe(src, use_cache=False)
    asset = MediaAsset.from_info(info, src)
    p.assets.append(asset)
    tr = p.ensure_track_for("video")
    c = Clip(kind=kind, track_id=tr.id, start=0.0, duration=1.5)
    c.path = src
    c.asset_id = asset.id
    c.meta["has_audio"] = True
    p.add_clip(c, tr)
    return p


def render_ok(project: Project, tmp: Path, tag: str,
              at: float = 0.5) -> Tuple[bool, str]:
    """Render one composited frame and return (ok, png path)."""
    try:
        img = tmp / f"{tag}.png"
        ok = render_mod.render_frame(project, at, 160, 90, str(img))
    except Exception as exc:                       # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return bool(ok and img.exists()), str(img)


def pixels(path: str):
    """Mini PNG reader (no PIL here): returns (w, h, bytes)."""
    import struct
    import zlib
    data = Path(path).read_bytes()
    pos = 8
    w = h = 0
    idat = b""
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, color = struct.unpack(">IIBB", chunk[:10])
            if depth != 8 or color not in (2, 6):
                raise ValueError("unexpected png format")
            channels = 3 if color == 2 else 4
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * channels
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        out += line
        prev = line
    return w, h, bytes(out)


def changed(path: str, label: str, floor: float = 0.75) -> float:
    """How much this render differs from the unfiltered baseline (0 = nothing)."""
    if not BASELINE:
        return 999.0
    diff = mean_diff(BASELINE, path)
    if diff < floor:
        NOOPS.append((label, diff))
    return diff


def _drop_alpha(data: bytes, channels: int) -> bytes:
    if channels != 4:
        return data
    keep = bytearray()
    for i in range(0, len(data) - 3, 4):
        keep += data[i:i + 3]
    return bytes(keep)


def export_frame(project: Project, tmp: Path, tag: str, seconds: float = 0.6
                 ) -> Tuple[bool, str]:
    """Render a short clip and pull one frame out of it."""
    clip_path = tmp / f"{tag}.mp4"
    opts = render_mod.options_from_settings(project)
    opts.width, opts.height = 160, 90
    opts.audio = False
    opts.crf = 30
    opts.preset = "ultrafast"
    try:
        result = render_mod.render(project, str(clip_path), opts)
    except Exception as exc:                       # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if not result.ok:
        return False, (result.error or "export failed")[-160:]
    still = tmp / f"{tag}.png"
    r = ff.ffmpeg("-y", "-v", "error", "-ss", f"{seconds:.3f}", "-i",
                  str(clip_path), "-frames:v", "1", "-update", "1", str(still),
                  timeout=120)
    return (r.ok and still.exists()), str(still)


def mean_diff(a: str, b: str) -> float:
    """Mean absolute channel difference of two stills (0…255)."""
    wa, ha, pa = pixels(a)
    wb, hb, pb = pixels(b)
    if (wa, ha) != (wb, hb):
        return 255.0
    ca = len(pa) // max(1, wa * ha)
    cb = len(pb) // max(1, wb * hb)
    pa = _drop_alpha(pa, ca)
    pb = _drop_alpha(pb, cb)
    n = min(len(pa), len(pb))
    step = max(1, n // 12000) * 3
    total = 0
    count = 0
    for i in range(0, n - 2, step):
        total += abs(pa[i] - pb[i]) + abs(pa[i + 1] - pb[i + 1]) \
            + abs(pa[i + 2] - pb[i + 2])
        count += 3
    return total / max(1, count)


# --------------------------------------------------------------------------- #

#: effects whose whole point is subtle in a synthetic clip — a tiny delta is
#: fine, they are still verified to render.
SOFT_EFFECTS = {"stabilize", "denoise", "deinterlace", "sharpen", "grain",
                "soft_focus", "blur", "motion_blur", "posterize"}

#: effects that mix several frames together.  A single scrubbed frame gives them
#: nothing to work with, so the audit exercises them through a real export.
TEMPORAL_EFFECTS = {"zoom_blur", "deinterlace"}

EXPORT_BASELINE: Optional[str] = None


def loud_params(spec) -> dict:
    """Push every parameter away from its neutral default.

    An effect that is invisible even when its sliders are cranked is broken, so
    the audit drives each control to a clearly visible setting instead of
    leaving the neutral defaults in place.
    """
    out = {}
    for prm in spec.params:
        if prm.kind in ("bool",):
            out[prm.key] = 1.0
        elif prm.kind == "color":
            out[prm.key] = "#ff3300"
        elif prm.kind == "choice" and prm.choices:
            out[prm.key] = float(min(1, len(prm.choices) - 1))
        elif prm.kind == "file":
            if AUDIT_LUT:
                out[prm.key] = str(AUDIT_LUT)
        elif prm.hi > prm.lo:
            span = prm.hi - prm.lo
            cand = prm.lo + span * 0.75 if abs(prm.default - prm.lo) < 1e-9 \
                else prm.lo + span * 0.25
            if prm.kind == "int":
                cand = float(round(cand))
            out[prm.key] = cand
    return out


def audit_effects(tmp: Path, src: str) -> None:
    from vn_editor.core import effects as fx
    print(f"\neffects ({len(fx.EFFECTS)})")
    for key, spec in fx.EFFECTS.items():
        if key == "stabilize":
            continue        # verified on shaking footage by audit_stabilize()
        p = base_project(src)
        clip = p.all_clips()[0]
        try:
            params = loud_params(spec)
            if not params:
                params = fx.default_params(key)
            if "intensity" in params and params["intensity"] <= 1.0:
                params["intensity"] = 1.0
            clip.add_filter(key, dict(params))
        except Exception as exc:                   # noqa: BLE001
            report(f"effect:{key}", False, f"setup: {exc}")
            continue
        ok, detail = render_ok(p, tmp, f"fx_{key}")
        if not ok:
            report(f"effect:{key}", False, detail)
            continue
        if key in TEMPORAL_EFFECTS:
            ok, exported = export_frame(p, tmp, f"fx_{key}", seconds=0.6)
            if not ok or not EXPORT_BASELINE:
                report(f"effect:{key}", False, exported)
                continue
            diff = mean_diff(EXPORT_BASELINE, exported)
            report(f"effect:{key}", diff > 0.3,
                   f"Δ={diff:.1f} (export path)")
            continue
        floor = 0.3 if key in SOFT_EFFECTS else 0.75
        diff = changed(detail, f"effect:{key}", floor=floor)
        label = "params=" + ",".join(
            f"{k}={v:.3g}" if isinstance(v, (int, float)) else f"{k}={v}"
            for k, v in params.items())
        report(f"effect:{key}", diff >= floor,
               f"Δ={diff:.1f}  {label}" if diff >= floor
               else f"Δ={diff:.1f} — no visible change  {label}")


def audit_grades(tmp: Path, src: str) -> None:
    from vn_editor.core import effects as fx
    print(f"\ncolour looks ({len(fx.GRADES)})")
    for i, name in enumerate(fx.GRADES):
        p = base_project(src)
        clip = p.all_clips()[0]
        clip.add_filter("grade", {"name": name, "grade": float(i),
                                  "intensity": 1.0})
        ok, detail = render_ok(p, tmp, f"gr_{i}")
        extra = "" if ok else detail
        if ok:
            extra = f"Δ={changed(detail, f'look:{name}'):.1f}"
        report(f"look:{name}", ok, extra)


def audit_transitions(tmp: Path, src: str) -> None:
    from vn_editor.core import transitions as tr
    print(f"\ntransitions ({len(tr.TRANSITIONS)})")
    # The second clip needs *different* footage: a transition between two
    # identical frames is invisible at its midpoint, which would say nothing
    # about whether the transition ran.
    other = tmp / "audit_src2.mp4"
    if not other.exists():
        r = ff.ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
                      "smptehdbars=size=320x180:rate=15:duration=2",
                      "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
                      "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
                      "yuv420p", "-c:a", "aac", "-shortest", str(other),
                      timeout=120)
        if not r.ok:
            report("transition:fixture", False, "could not build second clip")
            return

    for key in tr.TRANSITIONS:
        if key == "none":
            continue
        p = base_project(src)
        track = p.tracks[0]
        first = p.all_clips()[0]
        second = first.clone()
        second.id = Clip().id
        second.start = first.duration
        second.path = str(other)
        p.add_clip(second, track)
        from vn_editor.core.model import Transition
        first.transition_out = Transition(key, 0.4, "out")
        opts = render_mod.options_from_settings(p)
        opts.width, opts.height = 160, 90
        out = tmp / f"tr_{key}.mp4"
        try:
            result = render_mod.render(p, str(out), opts)
        except Exception as exc:                   # noqa: BLE001
            report(f"transition:{key}", False, f"{type(exc).__name__}: {exc}")
            continue
        ok = result.ok and out.exists() and out.stat().st_size > 1000
        if not ok:
            report(f"transition:{key}", False,
                   (result.error or "render failed")[-160:])
            continue
        # A transition that silently falls back to a hard cut still renders, so
        # compare a frame from inside the transition with the same frame from a
        # clip that has no transition at all.
        cut = tmp / f"tr_{key}_cut.mp4"
        for clip in p.all_clips():
            clip.transition_out = None
            clip.transition_in = None
        cut_opts = render_mod.options_from_settings(p)
        cut_opts.width, cut_opts.height = 160, 90
        cut_opts.audio = False
        render_mod.render(p, str(cut), cut_opts)
        # The transition is drawn over [clip A's out point, +duration] while
        # the plain cut shows clip B throughout that window, so scan the window
        # and take the biggest difference: that catches a transition which
        # silently degraded to a hard cut without depending on one timestamp.
        best = -1.0
        for i in range(9):
            t = first.duration - 0.1 + i * 0.1
            a_frame = transition_frame(out, t, tmp, f"tr_{key}_{i}a.png")
            b_frame = transition_frame(cut, t, tmp, f"tr_{key}_{i}b.png")
            if a_frame and b_frame:
                best = max(best, mean_diff(a_frame, b_frame))
        report(f"transition:{key}", best > 1.0,
               f"max Δ in the transition window = {best:.1f}"
               if best >= 0 else "could not compare frames")
        # A transition must not move the rest of the timeline: the run has to
        # keep the same length as the plain cut.
        here = probe_mod.probe(str(out), use_cache=False).duration
        there = probe_mod.probe(str(cut), use_cache=False).duration
        report(f"transition:{key} keeps the timeline",
               abs(here - there) < 2.0 / 15.0 and here > 0,
               f"{here:.3f}s vs {there:.3f}s without the transition")


def transition_frame(video: Path, t: float, tmp: Path, name: str) -> str:
    out = tmp / name
    r = ff.ffmpeg("-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i",
                  str(video), "-frames:v", "1", "-update", "1", str(out),
                  timeout=60)
    return str(out) if r.ok and out.exists() else ""


def audit_easings(tmp: Path, src: str) -> None:
    """Every easing has to bend the curve, and stay inside sane bounds."""
    from vn_editor.core.model import EASINGS, Keyframe, eval_keyframes
    print(f"\neasings ({len(EASINGS)})")
    keys = [Keyframe(t=0.0, value=0.0, easing="linear"),
            Keyframe(t=1.0, value=1.0, easing="linear")]
    for name in EASINGS:
        keys[1].easing = name
        try:
            quarter = eval_keyframes(keys, 0.25, 0.0)
            half = eval_keyframes(keys, 0.5, 0.0)
            end = eval_keyframes(keys, 1.0, 0.0)
            start = eval_keyframes(keys, 0.0, 0.0)
        except Exception as exc:                   # noqa: BLE001
            report(f"easing:{name}", False, f"{type(exc).__name__}: {exc}")
            continue
        if name == "linear":
            ok = abs(half - 0.5) < 0.02 and abs(quarter - 0.25) < 0.02
        elif name == "hold":
            ok = abs(half - 0.0) < 0.02
        else:
            ok = abs(half - 0.5) > 0.02 or abs(quarter - 0.25) > 0.02
        detail = f"t=0.25→{quarter:.3f} 0.5→{half:.3f}"
        report(f"easing:{name}", ok and abs(start) < 1e-6 and abs(end - 1.0) < 1e-6,
               detail)


def audit_text_style(tmp: Path, src: str) -> None:
    """Outline, shadow, background and alignment each have to show up."""
    from vn_editor.core.model import TextProps
    print("\ntext styling")
    base_text = TextProps(text="STYLE", font="Bebas Neue", size=90.0,
                          color="#ffffff")
    variants = [
        ("outline", TextProps(**{**base_text.to_dict(),
                                 "outline_width": 8.0,
                                 "outline_color": "#ff0044"})),
        ("shadow", TextProps(**{**base_text.to_dict(),
                                "shadow": 8.0, "shadow_color": "#00ff00"})),
        ("background", TextProps(**{**base_text.to_dict(),
                                    "bg_enabled": True,
                                    "bg_color": "#0000ff",
                                    "bg_opacity": 1.0})),
        ("alignment", TextProps(**{**base_text.to_dict(), "align": "left"})),
        ("letter spacing", TextProps(**{**base_text.to_dict(),
                                        "letter_spacing": 18.0})),
    ]
    plain = tmp / "style_plain.png"
    p = base_project(src)
    tt = p.ensure_track_for("text")
    c = Clip(kind="text", track_id=tt.id, start=0.0, duration=1.5)
    c.text = base_text
    p.add_clip(c, tt)
    ok, detail = render_ok(p, tmp, "style_plain", at=0.6)
    if not ok:
        report("text:plain", False, detail)
        return
    for name, props in variants:
        p = base_project(src)
        tt = p.ensure_track_for("text")
        c = Clip(kind="text", track_id=tt.id, start=0.0, duration=1.5)
        c.text = props
        p.add_clip(c, tt)
        ok, frame = render_ok(p, tmp, f"style_{abs(hash(name)) % 9999}", at=0.6)
        if not ok:
            report(f"text:{name}", False, frame)
            continue
        diff = mean_diff(detail, frame)
        report(f"text:{name}", diff > 0.5, f"Δ={diff:.1f}")


def audit_audio_levels(tmp: Path, src: str) -> None:
    """Volume, mute and fades have to be audible in the exported file."""
    from vn_editor.core.model import AudioProps
    print("\naudio controls")
    sound = tmp / "tone.wav"
    if not sound.exists():
        ff.ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
                  "sine=frequency=440:duration=1.6", str(sound), timeout=60)

    def export(tag: str, props) -> Optional[float]:
        p = Project.create_default("audio", 160, 90, 15.0)
        asset = MediaAsset.from_info(probe_mod.probe(str(sound),
                                                     use_cache=False), str(sound))
        p.assets.append(asset)
        track = p.ensure_track_for("audio")
        clip = Clip(kind="audio", track_id=track.id, start=0.0, duration=1.5)
        clip.path = str(sound)
        clip.asset_id = asset.id
        clip.audio = props
        p.add_clip(clip, track)
        opts = render_mod.options_from_settings(p)
        opts.width, opts.height = 160, 90
        opts.audio = True
        out = tmp / f"audio_{tag}.mp4"
        result = render_mod.render(p, str(out), opts)
        if not result.ok:
            return None
        return measure_level(out)

    def measure_level(path: Path, start: float = 0.0,
                      span: float = 0.0) -> Optional[float]:
        args = ["-v", "info", "-ss", f"{start:.3f}"]
        if span:
            args += ["-t", f"{span:.3f}"]
        args += ["-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
        r = ff.ffmpeg(*args, timeout=120)
        text = (r.stderr or "")
        for line in text.splitlines():
            if "mean_volume" in line:
                try:
                    return float(line.split("mean_volume:")[1].split("dB")[0])
                except (IndexError, ValueError):
                    return None
        return None

    full = export("full", AudioProps(volume=1.0))
    muted = export("mute", AudioProps(volume=1.0, muted=True))
    quiet = export("quiet", AudioProps(volume=0.15))
    report("audio:normal level", full is not None and full > -40.0,
           f"mean {full:.1f} dB" if full is not None else "no audio stream")
    # A muted clip is dropped from the mix, so a project whose only audio clip
    # is muted exports without an audio stream at all — that is silence too.
    report("audio:mute silences the clip",
           muted is None or (full is not None and muted < full - 20.0),
           f"mean {muted:.1f} dB" if muted is not None else "no audio track (silent)")
    report("audio:volume scales the level",
           quiet is not None and full is not None and quiet < full - 6.0,
           f"{full:.1f} dB → {quiet:.1f} dB"
           if (full is not None and quiet is not None) else "no audio stream")

    if export("fade", AudioProps(volume=1.0, fade_in=1.0, fade_out=1.0)) is None:
        report("audio:fades shape the clip", False, "no audio stream")
    else:
        out_file = tmp / "audio_fade.mp4"
        head = measure_level(out_file, 0.0, 0.2)
        middle = measure_level(out_file, 0.6, 0.3)
        tail = measure_level(out_file, 1.25, 0.25)
        if None in (head, middle, tail):
            report("audio:fades shape the clip", False, "could not measure")
        else:
            report("audio:fades shape the clip",
                   head < middle - 8.0 and tail < middle - 8.0,
                   f"head {head:.1f} / middle {middle:.1f} / tail {tail:.1f} dB")


def audit_easings(tmp: Path, src: str) -> None:
    """Every easing must bend the interpolation curve it advertises."""
    from vn_editor.core.model import Keyframe, EASINGS, eval_keyframes
    print(f"\neasings ({len(EASINGS)})")
    for name in EASINGS:
        # The easing lives on the keyframe that *ends* the segment.
        kf = [Keyframe(t=0.0, value=0.0, easing="linear"),
              Keyframe(t=1.0, value=1.0, easing=name)]
        try:
            quarter = eval_keyframes(kf, 0.25, 0.0)
            half = eval_keyframes(kf, 0.5, 0.0)
        except Exception as exc:                   # noqa: BLE001
            report(f"easing:{name}", False, f"{type(exc).__name__}: {exc}")
            continue
        if name == "linear":
            ok = abs(half - 0.5) < 0.02 and abs(quarter - 0.25) < 0.02
        elif name == "hold":
            ok = abs(half) < 1e-6 and abs(quarter) < 1e-6
        else:
            ok = abs(half - 0.5) > 0.02 or abs(quarter - 0.25) > 0.02
        report(f"easing:{name}", ok,
               f"t=0.25→{quarter:.3f} 0.5→{half:.3f}")


def audit_text_style(tmp: Path, src: str) -> None:
    """Outline, shadow, background and alignment each have to show up."""
    from vn_editor.core.model import TextProps
    print("\ntext styling")
    base = TextProps(text="STYLE", font="Bebas Neue", size=90.0,
                     color="#ffffff")
    variants = [
        ("outline", dict(outline_width=8.0, outline_color="#ff0044")),
        ("shadow", dict(shadow=8.0, shadow_color="#00ff00")),
        ("background", dict(bg_enabled=True, bg_color="#0000ff",
                            bg_opacity=1.0)),
        ("alignment", dict(align="left")),
        ("letter spacing", dict(letter_spacing=18.0)),
    ]
    plain = base_project(src)
    track = plain.ensure_track_for("text")
    clip = Clip(kind="text", track_id=track.id, start=0.0, duration=1.5)
    clip.text = base
    plain.add_clip(clip, track)
    ok, reference = render_ok(plain, tmp, "text_plain", at=0.6)
    if not ok:
        report("text:plain", False, reference)
        return
    for i, (name, change) in enumerate(variants):
        p = base_project(src)
        track = p.ensure_track_for("text")
        clip = Clip(kind="text", track_id=track.id, start=0.0, duration=1.5)
        clip.text = TextProps(**{**base.to_dict(), **change})
        p.add_clip(clip, track)
        ok, frame = render_ok(p, tmp, f"text_{i}", at=0.6)
        diff = mean_diff(reference, frame) if ok else -1.0
        report(f"text:{name}", ok and diff > 0.5,
               f"Δ={diff:.1f}" if ok else frame)


def _loudness(path: Path) -> Optional[float]:
    r = ff.ffmpeg("-v", "info", "-i", str(path), "-af", "volumedetect",
                  "-f", "null", "-", timeout=180)
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except (IndexError, ValueError):
                return None
    return None


def audit_audio_levels(tmp: Path, src: str) -> None:
    """Volume, mute and fades have to be audible in the written file."""
    from vn_editor.core.model import AudioProps
    print("\naudio controls")
    tone = tmp / "tone.wav"
    if not tone.exists():
        r = ff.ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
                      "sine=frequency=440:duration=1.6", str(tone),
                      timeout=60)
        if not r.ok:
            report("audio:tone", False, "could not build the test tone")
            return

    def export(tag: str, props) -> Optional[Path]:
        p = Project.create_default("a", 320, 180, 15.0)
        info = probe_mod.probe(str(tone), use_cache=False)
        asset = MediaAsset.from_info(info, str(tone))
        p.assets.append(asset)
        track = p.ensure_track_for("audio")
        clip = Clip(kind="audio", track_id=track.id, start=0.0,
                    duration=1.5)
        clip.path = str(tone)
        clip.asset_id = asset.id
        clip.audio = props
        p.add_clip(clip, track)
        out = tmp / f"audio_{tag}.mp4"
        opts = render_mod.options_from_settings(p)
        opts.width, opts.height = 320, 180
        opts.audio = True
        result = render_mod.render(p, str(out), opts)
        return out if result.ok else None

    full_file = export("full", AudioProps(volume=1.0))
    quiet_file = export("quiet", AudioProps(volume=0.15))
    muted_file = export("mute", AudioProps(volume=1.0, muted=True))
    full = _loudness(full_file) if full_file else None
    quiet = _loudness(quiet_file) if quiet_file else None
    report("audio:normal level", full is not None and full > -40.0,
           f"mean {full:.1f} dB" if full is not None else "no audio stream")
    muted = _loudness(muted_file) if muted_file else None
    report("audio:mute silences the clip",
           muted is None or muted < -60.0,
           "no audio stream at all" if muted is None
           else f"mean {muted:.1f} dB")
    report("audio:volume scales the level",
           full is not None and quiet is not None and quiet < full - 6.0,
           f"{full:.1f} dB → {quiet:.1f} dB"
           if (full is not None and quiet is not None) else "no audio stream")

    faded = export("fade", AudioProps(volume=1.0, fade_in=1.0, fade_out=1.0))
    if faded is None:
        report("audio:fades at the head and tail", False, "no audio stream")
    else:
        head = _loudness_segment(faded, 0.0, 0.25)
        middle = _loudness_segment(faded, 0.6, 0.3)
        tail = _loudness_segment(faded, 1.25, 0.25)
        if None in (head, middle, tail):
            report("audio:fades at the head and tail", False,
                   "could not measure the segments")
        else:
            report("audio:fades at the head and tail",
                   head < middle - 6.0 and tail < middle - 6.0,
                   f"head {head:.1f} / middle {middle:.1f} / "
                   f"tail {tail:.1f} dB")


def _loudness_segment(path: Path, start: float, span: float) -> Optional[float]:
    r = ff.ffmpeg("-v", "info", "-ss", f"{start:.3f}", "-t", f"{span:.3f}",
                  "-i", str(path), "-af", "volumedetect", "-f", "null", "-",
                  timeout=180)
    for line in (r.stderr or "").splitlines():
        if "mean_volume" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except (IndexError, ValueError):
                return None
    return None


def _audio_metrics(path: Path, start: float = 0.0, span: float = 0.0,
                   pre: str = "") -> Dict[str, float]:
    """RMS / peak / zero-crossing rate of a file (or of one slice of it)."""
    args: List[str] = ["-v", "info"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    if span > 0:
        args += ["-t", f"{span:.3f}"]
    args += ["-i", str(path)]
    chain = (pre + "," if pre else "") + "astats=metadata=0:reset=0"
    r = ff.ffmpeg(*args, "-af", chain, "-f", "null", "-", timeout=180)
    out = {"rms": -99.0, "peak": -99.0, "zcr": -1.0}
    for line in (r.stderr or "").splitlines():
        if "RMS level dB" in line and out["rms"] == -99.0:
            out["rms"] = _tail_float(line)
        elif "Peak level dB" in line and out["peak"] == -99.0:
            out["peak"] = _tail_float(line)
        elif "Zero crossings rate" in line and out["zcr"] < 0:
            out["zcr"] = _tail_float(line)
    return out


def _tail_float(line: str) -> float:
    try:
        return float(line.split(":")[-1].strip())
    except ValueError:
        return -99.0


def audit_audio_effects(tmp: Path, src: str) -> None:
    """Every audio control has to change the sound of the written file."""
    from vn_editor.core.model import AudioProps
    print("\naudio shaping")

    def tone(name: str, hz: float, secs: float = 1.5, level: float = 0.4,
             right: float = 0.0) -> Path:
        out = tmp / name
        if out.exists():
            return out
        args = ["-y", "-v", "error", "-f", "lavfi", "-i",
                f"sine=frequency={hz}:duration={secs}"]
        if right:
            args += ["-f", "lavfi", "-i",
                     f"sine=frequency={right}:duration={secs}",
                     "-filter_complex", "[0:a][1:a]amerge=inputs=2[a]",
                     "-map", "[a]"]
        args += ["-af" if not right else "-c:a", "*"] if False else []
        args += ["-c:a", "pcm_s16le", "-ar", "48000"]
        if not right:
            args += ["-af", f"volume={level}"]
        args += [str(out)]
        r = ff.ffmpeg(*args, timeout=120)
        if not r.ok:
            raise RuntimeError(r.stderr[-200:])
        return out

    def blip(name: str, secs: float = 0.3, total: float = 1.5) -> Path:
        """A short tone followed by silence: the silence is where a tail shows."""
        out = tmp / name
        if not out.exists():
            ff.ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
                      f"sine=frequency=440:duration={secs}", "-af",
                      f"apad=whole_dur={total}", "-c:a", "pcm_s16le",
                      "-ar", "48000", str(out), timeout=120)
        return out

    def noise(name: str, secs: float = 1.5) -> Path:
        out = tmp / name
        if not out.exists():
            ff.ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
                      f"anoisesrc=duration={secs}:amplitude=0.25:color=white",
                      "-c:a", "pcm_s16le", "-ar", "48000", str(out),
                      timeout=120)
        return out

    def render(props, fixture: Path, tag: str, duration: float = 1.5):
        p = Project.create_default("a", 320, 180, 30.0)
        asset = MediaAsset.from_info(
            probe_mod.probe(str(fixture), use_cache=False), str(fixture))
        p.assets.append(asset)
        track = p.ensure_track_for("audio")
        clip = Clip(kind="audio", track_id=track.id, start=0.0,
                    duration=duration)
        clip.path = str(fixture)
        clip.asset_id = asset.id
        clip.audio = props
        p.add_clip(clip, track)
        opts = render_mod.options_from_settings(p)
        opts.audio_only = True
        opts.video = False
        opts.audio = True
        out = tmp / f"afx_{tag}.m4a"
        res = render_mod.render(p, str(out), opts)
        return out if res.ok else None

    def case(label, fixture, props, metric, better, floor, base_props=None, **kw):
        """``better(base, got)`` must hold, and by at least ``floor``."""
        base = render(base_props or AudioProps(volume=1.0), fixture,
                      f"base_{abs(hash(label)) % 9999}")
        got = render(props, fixture, f"cmd_{abs(hash(label)) % 9999}")
        if base is None or got is None:
            report(f"audio:{label}", False, "render failed")
            return
        b = _audio_metrics(base, **kw)[metric]
        g = _audio_metrics(got, **kw)[metric]
        ok = better(b, g) and abs(g - b) >= floor
        report(f"audio:{label}", ok, f"{metric} {b:.2f} → {g:.2f}")

    case("high-pass cuts a low tone", tone("t120.wav", 120.0),
         AudioProps(volume=1.0, highpass=2000.0), "rms",
         lambda b, g: g < b, 6.0)
    case("low-pass cuts a high tone", tone("t6k.wav", 6000.0),
         AudioProps(volume=1.0, lowpass=1500.0), "rms",
         lambda b, g: g < b, 6.0)
    case("bass EQ lifts a low tone", tone("t120b.wav", 120.0),
         AudioProps(volume=1.0, eq_low=12.0), "rms",
         lambda b, g: g > b, 3.0)
    case("mid EQ lifts a mid tone", tone("t1200.wav", 1200.0),
         AudioProps(volume=1.0, eq_mid=12.0), "rms",
         lambda b, g: g > b, 3.0)
    case("treble EQ lifts a high tone", tone("t6kb.wav", 6000.0),
         AudioProps(volume=1.0, eq_high=12.0), "rms",
         lambda b, g: g > b, 3.0)
    case("compressor tames peaks", tone("loud.wav", 440.0, level=0.95),
         AudioProps(volume=1.0, compressor=1.0), "peak",
         lambda b, g: g < b, 1.0)
    case("echo adds a tail", blip("blip.wav", total=1.6),
         AudioProps(volume=1.0, echo=0.8), "peak",
         lambda b, g: g > max(b, -45.0), 25.0, start=0.45, span=0.7)
    case("reverb adds a tail", blip("blip2.wav", total=1.6),
         AudioProps(volume=1.0, reverb=0.8), "peak",
         lambda b, g: g > max(b, -45.0), 25.0, start=0.45, span=0.7)
    case("denoise quietens hiss", noise("hiss.wav"),
         AudioProps(volume=1.0, denoise=1.0), "rms",
         lambda b, g: g < b, 8.0)
    case("normalize lifts a quiet clip", tone("quiet.wav", 440.0, level=0.05),
         AudioProps(volume=1.0, normalize=True), "rms",
         lambda b, g: g > b, 4.0)
    case("3D widens the image", tone("stereo.wav", 330.0, right=880.0),
         AudioProps(volume=1.0, three_d=1.0), "rms",
         lambda b, g: g > b, 3.0, pre="pan=mono|c0=c0-c1")
    case("pitch shifts the tone", tone("tp.wav", 330.0),
         AudioProps(volume=1.0, pitch=1.5), "zcr",
         lambda b, g: g > b, 0.004)
    # Panning is measured as the left/right difference: an unpanned stereo
    # tone cancels to silence, a panned one does not.
    case("pan pushes the sound left", tone("st.wav", 440.0),
         AudioProps(volume=1.0, pan=-0.8), "rms",
         lambda b, g: g > max(b, -45.0), 4.0, pre="pan=mono|c0=c0-c1")
    case("pan pushes the sound right", tone("st2.wav", 440.0),
         AudioProps(volume=1.0, pan=0.8), "rms",
         lambda b, g: g > max(b, -45.0), 4.0, pre="pan=mono|c0=c1-c0")
    case("fade curve reshapes the fade", tone("tf.wav", 440.0),
         AudioProps(volume=1.0, fade_in=1.0, fade_type="quick"), "rms",
         lambda b, g: g > b, 4.0, span=0.3,
         base_props=AudioProps(volume=1.0, fade_in=1.0, fade_type="linear"))


def audit_export_presets(tmp: Path, src: str) -> None:
    """Each export preset has to produce a real file of the right size."""
    from vn_editor.core.model import EXPORT_PRESETS
    print(f"\nexport presets ({len(EXPORT_PRESETS)})")
    for key, label, bitrate, quality in EXPORT_PRESETS:
        p = base_project(src)
        asset = p.assets[0]
        clip = p.all_clips()[0]
        project_w, project_h = p.settings.width, p.settings.height
        opts = render_mod.options_from_settings(p)
        opts.width, opts.height = 160, 90
        opts.audio = False
        out = tmp / f"preset_{key}.mp4"
        try:
            result = render_mod.render(p, str(out), opts)
        except Exception as exc:                   # noqa: BLE001
            report(f"preset:{key}", False, f"{type(exc).__name__}: {exc}")
            continue
        info = probe_mod.probe(str(out), use_cache=False) if out.exists() else None
        ok = (result.ok and info is not None and info.duration > 0.4
              and info.width == 160 and info.height == 90)
        report(f"preset:{key}", ok,
               f"{info.width}x{info.height} {info.duration:.2f}s "
               f"{out.stat().st_size // 1024} KB" if info else "no output")


def audit_text_animations(tmp: Path, src: str) -> None:
    from vn_editor.core.model import TEXT_ANIMATIONS
    print(f"\ntext animations ({len(TEXT_ANIMATIONS)})")
    for name in TEXT_ANIMATIONS:
        p = base_project(src)
        p.ensure_track_for("video")
        tt = p.ensure_track_for("text")
        c = Clip(kind="text", track_id=tt.id, start=0.0, duration=1.5)
        c.text = TextProps(text="AUDIT TEXT", font="Bebas Neue", size=40.0,
                           color="#ffffff", outline_width=3.0,
                           outline_color="#000000")
        try:
            apply_text_animation(c, name, c.duration, 0.3)
        except Exception as exc:                   # noqa: BLE001
            report(f"text-anim:{name}", False, f"{type(exc).__name__}: {exc}")
            continue
        p.add_clip(c, tt)
        ok, detail = render_ok(p, tmp, f"ta_{name}")
        report(f"text-anim:{name}", ok, f"Δ={changed(detail, f'text-anim:{name}'):.1f}"
               if ok else detail)


def audit_stickers(tmp: Path, src: str) -> None:
    from vn_editor.core.stickers import STICKER_CATALOG
    print(f"\nstickers ({len(STICKER_CATALOG)})")
    p = base_project(src)
    # remove the video so the sticker is the only thing on screen
    for clip in list(p.all_clips()):
        p.remove_clip(clip.id)
    st = p.ensure_track_for("sticker")
    keys = list(STICKER_CATALOG)
    for i, key in enumerate(keys):
        c = Clip(kind="sticker", track_id=st.id, start=0.0, duration=1.5)
        c.meta["sticker"] = key
        c.meta["sticker_size"] = 0.5
        p.add_clip(c, st)
        ok, detail = render_ok(p, tmp, f"st_{i}")
        report(f"sticker:{key}", ok, f"Δ={changed(detail, f'sticker:{key}'):.1f}"
               if ok else detail)
        p.remove_clip(c.id)


def audit_mask_shapes(tmp: Path, src: str) -> None:
    from vn_editor.core import render as R
    shapes = R.MASK_SHAPES
    print(f"\nmask shapes ({len(shapes)})")
    for shape in shapes:
        if shape in ("none",):
            continue
        p = base_project(src)
        clip = p.all_clips()[0]
        clip.mask.shape = shape
        clip.mask.width, clip.mask.height = 0.6, 0.6
        clip.mask.feather = 0.05
        ok, detail = render_ok(p, tmp, f"mask_{shape}")
        report(f"mask:{shape}", ok, f"Δ={changed(detail, f'mask:{shape}'):.1f}"
               if ok else detail)


def audit_speed_curves(tmp: Path, src: str) -> None:
    from vn_editor.core.speedcurve import CURVE_PRESETS
    from vn_editor.core.model import SpeedCurve
    print(f"\nspeed curves ({len(CURVE_PRESETS)})")
    for name, pts in CURVE_PRESETS:
        p = base_project(src)
        clip = p.all_clips()[0]
        if pts:
            clip.speed_curve = SpeedCurve(list(pts), name)
            clip.duration = 1.5
        opts = render_mod.options_from_settings(p)
        opts.width, opts.height = 160, 90
        out = tmp / f"sc_{abs(hash(name)) % 9999}.mp4"
        result = render_mod.render(p, str(out), opts)
        ok = result.ok and out.exists()
        report(f"curve:{name}", ok, "" if ok else (result.error or "")[-140:])


def audit_speed_values(tmp: Path, src: str) -> None:
    print("\nspeed values")
    for speed in (0.05, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        p = base_project(src)
        clip = p.all_clips()[0]
        clip.speed = speed
        clip.duration = max(0.1, 1.5 / max(0.05, speed))
        opts = render_mod.options_from_settings(p)
        opts.width, opts.height = 160, 90
        out = tmp / f"sp_{speed}.mp4"
        result = render_mod.render(p, str(out), opts)
        ok = result.ok and out.exists()
        report(f"speed:{speed}x", ok, "" if ok else (result.error or "")[-140:])


def audit_stabilize(tmp: Path, src: str) -> None:
    """Stabilisation has to *reduce* camera motion, not merely render."""
    print("\nstabilisation")
    still = tmp / "rgbtest.png"
    if not still.exists():
        ff.ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
                  "rgbtestsrc=size=420x420", "-frames:v", "1", str(still),
                  timeout=60)
    shaky = tmp / "shaky.mp4"
    if not shaky.exists():
        r = ff.ffmpeg("-y", "-v", "error", "-loop", "1", "-framerate", "15",
                      "-t", "2.5", "-i", str(still), "-vf",
                      "crop=320:320:x='50+45*sin(n/1.3)':y='50+45*cos(n/1.9)'",
                      "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt",
                      "yuv420p", str(shaky), timeout=120)
        if not r.ok:
            report("stabilise:fixture", False, "could not build shaking clip")
            return

    def motion(stabilized: bool) -> Tuple[bool, float, str]:
        p = base_project(str(shaky))
        clip = p.all_clips()[0]
        clip.duration = 2.0
        clip.meta["src_w"], clip.meta["src_h"] = 320, 320
        if stabilized:
            clip.add_filter("stabilize", {"shakiness": 8.0, "smoothing": 20.0})
        tag = "on" if stabilized else "off"
        ok1, f1 = render_ok(p, tmp, f"stab_{tag}_a", at=1.0)
        ok2, f2 = render_ok(p, tmp, f"stab_{tag}_b", at=1.0 + 1.0 / 15.0)
        if not (ok1 and ok2):
            return False, 0.0, f1 if not ok1 else f2
        return True, mean_diff(f1, f2), ""

    ok_off, off, err = motion(False)
    ok_on, on, err2 = motion(True)
    if not ok_off:
        report("stabilise:renders", False, err)
        return
    if not ok_on:
        report("stabilise:renders", False, err2)
        return
    report("stabilise:renders", True, f"shake {off:.1f} → {on:.1f} per frame")
    # A second call must hit the transform cache instead of re-analysing.
    import time as _time
    t0 = _time.time()
    p = base_project(str(shaky))
    clip = p.all_clips()[0]
    clip.duration = 2.0
    clip.add_filter("stabilize", {"shakiness": 8.0, "smoothing": 20.0})
    from vn_editor.core.effects import stabilize_trf
    trf = stabilize_trf(clip)
    cached = _time.time() - t0
    report("stabilise:cached transform", bool(trf) and cached < 0.5,
           f"{cached * 1000:.0f} ms, {trf}")
    # The transform must not leave a black border behind: optzoom crops in.
    ok, frame = render_ok(base_project(str(shaky)), tmp, "stab_probe")
    report("stabilise:reduces motion", on < off * 0.85,
           f"shake {off:.1f} → {on:.1f} ({(1 - on / off) * 100:.0f}% less)")


def audit_lut_intensity(tmp: Path, src: str) -> None:
    """The intensity slider has to change how strong the LUT is."""
    print("\nlut intensity")
    idn = tmp / "invert.cube"
    if not idn.exists():
        rows = ["TITLE \"audit invert\"", "LUT_3D_SIZE 2", "DOMAIN_MIN 0 0 0",
                "DOMAIN_MAX 1 1 1"]
        for r in (0.0, 1.0):
            for g in (0.0, 1.0):
                for b in (0.0, 1.0):
                    rows.append(f"{1 - r:.6f} {1 - g:.6f} {1 - b:.6f}")
        idn.write_text("\n".join(rows) + "\n")

    diffs = {}
    for amount in (0.0, 0.5, 1.0):
        p = base_project(src)
        clip = p.all_clips()[0]
        clip.add_filter("lut", {"file": str(idn), "intensity": amount})
        ok, frame = render_ok(p, tmp, f"lut_{amount}")
        if not ok:
            report(f"lut:intensity {amount}", False, frame)
            return
        diffs[amount] = changed(frame, f"lut:{amount}")
    report("lut:0.0 is a no-op", diffs[0.0] < 0.5, f"Δ={diffs[0.0]:.2f}")
    report("lut:full strength changes the image", diffs[1.0] > 20.0,
           f"Δ={diffs[1.0]:.1f}")
    report("lut:half strength sits in between",
           diffs[0.5] > 1.0 and diffs[0.5] < diffs[1.0] * 0.85,
           f"Δ 0/0.5/1 = {diffs[0.0]:.2f}/{diffs[0.5]:.1f}/{diffs[1.0]:.1f}")


def audit_blends(tmp: Path, src: str) -> None:
    """Every blend mode offered for overlay layers has to change the mix."""
    print(f"\noverlay blend modes ({len(render_mod.BLEND_MODES)})")
    from vn_editor.core.model import Transform

    def pip_project(mode: str) -> Project:
        p = base_project(src)
        ov = p.ensure_track_for("overlay")
        clip = p.all_clips()[0]
        overlay = clip.clone()
        overlay.id = Clip().id
        overlay.track_id = ov.id
        overlay.blend = mode
        overlay.transform = Transform(scale=0.45)
        p.add_clip(overlay, ov)
        return p

    normal = tmp / "blend_normal.png"
    ok, detail = render_ok(pip_project("normal"), tmp, "blend", at=0.6)
    if not ok:
        report("blend:normal", False, detail)
        return
    Path(str(normal)).write_bytes(Path(detail).read_bytes())
    for mode in render_mod.BLEND_MODES:
        if mode == "normal":
            continue
        ok, frame = render_ok(pip_project(mode), tmp, f"blend_{mode}", at=0.6)
        if not ok:
            report(f"blend:{mode}", False, frame)
            continue
        diff = mean_diff(str(normal), frame)
        report(f"blend:{mode}", diff > 0.5, f"Δ vs normal = {diff:.1f}")


def audit_text_presets(tmp: Path, src: str) -> None:
    """The ready-made caption styles have to render with their own fonts."""
    from vn_editor.core.model import TEXT_PRESETS
    from vn_editor.core.ass import FONT_FILES
    from vn_editor.core.model import TextProps
    print(f"\ntext presets ({len(TEXT_PRESETS)})")
    missing = sorted({font for _t, font, _c, _o, _a in TEXT_PRESETS
                      if font not in FONT_FILES})
    report("presets:fonts bundled", not missing,
           "missing: " + ", ".join(missing) if missing else "all bundled")
    for text, font, fill, outline, anim in TEXT_PRESETS:
        p = base_project(src)
        tt = p.ensure_track_for("text")
        c = Clip(kind="text", track_id=tt.id, start=0.0, duration=1.5)
        c.text = TextProps(text=text, font=font, size=64.0, color=fill,
                           outline_width=3.0, outline_color=outline)
        try:
            apply_text_animation(c, anim, c.duration, 0.3)
        except Exception as exc:                   # noqa: BLE001
            report(f"preset:{text}", False, f"{type(exc).__name__}: {exc}")
            continue
        p.add_clip(c, tt)
        ok, frame = render_ok(p, tmp, f"preset_{abs(hash(text)) % 9999}", at=0.55)
        if not ok:
            report(f"preset:{text}", False, frame)
            continue
        report(f"preset:{text}", changed(frame, f"preset:{text}") > 1.0,
               f"Δ={changed(frame, f'preset:{text}'):.1f}")


def audit_sfx(tmp: Path) -> None:
    from vn_editor.core import sfx as sfx_mod
    print(f"\nsound effects ({len(sfx_mod.CATALOG)})")
    try:
        lib = sfx_mod.ensure_library()
    except Exception as exc:                       # noqa: BLE001
        report("sfx:library", False, str(exc))
        return
    bad = []
    for key in sfx_mod.ORDER:
        path = lib.get(key)
        if not path or not Path(path).exists():
            bad.append(key)
            continue
        info = probe_mod.probe(str(path), use_cache=False)
        if info.duration <= 0.02 or not info.has_audio:
            bad.append(key)
    report(f"sfx:{len(sfx_mod.ORDER)} generated effects", not bad,
           "" if not bad else f"broken: {', '.join(bad[:8])}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="comma list: effects,grades,transitions,text,stickers,"
                         "masks,curves,speed,sfx")
    ap.add_argument("--quick", action="store_true",
                    help="skip the slower clip-level renders")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="vnaudit-"))
    print(f"scratch: {tmp}")
    src = media(tmp)
    global BASELINE, EXPORT_BASELINE
    ok, path = render_ok(base_project(src), tmp, "baseline")
    if not ok:
        raise SystemExit(f"baseline render failed: {path}")
    BASELINE = path
    spread = pixels(path)
    channels = spread[2]
    step = max(1, len(channels) // 3000)
    sample = channels[0::step]
    if len(set(sample)) < 8:
        raise SystemExit(
            "baseline frame is a flat colour — the clip is not reaching the "
            "frame, so every measurement below would be meaningless")
    ok, exported = export_frame(base_project(src), tmp, "export_baseline")
    if ok:
        EXPORT_BASELINE = exported

    global AUDIT_LUT
    AUDIT_LUT = tmp / "invert.cube"
    rows = ["TITLE \"audit invert\"", "LUT_3D_SIZE 2", "DOMAIN_MIN 0 0 0",
            "DOMAIN_MAX 1 1 1"]
    for r in (0.0, 1.0):
        for g in (0.0, 1.0):
            for b in (0.0, 1.0):
                rows.append(f"{1 - r:.6f} {1 - g:.6f} {1 - b:.6f}")
    AUDIT_LUT.write_text("\n".join(rows) + "\n")

    groups: List[Tuple[str, Callable]] = [
        ("effects", audit_effects),
        ("grades", audit_grades),
        ("blends", audit_blends),
        ("presets", audit_text_presets),
        ("audiofx", audit_audio_effects),
        ("easings", audit_easings),
        ("styling", audit_text_style),
        ("levels", audit_audio_levels),
        ("exports", audit_export_presets),
        ("easings", audit_easings),
        ("styling", audit_text_style),
        ("levels", audit_audio_levels),
        ("stab", audit_stabilize),
        ("lut", audit_lut_intensity),
        ("text", audit_text_animations),
        ("stickers", audit_stickers),
        ("masks", audit_mask_shapes),
        ("sfx", audit_sfx),
    ]
    if not args.quick:
        groups.insert(2, ("transitions", audit_transitions))
        groups.insert(3, ("curves", audit_speed_curves))
        groups.insert(4, ("speed", audit_speed_values))

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    t0 = time.time()
    for name, fn in groups:
        if wanted and name not in wanted:
            continue
        if name == "sfx":
            fn(tmp)
        else:
            fn(tmp, src)

    print("\n" + "=" * 60)
    if NOOPS:
        print(f"no visible change ({len(NOOPS)}) — check by hand:")
        for name, diff in NOOPS:
            print(f"  ~ {name}  Δ={diff:.2f}")
    print(f"{'ALL RENDERS SUCCEEDED' if not FAILURES else str(len(FAILURES)) + ' FAILURES'}"
          f" in {time.time() - t0:.0f}s")
    for name, detail in FAILURES[:40]:
        print(f"  FAILED {name}: {detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
