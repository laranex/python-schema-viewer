# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.1] - 2026-10-19

### Fixed

- MCP: string arguments reach the tools exactly as sent. The MCP SDK parsed every string argument of an optional parameter (`table`, `revision`, `comparison`, `baseline`, `target`, and the `schema_compare` difference filters) as JSON first, so a JSON-array table ID such as `["default",null,"users"]`, the form SQLAlchemy, Django, Tortoise and database builds use, failed validation in `schema_get_relationships`, and a string such as `"null"` silently meant no filter. Such strings are now looked up like any other (an unknown ID is a tool error; an unknown `object_type`, `change`, `severity` or `certainty` is rejected by the tool schema). Tool names, input schemas, responses and error messages are unchanged; protocol tests over stdio cover single- and multi-connection documents.

## [0.6.0] - 2026-10-19

**Upgrading:** `serve --host` now refuses a host that is not loopback (`127.0.0.0/8`, `::1`, `localhost`) with a usage error (exit 2). To reach the viewer from another machine, keep it on `127.0.0.1` behind an authenticated HTTPS reverse proxy or an access-controlled tunnel; see [serve](docs/cli.md#serve).

### Added

- `laranex-schema-viewer scan`: finds schema snapshots by content in the Git index (staged bytes, also files tracked before they were ignored), the files Git would add, build artifact directories and, with `--history`, every reachable version, judges them against a path and SHA-256 allowlist (`.laranex-schema-allowlist.json` by default), prints a count summary and writes the `laranex-schema-scan` 1.0 report on request. Exit codes 0 clean, 1 confirmed, 2 failed or incomplete, 3 uncertain. A standard-library port of laranex-schema-viewer-ui 0.4.0's scanner (`laranex_schema_viewer.contract.scan`), checked against all 45 shared scan conformance cases and the shared report and allowlist JSON Schemas; on this repository its report is byte-identical to `contract/scan.mjs`'s. See [scan](docs/cli.md#scan).
- A pre-commit framework hook, `laranex-schema-scan` (`.pre-commit-hooks.yaml`), and documented plain Git hook and CI recipes.
- `init` appends an idempotent, commented block to `.gitignore` with `/schema-viewer.json`, `/schema-viewer.html` and the configured output, and reports what it added.

### Security

- `build` refuses, before extracting or writing anything, an output path that the Git index tracks (exit 1, with a `git rm --cached` hint), and warns on stderr when the output is in a Git work tree and not ignored.
- CI scans the repository, the unpacked wheel and sdist (no schema documents), the built documentation site (only the allowlisted synthetic demo) and, in the release workflow, the distributions it publishes; any non-zero exit fails, and the report is kept for 7 days on failure. The repository's own allowlist (`.laranex-schema-allowlist.json`, written by `scripts/write_allowlist.py` from an explicit directory list) lists the synthetic demo document, its published copy and two MCP recordings that embed synthetic documents; a test keeps it current.

- Every viewer response, including the 403 (host not allowed), 404, 501 and 503 (document unavailable or invalid) errors and the HTTP parser's own errors, is sent with `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and the Content-Security-Policy. Error bodies are plain text with only the status, never schema data.
- `serve --unsafe-allow-remote` is the only way to bind a non-loopback host. It is never on by default, has no configuration key, and prints a warning on every start: no authentication, no TLS, anyone who can reach the address can read the schema, and the `Host` check is not access control.
- The sdist ships only what builds the wheel: the package source, the build hook, `ui.lock.json`, `ui.stamp.json`, the viewer and the two runtime JSON Schemas, and the top-level notices. It no longer contains the tests, the shared contract fixtures (`tests/fixtures/contract`), the MCP conformance recordings, the docs or the documentation demo document. The build hook checks the runtime assets it packages, unchanged in strictness, so a wheel still builds from the sdist offline without Node.js; `scripts/check_wheel.py` also checks the sdist's contents.
- `.gitignore` ignores `/schema-viewer.json` and the viewer's offline download `/schema-viewer.html` at the repository root.

### Changed

- **Breaking:** `serve` refuses non-loopback hosts unless `--unsafe-allow-remote` is given (see above).
- `SECURITY.md` adds a who-enforces-what table, why snapshots and exports are sensitive, protecting every delivery path, the development-only viewer, MCP, Git protection with `scan`, its limitations, incident response and the responsibility boundary. A new `CONTRIBUTING.md` covers synthetic fixtures, the allowlist and the pre-commit scan; the README has a security notice, and the gate page documents access and retention for gate reports.
- Documentation never recommends committing schema documents, even to a private repository; the viewer page lists every download (schema files, images, the offline HTML page, the whole document), says that each one made from a private schema is sensitive, states who is responsible for exported files, and describes the 0.3.0 viewer's limitations. The CI example keeps its artifacts for 7 days.
- The packaged viewer is laranex-schema-viewer-ui 0.4.0. It loads the snapshot only from its own origin and bypasses the HTTP cache; `serve` already serves `/snapshot.json` beside the viewer, so nothing changes for it. `scripts/fetch_ui.py` also installs and checks the new shared scan files (`scan-report.schema.json`, `allowlist.schema.json`, `synthetic.json` and `conformance/scan`) into `tests/fixtures/contract`.
- CI (`.github/workflows/tests.yml`) runs a small matrix on pull requests and branch pushes (Python 3.10 and 3.14; the databases on 3.14) and the full one on pushes to `main`, manual runs and the release gate (`publish.yml`): Python 3.10 to 3.15, and the databases on 3.10 and 3.14, so both SQLAlchemy 2.0 and 2.1 are tested against PostgreSQL, MySQL and MariaDB. Jobs check out without persisted credentials and have timeouts.

## [0.5.0] - 2026-10-18

**Upgrading:** every build now writes a laranex-schema 2.0 document, and `serve` and `mcp` read laranex-schema 2.x only. Version 1 snapshots and laranex-schema 1.0 documents are no longer read or converted: rebuild them with `laranex-schema-viewer build`. MCP responses changed shape; see [the response format](docs/mcp.md#response-format).

### Added

- The laranex-schema 2.0 contract of laranex-schema-viewer-ui 0.3.0: a typed schema model (`laranex_schema_viewer.model`, frozen dataclasses independent of the JSON), canonical serialization to and from 2.0 JSON (`contract.serialize`, the only code that knows the document's field names), a port of the shared validator with its messages (strict producers accept exactly 2.0; lenient readers accept 2.x and reject 1.x), the graph builder, diagnostic ids, and the migration status rules the validator recomputes. The distributed JSON Schema is the shared one (`laranex_schema_viewer/contract/schema.json`).
- The shared fixtures and conformance cases are fetched into `tests/fixtures/contract` from the pinned npm tarball by `scripts/fetch_ui.py`, which records their hashes in `ui.stamp.json` for a test to check. All 14 valid documents pass strict validation and serialize back to identical bytes, all 53 invalid ones fail with their expected rule, and the 15 graph, 14 comparison, 13 request, 11 coverage-gap, 9 override, 6 comparison-diagnostic, 4 store and 14 migration cases pass byte for byte.
- MCP conformance recordings for every shared valid document (`tests/fixtures/mcp-conformance`, written by `scripts/record_mcp_conformance.py`), so other implementations can check the response format.
- `schema_get_table` sections `links`, `derived_links` and `diagnostics`; `schema_search` also finds indexes, foreign keys and constraints by name.
- Schema comparisons ([docs](docs/comparison.md)): a port of the shared comparison algorithm (`contract.comparison.compare_scopes`) that passes the 14 shared comparison conformance cases byte for byte. Database and combined builds compute the comparisons configured in `[[tool.laranex-schema-viewer.comparisons]]` (baseline and target scopes, a target schema `*` for every inspected tenant schema, severity overrides, a difference limit, default 500) or named with `build --compare BASELINE=TARGET` and `--compare-limit`, and store them in the document; an incomplete comparison gets a `comparison_incomplete` diagnostic. Comparisons read only inspected catalog data; tenant schema names never appear in difference keys, while a table, column or index may be named like a compared schema (each key starts with its table's name, and a column key names a column of its table).
- The shared producer rules around a comparison, ported into `contract.comparison` from laranex-schema-viewer-ui (`d9db852`) and used by `build`, `compare` and `check`: `expand_comparison_requests` (requests by connection name, `*` targets, explicit pairs over wildcard ones), `coverage_gaps` and `coverage_gap_text`, `DIFFERENCE_FIELDS` and `validate_severity_overrides` (which `compare_scopes` now enforces), `comparison_diagnostics` (the exact `comparison_incomplete` diagnostic) and `store_comparisons`. Each expanded pair takes the `limit` and `overrides` of the request that gave it. The gate expands its requests and words coverage gaps with the same functions.
- `laranex-schema-viewer compare`: compares scopes of a saved document without a database or application imports, prints a summary or (`--json`) the comparison objects, and with `--write` stores them in the document atomically.
- `laranex-schema-viewer check`, the CI schema drift gate ([docs](docs/check.md)): a thin layer over `contract.gate.gate_document`, a port of the shared reference gate (`gateDocument` of laranex-schema-viewer-ui) whose 23 conformance cases it reproduces byte for byte, both directly and through `check --json`. Findings are the listed differences of the checked comparisons, `drift` and `reconciliation` diagnostics, and migration tracks behind, diverged or uninitialized, labelled `connection · schema` with the shared messages; extraction, lint and inspection warnings (such as `unresolved_foreign_key`) never fail it. It checks the comparisons named as `BASELINE=TARGET` (computed in memory first, never written), else the configured `[[tool.laranex-schema-viewer.comparisons]]` as stored by `build` (a `*` target expanded by the gate), else every stored comparison. Thresholds: `--severity` (default `warning`) and `--certainty` (default `certain`), also configurable in `[tool.laranex-schema-viewer.check]`. Exit code 0 passed, 1 a finding meets the thresholds, 2 the check is incomplete or invalid (an invalid, unreadable or unparseable document, a newer 2.x minor validated only leniently, a requested comparison not found, an incomplete comparison, a database inspection that did not read tables or columns completely or left a feature unavailable, a failed inspection, an unknown migration status, invalid arguments or configuration), which wins over 1. Prints a deterministic summary, or with `--json` a `laranex-schema-gate` 1.0 report; `--output` writes the report atomically; validator errors go to stderr, never into the report. It reads only the document: no database, application import or subprocess, and it never writes the document.
- The `laranex-schema-gate` 1.0 report format of laranex-schema-viewer-ui (with `requested` and `comparisons`): `contract.gate` ports `gateDocument`, `validateGateReport` and the report's shape, the package distributes `contract/gate-report.schema.json`, and `scripts/fetch_ui.py` fetches the shared gate conformance cases and valid and invalid gate reports: the 6 valid reports pass and are reproduced byte for byte from their documents, and the 13 invalid ones fail with their expected rule. `contract.comparison.coverage_gaps` is the one coverage rule comparisons and the gate share.
- The read-only MCP tool `schema_compare`: lists stored comparisons and pages through their differences, filtered by comparison id, baseline and target scope, object type, change, severity and certainty. The other tools' responses are unchanged; the MCP conformance recordings cover the new tool, with `comparison-byte-limit.json` for differences cut by the response limit.

### Changed

- **Breaking:** severity overrides in `[[tool.laranex-schema-viewer.comparisons]]` (keys `object_type`, `change`, `field`, `severity`) are mapped to the contract's overrides and checked with the shared validator. Its messages are reported with the TOML key names and prefixed with the entry (`[[tool.laranex-schema-viewer.comparisons]] entry 1 overrides[0].field applies only to changed differences, not missing`, `... overrides[0].object_type must be one of ...`, `... overrides[0] has unknown keys: level`); two overrides for the same `(object_type, change, field)` are now an error (`overrides[1] repeats overrides[0]`).
- **Breaking:** comparison requests that cannot be expanded fail `build` and `compare` with the shared messages and write nothing: an unknown connection, a `*` target whose inspection lists no schemas, a `*` target that matches no schema once the baseline's own scope is left out (previously an empty result), and a comparison listed more than once (`Requested comparison app:tenant_a → app:tenant_b is listed more than once.`). Every error is listed, not only the first. `check` fails with exit 2 on a comparison configured or named twice, and reports the other expansion errors in its gate report as before.
- Storing comparisons (`build`, `compare --write`) follows the shared `storeComparisons`: a stored comparison with the same scopes is replaced together with every diagnostic about it, whatever its kind, and only comparisons and diagnostics change.
- **Breaking:** `build` writes laranex-schema 2.0 for every source, by default to `schema-viewer.json`. ORM adapters and database introspection build the schema model directly; the extraction worker returns 2.0 JSON, which the parent validates strictly.
- **Breaking:** connection drivers use the contract's names: `postgresql` (no longer `pgsql`), `mysql`, `mariadb`, `sqlite`. Index options ORMs declare for PostgreSQL are keyed `postgresql` (Tortoise's `postgres` included).
- **Breaking:** MCP responses use the document's own objects with camelCase fields instead of version 1 projections, and one envelope: `revision`, `total`, `offset`, `limit`, `items` and `nextOffset` (formerly `next_offset`). `schema_overview` reports the source, generator, connections with driver and coverage, counts (including drift and diagnostics by category, severity and certainty) and table summaries; foreign-key results are `{table, foreignKey}` entries. Tool names and arguments are unchanged. See [MCP](docs/mcp.md#response-format).
- Declared indexes list each key part once: a column, with its order, or an expression. They previously listed a column key as an expression (`users.email`) and again as a column, which also made combined builds report index key parts as drift.
- An unresolved foreign key gets one diagnostic naming it, with its own message, and a model-level diagnostic labels the field it is about.
- The declared document records the detected ORM in `generator.orm` and its extensions namespace even when `--orm` is not given.
- The packaged viewer is laranex-schema-viewer-ui 0.3.0, which reads laranex-schema 2.x and shows stored comparisons (the Compare tab, with `#view=compare` deep links), migration status and inferred associations.
- Pydantic is no longer a dependency of the package (the `mcp` extra still brings it in).
- The shared viewer, the laranex-schema and gate report JSON Schemas and the shared fixtures and conformance cases are no longer committed: `scripts/fetch_ui.py` installs them from the published `laranex-schema-viewer-ui` npm tarball that `ui.lock.json` pins by version, URL and SHA-512 integrity, checking the hash before reading the tarball, rejecting unsafe members, and checking the package version, the viewer manifest's hashes and the contract files. A build hook (`hatch_build.py`) never downloads: it refuses to build when the assets are missing, changed or not the locked ones, and never builds an sdist or wheel from an unverified local tarball. The sdist includes the lock, the stamp and the assets, so wheels build from it offline without Node.js. CI fetches before testing and building; `scripts/check_wheel.py` checks the wheel's assets against the stamp. From a git checkout, run `python scripts/fetch_ui.py` before installing.

### Removed

- **Breaking:** the version 1 snapshot format: its Pydantic models (`laranex_schema_viewer.document`, `SchemaDocument`), its JSON Schema, `read_snapshot`/`write_snapshot`, the `convert` command, `build --format` and the `format` configuration key, and the conversion from version 1 (`unified.from_v1`). Use `laranex_schema_viewer.read_document` and `write_document`.
- Reading laranex-schema 1.0 documents.

Migration tracks and inferred associations are part of the 2.0 document but are not computed yet; their arrays are empty.

## [0.4.0] - 2026-10-17

**Upgrading:** `build` now imports your models in an isolated worker by default. The worker gets a filtered environment without credentials, and import-time database connections, network access, subprocesses and file writes fail. Projects whose models depend on those need configuration or small fixes; see [Upgrading from 0.3](docs/security.md#upgrading). There is no in-process mode. Version 1 snapshots stay the default output, and `serve` and `mcp` still open them.

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

- **Breaking:** builds no longer see the caller's full environment. Settings that need a variable must list it in `env_passthrough`, or get a placeholder from `[tool.laranex-schema-viewer.env]`. Import-time writes outside `allow_writes` now fail the build. Database connections, network access and subprocesses at import time fail too. Migration notes are in [build security](docs/security.md#upgrading).
- The vendored viewer is laranex-schema-viewer-ui 0.2.0. With every connection shown, each connection is framed in its own region. With one connection shown, tables that its relationships reach on other connections appear as reference nodes. Tables with the same name on two connections are labelled with their connection, and deep links switch to the right connection. The viewer ships its own favicon, served from its manifest. The UI's validator now rejects a foreign key that crosses connections, which this package never writes.
- `serve` now shows the shared Laranex Schema Viewer UI (laranex-schema-viewer-ui 0.2.0, prebuilt and vendored, so no Node.js at runtime) in place of the HTML/SVG viewer. It opens declared, database and combined laranex-schema documents. A version 1 snapshot is converted explicitly with the same conversion as `convert`. The Content-Security-Policy, host check and loopback default are unchanged, and only the files the viewer's manifest lists are served.
- In a combined document, a declared connection with no database configured stays declared-only, with `unavailable` database coverage and an `inspection_unavailable` diagnostic. A database connection that the models don't declare stays database-only.

### Removed

- The HTML/SVG viewer (`static/index.html`, `app.js`, `graph.js`, `styles.css`) and its Node.js tests. `serve` uses the vendored shared UI instead.

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

[Unreleased]: https://github.com/laranex/python-schema-viewer/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/laranex/python-schema-viewer/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/laranex/python-schema-viewer/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/laranex/python-schema-viewer/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/laranex/python-schema-viewer/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/laranex/python-schema-viewer/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/laranex/python-schema-viewer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/laranex/python-schema-viewer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/laranex/python-schema-viewer/releases/tag/v0.1.0
