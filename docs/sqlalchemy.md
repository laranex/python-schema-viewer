# SQLAlchemy integration

The SQLAlchemy adapter reads SQLAlchemy 2.x metadata and mappers. It supports declarative bases, explicit `MetaData`, and mapper registries.

## Import mapped models

```bash
laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --module app.models --recursive \
  --output /absolute/path/to/schema-viewer.json
```

`--recursive` imports submodules of the selected packages. Without it, modules must be imported by your package or supplied individually with repeated `--module` flags.

## Select an explicit target

```bash
laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --target app.database:Base \
  --module app.models --recursive
```

The target syntax is `module:attribute`. A declarative base or registry exposes mapped models; a bare `MetaData` target exposes tables and constraints without discovering ORM relationships by itself. Import all relevant model modules before expecting complete metadata.

## Preserved metadata

Ordered composite primary and foreign keys, schema-qualified names, type strings, timezone metadata, constraints, and index definitions are represented in the document. Index key parts are columns (with `desc()` and `nulls_first()`/`nulls_last()` order) or SQL expressions. PostgreSQL partial indexes, `INCLUDE` columns and methods are kept under the index's `dialectOptions`, because metadata can target several dialects.

The tool does not infer ORM relationships from foreign keys or execute callable defaults. Missing mapper dependencies and import failures require investigation; see [Troubleshooting](./troubleshooting.md).
