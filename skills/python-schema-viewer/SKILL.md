---
name: python-schema-viewer
description: Install and configure Laranex Schema Viewer in a Python application - choose the ORM extra, build a laranex-schema 2.0 document from SQLAlchemy, SQLModel, Django, Tortoise ORM, or Peewee models (optionally reconciled with a read-only database inspection), open the ERD, or connect the read-only MCP server. Use for adopting or rebuilding with the package, not for answering schema questions from an existing document.
license: MIT
---

# Use Laranex Schema Viewer

The package imports model definitions once to save a laranex-schema 2.0 JSON document; the viewer and MCP server then read only that file. It needs no web-framework wiring or startup hooks, and a declared build opens no database connection.

If a document or MCP server already exists, answer schema questions from it (MCP tools, or `laranex_schema_viewer.read_document` on the JSON) instead of rebuilding. Missing or invalid evidence is not permission to import application code.

## 1. Install

Use the application's environment and package manager. Add the extra for its ORM (`sqlalchemy`, `sqlmodel`, `django`, `tortoise`, or `peewee`), `mcp` for agent access, and `postgresql` or `mysql` only for database inspection. Serving an existing document needs no ORM extra.

```bash
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp]'
```

## 2. Build

Build only when importing the application's models is within the authorized task. Imports and mapper callbacks run application code; never start the app or run migrations to build.

```bash
laranex-schema-viewer init        # optional: save defaults in pyproject.toml ([tool.laranex-schema-viewer])
laranex-schema-viewer build --module myapp.models --output /path/to/schema-viewer.json
```

- The ORM is detected from imported modules; pass `--orm` only when modules mix ORMs.
- `--recursive` imports every submodule, including package initializers. Prefer explicit `--module` values; `--target module:attribute` selects specific metadata, bases, or registries.
- **Django:** run from the directory with `manage.py`, or pass `--orm django --settings project.settings`. `--module` narrows to apps; database connections are blocked during the build.
- **Tortoise:** for several apps or a specific SQL dialect, pass `--orm tortoise --target module:TORTOISE_ORM`. Only app labels and the connection scheme are read.
- Config keys are `modules`, `targets`, `recursive`, `output`, `orm`, `settings`, and `source`; command-line flags override them.
- `--source database` reads a live database's catalogs instead, and `--source combined` reconciles both and records drift. The URL comes only from an environment variable (`LARANEX_SCHEMA_DATABASE_URL` by default) and should belong to a read-only role; never put it in arguments or `pyproject.toml`. Inspect a database only when the user authorized it.
- Every build writes laranex-schema 2.0 (default `schema-viewer.json`); `serve` and `mcp` read 2.x only, so rebuild files of any other format or version.
- Write output outside read-only reference projects. Report the actual table, model, and diagnostic counts from the build output.

## 3. View and connect

```bash
laranex-schema-viewer serve --snapshot /path/to/schema-viewer.json   # http://127.0.0.1:8765
laranex-schema-viewer mcp --snapshot /path/to/schema-viewer.json
```

In the ERD, solid edges are database foreign keys (accented when an ORM relationship uses them) and dashed edges are ORM relationships without a foreign key. Reload the viewer after a rebuild; restart MCP to pick up the new revision.

For MCP clients, configure a stdio server with the absolute executable path and arguments `mcp --snapshot /absolute/path/to/schema-viewer.json`, preserving unrelated settings. Skills do not register MCP or grant tool access.

## Limits

- No diff, check, export, or hints commands exist; comparisons, migration status and inferred associations are not produced yet (their document arrays are empty).
- The server is local and unauthenticated; do not expose it or publish documents without authorization.
- Package contribution rules live in the package repository's `AGENTS.md`, not in this skill.
