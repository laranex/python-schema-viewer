# Validation and limitations

This page summarizes what the automated checks cover and what they cannot establish. All checks use synthetic fixtures in `tests/fixtures/`; no application snapshot or report is distributed with this repository.

## Automated checks

| Check | Command | Covers |
| --- | --- | --- |
| Python tests | `python -m unittest discover -s tests -v` | Extraction, snapshots, queries, CLI, HTTP server, MCP stdio, skills |
| Browser tests | `LSV_BROWSER=1 python -m unittest tests.test_browser -v` | The served shared viewer in headless Chrome for declared (version 1 and laranex-schema), database and combined documents: every table as a node, edges, source badge, coverage, diagnostics, a table's indexes, relationships, drift and provenance badges, theme, phone layout; no exceptions, console errors, CSP violations or external requests |
| Lint | `ruff check .` | Python errors and undefined names |
| Packaging | `python -m build` and `python scripts/check_wheel.py` | Wheel assets, JSON Schema, type marker, CLI entry point, isolated import |

CI runs the Python checks on Python 3.10 through 3.15, and the browser tests in headless Chrome against snapshots built from `tests.fixtures.sqlalchemy_models`, `tests.fixtures.drift_models` and `tests/databases/drift-sqlite.sql`.

## Fixture coverage

The SQLAlchemy and SQLModel fixtures cover metadata-only tables, declarative and imperative mappings, relationships with and without database foreign keys, association tables, self references, multiple foreign-key paths, ordered composite foreign keys, PostgreSQL index details (predicates, `INCLUDE`, methods, expressions), schema and metadata-namespace collisions, timezone-aware types, callable defaults, and mapper/import failures.

The Django fixture app covers forward, reverse, one-to-one, and many-to-many relationships with automatic and explicit `through` tables, `db_constraint=False`, multi-table inheritance, proxy and unmanaged models, composite primary keys (Django 5.2+), descending, expression, and conditional indexes, check and conditional unique constraints, callable defaults, manage.py settings detection, and blocked connections. The Tortoise fixtures cover generated and explicit `through` tables, `on_delete` actions, `db_constraint=False`, partial indexes, `Meta.constraints`, schemas, and a `TORTOISE_ORM` config target with PostgreSQL types. The Peewee fixtures cover composite keys, deferrable named foreign keys, backrefs, generated join tables, unique, descending, expression, partial, and raw SQL indexes, field and table constraints, base-model skipping, and unbound models. These were run with Django 4.2, 5.2, and 6.1, Tortoise ORM 1.1, and Peewee 3.19 and 4.5.

Snapshot tests cover atomic writes, deterministic output, JSON Schema conformance, older-version compatibility, size limits, and reference validation. Query tests cover exact IDs, bounded pagination, pinned revisions, and response size limits.

The viewer itself is tested in the laranex-schema-viewer-ui repository. This repository's browser tests check that the vendored build works as served by `serve` for each document source.

## Validating your application

`scripts/validate_reference.py` compares a snapshot against an application's loaded definitions without connecting to a database:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/validate_reference.py /absolute/path/to/application \
  --module myapp.models
```

It recursively inventories `table=True` declarations under the module, builds twice in fresh processes to confirm byte-identical output, compares columns, keys, indexes, constraints, and foreign keys against the loaded tables, queries every table and model through the MCP server, and confirms that no application files changed. `--mapper-tests` optionally runs a reviewed file of zero-fixture mapper test classes. Outputs default to `.validation/` and must stay outside the application.

This imports application code. Run it only on trusted code, and keep the resulting snapshot and report private.

## Limitations

- Snapshots describe declared ORM metadata, not the deployed database. They cannot detect migration-only objects, drift, runtime tenant schemas, or data.
- No line or branch coverage percentage is claimed.
- DOM tests use fixed viewport metrics; only the browser smoke test exercises real layout and input.
- Compatibility is tested only for the Python versions in CI and the dependency versions resolved there.
