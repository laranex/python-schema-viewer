---
name: relationship-analysis
description: Analyze SQLAlchemy, SQLModel, Django, Tortoise ORM, and Peewee joins, composite foreign keys, self references, and association tables with Laranex Schema Viewer MCP tools or JSON, separating ORM declarations from database constraints.
license: MIT
---

# Analyze relationships

Prefer available read-only MCP tools. Find exact IDs through `schema_overview` or `schema_search` (`query_text`), then request each kind independently. Discover namespaced tool names rather than assuming prefixes.

```json
{"tool":"schema_get_relationships","arguments":{"table":"opaque-table-id","kind":"orm","limit":25}}
```

```json
{"tool":"schema_get_relationships","arguments":{"table":"opaque-table-id","kind":"foreign_key","limit":25}}
```

Follow `next_offset` with unchanged filters and `revision`; restart on mismatch. `schema_get_table` exposes separate FK, relationship, index, constraint, and model sections. `schema_get_model` includes outgoing declarations.

If MCP is unavailable, read the selected saved JSON with `laranex_schema_viewer.read_snapshot` when installed. Check format `laranex-schema-viewer`, version 1, scope `declared_metadata`. Physical FKs are `tables[].foreign_keys`; ORM declarations are top-level `relationships`. Join opaque IDs to tables/models. Record freshness limitations and do not silently mix JSON with an older pinned MCP revision. Metadata is not deployed database state.

## Trace each path

Retain ordered FK source/target pairs and update/delete rules. For ORM declarations inspect `direction`, `uselist`, `viewonly`, `back_populates`, `primary_join`, `secondary_join`, `join_pairs`, and `secondary`. Compare actual join-column pairs with constraints; matching table names alone cannot establish FK backing. Keep multiple FK paths between the same tables distinguishable.

Mapper direction and `uselist=false` do not prove database-enforced one-to-one cardinality. Check unconditional unique constraints/indexes and nullability. Partial uniqueness is conditional; view-only/custom joins may intentionally have no FK. ORM cascade behavior and FK action rules are different evidence; do not invent ORM cascade details absent from the snapshot.

For `MANYTOMANY`, inspect the secondary table and both join stages. Back-populated inverse declarations remain separate records. Self references retain local/remote roles even when both endpoints identify the same table. Do not infer polymorphism or tenant schemas from names.

In the ERD, inspect solid gray FKs and dashed teal ORM edges independently, including overlapping endpoints and secondary paths. All composite pairs remain in details although graph edges anchor to the first pair. Visibility controls do not change saved definitions.

Report declared joins, physical enforcement, association/inverse paths, and unresolved evidence separately. Source locations identify model classes; inspect authorized source rather than inventing relationship lines. Never execute join SQL, invoke relationship callables, rebuild models, or edit application definitions solely to analyze a saved path.

