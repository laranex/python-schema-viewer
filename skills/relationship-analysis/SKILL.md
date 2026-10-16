---
name: relationship-analysis
description: Analyze joins, foreign keys, composite keys, self references, one-to-one claims, and many-to-many join tables from a Laranex Schema Viewer snapshot (SQLAlchemy, SQLModel, Django, Tortoise ORM, or Peewee), keeping ORM declarations separate from database-enforced constraints.
license: MIT
---

# Analyze relationships

A snapshot holds two independent kinds of evidence: **database foreign keys** (`tables[].foreign_keys`) and **ORM relationships** (top-level `relationships`). Analyze each separately, then compare.

## Gather evidence

Find the table ID with `schema_overview` or `schema_search`, then fetch each kind on its own, paging through `next_offset` with the returned `revision`:

```json
{"tool":"schema_get_relationships","arguments":{"table":"opaque-table-id","kind":"orm","limit":25}}
```

```json
{"tool":"schema_get_relationships","arguments":{"table":"opaque-table-id","kind":"foreign_key","limit":25}}
```

`schema_get_table` sections and `schema_get_model` add context. Without MCP, read the saved JSON with `laranex_schema_viewer.read_snapshot` and join records by opaque ID.

## Trace each path

- **Foreign keys:** keep ordered column pairs and update/delete actions. Several foreign keys between the same tables are separate paths.
- **ORM relationships:** read `direction`, `uselist`, `viewonly`, `back_populates`, `primary_join`, `secondary_join`, `join_pairs`, and `secondary`. A relationship is backed by a foreign key only when its column pairs match one.
- **One-to-one:** `uselist=false` is an ORM claim. The database enforces it only with an unconditional unique constraint or index on the joining columns.
- **Many-to-many:** inspect the `secondary` join table and both join stages. Inverse declarations are separate records.
- **Self references:** keep local and remote roles although both ends are the same table.
- **Intentional gaps:** view-only or custom joins, and Django or Tortoise relations with `db_constraint=False`, may lack a foreign key. Django's usual `on_delete=CASCADE` runs in Python, not the database.

Do not infer polymorphism, tenancy, or cascades the snapshot does not record.

In the viewer, solid gray edges are foreign keys and dashed teal edges are ORM relationships. Composite edges anchor to the first column pair; the details panel lists all pairs.

## Reporting

Report declared joins, database enforcement, join-table and inverse paths, and unresolved evidence separately, citing the snapshot or revision. Source locations identify classes, not relationship lines. Never execute join SQL, call relationship callables, rebuild, or edit definitions to analyze a path.
