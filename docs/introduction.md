---
aside: false
---

<PackageIntroduction />

## From model definitions to a shared view

Laranex Schema Viewer extracts declared schema metadata from SQLAlchemy 2.x, SQLModel, Django, Tortoise ORM, and Peewee; see [supported ORMs](./orm-support.md). It saves a [laranex-schema 2.0](https://laranex.github.io/schema-viewer-ui/contract) JSON document, the format shared with the Laravel and Node.js schema viewers, that powers an interactive ERD and read-only MCP tools for coding agents. It can also read a database's catalogs, read-only, and reconcile them with your models.

Inspired by [Laravel Schema Viewer](https://github.com/laranex/laravel-schema-viewer), this Python package works independently of FastAPI, Flask, or any application server. Django support reads model metadata only and never starts the project.

## What you can explore

- Tables, columns, primary keys, foreign keys, unique constraints, and check constraints.
- Index expressions, PostgreSQL predicates, included columns, and index methods.
- ORM relationships, including self references and association tables.
- Separate graph layers for physical foreign keys and ORM declarations.
- Searchable table details, graph navigation, and portable JSON documents.

## How it works

1. **Build:** import trusted model definitions and extract metadata.
2. **Inspect:** open the saved document in the viewer.
3. **Ask:** connect Codex or Claude Code to the document through MCP.

Only the build step imports application modules. Neither the viewer nor MCP needs a running application or a database connection.

::: info Declared metadata, not a database audit
A declared document describes loaded Python definitions. It does not prove that migrations have run or that a deployed database matches those definitions. Model imports can execute application code; choose trusted modules with safe initialization.
:::

<LiveDemo />
