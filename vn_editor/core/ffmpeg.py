"""FFmpeg/FFprobe discovery, capability probing and subprocess helpers.

The editor must work on a stock Ubuntu box where the user may or may not have
installed ffmpeg, and it must also work with a zero-install static binary.  This
module hides all of that behind :func:`ffmpeg` / :func:`ffprobe`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# --------------------------------------------------------------------------- #
#  Discovery
# --------------------------------------------------------------------------- #

_APP_DIR = Path(__file__).resolve().parent.parent            # vn_editor/
_REPO_DIR = _APP_DIR.parent                                   # repo root

_SEARCH_DIRS = [
    _REPO_DIR / "vendor" / "ffmpeg",
    _APP_DIR / "vendor" / "ffmpeg",
    Path.home() / ".local" / "share" / "vn-editor" / "bin",
    Path.home() / ".local" / "bin",
    Path("/usr/bin"), Path("/usr/local/bin"), Path("/snap/bin"),
    Path("/opt/ffmpeg/bin"), Path("/usr/lib/ffmpeg"),
    Path("/var/lib/flatpak/exports/bin"),
]

_CREATE_NO_WINDOW = 0


def _is_executable(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


def _pypi_ffmpeg() -> Optional[Path]:
    """Last resort: the ``imageio-ffmpeg`` wheel ships a static ffmpeg."""
    try:
        import imageio_ffmpeg  # type: ignore
        p = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if _is_executable(p):
            return p
    except Exception:
        pass
    return None


def _find(name: str) -> Optional[Path]:
    env = os.environ.get(f"VN_{name.upper()}")
    if env:
        p = Path(env).expanduser()
        if _is_executable(p):
            return p
    which = shutil.which(name)
    if which:
        return Path(which)
    for d in _SEARCH_DIRS:
        p = d / name
        if _is_executable(p):
            return p
    return None


class Toolchain:
    """Holds the resolved binaries and the capabilities we measured."""

    def __init__(self) -> None:
        self.ffmpeg: Optional[Path] = _find("ffmpeg")
        self.ffprobe: Optional[Path] = _find("ffprobe")
        if self.ffmpeg is None:
            self.ffmpeg = _pypi_ffmpeg()
        self.filters: set = set()
        self.encoders: set = set()
        self.muxers: set = set()
        self.version: str = ""
        self._probed = False
        self._lock = threading.Lock()

    # -- capabilities ------------------------------------------------------ #
    def probe(self) -> None:
        with self._lock:
            if self._probed or self.ffmpeg is None:
                self._probed = True
                return
            try:
                out = subprocess.run(
                    [str(self.ffmpeg), "-hide_banner", "-version"],
                    capture_output=True, text=True, timeout=20,
                    creationflags=_CREATE_NO_WINDOW,
                ).stdout
                m = re.search(r"ffmpeg version (\S+)", out)
                self.version = m.group(1) if m else "unknown"
                # ffmpeg lists capabilities as "  TSC name  IO  description"
                row = re.compile(r"^\s*[A-Z.]{2,6}\s+(\S+)\s")
                for args, sink in ((["-filters"], self.filters),
                                   (["-encoders"], self.encoders),
                                   (["-muxers"], self.muxers)):
                    r = subprocess.run([str(self.ffmpeg), "-hide_banner", *args],
                                       capture_output=True, text=True,
                                       errors="replace", timeout=25,
                                       creationflags=_CREATE_NO_WINDOW)
                    for line in r.stdout.splitlines():
                        m = row.match(line)
                        if m:
                            sink.add(m.group(1))
                        else:
                            parts = line.split()
                            if len(parts) >= 2 and not line.startswith(" "):
                                sink.add(parts[1])
            except Exception:
                pass
            self._probed = True

    def has_filter(self, name: str) -> bool:
        self.probe()
        return name in self.filters

    def has_encoder(self, name: str) -> bool:
        self.probe()
        return name in self.encoders

    @property
    def ok(self) -> bool:
        return self.ffmpeg is not None

    def summary(self) -> str:
        if not self.ok:
            return "FFmpeg not found"
        self.probe()
        return (f"FFmpeg {self.version} · "
                f"{len(self.filters)} filters · "
                f"{'x264' if self.has_encoder('libx264') else 'mpeg4'}")

    def missing_essentials(self) -> List[str]:
        self.probe()
        need = []
        for f in ("overlay", "scale", "trim", "setpts", "fps", "format",
                  "afade", "volume", "amix", "adelay", "color"):
            if f not in self.filters:
                need.append(f)
        return need


TOOLCHAIN = Toolchain()

# Optional features degrade gracefully instead of exploding at render time.
OPTIONAL_FILTERS = {
    "zoompan", "rotate", "geq", "sendcmd", "xfade", "chromakey", "colorkey",
    "despill", "eq", "curves", "colorbalance", "colorlevels", "hue",
    "vibrance", "gblur", "boxblur", "unsharp", "nlmeans", "atadenoise",
    "noise", "lut3d", "ass", "subtitles", "atempo", "rubberband", "areverse",
    "reverse", "loudnorm", "dynaudnorm", "equalizer", "acompressor", "afade",
    "highpass", "lowpass", "minterpolate", "tmix", "tblend", "tpad", "loop",
    "colorchannelmixer", "blend", "maskedmerge", "split", "amix", "apad",
    "aevalsrc", "sine", "anoisesrc", "tremolo", "chorus", "aecho", "aphaser",
    "asetrate", "firequalizer", "extrastereo", "stereotools", "acrusher",
    "crop", "pad", "flip", "hflip", "vflip", "transpose", "subtitles",
    "edgedetect", "elbg", "kirsch", "convolution", "prewitt", "sobel",
    "gblur", "smartblur", "vignette", "colorize", "chromashift", "greyedge",
    "histeq", "limiter", "dilation", "erosion", "mestimate", "blackdetect",
    "silencedetect", "volumedetect", "sidedata", "showspectrum", "showwaves",
}


def filter_available(name: str) -> bool:
    return TOOLCHAIN.has_filter(name)


# --------------------------------------------------------------------------- #
#  Running ffmpeg
# --------------------------------------------------------------------------- #

@dataclass
class RunResult:
    returncode: int
    stdout: bytes = b""
    stderr: str = ""
    argv: Sequence[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def error_tail(self, lines: int = 14) -> str:
        txt = (self.stderr or "").strip().splitlines()
        return "\n".join(txt[-lines:])


def run(args: Sequence[str], timeout: float = 60.0,
        text: bool = True) -> RunResult:
    """Run ffmpeg/ffprobe (or any argv) and capture the result.

    Decoding is lenient: libass and some filters emit non-UTF-8 bytes in their
    log output, and we must never crash while reading a render log.
    """
    argv = [str(a) for a in args]
    kwargs = dict(capture_output=True, timeout=timeout,
                  creationflags=_CREATE_NO_WINDOW)
    if text:
        kwargs.update(encoding="utf-8", errors="replace")
    try:
        p = subprocess.run(argv, text=text, **kwargs)
        out = p.stdout
        return RunResult(p.returncode,
                         out.encode("utf-8", "replace") if text and isinstance(out, str) else out,
                         p.stderr or "", argv)
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr or ""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return RunResult(124, b"", f"timeout after {timeout}s\n{err}", argv)
    except FileNotFoundError as exc:
        return RunResult(127, b"", str(exc), argv)


def ffmpeg(*args: str, timeout: float = 60.0) -> RunResult:
    binary = TOOLCHAIN.ffmpeg
    if binary is None:
        return RunResult(127, b"", "ffmpeg executable not found", ("ffmpeg",))
    return run([str(binary), "-hide_banner", "-nostdin", *args], timeout=timeout)


def ffmpeg_probe(*args: str, timeout: float = 60.0) -> RunResult:
    """Like :func:`ffmpeg` but keeps the banner (needed for stderr parsing)."""
    binary = TOOLCHAIN.ffmpeg
    if binary is None:
        return RunResult(127, b"", "ffmpeg executable not found", ("ffmpeg",))
    return run([str(binary), "-nostdin", *args], timeout=timeout)


def ffprobe(*args: str, timeout: float = 30.0) -> RunResult:
    binary = TOOLCHAIN.ffprobe
    if binary is None:
        return RunResult(127, b"", "ffprobe not available", ("ffprobe",))
    return run([str(binary), "-hide_banner", *args], timeout=timeout)


def popen_ffmpeg(args: Sequence[str], **kw) -> subprocess.Popen:
    binary = TOOLCHAIN.ffmpeg
    if binary is None:
        raise RuntimeError("ffmpeg executable not found")
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("creationflags", _CREATE_NO_WINDOW)
    if sys.platform != "win32":
        kw.setdefault("start_new_session", True)
    return subprocess.Popen([str(binary), "-hide_banner", "-nostdin", *args], **kw)


def terminate(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            if sys.platform != "win32":
                import signal
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    proc.terminate()
            else:  # pragma: no cover
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Little helpers used all over the renderer
# --------------------------------------------------------------------------- #

def fmt_num(v: float, nd: int = 6) -> str:
    """Compact float formatting for filtergraph numbers."""
    if v == int(v) and abs(v) < 1e9:
        return str(int(v))
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s or "0"


def escape_filter_path(path: str) -> str:
    """Escape a filename for use inside a filtergraph option value."""
    p = str(path).replace("\\", "/")
    p = p.replace("'", r"'\''")
    p = p.replace(":", r"\:")
    p = p.replace("[", r"\[").replace("]", r"\]")
    p = p.replace(",", r"\,")
    p = p.replace(";", r"\;")
    return p


def escape_drawtext(text: str) -> str:
    """Escape free text for drawtext-style option values."""
    return (text.replace("\\", r"\\").replace(":", r"\:")
                .replace("'", r"'\''").replace("%", r"\%")
                .replace(",", r"\,").replace(";", r"\;")
                .replace("[", r"\[").replace("]", r"\]"))


def ffmpeg_version_tuple() -> tuple:
    TOOLCHAIN.probe()
    m = re.match(r"(\d+)\.(\d+)", TOOLCHAIN.version or "")
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def has_alphamerge() -> bool:
    return filter_available("alphamerge") and filter_available("alphaextract")


def hwaccel_available() -> Optional[str]:
    """Return a usable VAAPI device if one exists (never required)."""
    dev = "/dev/dri/renderD128"
    return dev if os.path.exists(dev) else None
