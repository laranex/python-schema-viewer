---
name: schema-diagnostics
description: Investigate Laranex Schema Viewer diagnostics - extraction and inspection gaps, drift between models and database, missing tables or models, unresolved foreign keys and joins - and suspected schema problems such as missing indexes, using read-only MCP evidence and saved laranex-schema 2.0 JSON. Separates recorded facts from heuristic recommendations.
license: MIT
---

# Diagnose saved schema evidence

## Gather evidence

`schema_overview` reports counts, including diagnostics by category, severity and certainty and the number of entities with drift:

```json
{"tool":"schema_overview","arguments":{"limit":25}}
```

A table's diagnostics (about it, its columns, indexes, foreign keys and constraints) are its `diagnostics` section:

```json
{"tool":"schema_get_table","arguments":{"table":"opaque-table-id","section":"diagnostics","limit":25}}
```

Diagnostics about connections, models, relationships, comparisons or the document are in top-level `diagnostics[]` (`id`, `kind`, `category`, `severity`, `certainty`, `subject`, `message`) of the saved JSON; read it with `laranex_schema_viewer.read_document` when installed. If the file is unavailable, report that rather than rebuilding. Inspect affected items with `schema_search`, `schema_get_table`, `schema_get_model`, and `schema_get_relationships`, paging through `nextOffset` with the returned `revision`. The local file can be newer than the revision a running MCP server pinned; note which one you used.

`category` says what kind of evidence it is: `extraction` and `inspection` (what could not be read), `reconciliation` (one source has what the other lacks), `drift` (both have it and disagree), `lint` (advice). `certainty` `certain` is a fact read from metadata; `likely` rests on normalized type or SQL text and may be wrong.

## Diagnostic kinds

| Kind | Meaning and next step |
| --- | --- |
| `unresolved_foreign_key` | The target is not in the imported metadata; the subject is the foreign key. Check imports, namespace, and schema; the table may still exist in the database. |
| `secondary_selectable` | A join uses a selectable rather than a plain table. SQL is kept; the table identity may be missing. |
| `unresolved_join_pair` | An alias or expression prevented a stable endpoint. Read the kept join text and authorized source; do not fabricate pairs. |
| `shared_table` | Two models declare one table; only the first is extracted as the table. |
| `unmanaged_model` | Django `managed = False`: Django neither creates nor migrates the table. |
| `skipped_base_model` | Peewee base class with only an implicit `id`; not treated as a table. |
| `unsupported_relation`, `unsupported_constraint`, `unsupported_constraint_option`, `unsupported_index` | The declaration exists but cannot be represented (e.g. generic relations, exclusion constraints, raw SQL indexes). Its absence from the tables is not absence from the schema. |
| `uncompiled_expression`, `unrendered_expression` | SQL could not be rendered for the backend; ORM expression text was kept. |
| `column_drift`, `index_drift`, `foreign_key_drift`, `constraint_drift`, `table_drift` | Combined builds: the database and the models disagree; the entity holds the database's value and `drift` lists both. |
| `declared_only`, `database_only`, `model_without_table`, `table_without_model` | Combined builds: one source has what the other lacks. |
| `inspection_unavailable`, `write_privileges` | A database feature or connection was not inspected, or the role could write (the session stayed read-only). |
| `comparison_incomplete` | A stored comparison left differences out (its limit) or a side's coverage was incomplete; read its differences with `schema_compare` and treat it as partial evidence. |

Import and mapper failures stop a build and leave the previous document in place, which may then be stale. Zero diagnostics does not prove complete imports, migration parity, or sound design.

## Qualify suspected problems

Missing primary keys, unindexed foreign keys, type mismatches, duplicate-looking indexes, and joins without foreign keys are candidates, not findings. Before judging an index, check key `parts` (order, expressions), `predicate` and `dialectOptions`, `included`, `method`, `backs`, and constraints; engine behavior and workload may also matter. Do not drop indexes or add migrations only because columns look similar. A `one-to-one` cardinality does not enforce uniqueness, joins without foreign keys can be intentional, and unqualified definitions are not `public`.

## Reporting

List recorded diagnostics, then hypotheses, then proposed fixes, citing the document or revision. Fix only within the authorized scope and never weaken validation. Do not start the application, run SQL or migrations, or import recursively to diagnose. There is no check or hints command; `compare` reports differences between stored scopes but gates nothing.
