# Validation and limitations

This page summarizes what the automated checks cover and what they cannot establish. All checks use synthetic fixtures in `tests/fixtures/`; no application snapshot or report is distributed with this repository.

## Automated checks

| Check | Command | Covers |
| --- | --- | --- |
| Python tests | `python -m unittest discover -s tests -v` | Extraction, snapshots, queries, CLI, HTTP server, MCP stdio, skills |
| Graph and DOM tests | `npm test` | FK/ORM edge layers, search, focus, layout, rendering a saved snapshot |
| Browser smoke test | `npm run test:browser` | Real Chromium rendering, layer toggles, selection, pointer dragging |
| Lint | `ruff check .` | Python errors and undefined names |
| Packaging | `python -m build` and `python scripts/check_wheel.py` | Wheel assets, JSON Schema, type marker, CLI entry point, isolated import |

CI runs the Python checks on Python 3.10 through 3.15, and the viewer checks against a snapshot built from `tests.fixtures.sqlalchemy_models`.

## Fixture coverage

The SQLAlchemy and SQLModel fixtures cover metadata-only tables, declarative and imperative mappings, relationships with and without database foreign keys, association tables, self references, multiple foreign-key paths, ordered composite foreign keys, PostgreSQL index details (predicates, `INCLUDE`, methods, expressions), schema and metadata-namespace collisions, timezone-aware types, callable defaults, and mapper/import failures.

Snapshot tests cover atomic writes, deterministic output, JSON Schema conformance, older-version compatibility, size limits, and reference validation. Query tests cover exact IDs, bounded pagination, pinned revisions, and response size limits.

Viewer tests cover independent FK/ORM visibility, search, selection, focus, edge details, column expansion, zoom, dragging, theme, and rendering schema strings as text.

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
