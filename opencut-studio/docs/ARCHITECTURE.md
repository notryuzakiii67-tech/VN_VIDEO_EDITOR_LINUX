# OpenCut Studio — architecture

## Principles

1. **The UI never touches FFmpeg directly.** All media work goes through
   `opencut.engine`, a thin facade over the media engine
   (`vn_editor.core`: data model, media probing, FFmpeg graph compiler,
   preview-frame renderer, exporter, undo history). If the engine is ever
   replaced or vendored differently, only that facade changes.
2. **Timeline operations are pure functions** (`opencut.timeline.ops`) on
   the project model. They know nothing about Qt or undo; the UI wraps
   them in history transactions. They are unit-tested without a display.
3. **Preview and export share one graph compiler**, so what you scrub is
   what you render (the engine's own test-suite holds them frame-equal).
4. **Failures are surfaced, never swallowed**: import reports every
   rejected file with a reason; export returns the encoder's message;
   project loading validates its envelope before touching the model.

## Layout

```
main.py                  entry point (also: --self-test)
opencut/
  engine.py              the ONLY import of the media engine (facade)
  project/store.py       .opencut format: create/save/load/migrate
  media/importer.py      probe-driven import + unsupported-media policy
  timeline/ops.py        add/split/trim/move/duplicate/delete/speed
  rendering/exporter.py  export presets, render call, output validation
  ui/
    main_window.py       workspace assembly, menus, project lifecycle
    media_panel.py       library: search, thumbnails, waveforms (workers)
    preview.py           scrub stills (worker) + pipe playback w/ A/V sync
    inspector.py         context-sensitive properties (one undo per gesture)
    timeline.py          multitrack timeline widget (paint + mouse editing)
    export_dialog.py     preset/destination/progress/cancel on a thread
    theme.py             original dark theme + palette
tests/                   pytest: unit + integration + Phase 1 acceptance
```

## Data flow

- **Import**: file → `probe` (ffprobe if present, else native container
  parsers / FFmpeg probing inside the engine) → `MediaAsset` in the
  library. Policy (stream-based, not extension-based): video needs a video
  stream with positive duration; audio likewise; images need readable
  dimensions; a "video" file containing only audio is treated as audio.
- **Editing**: UI gesture → `ops` mutation → undo snapshot pushed → panels
  refresh; preview re-renders the current frame from a *serialised
  snapshot* of the project so worker renders can never race edits.
- **Scrub**: `engine.render_frame(project_snapshot, t, w, h, png)` on a
  thread pool; a generation counter discards superseded frames.
- **Playback**: the engine compiles the timeline once into two pipes —
  raw RGB24 frames and s16le stereo PCM. Frames are paced against the
  audio clock (`QAudioSink.processedUSecs`) when audio exists, else a
  wall clock. No audio device → video-only playback.
- **Export**: `engine.render(project, output, options, progress, cancel)`
  on a worker thread; the dialog then re-probes the result
  (`exporter.validate_output`) before declaring success.

## Project format (`.opencut`, format_version 1)

A single JSON document: an OpenCut envelope (`format`, `format_version`,
`app`, `saved`) around the engine model's own serialisation (`project`).
The envelope is versioned independently so the format can migrate without
disturbing the model. Loading a newer format version is refused with an
actionable message; saves are atomic (temp file + rename). Media is
referenced by path, never embedded or modified; missing files are
reported on open, and `engine.relink_media` re-points an asset (and its
clips) at a moved file.

## Undo

The engine's history is snapshot-based: an edit stores the whole project
dict before/after. That is O(project size) per step with a 250-step cap —
simple and robust for the project sizes Phase 1 targets. A command-based
history is a known future optimisation (Phase 3), not a Phase 1 risk.

## Performance posture (modest hardware, no GPU required)

- Scrubbing renders *one* frame per request (seek-based, never decodes
  from the clip start); stills are capped at 960 px wide.
- Thumbnails/waveforms/stills render on worker threads; the UI thread
  keeps only painting and input.
- Export presets trade size/quality/speed explicitly; preview playback
  uses a reduced render scale. Proxy media and preview-quality settings
  are Phase 3.

## Known engine boundaries (relevant to later phases)

- The model already carries keyframes, transitions, speed curves, audio
  envelopes, masks/chroma data; Phases 2 and 4 *expose and verify* them in
  the UI rather than inventing parallel data.
- `ops.set_clip_speed` deliberately refuses speed-curve clips until the
  curve editor exists (Phase 2), instead of silently discarding the curve.
