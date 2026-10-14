# Release checklist

Steps to prepare a new OpenCut Studio release:

## Pre-release

- [ ] All tests pass: `.venv/bin/python -m pytest tests/`
- [ ] Self-test passes: `.venv/bin/python main.py --self-test`
- [ ] VN engine tests pass: `python tests/test_render.py`
- [ ] Version bumped in `opencut/__init__.py` and `pyproject.toml`
- [ ] `CHANGELOG.md` updated with new version section
- [ ] `docs/ROADMAP.md` updated (phase marked complete if applicable)
- [ ] `README.md` updated (feature list, test count)
- [ ] Phase report written in `docs/PHASE<N>_REPORT.md` (if applicable)
- [ ] No files > 3 MB outside `.git/` and `.venv/`
- [ ] `docs/DEPENDENCIES.md` updated if new dependencies added

## Release

- [ ] Commit all changes on the session branch
- [ ] Push to GitHub
- [ ] Create a Git tag: `git tag v<VERSION>`
- [ ] Push the tag: `git push origin v<VERSION>`
- [ ] Create a GitHub release from the tag with release notes from CHANGELOG

## Post-release

- [ ] Verify the CI workflow runs green on the pushed commit
- [ ] Verify `main.py --self-test` passes on a clean clone
- [ ] Update the roadmap if the phase is complete

## Version numbering

Pre-1.0: minor version bumps for each phase (0.1.0, 0.2.0, …).
Post-1.0: follow semver — major for breaking changes, minor for features,
patch for fixes.