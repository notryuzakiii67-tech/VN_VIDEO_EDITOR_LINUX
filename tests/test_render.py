#!/usr/bin/env python3
"""End-to-end render tests.

These render real files through the real graph compiler, so a green run means
the feature genuinely works with the local FFmpeg build.  Run with::

    python tests/test_render.py            # all tests
    python tests/test_render.py speed      # only matching tests

Requires FFmpeg.  Test media is generated on the fly into a temp directory.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vn_editor.core import ass as ass_mod          # noqa: E402
from vn_editor.core import ffmpeg as ff            # noqa: E402
from vn_editor.core import probe                   # noqa: E402
from vn_editor.core import render as R             # noqa: E402
from vn_editor.core import sfx                     # noqa: E402
from vn_editor.core.model import (Clip, FilterInstance, MaskProps,          # noqa: E402
                                  MediaAsset, Project, SpeedCurve, TextProps,
                                  Transition)

TMP = Path(tempfile.mkdtemp(prefix="vn-render-tests-"))
FAILURES = []
PASSES = []


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #

def sh(cmd, timeout=180, binary=False):
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=not binary, timeout=timeout,
                          encoding=None if binary else "utf-8",
                          errors=None if binary else "replace")


def make_source(name: str, w: int, h: int, dur: float, fps: float = 30,
                audio: bool = True) -> Path:
    """Synthetic but visually rich test clip."""
    out = TMP / name
    if out.exists():
        return out
    args = [ff.TOOLCHAIN.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"testsrc2=size={w}x{h}:rate={fps}:duration={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-g", str(int(fps))]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args += [str(out)]
    r = sh(args)
    if r.returncode != 0:
        raise RuntimeError(f"could not build test media: {r.stderr[-400:]}")
    return out


def make_green_clip(name="green.mp4", w=320, h=240, dur=2.0) -> Path:
    out = TMP / name
    if out.exists():
        return out
    sh([ff.TOOLCHAIN.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x00b140:s={w}x{h}:r=30:d={dur}",
        "-f", "lavfi", "-i", f"testsrc2=size=120x80:rate=30:duration={dur}",
        "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:(H-h)/2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(out)])
    return out


def base_project(w=540, h=960, fps=30.0, name="t") -> Project:
    p = Project.create_default(name, w, h, fps)
    p.settings.background = "#101418"
    return p


def add_asset(project: Project, path) -> MediaAsset:
    info = probe.probe(str(path), use_cache=False)
    a = MediaAsset.from_info(info, str(path))
    project.assets.append(a)
    return a


def add_video(project, asset, start=0.0, duration=2.0, src_in=0.0, speed=1.0,
              track=None, **meta):
    tr = track or project.tracks[0]
    c = Clip(kind="video", track_id=tr.id, start=start, duration=duration,
             src_in=src_in, speed=speed)
    c.path = asset.path
    c.asset_id = asset.id
    c.meta["has_audio"] = asset.has_audio
    c.meta["src_w"] = asset.width
    c.meta["src_h"] = asset.height
    c.meta["src_duration"] = asset.duration
    c.meta.update(meta)
    project.add_clip(c, tr)
    return c


def in_track(project, kind):
    for t in project.tracks:
        if t.kind == kind:
            return t
    return project.add_track(kind)


def render(p, out, **kw):
    opts = R.options_from_settings(p)
    opts.crf = 24
    opts.preset = "ultrafast"
    opts.audio = kw.pop("audio", True)
    for k, v in kw.items():
        setattr(opts, k, v)
    return R.render(p, str(TMP / out), opts)


def check(cond, msg, detail=""):
    if cond:
        return True
    FAILURES.append(f"{msg}" + (f"\n   {detail}" if detail else ""))
    return False


def ok_output(path: Path, min_dur=0.0, expect_w=None, expect_h=None,
              expect_audio=None):
    if not path.exists():
        return False, "output file missing"
    info = probe.probe(str(path), use_cache=False)
    if info.duration < min_dur - 0.25:
        return False, f"duration {info.duration:.2f}s < {min_dur}"
    if expect_w and info.width != expect_w:
        return False, f"width {info.width} != {expect_w}"
    if expect_h and info.height != expect_h:
        return False, f"height {info.height} != {expect_h}"
    if expect_audio is not None and info.has_audio != expect_audio:
        return False, f"has_audio {info.has_audio} != {expect_audio}"
    if not info.has_video:
        return False, "no video stream"
    return True, f"{info.width}x{info.height} {info.duration:.2f}s"


def frame_at(path: Path, t: float, out: str):
    o = TMP / out
    sh([ff.TOOLCHAIN.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-update", "1",
        str(o)])
    return o


def avg_rgb(png: Path):
    """Mean R,G,B of a PNG via an 8x8 downscale (robust, no PIL)."""
    raw = TMP / "tmp.rgb"
    r = sh([ff.TOOLCHAIN.ffmpeg, "-y", "-v", "error", "-i", str(png),
            "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "rgb24",
            str(raw)], binary=True)
    if r.returncode != 0 or not raw.exists():
        return None
    data = raw.read_bytes()
    n = len(data) // 3
    if not n:
        return None
    return (sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n)


def scrub_png(project, t: float, out: str, w: int = 160, h: int = 90) -> Path:
    """One composited frame from the interactive (scrub) path."""
    o = TMP / out
    R.render_frame(project, t, w, h, str(o))
    return o


def frame_diff(a: Path, b: Path) -> float:
    """Mean absolute channel difference between two stills (0…255)."""
    if not (a.exists() and b.exists()):
        return -1.0
    ra, rb = TMP / "diff_a.rgb", TMP / "diff_b.rgb"
    for src, dst in ((a, ra), (b, rb)):
        r = sh([ff.TOOLCHAIN.ffmpeg, "-y", "-v", "error", "-i", str(src),
                "-vf", "scale=64:64", "-f", "rawvideo", "-pix_fmt", "rgb24",
                str(dst)], binary=True)
        if r.returncode != 0:
            return -1.0
    da, db = ra.read_bytes(), rb.read_bytes()
    if not da or len(da) != len(db):
        return -1.0
    step = max(1, len(da) // 3000)
    total = count = 0
    for i in range(0, len(da) - 3, step * 3):
        total += abs(da[i] - db[i]) + abs(da[i + 1] - db[i + 1]) \
            + abs(da[i + 2] - db[i + 2])
        count += 3
    return total / max(1, count)


TESTS = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------- #
#  Tests
# --------------------------------------------------------------------------- #

@test("environment")
def t_env():
    check(ff.TOOLCHAIN.ok, "FFmpeg not found — install it or bundle it")
    missing = ff.TOOLCHAIN.missing_essentials()
    check(not missing, f"essential filters missing: {missing}")
    sup = R.probe_render_support()
    print(f"      ffmpeg={ff.TOOLCHAIN.version} "
          f"filters={len(ff.TOOLCHAIN.filters)} "
          f"x264={sup['libx264']} ass={sup['ass']} xfade={sup['xfade']} "
          f"zoompan={sup['zoompan']}")


@test("basic export")
def t_basic():
    src = make_source("src16x9.mp4", 640, 360, 6.0)
    p = base_project()
    a = add_asset(p, src)
    add_video(p, a, 0.0, 3.0)
    res = render(p, "basic.mp4")
    check(res.ok, "render failed", res.error)
    ok, why = ok_output(TMP / "basic.mp4", 2.5, 540, 960, True)
    check(ok, f"basic export wrong: {why}")


@test("vertical 9:16 from 16:9 with zoom")
def t_vertical():
    src = make_source("src2.mp4", 1280, 720, 5.0)
    p = base_project(1080, 1920)
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 2.0)
    c.transform.fit = "cover"
    c.transform.scale = 1.2
    c.transform.y = 0.55
    res = render(p, "vertical.mp4", width=1080, height=1920, crf=30)
    check(res.ok, "vertical render failed", res.error)
    ok, why = ok_output(TMP / "vertical.mp4", 1.8, 1080, 1920, True)
    check(ok, f"vertical output wrong: {why}")


@test("speed change keeps audio in sync")
def t_speed():
    src = make_source("src3.mp4", 640, 360, 8.0)
    for factor, label in ((2.0, "2x"), (0.5, "0.5x"), (4.0, "4x")):
        p = base_project()
        a = add_asset(p, src)
        c = add_video(p, a, 0.0, 2.0, speed=factor)
        out = TMP / f"speed_{label}.mp4"
        res = render(p, f"speed_{label}.mp4")
        if not res.ok:
            check(False, f"speed {label} render failed", res.error)
            continue
        ok, why = ok_output(out, 1.8, expect_audio=True)
        check(ok, f"speed {label}: {why}")


@test("speed curve ramp")
def t_speed_curve():
    src = make_source("src4.mp4", 640, 360, 6.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 2.0)
    c.speed_curve = SpeedCurve([(0.0, 0.5), (0.5, 2.0), (1.0, 0.5)],
                               "Slow Fast Slow")
    res = render(p, "ramp.mp4")
    check(res.ok, "speed ramp failed", res.error)
    ok, why = ok_output(TMP / "ramp.mp4", 1.8)
    check(ok, f"ramp output wrong: {why}")


@test("reverse and freeze frame")
def t_rev_freeze():
    src = make_source("src5.mp4", 640, 360, 5.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 1.5)
    c.reverse = True
    res = render(p, "reverse.mp4")
    check(res.ok, "reverse failed", res.error)
    ok, why = ok_output(TMP / "reverse.mp4", 1.3)
    check(ok, f"reverse output wrong: {why}")

    p2 = base_project()
    a2 = add_asset(p2, src)
    c2 = add_video(p2, a2, 0.0, 1.5, src_in=1.0)
    c2.freeze = True
    c2.freeze_at = 0.4
    res2 = render(p2, "freeze.mp4")
    check(res2.ok, "freeze failed", res2.error)
    ok, why = ok_output(TMP / "freeze.mp4", 1.3)
    check(ok, f"freeze output wrong: {why}")


@test("keyframed transform (zoom+pan)")
def t_keyframes():
    src = make_source("src6.mp4", 640, 360, 5.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 2.0)
    c.set_keyframe("scale", 0.0, 1.0)
    c.set_keyframe("scale", 2.0, 2.2)
    c.set_keyframe("x", 0.0, 0.4)
    c.set_keyframe("x", 2.0, 0.6)
    res = render(p, "keyframes.mp4")
    check(res.ok, "keyframe transform failed", res.error)
    ok, why = ok_output(TMP / "keyframes.mp4", 1.8)
    check(ok, f"keyframe output wrong: {why}")
    # the frames must actually differ
    f0 = frame_at(TMP / "keyframes.mp4", 0.1, "kf0.png")
    f1 = frame_at(TMP / "keyframes.mp4", 1.8, "kf1.png")
    a0, a1 = avg_rgb(f0), avg_rgb(f1)
    if a0 and a1:
        diff = sum(abs(x - y) for x, y in zip(a0, a1))
        check(diff > 1.0, f"keyframed zoom produced identical frames (diff={diff:.2f})")


@test("opacity keyframes")
def t_opacity():
    src = make_source("src7.mp4", 480, 270, 5.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 2.0)
    c.set_keyframe("opacity", 0.0, 0.0)
    c.set_keyframe("opacity", 1.0, 1.0)
    res = render(p, "opacity.mp4")
    check(res.ok, "opacity keyframes failed", res.error)


@test("multi-clip run keeps its timeline")
def t_multiclip():
    """Two clips end to end: no gap, no clipped tail.

    Every layer used to be padded with its transparent canvas, so ``concat``
    laid the run out too long and the second clip only appeared late — as bare
    background — with its tail cut off.
    """
    src = make_source("src_run.mp4", 640, 360, 4.0)
    p = base_project()
    a = add_asset(p, src)
    add_video(p, a, 0.0, 1.5, src_in=0.0)
    add_video(p, a, 1.5, 1.5, src_in=2.0)
    res = render(p, "run.mp4")
    if not check(res.ok, "multi-clip render failed", res.error):
        return
    ok, why = ok_output(TMP / "run.mp4", 2.9)
    check(ok, f"multi-clip output wrong: {why}")
    empty = base_project()
    render(empty, "run_empty.mp4", audio=False)
    bg = avg_rgb(frame_at(TMP / "run_empty.mp4", 0.5, "run_bg.png"))
    for t in (0.2, 1.6, 1.9, 2.85):
        f = avg_rgb(frame_at(TMP / "run.mp4", t, f"run_{int(t * 100)}.png"))
        if f and bg:
            d = sum(abs(x - y) for x, y in zip(f, bg))
            check(d > 12,
                  f"clip missing at t={t}s — the frame is the background",
                  f"Δ={d:.1f} vs an empty project")


@test("transitions")
def t_transitions():
    # Two clips that look nothing alike: a transition between two frames of the
    # same source is nearly invisible at its midpoint, which would say nothing
    # about whether the transition ran at all.
    src = make_source("src8.mp4", 640, 360, 6.0)
    other = make_green_clip("src8b.mp4", 640, 360, 2.0)
    keys = ["fade", "wipeleft", "circleopen", "slideup", "dissolve",
            "pixelize", "diagtr", "spin_dissolve", "dip_black", "flash"]

    def build(key):
        p = base_project()
        a, b = add_asset(p, src), add_asset(p, other)
        c1 = add_video(p, a, 0.0, 1.0, src_in=0.0)
        c2 = add_video(p, b, 1.0, 1.0)
        if key:
            c1.transition_out = Transition(key, 0.45, "out")
        return p

    plain = TMP / "tr_none.mp4"
    res = render(build(None), "tr_none.mp4")
    check(res.ok, "plain two-clip render failed", res.error)
    plain_dur = probe.probe(str(plain), use_cache=False).duration
    empty = base_project()
    render(empty, "tr_empty.mp4", audio=False)
    bg = avg_rgb(frame_at(TMP / "tr_empty.mp4", 0.5, "tr_bg.png"))

    for key in keys:
        out = TMP / f"tr_{key}.mp4"
        res = render(build(key), f"tr_{key}.mp4")
        if not check(res.ok, f"transition '{key}' failed", res.error):
            continue
        good, why = ok_output(out, 1.9)
        check(good, f"transition '{key}': {why}")
        dur = probe.probe(str(out), use_cache=False).duration
        check(abs(dur - plain_dur) < 0.15,
              f"transition '{key}' moved the timeline",
              f"{dur:.2f}s vs {plain_dur:.2f}s without a transition")
        # A transition that silently degrades to a hard cut still renders, so
        # compare frames from inside the transition with the same frames from
        # the identical project without one.
        best, mid_best = 0.0, None
        for i in range(7):
            t = 0.85 + i * 0.1
            diff = frame_diff(frame_at(out, t, f"tt_{key}_{i}.png"),
                              frame_at(plain, t, f"tp_{key}_{i}.png"))
            if diff > best:
                best, mid_best = diff, (t, diff)
        check(best > 8.0, f"transition '{key}' rendered as a hard cut",
              f"max Δ vs a plain cut = {best:.1f}")
        if key in ("fade", "dissolve") and bg:
            f = avg_rgb(frame_at(out, 1.2, f"tr_{key}_mid.png"))
            if f:
                d = sum(abs(x - y) for x, y in zip(f, bg))
                check(d > 20, f"'{key}' faded through the background "
                              "instead of mixing the clips", f"Δ={d:.1f}")
        if key == "dip_black":
            mid = avg_rgb(frame_at(out, 1.2, "tr_dip_mid.png"))
            plain_mid = avg_rgb(frame_at(plain, 1.2, "tr_dip_plain.png"))
            if mid and plain_mid:
                check(sum(mid) < sum(plain_mid) * 0.6,
                      "dip to black did not darken the frame",
                      f"{sum(mid) / 3:.0f} vs {sum(plain_mid) / 3:.0f} per channel")


@test("effects stack")
def t_effects():
    src = make_source("src9.mp4", 480, 270, 4.0)
    stacks = {
        "color": [("basic", {"brightness": 0.1, "contrast": 1.15,
                             "saturation": 1.3}),
                  ("vibrance", {"amount": 0.3}),
                  ("curves", {"shadows": 0.2, "highlights": -0.1})],
        "blur": [("blur", {"sigma": 6.0})],
        "pixel": [("mosaic", {"size": 20.0})],
        "grain": [("grain", {"amount": 20.0}), ("vignette", {"angle": 0.7})],
        "glow": [("glow", {"amount": 0.6, "radius": 14.0})],
        "vhs": [("vhs", {"amount": 0.7})],
        "mirror": [("mirror", {"mode": 0})],
        "cartoon": [("cartoon", {"levels": 6.0})],
        "scanline": [("scanline", {"amount": 0.5})],
        "split": [("rgb_split", {"offset": 8.0})],
        "thermal": [("heat", {"mix": 0.8})],
        "sepia": [("sepia", {"amount": 0.9})],
        "bw": [("bw", {"amount": 1.0})],
        "edge": [("edge", {"mode": 1})],
        "posterize": [("posterize", {"levels": 5.0})],
        "denoise": [("denoise", {"amount": 5.0})],
        "sharpen": [("sharpen", {"amount": 1.0})],
        "hue": [("hue", {"hue": 90.0, "saturation": 1.2})],
        "gb": [("grade", {"name": "Teal & Orange", "intensity": 1.0})],
        "advanced": [("glow", {"amount": 0.5}), ("noise_fix", {})],
    }
    for name, stack in stacks.items():
        p = base_project()
        a = add_asset(p, src)
        c = add_video(p, a, 0.0, 1.2)
        for kind, params in stack:
            if kind == "noise_fix":
                continue
            c.filters.append(FilterInstance(kind, params))
        out = TMP / f"fx_{name}.mp4"
        res = render(p, f"fx_{name}.mp4", audio=False)
        if not res.ok:
            check(False, f"effect stack '{name}' failed",
                  (res.error or "")[-500:])
            continue
        good, why = ok_output(out, 1.0)
        check(good, f"effect stack '{name}': {why}")


@test("chroma key")
def t_chroma():
    bg = make_source("bg_src.mp4", 640, 360, 3.0)
    green = make_green_clip()
    p = base_project()
    a1 = add_asset(p, bg)
    add_video(p, a1, 0.0, 1.5)
    ov = in_track(p, "overlay")
    a2 = add_asset(p, green)
    c = add_video(p, a2, 0.0, 1.5, track=ov)
    c.chroma.enabled = True
    c.chroma.color = "#00b140"
    c.chroma.similarity = 0.25
    c.chroma.blend = 0.1
    c.chroma.spill = 0.5
    c.transform.scale = 0.6
    res = render(p, "chroma.mp4", audio=False)
    check(res.ok, "chroma key failed", res.error)
    ok, why = ok_output(TMP / "chroma.mp4", 1.3)
    check(ok, f"chroma output wrong: {why}")


@test("masks")
def t_masks():
    src = make_source("src10.mp4", 480, 270, 3.0)
    for shape in ("rect", "ellipse", "linear", "heart", "star", "split"):
        p = base_project()
        ov = in_track(p, "overlay")
        a = add_asset(p, src)
        c = add_video(p, a, 0.0, 1.0, track=ov)
        c.mask = MaskProps(shape=shape, x=0.5, y=0.5, width=0.7,
                           height=0.7, feather=0.05)
        res = render(p, f"mask_{shape}.mp4", audio=False)
        if not res.ok:
            check(False, f"mask '{shape}' failed", (res.error or "")[-400:])
            continue
        ok, why = ok_output(TMP / f"mask_{shape}.mp4", 0.9)
        check(ok, f"mask '{shape}': {why}")

    # A mask that renders but changes nothing is still broken: the coverage
    # plane has to reach alphamerge, so compare a masked frame with a bare one.
    src2 = make_source("src10.mp4", 480, 270, 3.0)
    plain = base_project()
    ovp = in_track(plain, "overlay")
    cp = add_video(plain, add_asset(plain, src2), 0.0, 1.0, track=ovp)
    masked = base_project()
    ovm = in_track(masked, "overlay")
    cm = add_video(masked, add_asset(masked, src2), 0.0, 1.0, track=ovm)
    cm.mask = MaskProps(shape="ellipse", width=0.5, height=0.5, feather=0.02)
    a_img = scrub_png(plain, 0.5, "mask_plain.png")
    b_img = scrub_png(masked, 0.5, "mask_applied.png")
    diff = frame_diff(a_img, b_img)
    check(diff > 3.0, "mask has no effect on the scrub frame",
          f"frame difference {diff:.2f} — the alpha plane is not reaching "
          f"alphamerge")
    plain_out = render(plain, "mask_export_plain.mp4", audio=False)
    masked_out = render(masked, "mask_export_ellipse.mp4", audio=False)
    if plain_out.ok and masked_out.ok:
        pa = frame_at(TMP / "mask_export_plain.mp4", 0.5, "mask_exp_plain.png")
        pb = frame_at(TMP / "mask_export_ellipse.mp4", 0.5, "mask_exp.png")
        diff = frame_diff(pa, pb)
        check(diff > 3.0, "mask has no effect on the exported video",
              f"frame difference {diff:.2f}")
    else:
        check(False, "mask export comparison could not render")


@test("scrub preview shows the frame at the requested time")
def t_scrub_time():
    # testsrc draws a running timestamp, so a wrong frame is easy to spot: the
    # preview must match the export at the *same* time and not at another.
    src = make_source("counter.mp4", 320, 180, 3.0, 30, audio=False)
    p = base_project(320, 180, 30.0)
    p.settings.background = "#000000"
    add_video(p, add_asset(p, src), 0.0, 3.0)
    opts = R.options_from_settings(p)
    opts.width, opts.height = 320, 180
    opts.audio = False
    res = R.render(p, str(TMP / "counter_export.mp4"), opts)
    check(res.ok, "counter export failed", res.error)

    # A frame that is only the background would make the comparisons below
    # meaningless (and used to happen when a layer's timestamp was dropped).
    empty = base_project(320, 180, 30.0)
    empty.settings.background = "#000000"
    blank = scrub_png(empty, 0.5, "scrub_blank.png", 320, 180)
    content = scrub_png(p, 0.5, "scrub_content.png", 320, 180)
    check(frame_diff(blank, content) > 20.0,
          "the scrub frame does not contain the clip at all",
          f"difference {frame_diff(blank, content):.2f}")

    times = (0.5, 1.5, 2.5, 2.9)
    exports = {}
    for t in times:
        exports[t] = frame_at(TMP / "counter_export.mp4", t,
                              f"counter_{t}.png")
    for t in times:
        prev = TMP / f"scrub_{t}.png"
        R.render_frame(p, t, 320, 180, str(prev))
        same = frame_diff(prev, exports[t])
        nearest = min((frame_diff(prev, exports[u]), u) for u in times)
        check(same < 6.0,
              f"scrub frame at {t}s does not match the export at the same time",
              f"difference {same:.2f}")
        check(abs(nearest[1] - t) < 0.2,
              f"scrub frame at {t}s is actually the frame at {nearest[1]}s",
              f"closest match is {nearest[1]}s (difference {nearest[0]:.2f})")


@test("stabilisation reduces camera shake")
def t_stabilize():
    still = TMP / "stab_still.png"
    sh([ff.TOOLCHAIN.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
        "rgbtestsrc=size=420x420", "-frames:v", "1", str(still)])
    shaky = TMP / "shaky.mp4"
    sh([ff.TOOLCHAIN.ffmpeg, "-y", "-v", "error", "-loop", "1",
        "-framerate", "30", "-t", "2.0", "-i", str(still), "-vf",
        "crop=320:320:x='50+40*sin(n/1.3)':y='50+40*cos(n/1.9)'",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(shaky)])

    def shake(stabilized: bool) -> float:
        p = base_project(320, 320, 30.0)
        a = add_asset(p, shaky)
        c = add_video(p, a, 0.0, 2.0)
        c.meta["src_w"], c.meta["src_h"] = 320, 320
        if stabilized:
            c.add_filter("stabilize", {"shakiness": 8.0, "smoothing": 20.0})
        f1 = scrub_png(p, 1.0, f"stab_{stabilized}_a.png")
        f2 = scrub_png(p, 1.0 + 1 / 30, f"stab_{stabilized}_b.png")
        return frame_diff(f1, f2)

    free, held = shake(False), shake(True)
    check(held >= 0 and free > 0.2, "stabilisation test could not measure shake",
          f"raw shake {free:.2f}")
    check(held < free * 0.8, "stabilisation did not reduce the shake",
          f"{free:.2f} → {held:.2f} per frame")


@test("LUT intensity")
def t_lut():
    src = make_source("src16x9.mp4", 640, 360, 6.0)
    cube = TMP / "invert.cube"
    rows = ["TITLE \"invert\"", "LUT_3D_SIZE 2", "DOMAIN_MIN 0 0 0",
            "DOMAIN_MAX 1 1 1"]
    for r in (0.0, 1.0):
        for g in (0.0, 1.0):
            for b in (0.0, 1.0):
                rows.append(f"{1 - r:.6f} {1 - g:.6f} {1 - b:.6f}")
    cube.write_text("\n".join(rows) + "\n")

    frames = {}
    for amount in (0.0, 0.5, 1.0):
        p = base_project()
        a = add_asset(p, src)
        c = add_video(p, a, 0.0, 1.0)
        c.add_filter("lut", {"file": str(cube), "intensity": amount})
        frames[amount] = scrub_png(p, 0.5, f"lut_{amount}.png")
    base_img = frames[0.0]
    full = frame_diff(base_img, frames[1.0])
    half = frame_diff(base_img, frames[0.5])
    check(full > 5.0, "a full-strength LUT barely changes the frame",
          f"difference {full:.2f}")
    check(0.5 < half < full * 0.85, "LUT intensity does not scale the effect",
          f"0.5 → {half:.2f}, 1.0 → {full:.2f}")
    plain = base_project()
    add_video(plain, add_asset(plain, src), 0.0, 1.0)
    check(frame_diff(scrub_png(plain, 0.5, "lut_none.png"), base_img) < 0.4,
          "intensity 0 should be a no-op")

    # the export path has to apply it too, not just the scrub preview
    outs = {}
    for amount in (0.0, 1.0):
        p = base_project()
        c = add_video(p, add_asset(p, src), 0.0, 1.0)
        c.add_filter("lut", {"file": str(cube), "intensity": amount})
        res = render(p, f"lut_export_{amount}.mp4", audio=False)
        if not res.ok:
            check(False, f"LUT export failed at intensity {amount}", res.error)
            return
        outs[amount] = frame_at(TMP / f"lut_export_{amount}.mp4", 0.5,
                                f"lut_x_{amount}.png")
    diff = frame_diff(outs[0.0], outs[1.0])
    check(diff > 5.0, "the exported video ignores the LUT", f"difference {diff:.2f}")


@test("tint slider is visible")
def t_tint():
    src = make_source("src16x9.mp4", 640, 360, 6.0)
    plain = base_project()
    add_video(plain, add_asset(plain, src), 0.0, 1.0)
    bare = scrub_png(plain, 0.5, "tint_none.png")
    for amount, label in ((-0.5, "magenta"), (0.5, "green")):
        p = base_project()
        c = add_video(p, add_asset(p, src), 0.0, 1.0)
        c.add_filter("tint", {"tint": amount})
        diff = frame_diff(bare, scrub_png(p, 0.5, f"tint_{label}.png"))
        check(diff > 0.75, f"tint {label} is invisible (difference {diff:.2f})")


@test("levels effect")
def t_levels():
    src = make_source("src16x9.mp4", 640, 360, 6.0)
    plain = base_project()
    add_video(plain, add_asset(plain, src), 0.0, 1.0)
    bare = scrub_png(plain, 0.5, "levels_none.png")
    for params, label in (({"black": 0.3, "white": 0.8}, "black/white"),
                          ({"gamma": 0.5}, "gamma"),
                          ({"black": 0.2, "white": 0.9, "gamma": 1.8}, "all")):
        p = base_project()
        c = add_video(p, add_asset(p, src), 0.0, 1.0)
        c.add_filter("levels", dict(params))
        diff = frame_diff(bare, scrub_png(p, 0.5, f"levels_{label.split('/')[0]}.png"))
        check(diff > 1.0, f"levels ({label}) has no effect", f"{diff:.2f}")


@test("speed-curve presets render")
def t_curve_presets():
    from vn_editor.core.speedcurve import CURVE_PRESETS
    src = make_source("src16x9.mp4", 480, 270, 4.0)
    for name, pts in CURVE_PRESETS:
        p = base_project(270, 480, 30.0)
        c = add_video(p, add_asset(p, src), 0.0, 1.5)
        if pts:
            c.speed_curve = SpeedCurve([tuple(x) for x in pts], name)
            c.duration = 1.5
        res = render(p, f"curve_{abs(hash(name)) % 9999}.mp4", audio=False)
        check(res.ok, f"speed curve '{name}' failed", (res.error or "")[-300:])


@test("text / captions / stickers via libass")
def t_text():
    src = make_source("src11.mp4", 640, 360, 4.0)
    p = base_project()
    a = add_asset(p, src)
    add_video(p, a, 0.0, 2.5)
    tt = in_track(p, "text")
    styles = [("Bebas Neue", "POV: YOU ARE HIM", "#ffffff", "pop_in"),
              ("Anton", "INSANE CLUTCH", "#ffe14d", "slide_left"),
              ("Archivo Black", "GG", "#39d98a", "fade_in"),
              ("Bungee", "CLUTCHED", "#ff5c8a", "zoom_in")]
    for i, (font, text, color, anim) in enumerate(styles):
        c = Clip(kind="text", track_id=tt.id, start=0.2 + i * 0.5,
                 duration=1.4)
        c.text = TextProps(text=text, font=font, size=90, color=color,
                           outline_width=6, outline_color="#000000", shadow=3)
        c.transform.y = 0.22 + i * 0.18
        ass_mod.apply_text_animation(c, anim, c.duration, 0.3)
        p.add_clip(c, tt)
    res = render(p, "text.mp4")
    check(res.ok, "text render failed", res.error)
    ok, why = ok_output(TMP / "text.mp4", 2.3)
    check(ok, f"text output wrong: {why}")
    f = frame_at(TMP / "text.mp4", 1.4, "text_mid.png")
    rgb = avg_rgb(f)
    check(f.exists(), "no frame produced for the text render")
    if rgb:
        check(sum(rgb) > 6, f"text frame looks empty (mean={rgb})")


@test("text auto-fit never overflows")
def t_text_fit():
    from vn_editor.core.fonts import fit_size
    for font in ("Bebas Neue", "Anton", "Inter", "Montserrat"):
        w = fit_size("POV: YOU ARE HIM IN MINECRAFT", font, 200, 1020)
        check(w <= 200, f"{font} auto-fit grew the size ({w})")
        check(w >= 8, f"{font} auto-fit collapsed ({w})")


@test("audio mix, fades and sfx")
def t_audio():
    src = make_source("src12.mp4", 480, 270, 4.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 2.0)
    c.audio.fade_in = 0.3
    c.audio.fade_out = 0.3
    c.audio.volume = 0.8
    at = in_track(p, "audio")
    for key, start in (("thud", 0.2), ("fahhh", 0.9)):
        path = sfx.resolve(key)
        if not path:
            continue
        inf = probe.probe(path, use_cache=False)
        asset = MediaAsset.from_info(inf, path)
        p.assets.append(asset)
        ac = Clip(kind="audio", track_id=at.id, start=start,
                  duration=min(inf.duration, 1.3))
        ac.path = path
        ac.asset_id = asset.id
        ac.meta["has_audio"] = True
        p.add_clip(ac, at)
    res = render(p, "audio.mp4")
    check(res.ok, "audio mix failed", res.error)
    ok, why = ok_output(TMP / "audio.mp4", 1.8, expect_audio=True)
    check(ok, f"audio output wrong: {why}")
    # verify the mixed audio is not silent
    r = sh([ff.TOOLCHAIN.ffmpeg, "-v", "error", "-i", str(TMP / "audio.mp4"),
            "-af", "volumedetect", "-f", "null", "-"])
    tail = r.stderr
    import re
    m = re.search(r"mean_volume:\s*(-?[\d.]+)", tail)
    if m:
        check(float(m.group(1)) > -60, f"mixed audio is silent ({m.group(1)} dB)")


@test("sfx library integrity")
def t_sfx():
    paths = sfx.ensure_library()
    check(len(paths) >= 40, f"only {len(paths)} sound effects generated")
    import wave
    import array
    for key in ("fahhh", "thud", "get_out", "bruh", "vine_boom", "mc_anvil"):
        p = paths.get(key)
        check(p is not None and p.exists(), f"sfx '{key}' missing")
        if not p:
            continue
        with wave.open(str(p)) as w:
            n = w.getnframes()
            data = w.readframes(n)
        arr = array.array("h")
        usable = len(data) - len(data) % 2
        arr.frombytes(data[:usable])
        peak = max((abs(v) for v in arr), default=0) / 32768.0
        check(peak > 0.3, f"sfx '{key}' is too quiet (peak={peak:.2f})")
        check(len(arr) / w.getframerate() > 0.08, f"sfx '{key}' is too short")


@test("multi-track overlay / PIP")
def t_pip():
    bg = make_source("bg2_src.mp4", 640, 360, 4.0)
    pip = make_source("pip_src.mp4", 320, 240, 4.0)
    p = base_project()
    a1 = add_asset(p, bg)
    add_video(p, a1, 0.0, 2.5)
    ov = in_track(p, "overlay")
    a2 = add_asset(p, pip)
    c = add_video(p, a2, 0.4, 1.6, track=ov)
    c.transform.scale = 0.42
    c.transform.x = 0.72
    c.transform.y = 0.24
    c.transform.rotation = 8.0
    res = render(p, "pip.mp4")
    check(res.ok, "PIP failed", res.error)
    ok, why = ok_output(TMP / "pip.mp4", 2.3)
    check(ok, f"PIP output wrong: {why}")


@test("single frame scrub render")
def t_frame():
    src = make_source("src13.mp4", 640, 360, 4.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 3.0)
    c.transform.fit = "cover"
    tt = in_track(p, "text")
    txt = Clip(kind="text", track_id=tt.id, start=0.0, duration=3.0)
    txt.text = TextProps(text="PREVIEW", font="Bebas Neue", size=80)
    txt.transform.y = 0.5
    p.add_clip(txt, tt)
    for t in (0.2, 1.0, 2.5):
        out = str(TMP / f"frame_{t}.png")
        got = R.render_frame(p, t, 270, 480, out)
        check(got, f"frame render failed at t={t}")
        check(os.path.exists(out), f"frame png missing at t={t}")
        if os.path.exists(out) and os.path.getsize(out) < 500:
            check(False, f"frame png suspiciously small at t={t}")


@test("project save / load round trip")
def t_project_io():
    from vn_editor.core import project as pio
    src = make_source("src14.mp4", 640, 360, 4.0)
    p = base_project(name="RoundTrip")
    a = add_asset(p, src)
    c = add_video(p, a, 0.5, 2.0, src_in=0.25)
    c.set_keyframe("scale", 0.0, 1.0)
    c.set_keyframe("scale", 1.5, 1.6)
    c.filters.append(FilterInstance("blur", {"sigma": 3.0}))
    c.chroma.enabled = True
    c.mask = MaskProps(shape="ellipse", width=0.5, height=0.5)
    c.transition_out = Transition("fade", 0.5, "out")
    tt = in_track(p, "text")
    txt = Clip(kind="text", track_id=tt.id, start=0.0, duration=1.0)
    txt.text = TextProps(text="HELLO", font="Bungee", size=64)
    p.add_clip(txt, tt)
    p.beats = [0.5, 1.0, 1.5]
    p.markers = [{"t": 1.0, "label": "cut", "color": "#ff0000"}]
    path = str(TMP / "roundtrip.vnp")
    pio.save(p, path)
    check(os.path.exists(path), "project file not written")
    loaded = pio.load(path)
    check(loaded.name == "RoundTrip", "project name lost")
    check(abs(loaded.duration - p.duration) < 0.01,
          f"duration changed {loaded.duration} vs {p.duration}")
    check(len(loaded.all_clips()) == len(p.all_clips()),
          "clip count changed")
    lc = loaded.all_clips()
    vc = [c for c in lc if c.kind == "video"][0]
    check(abs(vc.src_in - 0.25) < 1e-6, "src_in lost")
    check(len(vc.animations.get("scale", [])) == 2, "keyframes lost")
    check(vc.chroma.enabled, "chroma key lost")
    check(vc.mask.shape == "ellipse", "mask lost")
    check(vc.transition_out is not None and vc.transition_out.kind == "fade",
          "transition lost")
    check(len(loaded.beats) == 3, "beat markers lost")
    check(len(loaded.markers) == 1, "markers lost")
    # and the reloaded project must still render
    res = render(loaded, "roundtrip.mp4")
    check(res.ok, "reloaded project failed to render", res.error)


@test("undo / redo history")
def t_undo():
    from vn_editor.core.undo import History, SnapshotCommand
    src = make_source("src15.mp4", 320, 180, 3.0)
    p = base_project()
    a = add_asset(p, src)
    h = History(p)
    c = add_video(p, a, 0.0, 1.0)
    h.push(SnapshotCommand("add clip", {}, p.snapshot()), _already_applied=True)
    before = p.duration
    with h.transaction("extend"):
        c.duration = 2.0
    check(abs(p.duration - 2.0) < 1e-6, "transaction did not apply")
    h.undo()
    check(abs(p.duration - before) < 1e-6, "undo did not restore duration")
    h.redo()
    check(abs(p.duration - 2.0) < 1e-6, "redo did not reapply")
    check(h.is_dirty, "history should be dirty")
    h.mark_saved()
    check(not h.is_dirty, "history should be clean after mark_saved")


@test("frontier: 4K-ish canvas export")
def t_big():
    src = make_source("src16.mp4", 640, 360, 3.0)
    p = base_project(1080, 1080, 24.0)
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 1.0)
    c.transform.fit = "cover"
    res = render(p, "square.mp4", audio=False)
    check(res.ok, "square render failed", res.error)
    ok, why = ok_output(TMP / "square.mp4", 0.9, 1080, 1080, False)
    check(ok, f"square output wrong: {why}")


@test("audio graph: many clips mix without clipping")
def t_many_audio():
    src = make_source("src17.mp4", 480, 270, 3.0)
    p = base_project()
    a = add_asset(p, src)
    c = add_video(p, a, 0.0, 2.0)
    c.audio.volume = 0.4
    at = in_track(p, "audio")
    for i in range(6):
        path = sfx.resolve(["pop", "ding", "whoosh", "thud", "clap",
                            "kick"][i])
        if not path:
            continue
        inf = probe.probe(path, use_cache=False)
        asset = MediaAsset.from_info(inf, path)
        p.assets.append(asset)
        ac = Clip(kind="audio", track_id=at.id, start=i * 0.25,
                  duration=min(inf.duration, 0.6))
        ac.path = path
        ac.asset_id = asset.id
        ac.meta["has_audio"] = True
        ac.audio.volume = 0.6
        p.add_clip(ac, at)
    res = render(p, "many_audio.mp4")
    check(res.ok, "many-clip audio mix failed", res.error)


# --------------------------------------------------------------------------- #
#  runner
# --------------------------------------------------------------------------- #

def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"VN Studio render tests — FFmpeg {ff.TOOLCHAIN.version or 'NOT FOUND'}")
    print(f"temp dir: {TMP}\n")
    started = time.time()
    for name, fn in TESTS:
        if only and only.lower() not in name.lower():
            continue
        t0 = time.time()
        before = len(FAILURES)
        print(f"  • {name} ...", end="", flush=True)
        try:
            fn()
        except Exception:
            FAILURES.append(f"{name} raised:\n" +
                            "".join(traceback.format_exc().splitlines(True)[-8:]))
        dt = time.time() - t0
        status = "ok" if len(FAILURES) == before else "FAIL"
        print(f"\r  [{'PASS' if status == 'ok' else 'FAIL'}] {name} "
              f"({dt:.1f}s)")
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s):\n")
        for f in FAILURES:
            print("  ✗", f)
        print()
    total = len([1 for name, _ in TESTS if not only or only.lower() in name.lower()])
    passed = total - len(FAILURES)
    print(f"{passed}/{total} passed in {time.time() - started:.0f}s")
    if not os.environ.get("VN_KEEP_TEST_TMP"):
        try:
            shutil.rmtree(TMP)
        except OSError:
            pass
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
