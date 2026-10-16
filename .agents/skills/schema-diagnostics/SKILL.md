---
name: schema-diagnostics
description: Investigate Laranex Schema Viewer extraction diagnostics, missing coverage, unresolved joins, and possible SQLAlchemy or SQLModel constraint/index issues, separating recorded facts from recommendations.
license: MIT
---

# Diagnose saved schema evidence

Prefer available read-only MCP tools. Start with `schema_overview` counts and `schema_search` (`query_text`), then inspect exact IDs with `schema_get_table`, `schema_get_model`, and `schema_get_relationships`. Read index/constraint/FK/relationship table sections separately. Follow `next_offset` with unchanged filters and `revision`; restart on mismatch. Discover client-namespaced tool names.

```json
{"tool":"schema_overview","arguments":{"limit":25}}
```

MCP exposes a diagnostic count, but the MVP has no diagnostic-message tool. Read top-level `diagnostics` in the selected saved JSON, using `laranex_schema_viewer.read_snapshot` when installed. JSON is also the fallback when MCP is unavailable. Check format `laranex-schema-viewer`, version 1, scope `declared_metadata`; distinguish local-file revision from the pinned MCP revision. If the file is unavailable, report inaccessible evidence rather than inventing a tool or rebuilding automatically.

## Recorded extraction problems

- `unresolved_foreign_key`: inspect the target, imports, namespace/schema, and selected metadata before concluding a declaration is broken. An absent metadata target may still exist in a database.
- `secondary_selectable`: secondary join is a selectable, not a plain table. SQL is preserved but a table identity may be absent.
- `unresolved_join_pair`: alias/expression prevented a stable endpoint. Inspect retained join text and authorized source; do not fabricate pairs.

Records contain `kind`, `subject`, and `message`, without severity/certainty fields. Import/mapper failures abort builds and preserve previous snapshots, which can then be stale. Zero diagnostics does not prove complete imports, migration parity, or a sound schema design.

## Qualify analysis candidates

Missing PKs, apparently unindexed FKs, type mismatches, duplicate-looking indexes, and joins without FKs are analysis candidates, not built-in hints. Cite the snapshot facts and missing evidence. Check ordered keys, predicates, expressions, INCLUDE columns, methods, and unique/check constraints before judging an index. Engine behavior, workload, and application invariants may be needed.

Do not drop indexes or add migrations solely because columns look similar. Scalar ORM behavior does not enforce one-to-one uniqueness; joins without FKs can be intentional. Unqualified tenant definitions must not become public schemas or evidence of runtime tenant enumeration. Saved metadata cannot prove deployed database state.

Report recorded extraction problems separately from hypotheses and proposed fixes, including snapshot/revision and unknown freshness. Investigate or repair only within authorized scope; do not weaken validation. Do not run startup, SQL, migrations, recursive imports, or publish evidence just to diagnose it. The MVP has no dedicated schema-check/hints/diff CLI command.

