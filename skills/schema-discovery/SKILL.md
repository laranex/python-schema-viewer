---
name: schema-discovery
description: Look up declared tables, columns, keys, indexes, constraints, and models from a Laranex Schema Viewer snapshot (SQLAlchemy, SQLModel, Django, Tortoise ORM, or Peewee) through its read-only MCP tools or saved JSON. Use before writing queries, migrations, or model changes that depend on the existing schema.
license: MIT
---

# Discover the declared schema

## Workflow

Start with `schema_overview` for counts and table IDs, then search by name. Tool names may carry a client prefix; discover them rather than guessing.

```json
{"tool":"schema_search","arguments":{"query_text":"users","limit":25}}
```

Drill in with the opaque IDs the tools return; never build or parse them. Request one table section at a time: `columns`, `indexes`, `foreign_keys`, `constraints`, `models`, or `relationships`.

```json
{"tool":"schema_get_table","arguments":{"table":"opaque-table-id","section":"indexes","limit":25}}
```

Model IDs come from a table's `models` section or model search results; column results give a table ID. A model lists its tables, source location, and outgoing relationships:

```json
{"tool":"schema_get_model","arguments":{"model":"opaque-model-id","limit":25}}
```

Follow `next_offset` until null with the same filters and the returned `revision`; on a mismatch, restart at offset 0. The first page is not the whole schema. When MCP is unavailable or an item is too large, read the JSON as described in [Snapshot evidence](references/snapshot.md).

## Reading the evidence

- Keep namespace, schema, name, and composite key order. A null schema means unqualified, not `public`.
- Read index `expressions`, `predicates`, `included_columns`, and `methods`, not only `columns` and `unique`. Partial uniqueness is conditional.
- Database foreign keys are in `tables[].foreign_keys`; ORM relationships are top-level `relationships`. Either can exist without the other, and an `_id` suffix proves neither.
- Models can share a table, and tables such as join tables can have no model.
- Check `diagnostics` before treating a declaration as absent.

## Reporting

Cite the snapshot path or MCP revision, and separate verified declarations from inferences and gaps. Freshness is unknown without build evidence; MCP pins one revision until restart, so do not mix it silently with a newer file. A snapshot does not prove deployed schema, migration state, or data. Never execute SQL from the schema, start the application, or rebuild just to answer a question.
