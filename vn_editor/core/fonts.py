"""Minimal TrueType metrics reader.

The editor needs to know how wide a string will be *before* rendering it, so
that captions can auto-shrink to fit the frame the way VN does.  Rather than
depend on Qt (unavailable in the headless render path) this reads the tables it
needs straight out of the TTF: ``head``/``hhea``/``hmtx``/``cmap``.
"""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class FontMetrics:
    __slots__ = ("units_per_em", "advances", "cmap", "ascent", "descent",
                 "cap_height", "path", "win_ascent", "win_descent")

    def __init__(self, path: str) -> None:
        self.path = path
        self.units_per_em = 1000
        self.advances: List[int] = []
        self.cmap: Dict[int, int] = {}
        self.ascent = 800
        self.descent = -200
        self.cap_height = 700
        self.win_ascent = 950
        self.win_descent = 250
        self._load()

    # -- parsing ----------------------------------------------------------- #
    def _load(self) -> None:
        data = Path(self.path).read_bytes()
        if len(data) < 12:
            return
        tag = data[:4]
        offset = 0
        if tag == b"ttcf":
            count = struct.unpack(">I", data[8:12])[0]
            if count:
                offset = struct.unpack(">I", data[12:16])[0]
        num_tables = struct.unpack(">H", data[offset + 4:offset + 6])[0]
        tables: Dict[bytes, Tuple[int, int]] = {}
        pos = offset + 12
        for _ in range(num_tables):
            if pos + 16 > len(data):
                break
            t = data[pos:pos + 4]
            o, ln = struct.unpack(">II", data[pos + 8:pos + 16])
            tables[t] = (o, ln)
            pos += 16

        head = tables.get(b"head")
        if head:
            o, _ = head
            self.units_per_em = struct.unpack(">H", data[o + 18:o + 20])[0] or 1000
        os2 = tables.get(b"OS/2")
        if os2:
            o, ln = os2
            try:
                if ln >= 72:
                    self.cap_height = struct.unpack(">h", data[o + 88:o + 90])[0] \
                        if ln >= 90 else int(self.units_per_em * 0.7)
                if ln >= 78:
                    # usWinAscent / usWinDescent: libass sizes glyphs by this
                    # span, so we need it to convert our pixel sizes.
                    wa, wd = struct.unpack(">HH", data[o + 74:o + 78])
                    if wa + wd > 0:
                        self.win_ascent, self.win_descent = int(wa), int(wd)
            except struct.error:
                pass
        hhea = tables.get(b"hhea")
        num_h = 0
        if hhea:
            o, _ = hhea
            self.ascent = struct.unpack(">h", data[o + 4:o + 6])[0]
            self.descent = struct.unpack(">h", data[o + 6:o + 8])[0]
            num_h = struct.unpack(">H", data[o + 34:o + 36])[0]
        hmtx = tables.get(b"hmtx")
        if hmtx and num_h:
            o, ln = hmtx
            # hmtx stores (advanceWidth, leftSideBearing) pairs: 4 bytes each
            n = min(num_h, max(0, ln // 4))
            self.advances = [
                struct.unpack(">H", data[o + i * 4:o + i * 4 + 2])[0]
                for i in range(n)
            ] or [self.units_per_em // 2]
        cmap = tables.get(b"cmap")
        if cmap:
            self.cmap = self._parse_cmap(data, *cmap)
        if not self.advances:
            self.advances = [self.units_per_em // 2]

    def _parse_cmap(self, data: bytes, off: int, length: int) -> Dict[int, int]:
        out: Dict[int, int] = {}
        try:
            n = struct.unpack(">H", data[off + 2:off + 4])[0]
            best = None
            for i in range(n):
                rec = off + 4 + i * 8
                pid, eid, sub = struct.unpack(">HHI", data[rec:rec + 8])
                # prefer Unicode BMP (3,1) then Unicode full (3,10)/(0,x)
                score = 0
                if (pid, eid) == (3, 1):
                    score = 3
                elif (pid, eid) == (3, 10):
                    score = 2
                elif pid == 0:
                    score = 1
                if score and (best is None or score > best[0]):
                    best = (score, off + sub)
            if best is None:
                return out
            fmt = struct.unpack(">H", data[best[1]:best[1] + 2])[0]
            if fmt == 4:
                return self._cmap4(data, best[1])
            if fmt == 12:
                return self._cmap12(data, best[1])
            if fmt == 6:
                first, count = struct.unpack(">HH", data[best[1] + 6:best[1] + 10])
                for i in range(count):
                    gid = struct.unpack(">H", data[best[1] + 10 + i * 2:
                                                   best[1] + 12 + i * 2])[0]
                    if gid:
                        out[first + i] = gid
        except (struct.error, IndexError):
            pass
        return out

    @staticmethod
    def _cmap4(data: bytes, off: int) -> Dict[int, int]:
        out: Dict[int, int] = {}
        try:
            segx2 = struct.unpack(">H", data[off + 6:off + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(f">{seg}H", data[off + 14:off + 14 + segx2])
            starts = struct.unpack(f">{seg}H", data[off + 16 + segx2:
                                                    off + 16 + 2 * segx2])
            deltas = struct.unpack(f">{seg}h", data[off + 16 + 2 * segx2:
                                                    off + 16 + 3 * segx2])
            range_off_base = off + 16 + 3 * segx2
            offsets = struct.unpack(f">{seg}H", data[range_off_base:
                                                     range_off_base + segx2])
            for i in range(seg):
                s, e, d = starts[i], ends[i], deltas[i]
                if s > e or s == 0xFFFF:
                    continue
                for c in range(s, min(e, 0xFFFE) + 1):
                    if offsets[i] == 0:
                        gid = (c + d) & 0xFFFF
                    else:
                        gp = (range_off_base + i * 2 + offsets[i]
                              + (c - s) * 2)
                        if gp + 2 > len(data):
                            continue
                        gid = struct.unpack(">H", data[gp:gp + 2])[0]
                        if gid:
                            gid = (gid + d) & 0xFFFF
                    if gid:
                        out[c] = gid
                    if len(out) > 20000:
                        return out
        except (struct.error, IndexError):
            pass
        return out

    @staticmethod
    def _cmap12(data: bytes, off: int) -> Dict[int, int]:
        out: Dict[int, int] = {}
        try:
            ngroups = struct.unpack(">I", data[off + 12:off + 16])[0]
            for i in range(min(ngroups, 20000)):
                p = off + 16 + i * 12
                sc, ec, sg = struct.unpack(">III", data[p:p + 12])
                for c in range(sc, min(ec, sc + 4000) + 1):
                    out[c] = sg + (c - sc)
        except struct.error:
            pass
        return out

    # -- measurement ------------------------------------------------------- #
    def advance(self, ch: str) -> float:
        code = ord(ch)
        gid = self.cmap.get(code, 0)
        if gid < len(self.advances):
            return self.advances[gid] / max(1, self.units_per_em)
        if self.advances:
            return self.advances[-1] / max(1, self.units_per_em)
        return 0.5

    def text_width(self, text: str, size: float, letter_spacing: float = 0.0) -> float:
        total = 0.0
        for ch in text:
            if ch == "\n":
                continue
            total += self.advance(ch) * size + letter_spacing
        return total

    def line_height(self, size: float) -> float:
        return (self.ascent - self.descent) / max(1, self.units_per_em) * size

    def cap(self, size: float) -> float:
        return self.cap_height / max(1, self.units_per_em) * size

    def libass_scale(self) -> float:
        """Fontsize multiplier that makes libass draw at *size* em pixels.

        Fontsize in ASS is the font's ascender-to-descender span (libass uses
        the OS/2 ``usWin`` metrics), not the em size.  Multiplying by this
        ratio keeps ``TextProps.size`` meaning "em size in canvas pixels", which
        is what the inspector preview and the auto-fit maths assume.
        """
        span = max(1, self.win_ascent + self.win_descent)
        return max(0.25, min(4.0, span / max(1, self.units_per_em)))


_FALLBACK = FontMetrics.__new__(FontMetrics)


@lru_cache(maxsize=64)
def load(path: str) -> FontMetrics:
    try:
        return FontMetrics(path)
    except Exception:
        return _fallback_metrics()


def _fallback_metrics() -> FontMetrics:
    m = FontMetrics.__new__(FontMetrics)
    m.path = ""
    m.units_per_em = 1000
    m.advances = [500]
    m.cmap = {}
    m.ascent = 800
    m.descent = -200
    m.cap_height = 700
    return m


def metrics_for_font(name: str) -> FontMetrics:
    from .ass import FONT_DIR, FONT_FILES
    fn = FONT_FILES.get(name)
    if fn:
        p = FONT_DIR / fn
        if p.exists():
            return load(str(p))
    # try the system font by filename search
    for d in ("/usr/share/fonts", "/usr/local/share/fonts",
              str(Path.home() / ".fonts"), str(Path.home() / ".local/share/fonts")):
        base = Path(d)
        if not base.exists():
            continue
        slug = name.replace(" ", "")
        for p in base.rglob("*.ttf"):
            if slug.lower() in p.name.lower().replace(" ", ""):
                return load(str(p))
    return _fallback_metrics()


def libass_scale(font: str) -> float:
    """Fontsize multiplier so libass draws text at *size* em pixels."""
    try:
        return metrics_for_font(font).libass_scale()
    except Exception:
        return 1.15


def fit_size(text: str, font: str, size: float, max_width: float,
             letter_spacing: float = 0.0, min_size: float = 8.0) -> float:
    """Largest size <= ``size`` whose widest line fits ``max_width``."""
    if not text.strip():
        return size
    m = metrics_for_font(font)
    widest = max((m.text_width(line, size, letter_spacing)
                  for line in text.split("\n")), default=0.0)
    if widest <= max_width or widest <= 0:
        return size
    scaled = size * (max_width / widest)
    return max(min_size, scaled)


def wrap_text(text: str, font: str, size: float, max_width: float,
              letter_spacing: float = 0.0) -> str:
    """Greedy word wrap into ``max_width`` (used when auto-fit is off)."""
    m = metrics_for_font(font)
    lines: List[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            trial = w if not cur else cur + " " + w
            if m.text_width(trial, size, letter_spacing) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return "\n".join(lines)
