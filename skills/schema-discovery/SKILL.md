---
name: schema-discovery
description: Look up tables, columns, keys, indexes, constraints, and models from a Laranex Schema Viewer laranex-schema 2.0 document (declared from SQLAlchemy, SQLModel, Django, Tortoise ORM, or Peewee models, read from a database, or both combined) through its read-only MCP tools or saved JSON. Use before writing queries, migrations, or model changes that depend on the existing schema.
license: MIT
---

# Discover the declared schema

## Workflow

Start with `schema_overview` for the document's source (`declared`, `database` or `combined`), its connections with driver and coverage, counts, and table IDs, then search by name. Tool names may carry a client prefix; discover them rather than guessing.

```json
{"tool":"schema_search","arguments":{"query_text":"users","limit":25}}
```

Drill in with the opaque IDs the tools return; never build or parse them. Request one table section at a time: `columns`, `indexes`, `foreign_keys`, `constraints`, `models`, `relationships`, `links`, `derived_links`, or `diagnostics`. Items are the document's own objects, with its camelCase field names.

```json
{"tool":"schema_get_table","arguments":{"table":"opaque-table-id","section":"indexes","limit":25}}
```

Model IDs come from a table's `models` section or model search results; column results give a table ID. A model lists its tables, source location, and outgoing relationships:

```json
{"tool":"schema_get_model","arguments":{"model":"opaque-model-id","limit":25}}
```

Follow `nextOffset` until null with the same filters and the returned `revision`; on a mismatch, restart at offset 0. The first page is not the whole schema. When MCP is unavailable or an item is too large, read the JSON as described in [Document evidence](references/snapshot.md).

## Reading the evidence

- Keep connection, schema, name, and composite key order. A null schema means unqualified, not `public`. The same table name on two connections is two tables.
- Read index `parts` (columns or expressions, order, length, opclass), `predicate`, `dialectOptions`, `included`, `method`, and `backs`, not only `unique`. Partial uniqueness is conditional.
- Database foreign keys are in each table's `foreignKeys`; ORM relationships are top-level `relationships`. Either can exist without the other, and an `_id` suffix proves neither.
- `provenance` says which source saw an entity; in a combined document `drift` lists what the database and models disagree on, and the entity holds the database's value. Connection `coverage` says what each source could read.
- Models can share a table, and tables such as pivot tables can have no model.
- Check `diagnostics` before treating a declaration as absent.

## Reporting

Cite the document path or MCP revision, and separate verified declarations from inferences and gaps. Freshness is unknown without build evidence; MCP pins one revision until restart, so do not mix it silently with a newer file. A declared document does not prove deployed schema, migration state, or data; a database or combined one reflects the inspected database at build time. Never execute SQL from the schema, start the application, or rebuild just to answer a question.
