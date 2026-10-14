# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for a suspected security problem.
Use GitHub's private vulnerability reporting ("Report a vulnerability"
on the repository's Security tab) or contact the maintainers privately.
Include steps to reproduce and the affected version; we aim to
acknowledge within 7 days.

## Security posture of the application

OpenCut Studio is a local desktop editor. Relevant design points:

- **No network behaviour.** The application makes no network requests
  and uploads nothing. (Optional AI features, if ever added, will be
  opt-in and documented.)
- **Media processing is invoked safely.** FFmpeg is executed with
  argument lists (never a shell string built from filenames), and
  user-controlled parameters are validated in the exporter/importer.
- **Project files are data.** `.opencut` files are JSON and are
  validated (envelope, format version) before any model object is
  constructed; media paths inside a project are only read, never
  executed.
- **Your media is never modified.** Import references files by path;
  rendering writes only to the destination the user chooses.
- **Plugins are sandboxed by design.** Plugins run in the same process
  but are discovered from a controlled directory, their requirements
  are checked before loading, and all failures are caught and logged.
  A broken plugin never crashes the application or corrupts a project
  file. Plugins that make network requests should document this
  clearly and require explicit user opt-in.

## Supported versions

Pre-1.0: only the latest release receives fixes.
