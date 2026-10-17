# Model discovery and extraction

## Choosing modules

`build` uses `--module` or `--target` options first, then the `[tool.laranex-schema-viewer]` section of `pyproject.toml`, which `laranex-schema-viewer init` can create. Without either, it detects a single `models` package or `models.py` and imports it recursively; see the [CLI reference](./cli.md#build). Name modules explicitly when your models live elsewhere or detection finds several candidates.

The ORM is detected from the imported modules: SQLAlchemy metadata or mapped classes, Tortoise models, or Peewee models. If modules declare models for several ORMs, pass `--orm`. Django is selected by a settings module instead, because Django must be configured before its models can be imported; see [Django](./django.md).

Pass one or more modules with `--module`. Use `--recursive` to import each selected package's discovered submodules. Use `--target module:attribute` for a metadata object, declarative base, or registry.

```bash
laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --module app.accounts.models \
  --module app.billing.models \
  --recursive --output schema-viewer.json
```

Model imports execute trusted Python code. Recursive imports are restricted to the build process; serving a document never discovers or reloads models.

## Document structure

`build` writes a [laranex-schema 2.0](https://laranex.github.io/schema-viewer-ui/contract) document. Its fields are camelCase:

| Field | Meaning |
| --- | --- |
| `format`, `version` | `"laranex-schema"` and `"2.0"` |
| `source`, `generator` | `declared` for a build from models; the producing package and the ORM |
| `connections` | One per ORM namespace, each with its coverage and `tables`: columns, keys, indexes, foreign keys, and constraints |
| `models` | Mapped Python model identities and the tables they map |
| `relationships` | Explicit ORM relationship declarations |
| `links`, `derivedLinks` | Graph edges relating foreign keys and relationships, built by the shared rules |
| `diagnostics` | What extraction could not represent |
| `comparisons`, `migrations`, `inferredAssociations` | Empty in this version |

IDs are opaque. Use returned IDs rather than constructing them from table names. A null `schema` means unqualified, not automatically PostgreSQL `public`.

## Check completeness

Compare extracted table identities with the metadata objects populated by the same imports. Count static declarations separately from loaded tables: failed imports, duplicate metadata, conditional definitions, and mapper errors can explain differences.

A saved document is a point-in-time artifact. Rebuild after changing models, then restart MCP to load the new revision.
