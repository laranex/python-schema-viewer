# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-10-17

### Changed

- New monochrome and navy color theme for the viewer and documentation site. The viewer follows the system light or dark preference and remembers the theme you pick.
- The documentation site opens in dark mode by default, and its landing page includes a 30-second "how it works" video.
- Viewer usability: selecting a table highlights its connections and fades unrelated tables, `/` focuses search, `Esc` clears search, selection, and focus, empty states are clearer, and the details panel shows a legend and keyboard shortcuts.

## [0.3.0] - 2026-10-17

### Added

- `build --orm` option and `orm` configuration key to choose the ORM adapter explicitly; `auto` (the default) detects it from the imported modules.
- Django ORM adapter (`--orm django`, `django` extra): tables, columns, keys, indexes, unique and check constraints, forward and reverse relationships, many-to-many join tables, multi-table inheritance, proxy and unmanaged models, and composite primary keys on Django 5.2+. Database connections are blocked during extraction.
- Tortoise ORM adapter (`--orm tortoise`, `tortoise` extra): tables, columns, keys with database `on_delete` actions, indexes including partial and PostgreSQL index types, unique and check constraints, forward, backward, and many-to-many relationships, and generated many-to-many join tables. Models are initialised with `Tortoise.init_models`, which does not connect. `--target module:TORTOISE_ORM` reads app labels and the SQL dialect from a Tortoise config.
- Peewee adapter (`--orm peewee`, `peewee` extra): tables, columns, composite primary keys, foreign keys with `on_delete`, `on_update`, and deferrable settings, all indexes from `fields_to_index()` including partial and expression indexes, field and table `CHECK`/`UNIQUE` constraints, backrefs, and `ManyToManyField` join tables. Base classes with only an implicit `id` are skipped with a diagnostic; raw SQL indexes and unparsed constraints are reported rather than guessed.
- Guides for Django, Tortoise ORM, and Peewee, a supported-ORM compatibility matrix, and agent skills that cover all five ORMs and the new diagnostic kinds.
- `build --settings` option and `settings` configuration key for the Django settings module; without options, `build` and `init` read it from `manage.py`.

### Changed

- PyPI publishing now runs the full test workflow on the release commit first, refuses versions already on PyPI, and never runs two publishes at once.
- SQLAlchemy is imported only when the SQLAlchemy adapter is used.
- Agent skills are reorganized as step-by-step workflows, with a tool reference and troubleshooting table for MCP, a diagnostic-kind table, and `init` and configuration coverage in the setup skill.

### Fixed

- Callable defaults without a module, such as `datetime.datetime.now`, were recorded as `None.datetime.now`; they are now named `datetime.now`.

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

[Unreleased]: https://github.com/laranex/python-schema-viewer/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/laranex/python-schema-viewer/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/laranex/python-schema-viewer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/laranex/python-schema-viewer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/laranex/python-schema-viewer/releases/tag/v0.1.0
