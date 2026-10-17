# Validation and limitations

This page summarizes what the automated checks cover and what they cannot establish. All checks use synthetic fixtures in `tests/fixtures/`; no application document or report is distributed with this repository.

## Automated checks

| Check | Command | Covers |
| --- | --- | --- |
| Python tests | `python -m unittest discover -s tests -v` | Extraction, the contract (validation, serialization, graph, comparison and migration rules against the shared fixtures), schema comparisons, documents, queries and MCP conformance, CLI, HTTP server, MCP stdio, skills |
| Browser tests | `LSV_BROWSER=1 python -m unittest tests.test_browser -v` | The served shared viewer in headless Chrome for declared, database, combined and two-connection documents: every table as a node, edges, source badge, coverage, diagnostics, a table's indexes, relationships, drift and provenance badges, theme, phone layout; no exceptions, console errors, CSP violations or external requests |
| Lint | `ruff check .` | Python errors and undefined names |
| Packaging | `python -m build` and `python scripts/check_wheel.py` | Wheel assets, the laranex-schema 2.0 JSON Schema, type marker, CLI entry point, isolated import |

CI runs the Python checks on Python 3.10 through 3.15, and the browser tests in headless Chrome against documents built from `tests.fixtures.sqlalchemy_models`, `tests.fixtures.drift_models` and `tests/databases/drift-sqlite.sql`.

## Fixture coverage

The SQLAlchemy and SQLModel fixtures cover metadata-only tables, declarative and imperative mappings, relationships with and without database foreign keys, association tables, self references, multiple foreign-key paths, ordered composite foreign keys, PostgreSQL index details (predicates, `INCLUDE`, methods, expressions), schema and metadata-namespace collisions, timezone-aware types, callable defaults, and mapper/import failures.

The Django fixture app covers forward, reverse, one-to-one, and many-to-many relationships with automatic and explicit `through` tables, `db_constraint=False`, multi-table inheritance, proxy and unmanaged models, composite primary keys (Django 5.2+), descending, expression, and conditional indexes, check and conditional unique constraints, callable defaults, manage.py settings detection, and blocked connections. The Tortoise fixtures cover generated and explicit `through` tables, `on_delete` actions, `db_constraint=False`, partial indexes, `Meta.constraints`, schemas, and a `TORTOISE_ORM` config target with PostgreSQL types. The Peewee fixtures cover composite keys, deferrable named foreign keys, backrefs, generated join tables, unique, descending, expression, partial, and raw SQL indexes, field and table constraints, base-model skipping, and unbound models. These were run with Django 4.2, 5.2, and 6.1, Tortoise ORM 1.1, and Peewee 3.19 and 4.5.

## The shared contract

`tests/fixtures/contract` is vendored unchanged from laranex-schema-viewer-ui by `scripts/sync_contract.py`, with a manifest of its source commit and every file's SHA-256; a test fails when a vendored file is edited. `tests/test_contract.py` checks the Python implementation against it independently of the TypeScript one: every valid document passes strict validation and serializes back to identical bytes, every invalid document fails with its expected rule, the stored links equal the Python graph builder's, the graph, comparison and migration conformance cases give the expected results (the 14 comparison cases byte for byte in canonical JSON), the comparisons stored in the shared valid documents are reproduced, and the Python shape definitions reproduce the shared JSON Schema. The inference cases are vendored for the milestone that computes them and are not run yet.

Comparison tests (`tests/test_comparison.py`, and `tests/test_comparison_databases.py` with the database URLs) cover configuration parsing, `*` expansion, overrides, limits and determinism, storing, the `compare` command (which must not open a database or import application code), builds over two SQLite connections, a combined document whose declared-only tables are left out, a large synthetic comparison, and real catalogs: PostgreSQL tenant schemas with planted drift (missing and extra tables, changed types, nullability, defaults, referential actions, check expressions, missing and extra indexes and constraints) and an identical tenant, and one application on PostgreSQL, MySQL and MariaDB. [Schema comparison](./comparison.md#accuracy-and-limits) lists the cross-driver differences those tests show the algorithm reports.

Document tests cover strict validation before atomic writes, deterministic output, refusal of other formats and versions, and size limits. Query tests cover exact IDs, bounded pagination, pinned revisions, response size limits, and the recorded MCP responses for every shared valid document (`tests/fixtures/mcp-conformance`).

The viewer itself is tested in the laranex-schema-viewer-ui repository. This repository's browser tests check that the vendored build works as served by `serve` for each document source.

## Validating your application

`scripts/validate_reference.py` compares a declared document against an application's loaded definitions without connecting to a database:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/validate_reference.py /absolute/path/to/application \
  --module myapp.models
```

It recursively inventories `table=True` declarations under the module, builds twice in fresh processes to confirm byte-identical output, compares columns, keys, indexes, constraints, and foreign keys against the loaded tables, queries every table and model through the MCP server, and confirms that no application files changed. `--mapper-tests` optionally runs a reviewed file of zero-fixture mapper test classes. Outputs default to `.validation/` and must stay outside the application.

This imports application code. Run it only on trusted code, and keep the resulting document and report private.

## Limitations

- Declared documents describe ORM metadata, not the deployed database; database and combined builds read the catalogs at build time. No document records migration status or data; comparisons cover only the tenant schemas an inspection read.
- Comparisons compare normalized SQL text for defaults, expressions and cross-driver types; equal values written differently are reported as `likely` differences.
- No line or branch coverage percentage is claimed.
- DOM tests use fixed viewport metrics; only the browser smoke test exercises real layout and input.
- Compatibility is tested only for the Python versions in CI and the dependency versions resolved there.
