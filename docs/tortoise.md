# Tortoise ORM integration

Install the `tortoise` extra (Tortoise ORM 1.x) in an environment that can import your models:

```bash
python -m pip install 'laranex-schema-viewer[tortoise,mcp]'
```

## Build from model modules

```bash
laranex-schema-viewer build --module app.models --recursive
```

The adapter is detected from the imported modules; add `--orm tortoise` to choose it explicitly. Models are registered with `Tortoise.init_models()`, which resolves relationships without opening a connection. `Tortoise.init()` and `generate_schemas()` are never called.

Relationship strings such as `"models.Author"` name an app label. With a single label the adapter uses it, defaulting to `models`.

## Use your Tortoise config

For several apps, or to render column types for your database, point `--target` at the config dictionary you pass to `Tortoise.init()`:

```bash
laranex-schema-viewer build --orm tortoise --target app.config:TORTOISE_ORM
```

The adapter reads the `apps` mapping and only the URL scheme or engine name of the default connection, such as `postgres` or `sqlite`, to choose SQL types. It does not read credentials or connect.

## What is extracted

| Tortoise declaration | Document |
| --- | --- |
| `Meta.table`, `Meta.schema`, `table_description`, field `description` | Table name, schema, and comments; the app label is the connection name |
| Fields | Columns with the dialect's SQL type, nullability, and defaults |
| `ForeignKeyField`, `OneToOneField` with `db_constraint=True` | Database foreign keys with their `on_delete` action, which Tortoise writes into the DDL |
| Forward and backward relations | ORM relationships; `related_name=False` hides the backward side |
| `ManyToManyField` | Many-to-many relationships and the generated `through` table with its keys and unique index |
| `unique`, `unique_together`, `db_index`, `Meta.indexes` | Unique constraints and indexes, including `PartialIndex` conditions and PostgreSQL index types |
| `Meta.constraints` (Tortoise 1.x) | `UniqueConstraint` and `CheckConstraint`; conditional uniqueness becomes a unique partial index |

Constraints in `Meta.constraints` are applied by Tortoise migrations, not by `generate_schemas()`.

## Reported limitations

- Tortoise has no composite primary keys or multi-table inheritance; abstract models contribute their fields to each concrete table.
- Without a config target, column types are Tortoise's generic SQL types and partial-index conditions are keyed as `tortoise` in the index's `dialectOptions`; with one, under the dialect's name (`postgresql`, `mysql`, `sqlite`).
- Index names that Tortoise generates during schema creation are not known in advance and are left empty.
- Unknown constraint classes are reported as `unsupported_constraint`.
