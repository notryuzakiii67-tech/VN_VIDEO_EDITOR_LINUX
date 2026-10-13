"""The filtergraph compiler.

Every visual clip becomes a **canvas-sized RGBA layer** with its geometry baked
in; the layers are then composited onto a transparent canvas in z-order.  That
uniform representation is what lets transitions, PiP, masks, chroma key and
text all compose without special cases.

Transform compilation (each mechanism verified against the bundled FFmpeg):

======================  =====================================================
case                    technique
======================  =====================================================
static scale + position ``scale`` to the display size, then ``overlay`` at the
                        computed offset.  ``overlay`` clips automatically, so
                        layers larger than the canvas behave correctly.
animated position only  same, with ``overlay=x='expr(t)'``.
animated scale          ``zoompan`` (``time``/``on``/``zoom`` variables) on a
                        stage that is the canvas scaled by ``q``; a transparent
                        ``pad`` provides the zoom-out room.  ``zoompan``
                        outputs a canvas-sized frame directly.
rotation                ``rotate`` with a ``t`` expression and expanded output.
opacity                 ``colorchannelmixer=aa`` (static) or a generated
                        ``sendcmd`` script driving the same option (animated).
======================  =====================================================

Speed uses ``setpts`` + ``atempo``/``rubberband`` (pitch preserved) and speed
*curves* compile into real time slices.

Transitions follow standard NLE semantics: the outgoing clip supplies handle
frames past its out-point (read from source when available, otherwise its last
frame is held) and dissolves into the incoming clip's opening frames, so
applying a transition never moves anything on the timeline.
"""
from __future__ import annotations

import math
import copy
import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import effects as fx_mod
from . import ffmpeg as _ff
from . import transitions as trans_mod
from . import ass as ass_mod
from .ass import AssBuilder, add_caption_events, add_text_clip, text_style_for
from .expr import color_to_ff, kf_expr, num
from .model import (Clip, Project, Track, Transform, clip_z_order,
                    eval_keyframes, keyframe_range)
from .speedcurve import clamp_speed, video_speed_graph, source_span

MIN_CLIP = 0.02
CANVAS_FMT = "yuva420p"
MAX_STAGE_FACTOR = 2          # zoompan stage = canvas * q, q <= this


# --------------------------------------------------------------------------- #
#  Options
# --------------------------------------------------------------------------- #

