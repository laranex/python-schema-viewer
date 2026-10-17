# Troubleshooting and limitations

## pip finds no matching version

`Could not find a version that satisfies the requirement laranex-schema-viewer (from versions: none)` usually means pip is running on Python older than 3.10, such as the `python3` bundled with macOS. Check with `python3 --version`, then install with Python 3.10+ or uv; see [installation](./installation.md).

## Modules declare models for several ORMs

`build` detects the ORM from the imported modules. When they contain models for more than one ORM, choose one with `--orm` or the `orm` configuration key, and narrow `--module` to that ORM's models.

## Django build stops with a connection error

Extraction blocks database connections. If an app's `ready()` hook or an imported module queries the database, the build stops with `Schema extraction must not connect to a database`. Move the query out of import time, or limit `--module` to apps that load without it. Do not add credentials to work around it.

## Module cannot be imported

Check `--project-root`, your virtual environment, and application dependencies. Import a dedicated model package instead of an application entry point where possible. Recursive discovery imports every selected submodule and can trigger application initialization.

## Tables are missing

Confirm that all relevant modules load, use repeated `--module` options or `--recursive`, and inspect the document's `diagnostics`. Compare actual runtime metadata with your expected table identities. Counting source declarations alone does not establish successful extraction.

Bare metadata targets describe tables; mapped model and ORM relationship discovery also needs loaded mappers.

## ORM relationships are missing

Check mapper configuration and referenced classes. A foreign key without a declared relationship is valid and should still appear in the foreign-key graph layer. Inspect layers independently.

## PostgreSQL details

A declared document preserves schema names, type strings, timezone metadata, partial predicates, and expression indexes. It does not connect to PostgreSQL to verify deployed types, extensions, permissions, or actual index creation. Null schema remains unqualified.

## MCP cannot start or shows old information

Use absolute executable and document paths. Install the `mcp` extra into the executable's environment. A missing, invalid, or oversized file, or one that is not a laranex-schema 2.x document, fails startup; files written by earlier versions of this package must be rebuilt. Rebuild separately, then restart the MCP server to load the new revision.

## What the MVP does not do

- Compare schemas between databases or tenant schemas, report migration status, or infer associations from foreign keys. The document has places for them, but this version leaves them empty.
- Query application data or infer undeclared ORM relationships.
- Guarantee that metadata from failed imports is complete.
- Persist viewer layouts or generate schema migrations.

Documents can contain sensitive names, comments, and default expressions. Review them before sharing; publish synthetic examples only.
