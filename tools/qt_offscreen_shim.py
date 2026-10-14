#!/usr/bin/env python3
"""Regenerate the offscreen Qt shim libraries (symbol-accurate).

The container has no display server and none of the platform libraries the
PySide6 wheels link against (libGL, libEGL, libxkbcommon, libdbus, libpulse,
the xcb family…).  The `offscreen` QPA plugin never calls into them, so empty
shared objects are enough — but they must export the *exact* symbol names and
versions the Qt binaries reference, otherwise the dynamic linker refuses to
load.

This script reads the undefined-symbol tables of every Qt library, bins the
symbols per provider, and compiles one stub per provider with a matching
version script.

Run:  python3 tools/qt_offscreen_shim.py [--out DIR]
Then: LD_LIBRARY_PATH=<DIR> QT_QPA_PLATFORM=offscreen python ...
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_OUT = Path(os.environ.get("QTSHIM_DIR", "/tmp/qtshim"))
HERE = DEFAULT_OUT
OUT = HERE / "lib"
SRC = HERE / "src"

def _pyside_dirs() -> list[Path]:
    """Locate the PySide6 Qt binaries, wherever this interpreter lives."""
    import site
    try:
        import PySide6
        roots = [Path(PySide6.__file__).resolve().parent.parent]
    except Exception:
        roots = []
    roots += [Path(p) for p in site.getsitepackages()]
    roots += [Path(sys.prefix) / "lib" / f"python3.{sys.version_info.minor}"
              / "site-packages"]
    out: list[Path] = []
    for root in roots:
        qt = root / "PySide6" / "Qt"
        for sub in ("lib", "plugins"):
            if (qt / sub).exists() and (qt / sub) not in out:
                out.append(qt / sub)
    return out


QT_LIB_DIRS = _pyside_dirs()

# provider soname -> (regex for member symbols, version tag or None)
PROVIDERS = [
    ("libdbus-1.so.3", re.compile(r"^dbus_"), "LIBDBUS_1_3"),
    ("libpulse.so.0", re.compile(r"^pa_"), "PULSE_0"),
    ("libpulse-simple.so.0", re.compile(r"^pa_simple_"), "PULSE_0"),
    ("libxkbcommon.so.0", re.compile(r"^xkb_"), "V_0.5.0"),
    ("libxkbcommon-x11.so.0", re.compile(r"^xkb_x11_"), "V_0.5.0"),
    ("libEGL.so.1", re.compile(r"^egl"), None),
    ("libGLX.so.0", re.compile(r"^glX"), None),
    ("libGL.so.1", re.compile(r"^gl[A-Z]"), None),
    ("libGLESv2.so.2", re.compile(r"^gl[A-Z]"), None),
    ("libexpat.so.1", re.compile(r"^XML_"), None),
    ("libxcb-xinput.so.0", re.compile(r"^xcb_input_"), None),
    ("libxcb-xkb.so.1", re.compile(r"^xcb_xkb_"), None),
    ("libxcb-randr.so.0", re.compile(r"^xcb_randr_"), None),
    ("libxcb-render.so.0", re.compile(r"^xcb_render_"), None),
    ("libxcb-shape.so.0", re.compile(r"^xcb_shape_"), None),
    ("libxcb-shm.so.0", re.compile(r"^xcb_shm_"), None),
    ("libxcb-sync.so.1", re.compile(r"^xcb_sync_"), None),
    ("libxcb-icccm.so.4", re.compile(r"^xcb_icccm_"), None),
    ("libxcb-image.so.0", re.compile(r"^xcb_image_"), None),
    ("libxcb-keysyms.so.1", re.compile(r"^xcb_key_symbols_"), None),
    ("libxcb-cursor.so.0", re.compile(r"^xcb_cursor_"), None),
    ("libxcb-util.so.1", re.compile(r"^xcb_aux_"), None),
    ("libxcb.so.1", re.compile(r"^xcb_"), None),
    ("libXext.so.6", re.compile(r"^(XShm|XMissingExtension|XSync|XShape)"), None),
    ("libXrender.so.1", re.compile(r"^XRender"), None),
    ("libX11.so.6", re.compile(r"^X(?!Render|Shm)"), None),
    ("libX11-xcb.so.1", re.compile(r"^XGetXCB"), None),
    ("libwayland-client.so.0", re.compile(r"^wl_"), None),
    ("libwayland-egl.so.1", re.compile(r"^wl_egl"), None),
    ("libwayland-cursor.so.0", re.compile(r"^wl_cursor"), None),
    ("libdrm.so.2", re.compile(r"^drm"), None),
    ("libgbm.so.1", re.compile(r"^gbm_"), None),
    ("libxshmfence.so.1", re.compile(r"^xshmfence_"), None),
    ("libfontconfig.so.1", re.compile(r"^Fc"), None),
    ("libfreetype.so.6", re.compile(r"^(FT_|ft_)"), None),
    ("libresolv.so.2", re.compile(r"^(__res_|res_)"), None),
]

NEEDED_RE = re.compile(r"NEEDED\s+\[?([^\]]+)\]?")


def system_has(soname: str) -> bool:
    """True when the real library already exists in the system search path."""
    from glob import glob
    for d in ("/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
              "/lib64", "/usr/lib64", "/lib", "/usr/lib"):
        if glob(f"{d}/{soname}*"):
            return True
    return False


def find_qt_binaries() -> list[Path]:
    seen: list[Path] = []
    for d in QT_LIB_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.rglob("*.so*")):
            if f.is_file() and not f.is_symlink():
                seen.append(f)
    return seen


VERNEED_RE = re.compile(r"File: (\S+)\s*\n(?:.*\n)*?.*Name: (\S+)", re.M)


def required_versions(paths: list[Path]) -> dict[str, set[str]]:
    """{soname: {version names}} that the Qt binaries demand."""
    out: dict[str, set[str]] = {}
    for p in paths:
        try:
            text = subprocess.run(["readelf", "--version-info", str(p)],
                                  capture_output=True, text=True, timeout=90).stdout
        except Exception:
            continue
        cur = None
        for line in text.splitlines():
            m = re.search(r"File: (\S+)", line)
            if m:
                cur = m.group(1)
                out.setdefault(cur, set())
                continue
            m = re.search(r"Name: (\S+)", line)
            if m and cur:
                out[cur].add(m.group(1))
    return out


def sonames_of(path: Path) -> set[str]:
    try:
        data = subprocess.run(["readelf", "-d", str(path)],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return set()
    return {m.group(1) for m in NEEDED_RE.finditer(data)}


def undefined_symbols(paths: list[Path]) -> dict[tuple[str, str], None]:
    """{(symbol, version)} referenced by any of *paths*."""
    out: dict[tuple[str, str], None] = {}
    for p in paths:
        for tool in (("nm", "-D", "--undefined-only", "--with-symbol-versions"),
                     ("readelf", "--dyn-syms", "-W")):
            try:
                r = subprocess.run([tool[0], *tool[1:], str(p)],
                                   capture_output=True, text=True, timeout=60)
            except Exception:
                continue
            if r.returncode != 0 or not r.stdout:
                continue
            for line in r.stdout.splitlines():
                sym = ver = ""
                if tool[0] == "nm":
                    if " U " not in line:
                        continue
                    tok = line.split()[-1]
                    if "@" in tok:
                        sym, ver = tok.split("@", 1)
                    else:
                        sym = tok
                else:
                    parts = line.split()
                    if not parts or "UND" not in parts:
                        continue
                    # readelf: ... Ndx Name@Version
                    cand = [t for t in parts if not t.startswith("[") and t not in
                            ("UND", "GLOBAL", "WEAK", "DEFAULT", "UNIQUE", "LOCAL", "HIDDEN")]
                    if len(cand) < 1:
                        continue
                    tok = cand[-1]
                    if "@" in tok:
                        sym, ver = tok.split("@", 1)
                    else:
                        sym = tok
                sym = sym.strip()
                if not sym or sym.startswith("__cxa") or sym.startswith("_Z"):
                    continue
                out[(sym, ver)] = None
            if out:
                break
    return out


def build(soname: str, symbols: list[str], versions: list[str] | None,
          extra_deps: list[str] | None = None) -> None:
    for dep in (extra_deps or []):
        if not (OUT / dep).exists():
            return
    stem = re.sub(r"\W+", "_", soname)
    src = SRC / f"{stem}.c"
    lines = [f"/* generated shim for {soname} — {len(symbols)} symbols */",
             "typedef void (*fn_t)(void);"]
    table = []
    used: set[str] = set()
    for sym in symbols:
        ident = re.sub(r"\W+", "_", sym)
        if ident in used:
            continue
        used.add(ident)
        lines.append(f"void {ident}(void) {{ }}")
        table.append(ident)
    if not table:
        lines.append("void shim_placeholder(void) { }")
        table.append("shim_placeholder")
    lines.append("fn_t shim_symbol_table[] = { " + ", ".join(table) + " };")
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = ["gcc", "-shared", "-fPIC", "-O0", "-w", "-Wl,-soname," + soname]
    for dep in (extra_deps or []):
        cmd.append(f"-L{OUT}")
        cmd.append("-l:" + dep)
    if versions:
        vmap = SRC / f"{stem}.map"
        body = "\n".join(f"{v} {{ global: *; }};" for v in versions)
        vmap.write_text(body + "\n")
        cmd.append("-Wl,--version-script=" + str(vmap))
    cmd += ["-o", str(OUT / soname), str(src)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"build failed for {soname}:\n{r.stderr[:2000]}")


def main() -> None:
    global HERE, OUT, SRC
    if "--out" in sys.argv:
        HERE = Path(sys.argv[sys.argv.index("--out") + 1]).expanduser()
        OUT = HERE / "lib"
        SRC = HERE / "src"
    OUT.mkdir(parents=True, exist_ok=True)
    SRC.mkdir(parents=True, exist_ok=True)
    qt = find_qt_binaries()
    print(f"scanning {len(qt)} Qt libraries…")
    syms = undefined_symbols(qt)
    print(f"found {len(syms)} distinct undefined symbols")

    req_versions = required_versions(qt)
    buckets: dict[str, list[str]] = {}
    versions: dict[str, list[str]] = {}
    unclaimed: list[str] = []
    for (sym, ver) in syms:
        placed = False
        for soname, rx, vtag in PROVIDERS:
            if rx.search(sym):
                buckets.setdefault(soname, []).append(sym)
                needed_v = sorted(req_versions.get(soname, set()))
                if needed_v:
                    versions[soname] = needed_v
                elif vtag:
                    versions[soname] = [vtag]
                placed = True
                break
        if not placed:
            unclaimed.append(sym)

    # libGL (glvnd) also exports the GLX entry points; Qt links GLX symbols
    # against libGL.so.1, so the stub must carry both families.
    if buckets.get("libGL.so.1") or buckets.get("libGLX.so.0"):
        merged = sorted(set(buckets.get("libGL.so.1", []))
                        | set(buckets.get("libGLX.so.0", [])))
        buckets["libGL.so.1"] = merged
        buckets["libGLX.so.0"] = merged

    built = 0
    skipped = []
    for soname, _rx, _v in PROVIDERS:
        lst = sorted(set(buckets.get(soname, [])))
        if not lst:
            continue
        if system_has(soname):
            skipped.append(soname)
            continue
        build(soname, lst, versions.get(soname))
        built += 1
    print(f"built {built} shim libraries in {OUT}")
    print(f"use: LD_LIBRARY_PATH={OUT} QT_QPA_PLATFORM=offscreen")
    if skipped:
        print("skipped (real library present): " + ", ".join(skipped))
    if unclaimed:
        interesting = [s for s in sorted(set(unclaimed))
                       if not s.startswith(("_", "pthread", "g_", "gdk_", "gtk_"))]
        print(f"{len(interesting)} unclaimed symbols (first 25): "
              + ", ".join(interesting[:25]))


if __name__ == "__main__":
    main()