@dataclass
class RenderOptions:
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    preview: bool = False
    preview_scale: float = 1.0
    codec: str = "libx264"
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    audio: bool = True
    video: bool = True
    threads: int = 0
    tune: str = ""
    pixel_format: str = "yuv420p"
    faststart: bool = True
    audio_only: bool = False      # build only the audio half of the graph
    extra_args: List[str] = field(default_factory=list)

    def out_size(self) -> Tuple[int, int]:
        w, h = int(self.width), int(self.height)
        if self.preview and self.preview_scale < 0.999:
            s = max(0.15, self.preview_scale)
            w = max(16, int(w * s) // 2 * 2)
            h = max(16, int(h * s) // 2 * 2)
        return w, h


def options_from_settings(project: Project, settings=None,
                          preview: bool = False) -> RenderOptions:
    from .project import SETTINGS
    st = settings or SETTINGS
    opts = RenderOptions(width=project.settings.width,
                         height=project.settings.height,
                         fps=project.settings.fps or 30.0)
    if preview:
        opts.preview = True
        opts.preview_scale = float(st.get("preview_scale", 0.5))
        opts.fps = min(float(project.settings.fps or 30.0),
                       float(st.get("preview_fps", 30)))
        opts.codec = "libx264"
        opts.crf = 26
        opts.preset = "ultrafast"
        opts.tune = "zerolatency"
        opts.audio = True
    else:
        opts.crf = int(st.get("export_crf", 20))
        opts.preset = str(st.get("export_preset_speed", "medium"))
        opts.audio_bitrate = str(st.get("export_audio_bitrate", "192k"))
        codec = str(st.get("export_codec", "libx264"))
        opts.codec = codec if _ff.TOOLCHAIN.has_encoder(codec) else "libx264"
    return opts


# --------------------------------------------------------------------------- #
#  Utilities
# --------------------------------------------------------------------------- #

class Labels:
    """Deterministic unique filter-pad labels."""

    def __init__(self, prefix: str = "v") -> None:
        self._n = 0
        self._prefix = prefix

    def __call__(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n}"

    next = __call__


def _fmt(v: float) -> str:
    return num(v, 5)


def _even(v: float, minimum: int = 2) -> int:
    n = int(round(v))
    n = max(minimum, n)
    return n if n % 2 == 0 else n + 1


def write_png(path: str, pixels) -> bool:
    """Minimal RGBA PNG writer (no Pillow dependency)."""
    try:
        import numpy as np
    except Exception:
        return False
    arr = np.asarray(pixels, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 4:
        return False
    h, w, _ = arr.shape
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    try:
        Path(path).write_bytes(png)
        return True
    except OSError:
        return False


#: Blend modes offered for overlay layers, mapped to FFmpeg's ``blend`` filter.
#: The renderer, the inspector and the project format all read this table.
BLEND_MODES: List[str] = ["normal", "screen", "multiply", "overlay", "soft_light",
                          "hard_light", "add", "subtract", "difference",
                          "lighten", "darken", "colour_dodge", "colour_burn"]

BLEND_FF: Dict[str, str] = {
    "normal": "normal", "screen": "screen", "multiply": "multiply",
    "overlay": "overlay", "soft_light": "softlight", "hard_light": "hardlight",
    "add": "addition", "subtract": "subtract", "difference": "difference",
    "lighten": "lighten", "darken": "darken", "colour_dodge": "dodge",
    "colour_burn": "burn",
}


#: Mask shapes understood by :func:`mask_png`; "none" means no mask at all.
MASK_SHAPES: List[str] = ["none", "rect", "ellipse", "radial", "linear", "heart",
                          "star", "split"]


def mask_png(path: str, shape: str, width: int, height: int, cx: float,
             cy: float, mw: float, mh: float, rotation: float = 0.0,
             feather: float = 0.02, invert: bool = False) -> bool:
    """Render a soft-edged mask to an RGBA PNG (alpha = coverage)."""
    try:
        import numpy as np
    except Exception:
        return False
    w, h = max(8, int(width)), max(8, int(height))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    x = xs - cx * w
    y = ys - cy * h
    rx = max(1.0, mw * w / 2.0)
    ry = max(1.0, mh * h / 2.0)
    if abs(rotation) > 1e-6:
        a = math.radians(-rotation)
        xr = x * math.cos(a) - y * math.sin(a)
        yr = x * math.sin(a) + y * math.cos(a)
    else:
        xr, yr = x, y
    if shape == "ellipse":
        cov = np.sqrt((xr / rx) ** 2 + (yr / ry) ** 2)
    elif shape == "radial":
        ang = np.arctan2(yr, xr)
        cov = np.sqrt((xr / rx) ** 2 + (yr / ry) ** 2) * (0.6 + 0.4 * np.cos(ang))
    elif shape == "linear":
        cov = np.clip(0.5 + xr / (rx * 2.0), 0.0, 1.0) * 4.0
    elif shape == "heart":
        nx, ny = xr / rx, yr / ry
        f = (nx ** 2 + ny ** 2 - 1) ** 3 - nx ** 2 * ny ** 3
        cov = np.where(f <= 0, 0.0, 3.0)
    elif shape == "star":
        ang = np.arctan2(yr, xr)
        rad = np.sqrt((xr / rx) ** 2 + (yr / ry) ** 2)
        limit = 0.72 + 0.28 * np.cos(5.0 * ang)
        cov = np.where(rad <= limit, 0.0, 3.0)
    elif shape == "split":
        cov = np.where(xr <= 0, 0.0, 3.0)
    else:  # rect
        cov = np.maximum(np.abs(xr) / rx, np.abs(yr) / ry)
    soft = max(1.0, float(feather) * max(w, h))
    alpha = np.clip((1.0 - cov) * (soft * 0.5) + 0.5, 0.0, 1.0)
    if invert:
        alpha = 1.0 - alpha
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0:3] = 255
    rgba[..., 3] = (alpha * 255.0).astype(np.uint8)
    return write_png(path, rgba)


def sendcmd_script(path: str, keys, default: float, duration: float,
                   fps: float, target: str = "colorchannelmixer",
                   option: str = "aa", lo: float = 0.0,
                   hi: float = 1.0) -> bool:
    """Generate a sendcmd script that animates a numeric filter option."""
    if not keys:
        return False
    step = 1.0 / max(1.0, fps)
    lines: List[str] = []
    t = 0.0
    while t <= duration + 1e-6 and len(lines) < 6000:
        v = eval_keyframes(keys, t, default)
        v = max(lo, min(hi, v))
        lines.append(f"{t:.4f} {target} {option} {v:.5f};")
        t += step
    try:
        Path(path).write_text("\n".join(lines) + "\n", "utf-8")
        return True
    except OSError:
        return False


def _is_greenish(color: str) -> bool:
    from .expr import color_to_rgb
    r, g, b = color_to_rgb(color)
    return g >= max(r, b)


class _MaskEval:
    """Mask geometry with optional keyframing, evaluated at a given time."""

    def __init__(self, clip: Clip, m, t: float = 0.0) -> None:
        self.clip = clip
        self.m = m
        self.t = t

    def _v(self, prop: str, base: float) -> float:
        keys = self.clip.animations.get(prop)
        if keys:
            return eval_keyframes(keys, self.t, base)
        return base

    def x(self) -> float:
        return self._v("x", self.m.x) if self.clip.animations.get("x") else self.m.x

    def y(self) -> float:
        return self._v("y", self.m.y) if self.clip.animations.get("y") else self.m.y

    def w(self) -> float:
        return max(0.02, min(3.0, self._v("scale", 1.0) * self.m.width))

    def h(self) -> float:
        return max(0.02, min(3.0, self._v("scale", 1.0) * self.m.height))

    def rot(self) -> float:
        return self._v("rotation", self.m.rotation)


# --------------------------------------------------------------------------- #
#  Graph builder
# --------------------------------------------------------------------------- #

class GraphBuilder:
    def __init__(self, project: Project, opts: RenderOptions) -> None:
        self.project = project
        self.opts = opts
        self.ow, self.oh = opts.out_size()
        self.fps = max(1.0, float(opts.fps))
        self.labels = Labels("g")
        self.inputs: List[List[str]] = []
        self.fragments: List[str] = []
        self.temp_files: List[str] = []
        self._input_cache: Dict[Tuple[str, int], int] = {}
        self.ass_builder = AssBuilder(self.ow, self.oh)
        self._text_index = 0
        self._tdir: Optional[str] = None
        self._own_tdir: bool = False

    # -- infrastructure ---------------------------------------------------- #
    def tempdir(self) -> str:
        """A private scratch directory for this builder.

        Each graph gets its own directory: the preview player and the scrub
        worker both render at the same time, and shared filenames would let one
        wipe the other's .ass / mask files mid-render.
        """
        if self._tdir is None:
            from .project import cache_dir
            import tempfile
            base = cache_dir() / "render"
            base.mkdir(parents=True, exist_ok=True)
            d = tempfile.mkdtemp(prefix="g", dir=str(base))
            self._tdir = d
            self._own_tdir = True
        return self._tdir

    def add_input(self, args: Sequence[str]) -> int:
        self.inputs.append([str(a) for a in args])
        return len(self.inputs) - 1

    def clip_input(self, clip: Clip) -> int:
        path = clip.path
        ss = 0.0 if clip.reverse else max(0.0, clip.src_in)
        key = (path, int(ss * 1000))
        if key in self._input_cache:
            return self._input_cache[key]
        if clip.kind == "image":
            args: List[str] = ["-loop", "1", "-i", path]
        elif clip.kind == "sticker":
            args = ["-loop", "1", "-i", path]
        else:
            args = []
            if ss > 0.002:
                args += ["-ss", f"{ss:.5f}"]
            args += ["-i", path]
        idx = self.add_input(args)
        self._input_cache[key] = idx
        return idx

    def frag(self, text: str) -> None:
        if text:
            self.fragments.append(text)

    # -- source geometry --------------------------------------------------- #
    def src_size(self, clip: Clip) -> Tuple[int, int]:
        w = clip.meta.get("src_w") or clip.meta.get("width") or 0
        h = clip.meta.get("src_h") or clip.meta.get("height") or 0
        if w and h:
            return int(w), int(h)
        asset = self.project.asset(clip.asset_id)
        if asset and asset.width and asset.height:
            r = asset.rotation
            if r % 180 == 90:
                return asset.height, asset.width
            return asset.width, asset.height
        from .probe import probe
        try:
            info = probe(clip.path)
            r = info.rotation
            if r % 180 == 90:
                return info.height or self.ow, info.width or self.oh
            return info.width or self.ow, info.height or self.oh
        except Exception:
            return self.ow, self.oh

    def display_size(self, clip: Clip, transform: Transform
                     ) -> Tuple[float, float, str]:
        """Return (pre_scale_w, pre_scale_h, mode) for the content box."""
        pw, ph = float(self.ow), float(self.oh)
        sw, sh = self.src_size(clip)
        t = transform
        cl, cr = min(0.95, max(0.0, t.crop_l)), min(0.95, max(0.0, t.crop_r))
        ct, cb = min(0.95, max(0.0, t.crop_t)), min(0.95, max(0.0, t.crop_b))
        cw = max(1.0, sw * (1.0 - cl - cr))
        ch = max(1.0, sh * (1.0 - ct - cb))
        aspect = cw / ch
        canvas_aspect = pw / ph
        if t.fit == "stretch":
            return pw, ph, "stretch"
        if t.fit == "cover":
            return pw, ph, "cover"
        if aspect >= canvas_aspect:
            fw = pw
            fh = fw / aspect
        else:
            fh = ph
            fw = fh * aspect
        return fw, fh, "fit"

    # -- transform compilation --------------------------------------------- #
    def build_transform(self, clip: Clip, src_label: str,
                        mask: bool = True) -> Tuple[Optional[str], int]:
        """Append the transform fragments; return (output label, stage factor).

        Returns ``None`` as the label when the caller must composite the layer
        itself (never happens now — kept for clarity).
        """
        t = clip.transform
        scale_keys = clip.animations.get("scale")
        x_keys = clip.animations.get("x")
        y_keys = clip.animations.get("y")
        rot_keys = clip.animations.get("rotation")
        op_keys = clip.animations.get("opacity")

        dw, dh, mode = self.display_size(clip, t)

        chain: List[str] = []
        if mode == "cover":
            chain.append(f"scale={self.ow}:{self.oh}:"
                         f"force_original_aspect_ratio=increase:flags=bicubic")
            chain.append(f"crop={self.ow}:{self.oh}")
        elif mode == "stretch":
            chain.append(f"scale={self.ow}:{self.oh}:flags=bicubic")
        else:
            chain.append(f"scale={_even(dw)}:{_even(dh)}:flags=bicubic:"
                         f"force_original_aspect_ratio=disable")
        chain.append("setsar=1")

        if t.flip_h:
            chain.append("hflip")
        if t.flip_v:
            chain.append("vflip")

        # Masking happens here: the content is now exactly the size the mask
        # PNG is generated at, which ``alphamerge`` requires.
        if mask and clip.mask.shape != "none":
            content = self.labels()
            self.frag(f"[{src_label}]" + ",".join(chain) + f"[{content}]")
            content = self.apply_mask(clip, content,
                                      self._current_w(dw, dh, mode),
                                      self._current_h(dw, dh, mode))
            chain = []
            src_label = content
            chain.append("null")

        if rot_keys:
            expr = kf_expr(rot_keys, t.rotation, "t")
            chain.append(f"rotate=a='({expr})*PI/180':ow='rotw(iw)':oh='roth(ih)'"
                         f":c=none:fillcolor=0x00000000")
        elif abs(t.rotation) > 1e-4:
            chain.append(f"rotate=a={_fmt(t.rotation)}*PI/180:"
                         f"ow='rotw(iw)':oh='roth(ih)':c=none:fillcolor=0x00000000")

        chain.append("format=rgba")

        # -- opacity ------------------------------------------------------- #
        cmd_path = ""
        if op_keys and clip.duration > 0.05:
            p = os.path.join(self.tempdir(), f"op_{clip.id}.txt")
            if sendcmd_script(p, op_keys, t.opacity, clip.duration, self.fps):
                cmd_path = p
                self.temp_files.append(p)
        if cmd_path:
            chain.append(f"sendcmd=f={_ff.escape_filter_path(cmd_path)}")
            chain.append("colorchannelmixer=aa=1")
        elif abs(t.opacity - 1.0) > 1e-4:
            chain.append(f"colorchannelmixer=aa="
                         f"{_fmt(max(0.0, min(1.0, t.opacity)))}")

        # -- animated scale: zoompan --------------------------------------- #
        if scale_keys:
            s_lo, _ = keyframe_range(scale_keys, t.scale)
            q = 1
            if s_lo < 1.0:
                q = int(math.ceil(1.0 / max(0.05, s_lo)))
            q = max(1, min(MAX_STAGE_FACTOR, q))
            sw = self.ow * q
            sh = self.oh * q
            padx = (sw - self._current_w(dw, dh, mode)) // 2
            pady = (sh - self._current_h(dw, dh, mode)) // 2
            if padx > 0 or pady > 0:
                chain.append(f"pad={sw}:{sh}:{max(0, padx)}:{max(0, pady)}:"
                             f"color=black@0")
            z = f"({num(q)}*({kf_expr(scale_keys, t.scale, 'time')}))"
            xsrc = kf_expr(x_keys, t.x, "time") if x_keys else _fmt(t.x)
            ysrc = kf_expr(y_keys, t.y, "time") if y_keys else _fmt(t.y)
            zc = f"max(0.05,{z})"
            xexpr = (f"max(0,min({sw}-{sw}/({zc}),"
                     f"{sw}*(0.5-({xsrc})/({zc}))))")
            yexpr = (f"max(0,min({sh}-{sh}/({zc}),"
                     f"{sh}*(0.5-({ysrc})/({zc}))))")
            chain.append(f"zoompan=z='{zc}':x='{xexpr}':y='{yexpr}':d=1:"
                         f"s={self.ow}x{self.oh}:fps={_fmt(self.fps)}")
            chain.append("format=rgba")
            out = self.labels()
            self.frag(f"[{src_label}]" + ",".join(chain) + f"[{out}]")
            return out, q

        # -- static / position-animated: content then overlay -------------- #
        content = self.labels()
        chain.append("format=rgba")
        self.frag(f"[{src_label}]" + ",".join(chain) + f"[{content}]")

        cw, chh = self._current_w(dw, dh, mode), self._current_h(dw, dh, mode)
        canvas = self.labels()
        self.frag(f"color=c=black@0.0:s={self.ow}x{self.oh}:r={_fmt(self.fps)}:"
                  f"d={_fmt(clip.duration + 0.6)},format=rgba[{canvas}]")
        x_expr = (f"({kf_expr(x_keys, t.x, 't')})*{self.ow}-w/2" if x_keys
                  else _fmt(t.x * self.ow - cw / 2.0))
        y_expr = (f"({kf_expr(y_keys, t.y, 't')})*{self.oh}-h/2" if y_keys
                  else _fmt(t.y * self.oh - chh / 2.0))
        out = self.labels()
        self.frag(f"[{canvas}][{content}]overlay=x='{x_expr}':y='{y_expr}':"
                  f"format=auto:eof_action=pass[{out}]")
        return out, 1

    def _current_w(self, dw: float, dh: float, mode: str) -> int:
        return self.ow if mode in ("cover", "stretch") else _even(dw)

    def _current_h(self, dw: float, dh: float, mode: str) -> int:
        return self.oh if mode in ("cover", "stretch") else _even(dh)

    # -- masks -------------------------------------------------------------- #
    def apply_mask(self, clip: Clip, prev: str,
                   mask_w: Optional[int] = None,
                   mask_h: Optional[int] = None) -> str:
        m = clip.mask
        if m.shape == "none":
            return prev
        mw = int(mask_w or self.ow)
        mh = int(mask_h or self.oh)
        anim_props = [p for p in ("x", "y", "scale", "rotation")
                      if clip.animations.get(p)]
        times = {0.0, clip.duration}
        for p in anim_props:
            for k in clip.animations.get(p, []):
                times.add(max(0.0, min(clip.duration, k.t)))
        tl = sorted(times)
        seg_labels: List[str] = []
        spans: List[Tuple[float, float]] = []
        if not anim_props or len(tl) <= 2:
            spans = [(0.0, clip.duration)]
        else:
            spans = [(a, b) for a, b in zip(tl, tl[1:]) if b - a > 1e-3]
        for (a, b) in spans:
            png = os.path.join(self.tempdir(),
                              f"mask_{clip.id}_{int(a * 1000)}.png")
            ev = _MaskEval(clip, m, a)
            if not mask_png(png, m.shape, mw, mh, ev.x(), ev.y(),
                            ev.w(), ev.h(), ev.rot(), m.feather, m.invert):
                return prev
            self.temp_files.append(png)
            mi = self.add_input(["-loop", "1", "-i", png])
            midx = self.labels()
            dur = max(0.03, b - a)
            # alphamerge takes its alpha from the *luma* of the second input, so
            # the coverage plane of the mask PNG is extracted first.
            self.frag(f"[{mi}:v]format=rgba,alphaextract,scale={mw}:{mh},"
                      f"fps={_fmt(self.fps)},loop=loop=-1:size=1:start=0,"
                      f"setpts=N/{_fmt(self.fps)}/TB,"
                      f"trim=duration={_fmt(dur)},format=gray[{midx}]")
            seg_labels.append(midx)
        if not seg_labels:
            return prev
        mask_all = seg_labels[0]
        if len(seg_labels) > 1:
            mask_all = self.labels()
            parts = "".join(f"[{s}]" for s in seg_labels)
            self.frag(f"{parts}concat=n={len(seg_labels)}:v=1:a=0,"
                      f"format=rgba[{mask_all}]")
        out = self.labels()
        self.frag(f"[{prev}][{mask_all}]alphamerge[{out}]")
        return out

    # -- one visual clip ---------------------------------------------------- #
    def composite(self, acc: str, layer: str, blend: str = "normal",
                  enable: str = "") -> str:
        """Put *layer* (already placed on a canvas-sized RGBA image) over *acc*.

        "normal" is a plain overlay.  Any other mode blends the two pictures
        with FFmpeg's ``blend`` filter and then restores the background where
        the layer is transparent, because ``blend`` itself ignores alpha:
        ``maskedmerge`` needs its inputs in a non-alpha format, so the blend
        stage runs in yuv420p and the result is composited back in rgba.
        """
        out = self.labels()
        if not blend or blend == "normal" or blend not in BLEND_FF:
            self.frag(f"[{acc}][{layer}]overlay=0:0:eof_action=pass:"
                      f"format=auto{enable}[{out}]")
            return out
        acc_b, acc_f = self.labels(), self.labels()
        layer_b, layer_m = self.labels(), self.labels()
        mask, acc_y, layer_y = self.labels(), self.labels(), self.labels()
        acc_y1, acc_y2, blended, merged, merged_rgba = (self.labels()
                                                        for _ in range(5))
        self.frag(f"[{acc}]format=rgba,split=2[{acc_b}][{acc_f}]")
        self.frag(f"[{layer}]format=rgba,split=2[{layer_b}][{layer_m}]")
        self.frag(f"[{layer_m}]format=rgba,alphaextract[{mask}]")
        self.frag(f"[{acc_b}]format=yuv420p[{acc_y}]")
        self.frag(f"[{layer_b}]format=yuv420p[{layer_y}]")
        self.frag(f"[{acc_y}]split=2[{acc_y1}][{acc_y2}]")
        self.frag(f"[{acc_y1}][{layer_y}]blend=all_mode={BLEND_FF[blend]}:"
                  f"all_opacity=1[{blended}]")
        self.frag(f"[{acc_y2}][{blended}][{mask}]maskedmerge[{merged}]")
        self.frag(f"[{merged}]format=rgba[{merged_rgba}]")
        self.frag(f"[{acc_f}][{merged_rgba}]overlay=0:0:eof_action=pass:"
                  f"format=auto{enable}[{out}]")
        return out

    def build_layer(self, clip: Clip, index: int, extend: float = 0.0) -> str:
        lab = self.labels()
        # Stabilisation runs on the raw (source-resolution) frames and before
        # anything converts to rgba — vidstabtransform cannot take a four-plane
        # input and segfaults on one, so its position here matters.
        chain = [f"fps={_fmt(self.fps)}"]
        stab = fx_mod.stabilize_filter(clip)
        if stab:
            chain.append(stab)
        chain.append("format=rgba")

        t = clip.transform
        if max(t.crop_l, t.crop_r, t.crop_t, t.crop_b) > 1e-4:
            cl, cr = min(0.95, max(0.0, t.crop_l)), min(0.95, max(0.0, t.crop_r))
            ct, cb = min(0.95, max(0.0, t.crop_t)), min(0.95, max(0.0, t.crop_b))
            chain.append(
                "crop=w='max(2,trunc(iw*(1-%s-%s)/2)*2)':"
                "h='max(2,trunc(ih*(1-%s-%s)/2)*2)':"
                "x='trunc(iw*%s)':y='trunc(ih*%s)'"
                % (_fmt(cl), _fmt(cr), _fmt(ct), _fmt(cb), _fmt(cl), _fmt(ct)))

        if clip.chroma.enabled:
            chain.append(
                f"chromakey=color={color_to_ff(clip.chroma.color)}:"
                f"similarity={_fmt(max(0.01, min(1.0, clip.chroma.similarity)))}:"
                f"blend={_fmt(max(0.0, min(1.0, clip.chroma.blend)))}")
            if clip.chroma.spill > 0.01:
                chain.append(
                    f"despill=type="
                    f"{'green' if _is_greenish(clip.chroma.color) else 'blue'}:"
                    f"mix={_fmt(max(0.0, min(1.0, clip.chroma.spill)))}")

        chain.extend(fx_mod.build_effect_chain(clip, self.labels))
        self.frag(f"[{index}:v]" + ",".join(chain) + f"[{lab}]")
        cur = lab

        for f in clip.filters:
            if not f.enabled or f.kind not in fx_mod.MULTIBRANCH:
                continue
            nxt = self.labels()
            self.frag(fx_mod.build_multibranch(f, cur, nxt, self.labels,
                                               clip.duration))
            cur = nxt

        spd = self.labels()
        self.frag(video_speed_graph(clip, cur, spd, self.labels, self.fps))
        cur = spd

        if extend > 0.001 and not clip.freeze:
            hl = self.labels()
            self.frag(f"[{cur}]tpad=stop_mode=clone:stop_duration={_fmt(extend)},"
                      f"trim=duration={_fmt(clip.duration + extend)}[{hl}]")
            cur = hl
        elif clip.freeze and extend > 0.001:
            hl = self.labels()
            self.frag(f"[{cur}]tpad=stop_mode=clone:stop_duration={_fmt(extend)},"
                      f"trim=duration={_fmt(clip.duration + extend)}[{hl}]")
            cur = hl

        out, _ = self.build_transform(clip, cur)
        return out


# --------------------------------------------------------------------------- #
#  Layer collection / runs
# --------------------------------------------------------------------------- #

def _visible_tracks(project: Project) -> List[Track]:
    solo = any(t.solo for t in project.tracks
               if t.kind in ("video", "overlay", "text", "sticker"))
    out = []
    for t in clip_z_order(project):
        if t.hidden or t.muted:
            continue
        if solo and not t.solo:
            continue
        out.append(t)
    return out


def _clip_has_video(clip: Clip) -> bool:
    return clip.kind in ("video", "image", "sticker", "shape")


def collect_layers(project: Project) -> Dict[str, List[Clip]]:
    out: Dict[str, List[Clip]] = {}
    for tr in _visible_tracks(project):
        if tr.kind == "text":
            continue
        clips = []
        for c in sorted(tr.clips, key=lambda x: x.start):
            if not c.enabled or not _clip_has_video(c):
                continue
            if c.kind in ("video", "image", "sticker") and not c.path:
                continue
            clips.append(c)
        if clips:
            out[tr.id] = clips
    return out


def text_clips(project: Project) -> List[Clip]:
    out: List[Clip] = []
    for tr in _visible_tracks(project):
        if tr.kind != "text":
            continue
        for c in sorted(tr.clips, key=lambda x: x.start):
            if c.enabled and c.text:
                out.append(c)
    return out


def _adjacent(a: Clip, b: Clip) -> bool:
    return abs(a.end - b.start) < 0.02


def _transition_between(a: Clip, b: Optional[Clip]):
    if b is None:
        return None
    if a.transition_out is not None and a.transition_out.kind != "none":
        return a.transition_out
    if b.transition_in is not None and b.transition_in.kind != "none":
        return b.transition_in
    return None


def _handle_after(project: Project, clip: Clip) -> float:
    """Seconds of media available after the clip's out-point."""
    if clip.freeze or clip.kind in ("image", "sticker", "shape", "text"):
        return 30.0
    asset = project.asset(clip.asset_id)
    dur = asset.duration if asset else clip.meta.get("src_duration", 0.0)
    if not dur:
        from .probe import probe
        try:
            dur = probe(clip.path).duration
        except Exception:
            dur = 0.0
    if not dur:
        return 0.0
    speed = clamp_speed(clip.speed)
    used = source_span(clip)
    remaining = max(0.0, dur - (clip.src_in + used))
    return remaining / max(1e-6, speed)


# --------------------------------------------------------------------------- #
#  Audio
# --------------------------------------------------------------------------- #

def _build_audio(builder: GraphBuilder, project: Project,
                 duration: float) -> str:
    from .audio import AudioInput, build_mix
    solo = any(t.solo for t in project.tracks if t.kind == "audio")
    inputs: List[AudioInput] = []
    for track in project.tracks:
        if track.kind == "audio" and (track.muted or (solo and not track.solo)):
            continue
        for clip in sorted(track.clips, key=lambda c: c.start):
            if clip.kind in ("text", "sticker", "shape", "adjustment"):
                continue
            if not clip.enabled or not clip.path or clip.audio.muted:
                continue
            has_audio = (clip.kind == "audio"
                         or bool(clip.meta.get("has_audio",
                                               clip.kind == "video")))
            if not has_audio:
                continue
            idx = builder.clip_input(clip)
            inputs.append(AudioInput(clip, idx, max(0.0, clip.src_in),
                                     getattr(track, "volume", 1.0)))
    if not inputs:
        return ""
    graph = build_mix(inputs, duration + 0.5, builder.labels)
    if not graph.strip().endswith("]"):
        return ""
    label = graph.rsplit("[", 1)[1].rstrip("]")
    builder.frag(graph)
    return label


# --------------------------------------------------------------------------- #
#  Full graph
# --------------------------------------------------------------------------- #

def build_full_graph(project: Project, opts: RenderOptions,
                     start: float = 0.0, duration: float = 0.0,
                     captions: Optional[Sequence[Dict[str, Any]]] = None,
                     ) -> Tuple[List[str], GraphBuilder, float]:
    builder = GraphBuilder(project, opts)
    total = project.duration
    if duration <= 0:
        duration = max(0.05, total - start)
    duration = max(0.05, duration)
    ow, oh, fps = builder.ow, builder.oh, builder.fps

    bg = project.settings.background or "#000000"
    acc = ""
    if not opts.audio_only:
        acc = builder.labels()
        builder.frag(f"color=c={color_to_ff(bg)}:s={ow}x{oh}:r={_fmt(fps)}:"
                     f"d={_fmt(duration + 0.6)},format={CANVAS_FMT}[{acc}]")

    layers_by_track = collect_layers(project) if not opts.audio_only else {}
    for tr in ([] if opts.audio_only else _visible_tracks(project)):
        clips = layers_by_track.get(tr.id)
        if not clips:
            continue
        i = 0
        while i < len(clips):
            j = i
            while j + 1 < len(clips) and _adjacent(clips[j], clips[j + 1]):
                j += 1
            run = clips[i:j + 1]
            i = j + 1
            acc = _build_run(builder, project, run, acc)

    # vector stickers share the same libass pass (drawn first, under text).
    # Events are authored on the timeline, so a graph that begins mid-timeline
    # (the preview pipe) shifts them back by ``start``.
    for c in ([] if opts.audio_only else ass_mod.sticker_clips(project)):
        ass_mod.add_sticker_clip(builder.ass_builder, c, builder._text_index,
                                 timeline_offset=-start)
        builder._text_index += 1

    # text + captions through libass (vector-sharp at any scale)
    for c in ([] if opts.audio_only else text_clips(project)):
        style = text_style_for(builder.ass_builder, c, builder._text_index)
        builder._text_index += 1
        add_text_clip(builder.ass_builder, c, style, timeline_offset=-start)
    if captions and not opts.audio_only:
        from .fonts import libass_scale
        style = builder.ass_builder.add_style(
            "Caption", font="Bebas Neue",
            size=ow * 0.052 * libass_scale("Bebas Neue"),
            primary="&H00FFFFFF", outline_col="&H00000000",
            back_col="&HA0000000", bold=False, outline=2.5, shadow=0.0,
            align=2, margin_v=int(oh * 0.06))
        add_caption_events(builder.ass_builder, captions, "Caption",
                           offset=-start)
    if builder.ass_builder.events:
        ass_path = os.path.join(builder.tempdir(), "overlay.ass")
        try:
            Path(ass_path).write_text(builder.ass_builder.build(), "utf-8")
            builder.temp_files.append(ass_path)
            out = builder.labels()
            builder.frag(f"[{acc}]ass={_ff.escape_filter_path(ass_path)}[{out}]")
            acc = out
        except OSError:
            pass

    # final format conversion
    if not opts.audio_only:
        final = builder.labels()
        builder.frag(f"[{acc}]format={opts.pixel_format},setsar=1[{final}]")
        acc = final

    audio_label = _build_audio(builder, project, duration) if opts.audio else ""
    builder.final_label = acc
    builder.audio_label = audio_label

    argv: List[str] = ["-y", "-hide_banner"]
    if opts.threads:
        argv += ["-threads", str(opts.threads)]
    for args in builder.inputs:
        argv += args
    argv += ["-filter_complex", ";".join(builder.fragments)]
    argv += ["-map", f"[{acc}]"] if acc else []
    if audio_label:
        argv += ["-map", f"[{audio_label}]"]
    argv += ["-t", f"{duration:.4f}"]
    if opts.video:
        argv += ["-c:v", opts.codec, "-pix_fmt", opts.pixel_format]
        if opts.codec in ("libx264", "libx265"):
            argv += ["-crf", str(opts.crf), "-preset", opts.preset]
            if opts.codec == "libx264":
                argv += ["-x264-params",
                         f"keyint={int(max(1, round(fps)) * 2)}:"
                         f"min-keyint={int(max(1, round(fps)))}:scenecut=40"]
        elif opts.codec.endswith("_vaapi"):
            argv += ["-b:v", f"{max(1, 24 - opts.crf) * 1000}k"]
        elif opts.codec in ("mpeg4", "libxvid"):
            argv += ["-q:v", str(max(1, min(10, opts.crf // 4)))]
        if opts.tune:
            argv += ["-tune", opts.tune]
    if audio_label:
        argv += ["-c:a", "aac", "-b:a", opts.audio_bitrate, "-ar", "48000",
                 "-ac", "2"]
    else:
        argv += ["-an"]
    if opts.faststart and opts.codec:
        argv += ["-movflags", "+faststart"]
    argv += opts.extra_args
    return argv, builder, duration


def _split_tail(argv: List[str]) -> List[str]:
    """Drop the encoder/muxer half of a ``build_full_graph`` argv."""
    for i, a in enumerate(argv):
        if a in ("-c:v", "-an", "-c:a", "-movflags"):
            return argv[:i]
    return argv


def _pipe_prefix(argv: List[str]) -> Tuple[List[str], str]:
    """Split a full-graph argv into (inputs + filter_complex, duration text)."""
    head = _split_tail(argv)
    dur = "0"
    out: List[str] = []
    i = 0
    while i < len(head):
        a = head[i]
        if a == "-t" and i + 1 < len(head):
            dur = head[i + 1]
            i += 2
            continue
        if a == "-map" and i + 1 < len(head):
            i += 2
            continue
        out.append(a)
        i += 1
    return out, dur


def build_pipe_command(project: Project, opts: RenderOptions,
                       start: float = 0.0, duration: float = 0.0,
                       captions: Optional[Sequence[Dict[str, Any]]] = None,
                       ) -> Tuple[List[str], GraphBuilder, float]:
    """Raw RGB24 frames on stdout — used by the preview player.

    The audio half of the graph, if any, is sunk to the null muxer so FFmpeg
    does not complain about an unconnected filtergraph output.
    """
    opts = copy.copy(opts)
    opts.pixel_format = "rgb24"
    argv, builder, dur = build_full_graph(project, opts, start, duration,
                                          captions)
    prefix, duration_txt = _pipe_prefix(argv)
    w, h = opts.out_size()
    out = prefix + ["-map", f"[{builder.final_label}]", "-an",
                    "-t", duration_txt, "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{w}x{h}", "pipe:1"]
    if builder.audio_label:
        out += ["-map", f"[{builder.audio_label}]", "-vn",
                "-f", "null", "-"]
    return out, builder, dur


def build_audio_pipe_command(project: Project, opts: RenderOptions,
                             start: float = 0.0, duration: float = 0.0,
                             sample_rate: int = 48000) -> List[str]:
    """Raw s16le stereo PCM on stdout — used by the preview player.

    Only the audio half of the graph is compiled, so playback does not pay for
    video filters twice.
    """
    opts = copy.copy(opts)
    opts.audio = True
    opts.video = False
    opts.audio_only = True
    argv, builder, dur = build_full_graph(project, opts, start, duration)
    prefix, duration_txt = _pipe_prefix(argv)
    if not builder.audio_label:
        return []
    return prefix + ["-map", f"[{builder.audio_label}]",
                     "-t", duration_txt,
                     "-f", "s16le", "-acodec", "pcm_s16le",
                     "-ar", str(sample_rate), "-ac", "2", "pipe:1"]


def _build_run(builder: GraphBuilder, project: Project, run: List[Clip],
               acc: str) -> str:
    layers: List[str] = []
    for i, clip in enumerate(run):
        nxt = run[i + 1] if i + 1 < len(run) else None
        tr = _transition_between(clip, nxt)
        extend = 0.0
        if tr is not None:
            want = max(0.05, float(tr.duration))
            avail = _handle_after(project, clip)
            extend = want                # tpad covers any shortfall
        idx = builder.clip_input(clip)
        layers.append(builder.build_layer(clip, idx, extend))

    cur = layers[0]
    joined = run[0].duration
    for i in range(1, len(run)):
        nxt = run[i]
        tr = _transition_between(run[i - 1], nxt)
        d = 0.0
        if tr is not None:
            d = max(0.04, min(float(tr.duration), run[i - 1].duration * 0.9,
                              nxt.duration * 0.9))
        out = builder.labels()
        if d <= 0.001:
            builder.frag(f"[{cur}][{layers[i]}]concat=n=2:v=1:a=0[{out}]")
        else:
            frag = trans_mod.build(tr.kind, joined, d, cur, layers[i], out,
                                   builder.labels, builder.ow, builder.oh,
                                   builder.fps)
            if frag is None:
                builder.frag(f"[{cur}][{layers[i]}]concat=n=2:v=1:a=0[{out}]")
            else:
                builder.frag(frag)
        cur = out
        joined += nxt.duration

    start = max(0.0, run[0].start)
    placed = builder.labels()
    builder.frag(f"[{cur}]setpts=PTS-STARTPTS+{_fmt(start)}/TB[{placed}]")
    enable = (f":enable='between(t,{_fmt(start)},"
              f"{_fmt(start + joined + 0.05)})'")
    return builder.composite(acc, placed, run[0].blend, enable)


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #

@dataclass
class RenderResult:
    ok: bool
    output: str = ""
    error: str = ""
    duration: float = 0.0
    frames: int = 0
    elapsed: float = 0.0


def render(project: Project, output: str, opts: Optional[RenderOptions] = None,
           start: float = 0.0, duration: float = 0.0,
           progress: Optional[Callable[[float, str], None]] = None,
           captions: Optional[Sequence[Dict[str, Any]]] = None,
           cancel: Optional[Callable[[], bool]] = None) -> RenderResult:
    import time
    t0 = time.time()
    opts = opts or options_from_settings(project)
    if opts.codec.startswith("libx26") and not _ff.TOOLCHAIN.has_encoder(opts.codec):
        opts.codec = "libx264"
    if _ff.TOOLCHAIN.ffmpeg is None:
        return RenderResult(False, output,
                            "FFmpeg was not found. Install it with "
                            "`sudo apt install ffmpeg` or run ./install.sh",
                            0.0, 0, 0.0)
    argv, builder, dur = build_full_graph(project, opts, start, duration,
                                          captions)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    if opts.preview:
        argv = argv[:]  # fragmented output for progressive playback
    full = ["-progress", "pipe:2", "-nostats"] + argv + [str(output)]
    proc = _ff.popen_ffmpeg(full, stdout=_ff.subprocess.DEVNULL,
                            stderr=_ff.subprocess.PIPE)
    frames = 0
    err_tail: List[str] = []
    try:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("frame="):
                try:
                    frames = int(line.split("=", 1)[1])
                except ValueError:
                    pass
            elif line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                try:
                    secs = float(line.split("=", 1)[1]) / 1_000_000.0
                    if progress and dur > 0:
                        progress(min(0.999, secs / dur),
                                 f"{secs:.1f} / {dur:.1f} s")
                except ValueError:
                    pass
            elif line.startswith(("progress=", "bitrate=", "total_size=",
                                  "out_time=", "dup_frames=", "drop_frames=",
                                  "speed=")):
                if progress and line == "progress=end":
                    progress(1.0, "finishing")
            else:
                err_tail.append(line)
                if len(err_tail) > 240:
                    del err_tail[:120]
            if cancel is not None and cancel():
                _ff.terminate(proc)
                _cleanup(builder)
                return RenderResult(False, output, "cancelled", dur, frames,
                                    time.time() - t0)
        proc.wait(timeout=40)
    except Exception as exc:
        _ff.terminate(proc)
        _cleanup(builder)
        return RenderResult(False, output,
                            f"{exc}\n" + "\n".join(err_tail[-14:]), dur,
                            frames, time.time() - t0)
    rc = proc.returncode
    ok = rc == 0 and os.path.exists(output) and os.path.getsize(output) > 512
    _cleanup(builder)
    if progress:
        progress(1.0 if ok else 0.0, "done" if ok else "failed")
    if ok:
        return RenderResult(True, output, "", dur, frames, time.time() - t0)
    important = [l for l in err_tail
                 if re.search(r"error|invalid|failed|no such|unable|"
                              r"cannot|unknown|not found|denied",
                              l, re.IGNORECASE)]
    err = "\n".join(important[-18:] or err_tail[-18:])
    return RenderResult(False, output, err, dur, frames, time.time() - t0)


def _cleanup(builder: GraphBuilder) -> None:
    """Remove this builder's scratch files (never another render's)."""
    import shutil
    for f in builder.temp_files:
        try:
            if os.path.exists(f) and f.endswith(".txt"):
                os.unlink(f)
        except OSError:
            pass
    if getattr(builder, "_own_tdir", False) and builder._tdir:
        try:
            shutil.rmtree(builder._tdir, ignore_errors=True)
        except Exception:
            pass
        builder._tdir = None
        builder._own_tdir = False


# --------------------------------------------------------------------------- #
#  Single-frame preview (scrubbing)
# --------------------------------------------------------------------------- #

def render_frame(project: Project, t: float, width: int, height: int,
                 out_path: str,
                 captions: Optional[Sequence[Dict[str, Any]]] = None) -> bool:
    """Render one composited frame.  Keyframes are evaluated in Python so the
    graph stays tiny — this is the interactive scrub path."""
    opts = RenderOptions(width=width, height=height,
                         fps=project.settings.fps or 30.0)
    builder = GraphBuilder(project, opts)
    bg = project.settings.background or "#000000"
    acc = builder.labels()
    # The base is a solid canvas whose PTS starts at the requested time, so the
    # single frame we keep is the one libass evaluates for that moment.  Text,
    # captions and stickers are authored on the timeline clock, so nothing has
    # to be re-timed per frame.
    builder.frag(f"color=c={color_to_ff(bg)}:s={builder.ow}x{builder.oh}:"
                 f"r={_fmt(builder.fps)}:d={_fmt(max(0.2, t + 0.1))},"
                 f"format={CANVAS_FMT},setpts=PTS+{_fmt(t)}/TB[{acc}]")

    for tr in _visible_tracks(project):
        if tr.kind == "text":
            continue
        clip = tr.clip_at(t)
        if clip is None or not clip.enabled or not _clip_has_video(clip):
            continue
        if clip.kind in ("video", "image", "sticker") and not clip.path:
            continue
        rel = clip.local_time(t)
        src_t = clip.src_time(t)
        stabilized = (clip.kind == "video" and bool(clip.path)
                      and not clip.freeze
                      and fx_mod.stabilize_instance(clip) is not None)
        read_from = max(0.0, clip.src_in) if stabilized else None
        idx = builder.add_input(_frame_input_args(clip, src_t, read_from))
        layer = _frame_layer(
            builder, project, clip, idx, rel,
            head_frame=clip.kind == "video" and not stabilized,
            stab_at=(src_t - read_from) if stabilized else None,
            at_time=t)
        if layer is None:
            continue
        acc = builder.composite(acc, layer, clip.blend)

    stickers = [c for c in ass_mod.sticker_clips(project) if c.contains(t)]
    texts = [c for c in text_clips(project) if c.contains(t)]
    caps = [c for c in (captions or [])
            if float(c.get("start", 0.0)) <= t <= float(c.get("end", 0.0))]
    if texts or caps or stickers:
        ab = AssBuilder(builder.ow, builder.oh)
        for i, c in enumerate(stickers):
            ass_mod.add_sticker_clip(ab, c, i)
        for i, c in enumerate(texts):
            style = text_style_for(ab, c, len(stickers) + i)
            add_text_clip(ab, c, style)
        if caps:
            from .fonts import libass_scale
            st = ab.add_style("Caption", font="Bebas Neue",
                              size=builder.oh * 0.052 *
                              libass_scale("Bebas Neue"),
                              primary="&H00FFFFFF",
                              outline_col="&H00000000", outline=2.5, align=2,
                              margin_v=int(builder.oh * 0.06))
            add_caption_events(ab, caps, st, offset=0.0)
        ass_path = os.path.join(builder.tempdir(), "scrub.ass")
        try:
            Path(ass_path).write_text(ab.build(), "utf-8")
            out = builder.labels()
            builder.frag(f"[{acc}]ass={_ff.escape_filter_path(ass_path)}[{out}]")
            acc = out
        except OSError:
            pass

    argv = ["-y", "-hide_banner"]
    for args in builder.inputs:
        argv += args
    argv += ["-filter_complex", ";".join(builder.fragments),
             "-map", f"[{acc}]", "-frames:v", "1", "-update", "1", "-q:v", "3",
             str(out_path)]
    r = _ff.ffmpeg(*argv, timeout=120)
    _cleanup(builder)
    return r.ok and os.path.exists(out_path)


def _frame_input_args(clip: Clip, src_t: float,
                      read_from: Optional[float] = None) -> List[str]:
    """Input arguments for one layer of a scrubbed frame.

    Stabilisation reads its per-frame transforms in order, so a clip that is
    stabilised is decoded from the start of the clip instead of seeking
    straight to the requested frame — the transform file and the decoded
    frames then stay in step, and the overlay keeps the last decoded frame.
    """
    if clip.kind in ("image", "sticker"):
        return ["-loop", "1", "-i", clip.path]
    args: List[str] = []
    if read_from is not None:
        # No ``-t`` here: cutting the input short makes FFmpeg tear the
        # vidstabtransform filter down while frames are still being pulled,
        # which crashes it.  The single output frame stops the pipeline anyway.
        if read_from > 0.002:
            args += ["-ss", f"{read_from:.5f}"]
    elif src_t > 0.002:
        args += ["-ss", f"{src_t:.5f}"]
    args += ["-i", clip.path]
    return args


def _frame_layer(builder: GraphBuilder, project: Project, clip: Clip,
                 idx: int, rel: float, head_frame: bool = False,
                 stab_at: Optional[float] = None,
                 at_time: float = 0.0) -> Optional[str]:
    """One layer of a scrubbed frame.

    ``head_frame`` keeps only the first decoded frame: the caller has seeked
    straight to the frame it wants, and letting the rest of the clip through
    would make ``overlay`` pick a much later frame.  ``stab_at`` does the same
    after stabilisation, which has to see every frame in order to consume its
    transform file — there the wanted frame is selected by timestamp.

    The finished layer is stamped with the timeline time it belongs to
    (``at_time``).  Without that stamp its single frame sits at PTS 0, i.e.
    before the canvas, and ``overlay``'s ``eof_action=pass`` would drop the
    whole layer and show the bare background.
    """
    t = clip.transform

    def prop(name: str) -> float:
        keys = clip.animations.get(name)
        base = float(getattr(t, name, 0.0))
        return eval_keyframes(keys, rel, base) if keys else base

    scale = max(0.02, prop("scale"))
    x = prop("x")
    y = prop("y")
    rotation = prop("rotation")
    opacity = max(0.0, min(1.0, prop("opacity")))
    if opacity < 0.004:
        return None

    # vidstabtransform must see the raw frames, never an rgba stream.
    chain: List[str] = []
    if head_frame:
        chain.append("trim=end_frame=1,setpts=PTS-STARTPTS")
    chain.append(f"fps={_fmt(builder.fps)}")
    stab = fx_mod.stabilize_filter(clip)
    if stab:
        chain.append(stab)
    if stab_at is not None and stab_at > 0.002:
        # Keep exactly the frame the timeline is sitting on (the transforms
        # before it have already been consumed in order).
        chain.append(f"trim=start={_fmt(stab_at)}:"
                     f"end={_fmt(stab_at + 1.0 / max(1.0, builder.fps))}")
        chain.append("setpts=PTS-STARTPTS")
    chain.append("format=rgba")
    if max(t.crop_l, t.crop_r, t.crop_t, t.crop_b) > 1e-4:
        cl, cr = min(0.95, max(0.0, t.crop_l)), min(0.95, max(0.0, t.crop_r))
        ct, cb = min(0.95, max(0.0, t.crop_t)), min(0.95, max(0.0, t.crop_b))
        chain.append("crop=w='max(2,trunc(iw*(1-%s-%s)/2)*2)':"
                     "h='max(2,trunc(ih*(1-%s-%s)/2)*2)':"
                     "x='trunc(iw*%s)':y='trunc(ih*%s)'"
                     % (_fmt(cl), _fmt(cr), _fmt(ct), _fmt(cb),
                        _fmt(cl), _fmt(ct)))
    if clip.chroma.enabled:
        chain.append(f"chromakey=color={color_to_ff(clip.chroma.color)}:"
                     f"similarity={_fmt(max(0.01, min(1.0, clip.chroma.similarity)))}:"
                     f"blend={_fmt(max(0.0, min(1.0, clip.chroma.blend)))}")
        if clip.chroma.spill > 0.01:
            chain.append(f"despill=type="
                         f"{'green' if _is_greenish(clip.chroma.color) else 'blue'}")
    chain.extend(fx_mod.build_effect_chain(clip, builder.labels))

    dw, dh, mode = builder.display_size(clip, t)
    if mode == "cover":
        chain.append(f"scale={builder.ow}:{builder.oh}:"
                     f"force_original_aspect_ratio=increase:flags=bicubic")
        chain.append(f"crop={builder.ow}:{builder.oh}")
    elif mode == "stretch":
        chain.append(f"scale={builder.ow}:{builder.oh}:flags=bicubic")
    else:
        chain.append(f"scale={_even(dw * scale)}:{_even(dh * scale)}:"
                     f"flags=bicubic")
    chain.append("setsar=1")
    if t.flip_h:
        chain.append("hflip")
    if t.flip_v:
        chain.append("vflip")
    if abs(rotation) > 1e-4:
        chain.append(f"rotate=a={_fmt(rotation)}*PI/180:ow='rotw(iw)':"
                     f"oh='roth(ih)':c=none:fillcolor=0x00000000")
    chain.append("format=rgba")
    if opacity < 0.999:
        chain.append(f"colorchannelmixer=aa={_fmt(opacity)}")

    lab = builder.labels()
    builder.frag(f"[{idx}:v]" + ",".join(chain) + f"[{lab}]")
    cur = lab
    for f in clip.filters:
        if not f.enabled or f.kind not in fx_mod.MULTIBRANCH:
            continue
        nxt = builder.labels()
        builder.frag(fx_mod.build_multibranch(f, cur, nxt, builder.labels,
                                              clip.duration))
        cur = nxt
    cur = builder.apply_mask(clip, cur, builder._current_w(dw, dh, mode),
                             builder._current_h(dw, dh, mode))

    canvas = builder.labels()
    builder.frag(f"color=c=black@0.0:s={builder.ow}x{builder.oh}:"
                 f"r={_fmt(builder.fps)}:d=0.2,format=rgba[{canvas}]")
    out = builder.labels()
    builder.frag(f"[{canvas}][{cur}]overlay=x='{_fmt(x * builder.ow)}-w/2':"
                 f"y='{_fmt(y * builder.oh)}-h/2':format=auto[{out}]")
    if at_time > 0.002:
        stamped = builder.labels()
        builder.frag(f"[{out}]setpts=PTS+{_fmt(at_time)}/TB[{stamped}]")
        return stamped
    return out


def probe_render_support() -> Dict[str, bool]:
    return {
        "xfade": _ff.filter_available("xfade"),
        "zoompan": _ff.filter_available("zoompan"),
        "ass": _ff.filter_available("ass"),
        "chromakey": _ff.filter_available("chromakey"),
        "lut3d": _ff.filter_available("lut3d"),
        "libx264": _ff.TOOLCHAIN.has_encoder("libx264"),
        "libx265": _ff.TOOLCHAIN.has_encoder("libx265"),
        "rubberband": _ff.filter_available("rubberband"),
        "minterpolate": _ff.filter_available("minterpolate"),
        "loudnorm": _ff.filter_available("loudnorm"),
    }
