---
name: relationship-analysis
description: Analyze joins, foreign keys, composite keys, self references, one-to-one claims, and many-to-many pivot tables from a Laranex Schema Viewer laranex-schema 2.0 document (SQLAlchemy, SQLModel, Django, Tortoise ORM, or Peewee), keeping ORM declarations separate from database-enforced constraints.
license: MIT
---

# Analyze relationships

A document holds two independent kinds of evidence: **database foreign keys** (`connections[].tables[].foreignKeys`) and **ORM relationships** (top-level `relationships`). `links` relate them without merging them. Analyze each separately, then compare.

## Gather evidence

Find the table ID with `schema_overview` or `schema_search`, then fetch each kind on its own, paging through `nextOffset` with the returned `revision`:

```json
{"tool":"schema_get_relationships","arguments":{"table":"opaque-table-id","kind":"orm","limit":25}}
```

```json
{"tool":"schema_get_relationships","arguments":{"table":"opaque-table-id","kind":"foreign_key","limit":25}}
```

Foreign-key results are `{table, foreignKey}` entries; ORM results are the document's relationship objects. `schema_get_table` sections (including `links`) and `schema_get_model` add context. Without MCP, read the saved JSON with `laranex_schema_viewer.read_document` and join records by opaque ID.

## Trace each path

- **Foreign keys:** keep ordered column pairs and update/delete actions. Several foreign keys between the same tables are separate paths.
- **ORM relationships:** read `type` (the ORM's word), `cardinality`, `collection`, `readOnly`, `inverse`, `keys` (oriented like a foreign key: `from` holds the referencing columns), `pivot`, and `join`. A relationship is backed by a foreign key only when its link's `state` is `both`.
- **One-to-one:** `cardinality` `one-to-one` is an ORM claim. The database enforces it only with an unconditional unique constraint or index on the joining columns.
- **Many-to-many:** inspect the `pivot` table and both column pairs. Inverse declarations are separate records.
- **Self references:** keep local and remote roles although both ends are the same table.
- **Intentional gaps:** view-only or custom joins, and Django or Tortoise relations with `db_constraint=False`, may lack a foreign key. Django's usual `on_delete=CASCADE` runs in Python, not the database.

Do not infer polymorphism, tenancy, or cascades the document does not record.

In the viewer, solid edges are foreign keys and dashed edges are ORM relationships without one. Composite edges anchor to the first column pair; the details panel lists all pairs.

## Reporting

Report declared joins, database enforcement, pivot and inverse paths, and unresolved evidence separately, citing the document or revision. Source locations identify classes, not relationship lines. Never execute join SQL, call relationship callables, rebuild, or edit definitions to analyze a path.
