# Dependencies and licence record

OpenCut Studio is licensed **GPL-3.0-or-later** (see `LICENSE`).

## Why GPL-3.0, and not MIT

| | GPL-3.0-or-later (chosen) | MIT |
|---|---|---|
| Derivatives must stay open | Yes | No |
| Fits GPL FFmpeg builds (libx264 etc.) | Yes, naturally | Awkward: the app is permissive but its encoder is copyleft |
| Patent grant | Explicit | Implicit/none |
| Reuse of our code in closed products | No | Yes |

For a community editor that *wants* improvements contributed back — and
that renders through GPL codec builds in the common case — GPL-3.0 is the
honest, low-friction choice. Trade-off accepted: some commercial embedders
will not be able to use the code.

## Runtime dependencies

| Dependency | Used for | Licence |
|---|---|---|
| Python 3.10+ | application runtime | PSF Licence |
| PySide6-Essentials / -Addons (Qt 6) | desktop UI, multimedia | LGPL-3.0-or-later / GPL-3.0 (we comply via dynamic linking) |
| NumPy | audio/frame measurement in tests and analysis | BSD-3-Clause |
| imageio-ffmpeg | fallback FFmpeg binary when no system FFmpeg exists | BSD-2-Clause (the package) — **see binary note below** |
| FFmpeg (system, via apt) | probing, decoding, filtering, encoding | LGPL-2.1+ or GPL-2.0+ **depending on build flags**; Ubuntu's package is GPL-enabled (libx264/libx265 inside) |

**Bundled-binary note.** The static FFmpeg shipped by `imageio-ffmpeg`
(and used by the bundled media engine in development) is a GPL build
(it includes libx264). Distributing OpenCut Studio under GPL-3.0 is
compatible with that; anyone redistributing the binary alone should read
FFmpeg's own licence summary. On Ubuntu we recommend the distribution's
`ffmpeg` package, whose licence is documented by Debian/Ubuntu.

## Fonts (bundled with the media engine, `vn_editor/resources/fonts`)

All 21 families are under the **SIL Open Font License 1.1** (see
`OFL-LICENSE.txt` alongside them), including the default caption font
**Bebas Neue**. No proprietary fonts are bundled; system fonts installed
by the user remain the user's responsibility.

## Development-only dependencies

| Dependency | Used for | Licence |
|---|---|---|
| pytest | test suite | MIT |

## Assets

The UI is original: one accent colour, Qt standard icons/glyphs (▶ ■),
and procedurally generated clip colours. No third-party icon packs,
screenshots, or branding from other editors are used. Test fixtures are
synthesised at test time from FFmpeg `lavfi` sources (testsrc2, sine,
color) and are not redistributed media.

## Not yet audited (future phases)

AI models (transcription etc., Phase 5) and any plugin-contributed
dependencies will be recorded here *before* they ship, with model-card
licences and redistribution terms. Nothing in Phases 1–4 requires them.
