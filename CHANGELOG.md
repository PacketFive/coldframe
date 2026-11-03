# Changelog

All notable changes to coldframe are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-02

The first release on PyPI.

### Added

- `coldframe new` scaffolds a project with a `pyproject.toml`, tests,
  `AGENTS.md` and an Azure Pipelines or GitHub Actions pipeline.
- `coldframe check` runs the format, lint, typecheck, test and audit gates
  and reports each as passed, failed, errored or skipped, with JUnit XML
  and GitHub annotation output.
- `coldframe fix`, `coldframe info` and `coldframe gates`.
- The format gate reads both the `Would reformat:` lines of older ruff and
  the `unformatted:` diagnostics of ruff 0.17 and later.

### Changed

- Renamed from pybootstrap, a name already taken on PyPI. The
  `pybootstrap` command, the `[tool.pybootstrap]` table and
  `PYBOOTSTRAP_FORCE_COLOR` keep working, so projects made with the
  earlier name need no change.
