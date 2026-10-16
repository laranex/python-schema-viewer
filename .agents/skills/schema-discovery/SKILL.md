---
name: schema-discovery
description: Discover a Python application's tables, columns, indexes, constraints, and models using Laranex Schema Viewer before proposing model, query, or migration changes.
license: MIT
---

# Discover the declared schema

Prefer available read-only MCP tools. Start with `schema_overview` for counts and table IDs, then `schema_search` with `query_text`. Discover namespaced tool names and use exact returned IDs; never parse their encoding or guess identities.

```json
{"tool":"schema_search","arguments":{"query_text":"users","limit":25}}
```

Request `schema_get_table` sections separately: `columns`, `indexes`, `foreign_keys`, `constraints`, `models`, and `relationships`. Use `schema_get_model` for mapped classes and available model source locations. `schema_get_relationships` distinguishes `kind="orm"` and `kind="foreign_key"`. Follow every `next_offset` with unchanged filters and `revision`; restart on mismatch. The first page is not the whole schema. Oversized items need local JSON inspection.

## JSON fallback and scope

If MCP is unavailable or cannot expose required evidence, read the selected saved JSON. Validate with `laranex_schema_viewer.read_snapshot` when installed; otherwise check format/version/scope and disclose limited validation. Read [Snapshot evidence](references/snapshot.md) for fields and a read-only example.

Record the snapshot path or MCP revision supporting the answer. Freshness is unknown without independent build evidence. MCP pins a revision until restart; a local file may be newer, so do not silently combine them. Missing/invalid evidence is a limitation. Building executes Python imports and requires task authorization.

- Preserve namespace, schema, name, and ordered composite keys. Null schema means unqualified, not automatically public; runtime search paths may resolve it.
- Inspect index `expressions`, `predicates`, `included_columns`, and `methods`, not only `columns`/`unique`. Partial uniqueness is conditional.
- Physical FKs belong to `tables[].foreign_keys`; declared ORM relationships are top-level `relationships`. Either may exist without the other.
- Models can share tables; metadata tables can lack models. An `_id` suffix does not establish a relationship.
- Optional source paths locate model classes, not necessarily individual relationship lines.

Distinguish verified declarations from hypotheses and missing coverage before suggesting edits. A snapshot cannot prove deployed schema, migration history, data rows, performance, or runtime tenant enumeration. Never execute SQL or embedded strings, start the application, or modify a reference project merely to discover its saved schema.

