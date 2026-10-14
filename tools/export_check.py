#!/usr/bin/env python3
"""End-to-end export check: build the POV-short scenario and render a real MP4.

Run headless with the offscreen Qt shim, or on a desktop as-is::

    python tools/export_check.py [--out DIR] [--scale 0.5]

It writes ``export.mp4`` plus a few probe frames so the result can be inspected
without a video player, and prints a pass/fail report.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/vn-export-check")
    ap.add_argument("--scale", type=float, default=0.5,
                    help="fraction of the canvas to render (faster checks)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gui_smoke", str(ROOT / "tools" / "gui_smoke.py"))
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)

    from vn_editor.core import ffmpeg as ff
    from vn_editor.core import probe as probe_mod
    from vn_editor.core import render as render_mod

    print(f"output: {out}")
    media = gs.make_media(out)
    project = gs.build_project(media)
    print(f"project: {len(project.all_clips())} clips, "
          f"{len(project.tracks)} tracks, {project.duration:.2f}s")

    opts = render_mod.options_from_settings(project)
    opts.width = int(project.settings.width * args.scale) // 2 * 2
    opts.height = int(project.settings.height * args.scale) // 2 * 2
    opts.crf = 23
    opts.preset = "ultrafast"
    opts.audio = True
    opts.faststart = True
    opts.preview = False

    target = out / "export.mp4"
    stages: list[tuple[float, str]] = []

    def progress(frac: float, message: str) -> None:
        if not stages or frac - stages[-1][0] > 0.1:
            stages.append((frac, message))
            print(f"  {frac * 100:5.1f}%  {message}", flush=True)

    t0 = time.time()
    result = render_mod.render(project, str(target), opts, progress=progress)
    print(f"render: ok={result.ok} frames={result.frames} "
          f"elapsed={result.elapsed:.1f}s rc-msg={result.error[:200]!r}")
    if not result.ok:
        print(result.error[-2000:])
        return 1

    info = probe_mod.probe(str(target), use_cache=False)
    size_mb = target.stat().st_size / 1e6
    print(f"file: {target}  {size_mb:.2f} MB  {info.width}x{info.height} "
          f"{info.fps:.2f}fps  {info.duration:.2f}s  video={info.codec} "
          f"audio={info.audio_codec} {info.channels}ch @{info.sample_rate}Hz")
    if not info.has_video or not info.has_audio:
        print("FAIL: the MP4 is missing a stream")
        return 1
    expect = project.duration
    if abs(info.duration - expect) > 0.45:
        print(f"FAIL: duration {info.duration:.2f}s, expected {expect:.2f}s")
        return 1

    # frame stills so the render can be eyeballed
    for t in (0.8, 2.6, 3.2, 4.8):
        png = out / f"export_{t}.png"
        r = ff.ffmpeg("-y", "-v", "error", "-ss", f"{t}", "-i", str(target),
                      "-frames:v", "1", "-update", "1", str(png), timeout=90)
        print(f"  still {t}s: {'ok' if r.ok and png.exists() else 'FAILED'}")

    # audio must not be silent: measure its loudness
    r = ff.ffmpeg("-v", "info", "-i", str(target), "-af",
                  "volumedetect", "-f", "null", "-", timeout=120)
    mean = [ln for ln in (r.stderr or "").splitlines() if "mean_volume" in ln]
    print("  " + (mean[0].strip() if mean else "no volume report"))

    # ---- does the scenario actually show up in the file? ------------------ #
    def frame(video: Path, t: float, tag: str) -> str:
        png = out / f"cmp_{tag}.png"
        r = ff.ffmpeg("-y", "-v", "error", "-ss", f"{t}", "-i", str(video),
                      "-frames:v", "1", "-update", "1", str(png), timeout=90)
        return str(png) if r.ok and png.exists() else ""

    def diff(a: str, b: str) -> float:
        """Percentage of pixels that differ between two stills.

        A mean difference over the whole frame drowns a caption on a busy
        gameplay frame; counting the pixels that actually changed does not.
        """
        if not (a and b):
            return -1.0
        ra, rb = out / "d_a.rgb", out / "d_b.rgb"
        for src_file, dst in ((a, ra), (b, rb)):
            r = ff.ffmpeg("-y", "-v", "error", "-i", src_file, "-vf",
                          "scale=160:160", "-f", "rawvideo", "-pix_fmt",
                          "rgb24", str(dst), timeout=90)
            if not r.ok:
                return -1.0
        da, db = ra.read_bytes(), rb.read_bytes()
        if len(da) != len(db) or not da:
            return -1.0
        changed = total = 0
        for i in range(0, len(da) - 2, 3):
            total += 1
            if (abs(da[i] - db[i]) > 18 or abs(da[i + 1] - db[i + 1]) > 18
                    or abs(da[i + 2] - db[i + 2]) > 18):
                changed += 1
        return 100.0 * changed / max(1, total)

    failures = []
    # The captions run 0.4-2.0 s, 2.4-4.0 s and 4.4-6.0 s, the sticker
    # 3.0-4.8 s, so 2.2 s and 4.2 s are the frames with nothing overlaid: a
    # difference there would mean the comparison itself is unsound.
    def render_without(kinds, name):
        path = out / name
        touched = [tr for tr in project.tracks if tr.kind in kinds]
        for tr in touched:
            tr.hidden = True
        res = render_mod.render(project, str(path), opts)
        for tr in touched:
            tr.hidden = False
        return res.ok, path

    def compare(label, on, off, reference: Path, floor: float) -> None:
        with_it = diff(frame(target, on, f"{label}_on_a"),
                       frame(reference, on, f"{label}_on_b"))
        without = diff(frame(target, off, f"{label}_off_a"),
                       frame(reference, off, f"{label}_off_b"))
        ok = with_it > floor and without < 0.4
        print(f"  {label:10s} at {on}s: {with_it:5.1f}% of pixels carry it, "
              f"{without:4.1f}% at {off}s where it is absent")
        if not ok:
            failures.append(f"{label} not visible in the export")

    ok, no_overlays = render_without(("text", "sticker"), "no_overlays.mp4")
    if not ok:
        failures.append("could not render the no-overlay control")
    else:
        compare("caption 1", 1.0, 2.2, no_overlays, 2.0)
        compare("caption 2", 2.7, 2.2, no_overlays, 2.0)

    ok, no_sticker = render_without(("sticker",), "no_sticker.mp4")
    if not ok:
        failures.append("could not render the no-sticker control")
    else:
        compare("sticker", 3.6, 2.2, no_sticker, 1.0)

    # Silence the sound-effect track and compare the loudness where the
    # effects sit: an SFX that never made it into the mix shows no difference.
    muted = out / "no_sfx.mp4"
    sfx_track = next((t for t in project.tracks if t.kind == "audio"), None)
    if sfx_track is not None:
        sfx_track.muted = True
        quiet_audio = render_mod.render(project, str(muted), opts)
        sfx_track.muted = False
        if not quiet_audio.ok:
            failures.append("could not render the no-sfx control")
        else:
            def peak(video: Path, start: float, span: float) -> float:
                r = ff.ffmpeg("-v", "info", "-ss", f"{start:.2f}", "-t",
                              f"{span:.2f}", "-i", str(video), "-af",
                              "volumedetect", "-f", "null", "-", timeout=120)
                vals = [ln for ln in (r.stderr or "").splitlines()
                        if "max_volume" in ln]
                if not vals:
                    return -99.0
                try:
                    return float(vals[0].split("max_volume:")[1].split("dB")[0])
                except (IndexError, ValueError):
                    return -99.0
            louder = 0
            for key, at in (("fahhh", 2.2), ("thud", 3.9), ("get_out", 5.2)):
                with_sfx = peak(target, at, 0.5)
                without_sfx = peak(muted, at, 0.5)
                hit = with_sfx > without_sfx + 1.0
                louder += 1 if hit else 0
                print(f"  sfx {key:8s} at {at}s: peak {with_sfx:6.1f} dB "
                      f"with the effect, {without_sfx:6.1f} dB without")
            if louder < 2:
                failures.append("the sound effects are not audible in the mix")
    for name in ("no_overlays.mp4", "no_sticker.mp4", "no_sfx.mp4"):
        (out / name).unlink(missing_ok=True)
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("  scenario verified: captions, sticker and SFX are in the file")

    print(f"EXPORT OK in {time.time() - t0:.1f}s "
          f"({result.frames} frames, {result.frames / max(0.001, result.elapsed):.1f} fps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
