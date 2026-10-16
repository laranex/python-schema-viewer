---
name: python-schema-viewer
description: Install and configure Laranex Schema Viewer in a Python application - choose the ORM extra, build a snapshot from SQLAlchemy, SQLModel, Django, Tortoise ORM, or Peewee models, open the ERD, or connect the read-only MCP server. Use for adopting or rebuilding with the package, not for answering schema questions from an existing snapshot.
license: MIT
---

# Use Laranex Schema Viewer

The package imports model definitions once to save a JSON snapshot; the viewer and MCP server then read only that file. It needs no web-framework wiring, startup hooks, or database connection.

If a snapshot or MCP server already exists, answer schema questions from it (MCP tools, or `laranex_schema_viewer.read_snapshot` on the JSON) instead of rebuilding. Missing or invalid evidence is not permission to import application code.

## 1. Install

Use the application's environment and package manager. Add the extra for its ORM (`sqlalchemy`, `sqlmodel`, `django`, `tortoise`, or `peewee`) and `mcp` for agent access. Serving an existing snapshot needs no ORM extra.

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
- Config keys are `modules`, `targets`, `recursive`, `output`, `orm`, and `settings`; command-line flags override them.
- Write output outside read-only reference projects. Report the actual table, model, and diagnostic counts from the build output.

## 3. View and connect

```bash
laranex-schema-viewer serve --snapshot /path/to/schema-viewer.json   # http://127.0.0.1:8765
laranex-schema-viewer mcp --snapshot /path/to/schema-viewer.json
```

In the ERD, solid gray edges are database foreign keys and dashed teal edges are ORM relationships. Reload the viewer after a rebuild; restart MCP to pick up the new revision.

For MCP clients, configure a stdio server with the absolute executable path and arguments `mcp --snapshot /absolute/path/to/schema-viewer.json`, preserving unrelated settings. Skills do not register MCP or grant tool access.

## Limits

- No diff, check, export, or hints commands exist, and nothing reflects a live database.
- The server is local and unauthenticated; do not expose it or publish snapshots without authorization.
- Package contribution rules live in the package repository's `AGENTS.md`, not in this skill.
