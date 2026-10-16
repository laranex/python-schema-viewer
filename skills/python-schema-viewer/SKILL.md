---
name: python-schema-viewer
description: Install and configure Laranex Schema Viewer in a Python application, build SQLAlchemy or SQLModel snapshots, open the ERD, or connect its read-only MCP server. Use for package adoption rather than routine schema analysis.
license: MIT
---

# Use Laranex Schema Viewer

Inspect the application's dependencies and ORM module structure before choosing extras or imports. The package is framework-independent; do not add FastAPI wiring, startup hooks, or database connections to use it.

## Read existing evidence

Prefer available read-only MCP tools for schema facts. Start with `schema_overview` and `schema_search` (`query_text`), then use exact opaque IDs with `schema_get_table`, `schema_get_model`, and `schema_get_relationships`. Follow `next_offset` with unchanged filters and `revision`; restart on revision mismatch. Discover client-namespaced tool names rather than guessing prefixes.

```json
{"tool":"schema_overview","arguments":{"limit":25}}
```

If MCP is unavailable, read the selected saved JSON with `laranex_schema_viewer.read_snapshot`. Check format `laranex-schema-viewer`, version 1, scope `declared_metadata`; its top-level collections are `tables`, `models`, `relationships`, and `diagnostics`. Report unknown freshness. Saved metadata does not prove deployed state. Missing/invalid evidence is not permission to import application code.

## Build and view

Use the application's environment and package manager. Install the `sqlalchemy` or `sqlmodel` extra, adding `mcp` for MCP use.

```bash
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp]'
laranex-schema-viewer build --project-root /path/to/application --module myapp.models --recursive --output /path/to/schema-viewer.json
laranex-schema-viewer serve --snapshot /path/to/schema-viewer.json
laranex-schema-viewer mcp --snapshot /path/to/schema-viewer.json
```

Build only when model loading is within the authorized task. `--recursive` imports every submodule, including initializers; omit it for explicit-module builds. Repeat `--module` or use `--target module:attribute` for selected metadata, bases, or registries. Metadata alone does not discover all classes. Imports and mapper callbacks can have side effects; never start the ASGI app or run migrations just to build. Keep outputs outside read-only reference projects and measure runtime counts.

`serve` opens a local ERD at `http://127.0.0.1:8765`. Search, inspect table/edge details, drag, pan/zoom, and focus. Solid gray edges are FKs; dashed teal edges are ORM declarations. Keep layers separate, including association-table paths. Viewer/MCP read saved snapshots only. Reload the viewer after rebuilding; restart MCP for its new pinned revision.

## Connect agents

Configure the client's stdio MCP server with the absolute executable and arguments `mcp --snapshot /absolute/path/to/schema-viewer.json`. Preserve unrelated configuration. Skills do not register MCP or grant tool access. Respect existing client trust settings.

Do not recommend nonexistent diff/check/export commands, a hints engine, live reflection, or production authentication. Do not expose the server or publish snapshots without authorization. Package contribution instructions belong to the package repository’s root `AGENTS.md`, not this application skill.

