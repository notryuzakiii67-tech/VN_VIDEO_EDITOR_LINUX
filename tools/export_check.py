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

    print(f"EXPORT OK in {time.time() - t0:.1f}s "
          f"({result.frames} frames, {result.frames / max(0.001, result.elapsed):.1f} fps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
