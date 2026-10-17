# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The laranex-schema 1.0 contract shared with laranex-schema-viewer-ui (`laranex_schema_viewer.contract`): Pydantic models, strict (producer) and lenient (reader) validation with the shared reference rules, canonical serialization and revisions, and the shared graph builder for links and derived links. The contract's valid, invalid and graph conformance fixtures run in the test suite.
- Unified serialization (`laranex_schema_viewer.unified.from_v1`): every adapter's declared metadata, and any version 1 snapshot, as a laranex-schema 1.0 document. Database and ORM defaults stay apart, index key parts keep their order with dialect options, keys are oriented like foreign keys, secondary tables become pivots, and inverse relationships are resolved from back_populates.
- `build` runs application code in an isolated extraction worker. `--isolation process` (the default) uses a subprocess with an allowlisted environment, so database URLs, passwords and tokens are withheld; it also gets a private `HOME` and `TMPDIR`, a timeout (`--timeout`, default 120 seconds), and cleanup of its whole process group. `--isolation container` runs Docker or Podman with no network, a read-only project and root filesystem, dropped capabilities and a version-checked image. A level that cannot be established fails the build instead of falling back.
- Best-effort guards in the worker: audit hooks refuse network access, subprocesses and writes outside allowed directories; file-backed `sqlite3` is refused; SQLAlchemy, Peewee and Tortoise connect calls raise. Blocked operations are printed as warnings. New configuration keys: `isolation`, `timeout`, `env_passthrough`, `env`, `allow_writes`, `container_image` and `container_runtime`. See [build security](docs/security.md).
- Live database introspection (`laranex_schema_viewer.introspection`) for PostgreSQL, MySQL, MariaDB and SQLite, as a laranex-schema 1.0 document with database provenance: native column types, defaults, identity and generated columns, enums, collations, comments, composite primary and foreign keys across schemas, unique and check constraints, and indexes with key order, nulls order, prefix lengths, collations, operator classes, methods, predicates, included columns and the constraints they back. It runs in its own worker, which never imports application code, receives the URL on stdin, and verifies a database-enforced read-only session. It refuses roles that could change the schema or data unless that is explicitly accepted. New `postgresql` and `mysql` extras install the drivers.
- Reconciliation (`laranex_schema_viewer.reconcile.combine`): declared models and a live database as one combined laranex-schema document. Database facts win; ORM defaults, models and relationships come from the declarations. Provenance is recorded on every table, column, key, index and constraint, and drift entries and diagnostics cover types, nullability, defaults, comments, primary keys, referential actions, index uniqueness and key parts, check expressions, missing and extra columns, tables, keys, indexes and constraints. Comparisons that rely on normalizing type names or SQL text are marked `likely`.
- `build --source database|combined` writes laranex-schema 1.0 documents from a live database, or from models reconciled with it, and `--format laranex-schema` does the same for declared builds. The database URL is read only from an environment variable (`--database-url-env`, default `LARANEX_SCHEMA_DATABASE_URL`), never from arguments or configuration. New options: `--schema`, `--database-connection` and `--allow-write-privileges`, with matching configuration keys. See [database introspection](docs/database.md).
- Several database connections: `[tool.laranex-schema-viewer.databases.NAME]` (`url_env`, `schemas`, `namespace`) or a repeatable `--database NAME=ENV_VAR`. Each connection is inspected by its own introspection worker that receives only its own URL, drivers can be mixed, and the output has one connection per name with connection-qualified table ids. Combined builds reconcile each connection with its declared namespace (by name, or the explicit `namespace` mapping); unmatched connections stay database-only or declared-only with `unavailable` coverage, and relationships across connections never call for foreign keys. The single-database options are unchanged. See [database introspection](docs/database.md#several-connections).
- `convert` turns a version 1 snapshot into a laranex-schema 1.0 document.
- `mcp` reads laranex-schema documents (declared, database or combined) with the same tools and field names, adding provenance, drift, coverage, index parts and relationship cardinality. Responses for version 1 snapshots are unchanged; a recorded set of 113 responses guards them.

### Changed

- The vendored viewer is laranex-schema-viewer-ui 0.2.0. With every connection shown, each connection is framed in its own region. With one connection shown, tables that its relationships reach on other connections appear as reference nodes. Tables with the same name on two connections are labelled with their connection, and deep links switch to the right connection. The viewer ships its own favicon, served from its manifest. The UI's validator now rejects a foreign key that crosses connections, which this package never writes.
- `serve` now shows the shared Laranex Schema Viewer UI (laranex-schema-viewer-ui 0.2.0, prebuilt and vendored, so no Node.js at runtime) in place of the HTML/SVG viewer. It opens declared, database and combined laranex-schema documents. A version 1 snapshot is converted explicitly with the same conversion as `convert`. The Content-Security-Policy, host check and loopback default are unchanged, and only the files the viewer's manifest lists are served.
- Builds no longer see the caller's full environment. Settings that need a variable must list it in `env_passthrough`, or get a placeholder from `[tool.laranex-schema-viewer.env]`. Import-time writes outside `allow_writes` now fail the build. Database connections, network access and subprocesses at import time fail too. Migration notes are in [build security](docs/security.md#upgrading).
- `reconcile.combine` reconciles every connection of a database document, and keeps declared connections it has no database for as declared-only, with an `inspection_unavailable` diagnostic (previously such documents failed validation). Without `connection=`, a database connection the models do not declare stays database-only instead of failing.
- `serve` and `mcp` explain that a laranex-schema document is not a version 1 snapshot, instead of reporting validation errors.

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
