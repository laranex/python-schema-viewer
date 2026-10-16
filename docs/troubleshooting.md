# Troubleshooting and limitations

## Module cannot be imported

Check `--project-root`, your virtual environment, and application dependencies. Import a dedicated model package instead of an application entry point where possible. Recursive discovery imports every selected submodule and can trigger application initialization.

## Tables are missing

Confirm that all relevant modules load, use repeated `--module` options or `--recursive`, and inspect the snapshot's `diagnostics`. Compare actual runtime metadata with your expected table identities. Counting source declarations alone does not establish successful extraction.

Bare metadata targets describe tables; mapped model and ORM relationship discovery also needs loaded mappers.

## ORM relationships are missing

Check mapper configuration and referenced classes. A foreign key without a declared relationship is valid and should still appear in the foreign-key graph layer. Inspect layers independently.

## PostgreSQL details

The snapshot preserves declared schema names, type strings, timezone metadata, partial predicates, and expression indexes. It does not connect to PostgreSQL to verify deployed types, extensions, permissions, or actual index creation. Null schema remains unqualified.

## MCP cannot start or shows old information

Use absolute executable and snapshot paths. Install the `mcp` extra into the executable's environment. A missing, invalid, oversized, or unsupported-version snapshot fails startup. Rebuild separately, then restart the MCP server to load the new revision.

## What the MVP does not do

- Reflect a live database or compare migrations with production.
- Query application data or infer undeclared ORM relationships.
- Guarantee that metadata from failed imports is complete.
- Persist viewer layouts or generate schema migrations.

Snapshots can contain sensitive names, comments, and default expressions. Review them before sharing; publish synthetic examples only.
