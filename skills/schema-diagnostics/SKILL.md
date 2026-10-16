---
name: schema-diagnostics
description: Investigate Laranex Schema Viewer extraction diagnostics, missing tables or models, unresolved foreign keys and joins, and suspected schema problems such as missing indexes, using read-only MCP evidence and saved JSON. Separates recorded facts from ORM-specific recommendations.
license: MIT
---

# Diagnose saved schema evidence

## Gather evidence

`schema_overview` reports counts, including the number of diagnostics:

```json
{"tool":"schema_overview","arguments":{"limit":25}}
```

MCP has no tool for diagnostic messages. Read top-level `diagnostics[]` (`kind`, `subject`, `message`) from the saved JSON, using `laranex_schema_viewer.read_snapshot` when installed; if the file is unavailable, report that rather than rebuilding. Inspect affected items with `schema_search`, `schema_get_table`, `schema_get_model`, and `schema_get_relationships`, paging through `next_offset` with the returned `revision`. The local file can be newer than the revision a running MCP server pinned; note which one you used.

## Diagnostic kinds

| Kind | Meaning and next step |
| --- | --- |
| `unresolved_foreign_key` | The target is not in the imported metadata. Check imports, namespace, and schema; the table may still exist in the database. |
| `secondary_selectable` | A join uses a selectable rather than a plain table. SQL is kept; the table identity may be missing. |
| `unresolved_join_pair` | An alias or expression prevented a stable endpoint. Read the kept join text and authorized source; do not fabricate pairs. |
| `shared_table` | Two models declare one table; only the first is extracted as the table. |
| `unmanaged_model` | Django `managed = False`: Django neither creates nor migrates the table. |
| `skipped_base_model` | Peewee base class with only an implicit `id`; not treated as a table. |
| `unsupported_relation`, `unsupported_constraint`, `unsupported_constraint_option`, `unsupported_index` | The declaration exists but cannot be represented (e.g. generic relations, exclusion constraints, raw SQL indexes). Its absence from the tables is not absence from the schema. |
| `uncompiled_expression`, `unrendered_expression` | SQL could not be rendered for the backend; ORM expression text was kept. |

Import and mapper failures stop a build and leave the previous snapshot in place, which may then be stale. Zero diagnostics does not prove complete imports, migration parity, or sound design.

## Qualify suspected problems

Missing primary keys, unindexed foreign keys, type mismatches, duplicate-looking indexes, and joins without foreign keys are candidates, not findings. Before judging an index, check key order, predicates, expressions, included columns, methods, and constraints; engine behavior and workload may also matter. Do not drop indexes or add migrations only because columns look similar. `uselist=false` does not enforce one-to-one, joins without foreign keys can be intentional, and unqualified definitions are not `public`.

## Reporting

List recorded diagnostics, then hypotheses, then proposed fixes, citing the snapshot or revision. Fix only within the authorized scope and never weaken validation. Do not start the application, run SQL or migrations, or import recursively to diagnose. There is no check, hints, or diff command.
