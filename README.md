# Laranex Schema Viewer

Open-source, framework-independent schema snapshots, an interactive ERD, and read-only MCP tools for SQLAlchemy 2.x and SQLModel. Inspired by [Laravel Schema Viewer](https://github.com/laranex/laravel-schema-viewer).

Build once from your Python model definitions. Browse or query the saved JSON without starting your application or accessing its database.

## Install

Requires Python 3.10+. In the application's environment, install the relevant extra:

```bash
pip install 'laranex-schema-viewer[sqlalchemy,mcp]'
# For SQLModel:
pip install 'laranex-schema-viewer[sqlmodel,mcp]'
```

This repository is an unreleased MVP. Until published, install from its local checkout:

```bash
pip install '/absolute/path/to/python-schema-viewer[sqlmodel,mcp]'
```

The base package needs Pydantic only. SQLAlchemy, SQLModel, and the MCP SDK are optional extras. The viewer's assets are bundled: no Node installation, CDN, frontend build, or web framework is required to use it.

## Build and serve

```bash
laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --module myapp.models \
  --recursive \
  --output /absolute/path/to/schema-viewer.json

laranex-schema-viewer serve --snapshot /absolute/path/to/schema-viewer.json
```

Open `http://127.0.0.1:8765`. Search by table, column, or model; select a table or edge for details; drag tables or the background; scroll to zoom; use Fit, Arrange, and Focus on neighbors. Toggle All columns to expand tables.

**Foreign keys and declared ORM relationships are separate.** Solid gray arrows represent referencing → referenced tables. Dashed teal arrows represent the declaring model → related model; their direction/uselist fields describe the ORM declaration, not a database guarantee. Many-to-many declarations traverse their secondary table. Both layers can be toggled independently, even when they join the same tables.

`--recursive` is opt-in and imports every submodule beneath the named package, including its `__init__.py` files. Use it only for a trusted model package. Any import or mapper configuration failure aborts the build and leaves an existing snapshot intact. Imports execute application code; the loader is not a security sandbox. The extractor itself does not create engines, query databases, instantiate models, or invoke callable column defaults. Mapper configuration can execute application-defined callbacks or relationship callables.

Neither `serve` nor `mcp` accepts model-loading options. They use saved snapshots entirely independently of the application. Rebuild after changing models; reload the viewer to see the new file. Restart MCP after rebuilding because each MCP process pins one snapshot revision.

## Selecting definitions

Repeat `--module` for explicit modules, or use `--target module:attribute` for a SQLAlchemy `MetaData`, declarative base, mapped class, or registry:

```bash
laranex-schema-viewer build --module myapp.user --module myapp.billing
laranex-schema-viewer build --target myapp.db:Base --module myapp.models
laranex-schema-viewer build --target myapp.db:metadata
```

A metadata-only target extracts tables and constraints. Supply model modules or a declarative registry/base to include mapped classes and relationships. For SQLModel, use your concrete model modules; passing only `SQLModel.metadata` does not discover model classes. Execute each build in a fresh process to avoid stale Python imports and ORM registries.

Explicit imports are required for completeness: definitions in modules you never import cannot appear in metadata. The reference backend's package initializer omits model modules, so its validation uses recursive discovery.

Programmatic extraction, in a dedicated build process:

```python
from pathlib import Path
from laranex_schema_viewer.adapters.sqlalchemy import SQLAlchemyAdapter
from laranex_schema_viewer import write_snapshot
from myapp.models import Base, User

document = SQLAlchemyAdapter(
    metadata={"application": Base.metadata},
    models=[User],
    project_root=Path.cwd(),
).extract()
write_snapshot(document, "schema-viewer.json")
```

## MCP for Codex and Claude Code

Install the `mcp` extra in the environment containing the executable. The implementation uses the official Python MCP SDK 2.x and its stdio transport. See [docs/mcp.md](docs/mcp.md) for configuration, query behavior, and verified documentation sources.

```bash
laranex-schema-viewer mcp --snapshot /absolute/path/to/schema-viewer.json
```

Five read-only tools are available: `schema_overview`, `schema_search`, `schema_get_table`, `schema_get_model`, and `schema_get_relationships`. There is no query execution, model loading, file selection by tool callers, or write operation.

## Snapshot contents and scope

Snapshots contain tables; columns and types; ordered primary keys; indexes with expressions, predicates, and included columns; foreign-key pairs/actions; unique/check constraints; mapped models; ORM relationship joins/direction/inverses; source locations; and diagnostics. JSON is validated, deterministically ordered, versioned, and written atomically. Callable defaults are described without execution. No volatile build timestamps are included.

The snapshot describes **declared ORM metadata**, not the deployed database. It cannot detect migration-only tables/indexes, database drift, or actual runtime tenant schemas. Unqualified table schemas remain `null`; they are not relabeled `public`. Namespace identifiers distinguish multiple metadata objects. Entity IDs are opaque strings; consumers must not parse their internal encoding.

The JSON contract is shipped as [schema.json](src/laranex_schema_viewer/schema.json). Further design and current limitations are in [docs/architecture.md](docs/architecture.md).

## Development and validation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m unittest discover -s tests -v
npm install
npm test
python -m build
```

The test suite covers SQLAlchemy/SQLModel extraction, independent graph layers, DOM interactions, CLI behavior, HTTP routing, snapshots, and real MCP stdio round trips. The browser smoke check requires Node 22+ and Chrome/Chromium:

```bash
python -m laranex_schema_viewer build \
  --project-root . --module tests.fixtures.sqlalchemy_models \
  --output .validation/example.json
python -m laranex_schema_viewer serve --snapshot .validation/example.json
# In another terminal:
VIEWER_URL=http://127.0.0.1:8765 CHROME_BIN=/path/to/chrome npm run test:browser
```

Reference-project validation writes only to this checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/validate_reference.py \
  /absolute/path/to/reference/backend
```

See [docs/validation.md](docs/validation.md) for the actual reference counts, completed checks, and environment restrictions. Reference snapshots are ignored and are not distributed with the package.

The [reference integration guide](docs/reference-integration.md) records the editable companion environment, recursive CLI discovery, local URL, MCP configuration, metadata comparisons, fixes, and latest executed test results.

## License

MIT. Laravel Schema Viewer design tokens retain Laranex's original notice; see [NOTICE](NOTICE) and [LICENSE](LICENSE).

