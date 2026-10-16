---
aside: false
---

<PackageIntroduction />

## From model definitions to a shared view

Laranex Schema Viewer extracts declared schema metadata from SQLAlchemy 2.x and SQLModel. It produces a JSON snapshot that powers an interactive ERD and read-only MCP tools for coding agents.

Inspired by [Laravel Schema Viewer](https://github.com/laranex/laravel-schema-viewer), this Python package works independently of FastAPI, Flask, Django, or any application server.

## What you can explore

- Tables, columns, primary keys, foreign keys, unique constraints, and check constraints.
- Index expressions, PostgreSQL predicates, included columns, and index methods.
- ORM relationships, including self references and association tables.
- Separate graph layers for physical foreign keys and ORM declarations.
- Searchable table details, graph navigation, and portable JSON snapshots.

## How it works

1. **Build:** import trusted model definitions and extract metadata.
2. **Inspect:** open the saved snapshot in the viewer.
3. **Ask:** connect Codex or Claude Code to the snapshot through MCP.

Only the build step imports application modules. Neither the viewer nor MCP needs a running application or a database connection.

::: info Declared metadata, not a database audit
The snapshot describes loaded Python definitions. It does not prove that migrations have run or that a deployed database matches those definitions. Model imports can execute application code; choose trusted modules with safe initialization.
:::

<LiveDemo />
