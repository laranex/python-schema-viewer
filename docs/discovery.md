# Model discovery and extraction

## Explicit by default

Pass one or more modules with `--module`. Use `--recursive` to import each selected package's discovered submodules. Use `--target module:attribute` for a metadata object, declarative base, or registry.

```bash
laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --module app.accounts.models \
  --module app.billing.models \
  --recursive --output schema-viewer.json
```

Model imports execute trusted Python code. Recursive imports are restricted to the build process; serving a snapshot never discovers or reloads models.

## Snapshot structure

The versioned JSON document contains:

| Field | Meaning |
| --- | --- |
| `format`, `version` | Snapshot identity and format version |
| `scope` | `declared_metadata`, not a reflected database |
| `tables` | Columns, keys, indexes, and constraints |
| `models` | Mapped Python model identities and table links |
| `relationships` | Explicit ORM relationship declarations |
| `diagnostics` | Recorded extraction warnings and errors |

IDs are opaque. Use returned IDs rather than constructing them from table names. Null `schema_name` means unqualified, not automatically PostgreSQL `public`.

## Check completeness

Compare extracted table identities with the metadata objects populated by the same imports. Count static declarations separately from loaded tables: failed imports, duplicate metadata, conditional definitions, and mapper errors can explain differences.

A saved snapshot is a point-in-time artifact. Rebuild after changing models, then restart MCP to load the new revision.
