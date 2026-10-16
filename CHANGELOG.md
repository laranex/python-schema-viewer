# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `build --orm` option and `orm` configuration key to choose the ORM adapter explicitly; `auto` (the default) detects it from the imported modules.
- Django ORM adapter (`--orm django`, `django` extra): tables, columns, keys, indexes, unique and check constraints, forward and reverse relationships, many-to-many join tables, multi-table inheritance, proxy and unmanaged models, and composite primary keys on Django 5.2+. Database connections are blocked during extraction.
- `build --settings` option and `settings` configuration key for the Django settings module; without options, `build` and `init` read it from `manage.py`.

### Changed

- PyPI publishing now runs the full test workflow on the release commit first, refuses versions already on PyPI, and never runs two publishes at once.
- SQLAlchemy is imported only when the SQLAlchemy adapter is used.

## [0.2.0] - 2026-10-16

### Added

- `init` command that asks for model modules, recursive import, and the snapshot path, then adds a `[tool.laranex-schema-viewer]` section to `pyproject.toml`.
- `build` reads defaults from `[tool.laranex-schema-viewer]` and, without options or configuration, detects a single `models` package or `models.py`.
- `serve` and `mcp` default `--snapshot` to the configured output path.
- uv installation instructions and a note that the `python3` bundled with macOS (3.9) is unsupported.

## [0.1.0] - 2026-10-16

### Added

- Snapshot extraction for SQLAlchemy 2.x and SQLModel, keeping database foreign keys separate from declared ORM relationships.
- Interactive HTML/SVG ERD viewer served locally from a saved snapshot.
- Read-only MCP server over stdio with bounded, paginated schema queries.
- Distributable agent skills, installable with `npx skills add laranex/python-schema-viewer`.
- Documentation site and security policy.

[Unreleased]: https://github.com/laranex/python-schema-viewer/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/laranex/python-schema-viewer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/laranex/python-schema-viewer/releases/tag/v0.1.0
