"""Media inspection.

Three independent strategies, in order of preference:

1. ``ffprobe`` when the user has it (most accurate).
2. A pure-Python container parser (MP4/MOV, Matroska/WebM, WAV, MP3, FLAC, OGG).
   This means the editor still identifies media correctly with a static ffmpeg
   build that has no ``ffprobe`` side-car, which is exactly the situation on a
   zero-install machine.
3. Parsing the ``Input #0`` block of ``ffmpeg -i`` stderr.

The public entry point is :func:`probe`.
"""
from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ffmpeg as _ff

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv",
             ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".3g2", ".ogv", ".vob",
             ".mts", ".m2v", ".y4m", ".gif", ".apng"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
             ".wma", ".aiff", ".aif", ".ac3", ".dts", ".mka", ".amr", ".caf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff",
             ".heic", ".avif", ".jfif"}


@dataclass
class MediaInfo:
    path: str
    kind: str = "video"                  # video | audio | image
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    rotation: int = 0
    codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    has_audio: bool = False
    has_video: bool = False
    bitrate: int = 0
    size_bytes: int = 0
    frame_count: int = 0
    pix_fmt: str = ""
    source: str = ""                     # which strategy produced this
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def display_width(self) -> int:
        return self.height if self.rotation % 180 == 90 else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation % 180 == 90 else self.height

    @property
    def aspect(self) -> float:
        if self.display_height:
            return self.display_width / self.display_height
        return 16 / 9

    @property
    def display_duration(self) -> float:
        # A still image has no inherent duration; the timeline decides.
        return self.duration if self.kind != "image" else max(self.duration, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("extra", None)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MediaInfo":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def classify(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return "video"


# --------------------------------------------------------------------------- #
#  1. ffprobe
# --------------------------------------------------------------------------- #

def _probe_ffprobe(path: str) -> Optional[MediaInfo]:
    if _ff.TOOLCHAIN.ffprobe is None:
        return None
    r = _ff.ffprobe("-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(path), timeout=30)
    if not r.ok or not r.stdout:
        return None
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return None
    return _from_ffprobe_json(path, data)


def _from_ffprobe_json(path: str, data: Dict[str, Any]) -> MediaInfo:
    info = MediaInfo(path=str(path), source="ffprobe",
                     size_bytes=os.path.getsize(path) if os.path.exists(path) else 0)
    fmt = data.get("format", {})
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.bitrate = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        info.bitrate = 0

    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and not info.has_video:
            if st.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            info.codec = st.get("codec_name", "") or ""
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
            info.pix_fmt = st.get("pix_fmt", "") or ""
            info.fps = _parse_fraction(st.get("avg_frame_rate")) or \
                _parse_fraction(st.get("r_frame_rate")) or 0.0
            try:
                info.frame_count = int(st.get("nb_frames") or 0)
            except (TypeError, ValueError):
                info.frame_count = 0
            if not info.duration:
                d = st.get("duration")
                if d:
                    try:
                        info.duration = float(d)
                    except (TypeError, ValueError):
                        pass
            for sd in st.get("side_data_list", []) or []:
                rot = sd.get("rotation")
                if rot is not None:
                    try:
                        info.rotation = int(round(float(rot))) % 360
                    except (TypeError, ValueError):
                        pass
        elif kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = st.get("codec_name", "") or ""
            try:
                info.sample_rate = int(st.get("sample_rate") or 0)
            except (TypeError, ValueError):
                info.sample_rate = 0
            info.channels = int(st.get("channels") or 0)

    ext_kind = classify(str(path))
    if ext_kind == "image":
        info.kind = "image"
    elif not info.has_video:
        info.kind = "audio"
    else:
        info.kind = "video"
    if info.kind == "image":
        info.duration = 0.0
        if not info.fps:
            info.fps = 30.0
    if info.kind == "audio" and not info.channels:
        info.channels = 2
    return info


def _parse_fraction(v: Any) -> float:
    if not v or v in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in str(v):
            return float(Fraction(str(v)))
        return float(v)
    except (ValueError, ZeroDivisionError):
        return 0.0


# --------------------------------------------------------------------------- #
#  2. Native container parsers
# --------------------------------------------------------------------------- #

def _read_box_header(f) -> Optional[tuple]:
    hdr = f.read(8)
    if len(hdr) < 8:
        return None
    size, btype = struct.unpack(">I4s", hdr)
    header_len = 8
    if size == 1:
        ext = f.read(8)
        if len(ext) < 8:
            return None
        size = struct.unpack(">Q", ext)[0]
        header_len = 16
    elif size == 0:
        size = 1 << 62
    if size < header_len:
        return None
    return size, btype.decode("latin-1"), header_len


def _iter_boxes(f, end: int):
    while f.tell() < end - 8:
        start = f.tell()
        hdr = _read_box_header(f)
        if hdr is None:
            return
        size, btype, hlen = hdr
        pstart = start + hlen
        pend = min(start + size, end)
        yield btype, pstart, pend
        if pend <= start:
            return
        f.seek(pend)


def _parse_mp4(path: str) -> Optional[MediaInfo]:
    try:
        size_on_disk = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[4:8] not in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"styp"):
                return None
            f.seek(0)
            info = _parse_mp4_boxes(f, size_on_disk, path)
            return info
    except (OSError, struct.error):
        return None


def _parse_mp4_boxes(f, total: int, path: str) -> Optional[MediaInfo]:
    info = MediaInfo(path=str(path), source="native-mp4", size_bytes=total)
    mvhd_timescale = 0
    mvhd_duration = 0
    found_moov = False
    video_props: List[Dict[str, Any]] = []
    audio_props: List[Dict[str, Any]] = []

    for btype, ps, pe in _iter_boxes(f, total):
        if btype == "moov":
            found_moov = True
            f.seek(ps)
            for bt2, p2, e2 in _iter_boxes(f, pe):
                if bt2 == "mvhd":
                    f.seek(p2)
                    ver = f.read(1)[0]
                    f.read(3)
                    if ver == 1:
                        f.read(16)
                        ts = struct.unpack(">I", f.read(4))[0]
                        dur = struct.unpack(">Q", f.read(8))[0]
                    else:
                        f.read(8)
                        ts = struct.unpack(">I", f.read(4))[0]
                        dur = struct.unpack(">I", f.read(4))[0]
                    mvhd_timescale, mvhd_duration = ts, dur
                elif bt2 == "trak":
                    f.seek(p2)
                    trak = _parse_trak(f, p2, e2)
                    if trak:
                        (video_props if trak["has_video"] else audio_props).append(trak)
        elif btype == "moof":
            break

    if not found_moov:
        return None

    v = video_props[0] if video_props else None
    a = audio_props[0] if audio_props else None
    if v:
        info.has_video = True
        info.codec = v.get("codec", "")
        info.width = v.get("width", 0)
        info.height = v.get("height", 0)
        info.rotation = v.get("rotation", 0)
        info.fps = v.get("fps", 0.0) or 0.0
        info.frame_count = v.get("frames", 0)
        if mvhd_timescale and mvhd_duration:
            info.duration = mvhd_duration / mvhd_timescale
        elif v.get("duration") and v.get("timescale"):
            info.duration = v["duration"] / v["timescale"]
    if a:
        info.has_audio = True
        info.audio_codec = a.get("audio_codec") or a.get("codec", "")
        info.sample_rate = a.get("sample_rate", 0)
        info.channels = a.get("channels", 0)
        if not info.duration and a.get("duration") and a.get("timescale"):
            info.duration = a["duration"] / a["timescale"]

    ext_kind = classify(path)
    if ext_kind == "image":
        info.kind = "image"
    elif not info.has_video:
        info.kind = "audio"
    else:
        info.kind = "video"
    if info.kind == "image":
        info.duration = 0.0
        info.fps = info.fps or 30.0
    if info.fps <= 0 and info.kind == "video":
        info.fps = 30.0
    if total and info.duration:
        info.bitrate = int(total * 8 / info.duration)
    return info


def _parse_trak(f, start: int, end: int) -> Optional[Dict[str, Any]]:
    out: Dict[str, Any] = {"has_video": False, "has_audio": False, "rotation": 0}
    for btype, ps, pe in _iter_boxes(f, end):
        if btype == "tkhd":
            f.seek(ps)
            ver = f.read(1)[0]
            f.read(3)
            if ver == 1:
                f.read(16)
                f.read(4)                       # track id
                f.read(4)
                f.read(8)                       # duration
            else:
                f.read(8)
                f.read(4)
                f.read(4)
                f.read(4)
            f.read(8)                           # reserved
            f.read(2)                           # layer
            f.read(2)                           # alternate group
            f.read(2)                           # volume
            f.read(2)                           # reserved
            m = struct.unpack(">9i", f.read(36))
            w = struct.unpack(">I", f.read(4))[0] / 65536.0
            h = struct.unpack(">I", f.read(4))[0] / 65536.0
            out["tkhd_w"], out["tkhd_h"] = w, h
            # rotation from the 2x2 part of the matrix (a,b,c,d)
            a, b, c, d = m[0], m[1], m[3], m[4]
            rot = 0
            if a == 0 and b > 0 and c < 0 and d == 0:
                rot = 90
            elif a == 0 and b < 0 and c > 0 and d == 0:
                rot = 270
            elif a < 0 and d < 0:
                rot = 180
            out["rotation"] = rot
        elif btype == "mdia":
            f.seek(ps)
            for bt2, p2, e2 in _iter_boxes(f, pe):
                if bt2 == "mdhd":
                    f.seek(p2)
                    ver = f.read(1)[0]
                    f.read(3)
                    if ver == 1:
                        f.read(16)
                        ts = struct.unpack(">I", f.read(4))[0]
                        dur = struct.unpack(">Q", f.read(8))[0]
                    else:
                        f.read(8)
                        ts = struct.unpack(">I", f.read(4))[0]
                        dur = struct.unpack(">I", f.read(4))[0]
                    out["timescale"], out["duration"] = ts, dur
                elif bt2 == "hdlr":
                    f.seek(p2 + 8)
                    handler = f.read(4).decode("latin-1", "replace")
                    if handler == "vide":
                        out["has_video"] = True
                    elif handler == "soun":
                        out["has_audio"] = True
                elif bt2 == "minf":
                    f.seek(p2)
                    for bt3, p3, e3 in _iter_boxes(f, e2):
                        if bt3 == "stbl":
                            f.seek(p3)
                            for bt4, p4, e4 in _iter_boxes(f, e3):
                                if bt4 == "stsd":
                                    _parse_stsd(f, p4, e4, out)
                                elif bt4 == "stts":
                                    f.seek(p4 + 4)
                                    n = struct.unpack(">I", f.read(4))[0]
                                    total = 0
                                    frames = 0
                                    for _ in range(min(n, 100000)):
                                        cnt, delta = struct.unpack(">II", f.read(8))
                                        total += cnt * delta
                                        frames += cnt
                                    ts = out.get("timescale") or 0
                                    if ts and frames:
                                        dur_s = total / ts
                                        if dur_s > 0:
                                            out["fps"] = frames / dur_s
                                    out["frames"] = frames
    if out.get("has_video") and not out.get("width"):
        out["width"] = int(round(out.get("tkhd_w", 0)))
        out["height"] = int(round(out.get("tkhd_h", 0)))
    return out if (out.get("has_video") or out.get("has_audio")) else None


def _parse_stsd(f, start: int, end: int, out: Dict[str, Any]) -> None:
    """``start`` = payload of the ``stsd`` box (version/flags then entry count)."""
    AUDIO_FOURCC = {b"mp4a", b"ac-3", b"ec-3", b"alac", b"Opus", b"fLaC",
                    b"lpcm", b"sowt", b"twos", b"ulaw", b"alaw", b".mp3",
                    b"samr", b"sawb", b"dtsc", b"dtsh", b"dtsl"}
    try:
        f.seek(start + 4)
        entry_count = struct.unpack(">I", f.read(4))[0]
        if entry_count <= 0:
            return
        struct.unpack(">I", f.read(4))[0]        # entry size
        fmt = f.read(4)
        codec = fmt.decode("latin-1", "replace").strip("\x00 ")
        is_audio = fmt in AUDIO_FOURCC or (out.get("has_audio")
                                           and not out.get("has_video"))
        if out.get("has_video") and not is_audio:
            out["codec"] = codec
        elif is_audio:
            out["audio_codec"] = codec
        else:
            out["codec"] = codec
        f.read(6)                                 # reserved
        f.read(2)                                 # data reference index
        if is_audio:
            f.read(2)                             # version
            f.read(2)                             # revision
            f.read(4)                             # vendor
            ch = struct.unpack(">H", f.read(2))[0]
            f.read(2)                             # sample size
            f.read(2)                             # compression id
            f.read(2)                             # packet size
            sr = struct.unpack(">I", f.read(4))[0] / 65536.0
            out["channels"] = ch
            out["sample_rate"] = int(round(sr))
        else:
            f.read(2)                             # pre_defined
            f.read(2)                             # reserved
            f.read(12)                            # pre_defined[3]
            w, h = struct.unpack(">HH", f.read(4))
            if w and h:
                out["width"], out["height"] = w, h
    except (OSError, struct.error):
        pass


# -- Matroska / WebM -------------------------------------------------------- #

_EBML_IDS = {
    0x1A45DFA3: "ebml", 0x18538067: "segment", 0x1549A966: "info",
    0x2AD7B1: "timecode_scale", 0x4489: "duration", 0x1654AE6B: "tracks",
    0xAE: "track_entry", 0x83: "track_type", 0x86: "codec_id",
    0x23E383: "default_duration", 0xE0: "video", 0xB0: "pixel_width",
    0xBA: "pixel_height", 0x54B0: "display_width", 0x54BA: "display_height",
    0xE1: "audio", 0xB5: "sampling_rate", 0x9F: "channels",
    0x22B59C: "language", 0x536E: "name", 0xE2: "codec_private",
    0x9C: "flag_lacing", 0x88: "flag_default",
}


def _ebml_read_vint(f, keep_marker: bool) -> Optional[tuple]:
    b = f.read(1)
    if not b:
        return None
    first = b[0]
    if first == 0:
        return None
    length = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 8:
            return None
    rest = f.read(length - 1)
    if len(rest) < length - 1:
        return None
    value = first if keep_marker else (first & (mask - 1))
    for byte in rest:
        value = (value << 8) | byte
    return value, length


def _ebml_uint(f, n: int) -> int:
    data = f.read(n)
    if len(data) < n:
        raise EOFError
    return int.from_bytes(data, "big")


def _parse_mkv(path: str) -> Optional[MediaInfo]:
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"\x1a\x45\xdf\xa3":
                return None
            f.seek(0)
            return _mkv_walk(f, os.path.getsize(path), path, 0)
    except (OSError, EOFError, struct.error):
        return None


def _mkv_walk(f, size: int, path: str, depth: int) -> Optional[MediaInfo]:
    info = MediaInfo(path=str(path), source="native-mkv",
                     size_bytes=os.path.getsize(path) if os.path.exists(path) else 0)
    state: Dict[str, Any] = {"timecode_scale": 1_000_000, "duration": 0.0}
    tracks: List[Dict[str, Any]] = []
    end = f.tell() + size if size < (1 << 50) else f.seek(0, 2)

    while f.tell() < end - 2:
        pos = f.tell()
        idv = _ebml_read_vint(f, keep_marker=True)
        if idv is None:
            break
        eid, _ = idv
        sz = _ebml_read_vint(f, keep_marker=False)
        if sz is None:
            break
        esize, _ = sz
        unknown = (esize == (1 << (7 * 8)) - 1) or esize == 0
        name = _EBML_IDS.get(eid)
        data_start = f.tell()
        if name == "segment":
            sub = _mkv_walk(f, esize if not unknown else (1 << 50), path, depth + 1)
            if sub:
                info.duration = sub.duration
                info.has_video = sub.has_video
                info.has_audio = sub.has_audio
                info.width, info.height = sub.width, sub.height
                info.fps = sub.fps
                info.codec, info.audio_codec = sub.codec, sub.audio_codec
                info.sample_rate, info.channels = sub.sample_rate, sub.channels
            continue
        elif name == "info":
            _mkv_info(f, esize, state)
        elif name == "tracks":
            tracks = _mkv_tracks(f, esize)
        else:
            if not unknown:
                f.seek(data_start + esize)
            else:
                f.seek(data_start + 1)
                continue
        if f.tell() <= pos:
            break

    if tracks:
        v = next((t for t in tracks if t.get("type") == 1), None)
        a = next((t for t in tracks if t.get("type") == 2), None)
        if v:
            info.has_video = True
            info.width = v.get("pixel_width") or v.get("display_width") or 0
            info.height = v.get("pixel_height") or v.get("display_height") or 0
            info.codec = v.get("codec_id", "")
            dd = v.get("default_duration")
            if dd:
                info.fps = 1e9 / dd
        if a:
            info.has_audio = True
            info.audio_codec = a.get("codec_id", "")
            info.sample_rate = int(a.get("sampling_rate") or 0)
            info.channels = int(a.get("channels") or 0)
        tcs = state.get("timecode_scale") or 1_000_000
        if state.get("duration"):
            info.duration = state["duration"] * tcs / 1e9
    ext_kind = classify(path)
    if ext_kind == "image":
        info.kind = "image"
    elif not info.has_video:
        info.kind = "audio"
    else:
        info.kind = "video"
    if info.kind == "video" and info.fps <= 0:
        info.fps = 30.0
    return info


def _mkv_info(f, size: int, state: Dict[str, Any]) -> None:
    end = f.tell() + size
    while f.tell() < end - 2:
        idv = _ebml_read_vint(f, True)
        if idv is None:
            return
        eid, _ = idv
        sz = _ebml_read_vint(f, False)
        if sz is None:
            return
        esize, ewidth = sz
        name = _EBML_IDS.get(eid)
        if name == "timecode_scale":
            state["timecode_scale"] = _ebml_uint(f, esize)
        elif name == "duration":
            raw = f.read(esize)
            if esize == 4:
                state["duration"] = struct.unpack(">f", raw)[0]
            elif esize == 8:
                state["duration"] = struct.unpack(">d", raw)[0]
            else:
                state["duration"] = int.from_bytes(raw, "big")
        else:
            f.seek(f.tell() + esize)


def _mkv_tracks(f, size: int) -> List[Dict[str, Any]]:
    end = f.tell() + size
    tracks: List[Dict[str, Any]] = []
    while f.tell() < end - 2:
        idv = _ebml_read_vint(f, True)
        if idv is None:
            break
        eid, _ = idv
        sz = _ebml_read_vint(f, False)
        if sz is None:
            break
        esize, _ = sz
        if eid == 0xAE:
            tracks.append(_mkv_track_entry(f, esize))
        else:
            f.seek(f.tell() + esize)
    return tracks


def _mkv_track_entry(f, size: int) -> Dict[str, Any]:
    end = f.tell() + size
    t: Dict[str, Any] = {}
    while f.tell() < end - 2:
        idv = _ebml_read_vint(f, True)
        if idv is None:
            break
        eid, _ = idv
        sz = _ebml_read_vint(f, False)
        if sz is None:
            break
        esize, _ = sz
        name = _EBML_IDS.get(eid)
        if name in ("video", "audio"):
            sub_end = f.tell() + esize
            while f.tell() < sub_end - 2:
                i2 = _ebml_read_vint(f, True)
                if i2 is None:
                    break
                e2, _ = i2
                s2 = _ebml_read_vint(f, False)
                if s2 is None:
                    break
                sz2, _ = s2
                n2 = _EBML_IDS.get(e2)
                if n2 == "sampling_rate":
                    raw = f.read(sz2)
                    t["sampling_rate"] = struct.unpack(">f", raw)[0] if sz2 == 4 else int.from_bytes(raw, "big")
                elif n2 in ("pixel_width", "pixel_height", "display_width", "display_height", "channels"):
                    t[n2] = _ebml_uint(f, sz2)
                else:
                    f.seek(f.tell() + sz2)
        elif name == "codec_id":
            raw = f.read(esize)
            t["codec_id"] = raw.split(b"\x00")[0].decode("latin-1", "replace")
        elif name in ("track_type", "default_duration"):
            t[name] = _ebml_uint(f, esize)
        else:
            f.seek(f.tell() + esize)
    return t


# -- Simple audio containers ------------------------------------------------- #

def _parse_wav(path: str) -> Optional[MediaInfo]:
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return None
            f.read(4)
            if f.read(4) != b"WAVE":
                return None
            rate = channels = bits = 0
            data_size = 0
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid, csize = struct.unpack("<4sI", hdr)
                if cid == b"fmt ":
                    fmt = f.read(min(csize, 40))
                    if len(fmt) >= 16:
                        _tag, channels, rate = struct.unpack("<HHI", fmt[:8])
                        bits = struct.unpack("<H", fmt[14:16])[0]
                    f.seek(csize - min(csize, 40), 1)
                elif cid == b"data":
                    data_size = csize
                    f.seek(csize, 1)
                else:
                    f.seek(csize, 1)
            if not rate:
                return None
            dur = data_size / (rate * max(channels, 1) * max(bits // 8, 1)) if data_size else 0.0
            return MediaInfo(path=str(path), kind="audio", source="native-wav",
                             duration=dur, sample_rate=rate, channels=channels or 2,
                             has_audio=True, audio_codec="pcm_s16le" if bits == 16 else "pcm",
                             size_bytes=os.path.getsize(path))
    except (OSError, struct.error):
        return None


def _parse_mp3(path: str) -> Optional[MediaInfo]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(10)
            if head[:3] != b"ID3" and not (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
                return None
            rate, bitrate, channels = 44100, 128000, 2
            dur = 0.0
            f.seek(0)
            chunk = f.read(min(size, 400000))
            m = re.search(rb"Xing|Info", chunk)
            if m:
                off = m.end() + 4                     # flags
                flags = struct.unpack(">I", chunk[m.end():m.end() + 4])[0]
                p = m.end() + 4
                if flags & 1:
                    frames = struct.unpack(">I", chunk[p:p + 4])[0]
                    p += 4
                    # decode the first real frame header for bitrate/samplerate
                    idx = m.start()
                    if idx > 4:
                        h = chunk[idx - 4:idx]
                    else:
                        h = b"\xff\xfb\x90\x00"
                    rate, bitrate, channels = _mp3_header(h)
                    samples_per_frame = 1152
                    dur = frames * samples_per_frame / rate if rate else 0.0
            if dur == 0.0:
                idx = chunk.find(b"\xff")
                for i in range(len(chunk) - 4):
                    if chunk[i] == 0xFF and (chunk[i + 1] & 0xE0) == 0xE0:
                        idx = i
                        break
                rate, bitrate, channels = _mp3_header(chunk[idx:idx + 4])
                dur = (size * 8) / bitrate if bitrate else 0.0
            return MediaInfo(path=str(path), kind="audio", source="native-mp3",
                             duration=dur, sample_rate=rate, channels=channels,
                             has_audio=True, audio_codec="mp3", bitrate=bitrate,
                             size_bytes=size)
    except (OSError, struct.error, IndexError):
        return None


_MP3_RATES = [(44100, 48000, 32000), (22050, 24000, 16000), (11025, 12000, 8000)]
_MP3_BR = [None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None]
_MP3_BR_V2 = [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None]


def _mp3_header(h: bytes) -> tuple:
    if len(h) < 4:
        return 44100, 128000, 2
    try:
        b1, b2, b3 = h[1], h[2], h[3]
        ver = (b1 >> 3) & 0x03
        layer = (b1 >> 1) & 0x03
        br_idx = (b2 >> 4) & 0x0F
        sr_idx = (b2 >> 2) & 0x03
        mode = (b3 >> 6) & 0x03
        channels = 1 if mode == 3 else 2
        table = _MP3_BR_V2 if ver == 2 else _MP3_BR
        bitrate = (table[br_idx] or 128) * 1000
        group = _MP3_RATES[2 if ver == 2 else (1 if ver == 0 else 0)]
        rate = group[sr_idx] if sr_idx < 3 else 44100
        return rate, bitrate, channels
    except Exception:
        return 44100, 128000, 2


def _parse_flac(path: str) -> Optional[MediaInfo]:
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"fLaC":
                return None
            hdr = f.read(4)
            size = int.from_bytes(hdr[1:4], "big")
            data = f.read(size)
            if len(data) < 18:
                return None
            bits = data[10:18]
            value = int.from_bytes(bits, "big")
            total = value & ((1 << 36) - 1)
            rate = (value >> 44) & 0xFFFFF
            channels = ((value >> 41) & 0x07) + 1
            dur = total / rate if rate else 0.0
            return MediaInfo(path=str(path), kind="audio", source="native-flac",
                             duration=dur, sample_rate=rate, channels=channels,
                             has_audio=True, audio_codec="flac",
                             size_bytes=os.path.getsize(path))
    except (OSError, struct.error):
        return None


def _parse_ogg(path: str) -> Optional[MediaInfo]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if f.read(4) != b"OggS":
                return None
            codec = ""
            rate, channels = 48000, 2
            f.seek(0)
            head = f.read(4096)
            if b"OpusHead" in head:
                codec = "opus"
                i = head.index(b"OpusHead")
                channels = head[i + 9]
                rate = struct.unpack("<I", head[i + 12:i + 16])[0]
            elif b"\x01vorbis" in head:
                codec = "vorbis"
                i = head.index(b"\x01vorbis")
                channels = head[i + 11]
                rate = struct.unpack("<I", head[i + 12:i + 16])[0]
            elif b"fLaC" in head:
                codec = "flac"
            # scan backwards for the last page granule
            tail = min(size, 65536)
            f.seek(size - tail)
            data = f.read(tail)
            idx = data.rfind(b"OggS")
            dur = 0.0
            if idx >= 0 and idx + 14 <= len(data):
                granule = struct.unpack("<q", data[idx + 6:idx + 14])[0]
                if granule > 0:
                    dur = granule / (rate if rate else 48000)
                    if codec == "vorbis" and rate:
                        dur = granule / rate
            return MediaInfo(path=str(path), kind="audio", source="native-ogg",
                             duration=dur, sample_rate=rate, channels=channels,
                             has_audio=True, audio_codec=codec, size_bytes=size)
    except (OSError, struct.error):
        return None


def _parse_image_native(path: str) -> Optional[MediaInfo]:
    """Minimal PNG/JPEG/BMP header reading."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return MediaInfo(path=str(path), kind="image", width=w, height=h,
                                 fps=30.0, source="native-png", has_video=True,
                                 codec="png", size_bytes=os.path.getsize(path))
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        break
                    if b != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if not marker:
                        break
                    if marker[0] in (0xD8, 0x01) or 0xD0 <= marker[0] <= 0xD7:
                        continue
                    ln = struct.unpack(">H", f.read(2))[0]
                    if 0xC0 <= marker[0] <= 0xCF and marker[0] not in (0xC4, 0xC8, 0xCC):
                        seg = f.read(5)
                        h, w = struct.unpack(">HH", seg[1:5])
                        return MediaInfo(path=str(path), kind="image", width=w,
                                         height=h, fps=30.0, source="native-jpeg",
                                         has_video=True, codec="mjpeg",
                                         size_bytes=os.path.getsize(path))
                    f.seek(ln - 2, 1)
            if head[:2] == b"BM":
                w, h = struct.unpack("<ii", head[18:26])
                return MediaInfo(path=str(path), kind="image", width=abs(w),
                                 height=abs(h), fps=30.0, source="native-bmp",
                                 has_video=True, codec="bmp",
                                 size_bytes=os.path.getsize(path))
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                if head[12:16] == b"VP8X":
                    w = int.from_bytes(head[24:27], "little") + 1
                    h = int.from_bytes(head[27:30], "little") + 1
                    return MediaInfo(path=str(path), kind="image", width=w, height=h,
                                     fps=30.0, source="native-webp", has_video=True,
                                     codec="webp", size_bytes=os.path.getsize(path))
    except (OSError, struct.error):
        return None
    return None


def _native(path: str) -> Optional[MediaInfo]:
    kind = classify(path)
    if kind == "image":
        return _parse_image_native(path)
    ext = Path(path).suffix.lower()
    if ext in (".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".m4a", ".aac"):
        r = _parse_mp4(path)
        if r:
            return r
    if ext in (".mkv", ".webm", ".mka"):
        r = _parse_mkv(path)
        if r:
            return r
    if ext == ".wav":
        return _parse_wav(path)
    if ext in (".mp3",):
        return _parse_mp3(path)
    if ext == ".flac":
        return _parse_flac(path)
    if ext in (".ogg", ".oga", ".opus"):
        return _parse_ogg(path)
    return None


# --------------------------------------------------------------------------- #
#  3. ffmpeg stderr fallback
# --------------------------------------------------------------------------- #

_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_RE_STREAM = re.compile(r"Stream #\d+:(?P<idx>\d+)(?:\[[^\]]*\])?(?:\((?P<lang>[^)]*)\))?: "
                        r"(?P<kind>Video|Audio|Subtitle|Data|Attachment): (?P<rest>.+)$")
_RE_DIM = re.compile(r"(\d{2,5})x(\d{2,5})")
_RE_FPS = re.compile(r"([\d.]+)\s*fps")
_RE_SR = re.compile(r"(\d+)\s*Hz")
_RE_ROT = re.compile(r"rotate\s*:\s*(\d+)")
_RE_ATTACH = re.compile(r"\(attached pic\)")


def _probe_ffmpeg_stderr(path: str) -> Optional[MediaInfo]:
    r = _ff.ffmpeg_probe("-i", str(path), timeout=30)
    text = r.stderr
    if "Input #0" not in text and "Stream #" not in text:
        return None
    info = MediaInfo(path=str(path), source="ffmpeg-stderr",
                     size_bytes=os.path.getsize(path) if os.path.exists(path) else 0)
    m = _RE_DURATION.search(text)
    if m:
        h, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info.duration = h * 3600 + mm * 60 + ss
        dm = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
        if dm:
            info.bitrate = int(dm.group(1)) * 1000
    for line in text.splitlines():
        line = line.strip()
        sm = _RE_STREAM.match(line)
        if not sm:
            continue
        kind, rest = sm.group("kind"), sm.group("rest")
        if kind == "Video" and not info.has_video and not _RE_ATTACH.search(rest):
            info.has_video = True
            codec = rest.split(",")[0].split("(")[0].strip()
            info.codec = codec
            dm = _RE_DIM.search(rest)
            if dm:
                info.width, info.height = int(dm.group(1)), int(dm.group(2))
            fm = _RE_FPS.search(rest)
            if fm:
                try:
                    info.fps = float(fm.group(1))
                except ValueError:
                    pass
            rm = _RE_ROT.search(rest)
            if rm:
                info.rotation = int(rm.group(1)) % 360
        elif kind == "Audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = rest.split(",")[0].split("(")[0].strip()
            sm2 = _RE_SR.search(rest)
            if sm2:
                info.sample_rate = int(sm2.group(1))
            if "mono" in rest:
                info.channels = 1
            elif "stereo" in rest:
                info.channels = 2
            else:
                cm = re.search(r",\s*(\d+) channels", rest)
                info.channels = int(cm.group(1)) if cm else 2
    ext_kind = classify(path)
    if ext_kind == "image":
        info.kind = "image"
    elif not info.has_video:
        info.kind = "audio"
    else:
        info.kind = "video"
    if info.kind == "image":
        info.duration = 0.0
        info.fps = info.fps or 30.0
    if info.kind == "video" and info.fps <= 0:
        info.fps = 30.0
    return info


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #

def probe(path: str, use_cache: bool = True) -> MediaInfo:
    """Best-effort media inspection; always returns an object (never raises)."""
    path = str(path)
    if use_cache:
        hit = _CACHE.get((path, os.path.getmtime(path) if os.path.exists(path) else 0))
        if hit is not None:
            return hit
    info: Optional[MediaInfo] = None
    if _ff.TOOLCHAIN.ffprobe is not None:
        info = _probe_ffprobe(path)
    if info is None:
        try:
            info = _native(path)
        except Exception:
            info = None
    if info is None or (info.kind == "video" and (not info.width or not info.duration)):
        fallback = _probe_ffmpeg_stderr(path)
        if fallback and (info is None or (fallback.width and not info.width)
                         or (fallback.duration and not info.duration)):
            if info is not None:
                for attr in ("duration", "width", "height", "fps", "rotation",
                             "codec", "audio_codec", "sample_rate", "channels",
                             "has_audio", "has_video", "bitrate", "frame_count"):
                    val = getattr(fallback, attr)
                    if val and not getattr(info, attr):
                        setattr(info, attr, val)
            else:
                info = fallback
    if info is None:
        info = MediaInfo(path=path, kind=classify(path), source="unknown")
        if info.kind == "image":
            info.fps = 30.0
    if info.kind == "video" and info.fps <= 0:
        info.fps = 30.0
    if info.kind == "video" and info.duration <= 0 and info.frame_count and info.fps:
        info.duration = info.frame_count / info.fps
    if use_cache:
        _CACHE[(path, os.path.getmtime(path) if os.path.exists(path) else 0)] = info
    return info


_CACHE: Dict[tuple, MediaInfo] = {}


def clear_cache() -> None:
    _CACHE.clear()


def make_thumbnail(path: str, out: str, at: float = 1.0, width: int = 256) -> bool:
    """Extract a poster frame (used by the media bin)."""
    info = probe(path, use_cache=False)
    args = ["-y", "-v", "error"]
    if info.kind == "video" and info.duration > 0:
        args += ["-ss", f"{max(0.0, min(at, max(info.duration - 0.05, 0.0))):.3f}"]
    args += ["-i", str(path), "-frames:v", "1",
             "-vf", f"scale={width}:-2:flags=bilinear", "-q:v", "4", str(out)]
    r = _ff.ffmpeg(*args, timeout=45)
    if not r.ok:
        args = ["-y", "-v", "error", "-i", str(path), "-frames:v", "1",
                "-vf", f"scale={width}:-2:flags=bilinear", str(out)]
        r = _ff.ffmpeg(*args, timeout=45)
    return r.ok and os.path.exists(out)


def waveform_peaks(path: str, buckets: int = 900,
                   start: float = 0.0, duration: float = 0.0) -> List[float]:
    """Decode a mono peak envelope for timeline waveform drawing."""
    import array
    args = ["-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration > 0:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", "4000", "-f", "s16le", "-"]
    r = _ff.ffmpeg(*args, timeout=120)
    if not r.ok or not r.stdout:
        return []
    samples = array.array("h")
    usable = len(r.stdout) - (len(r.stdout) % 2)
    samples.frombytes(r.stdout[:usable])
    if not samples:
        return []
    step = max(1, len(samples) // buckets)
    peaks: List[float] = []
    for i in range(0, len(samples) - step + 1, step):
        chunk = samples[i:i + step]
        peak = 0
        for v in chunk:
            av = v if v >= 0 else -v
            if av > peak:
                peak = av
        peaks.append(min(1.0, peak / 32768.0))
    return peaks
