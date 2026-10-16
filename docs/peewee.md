# Peewee integration

Install the `peewee` extra (Peewee 3.19 or 4.x) in an environment that can import your models:

```bash
python -m pip install 'laranex-schema-viewer[peewee,mcp]'
```

## Build from model modules

```bash
laranex-schema-viewer build --module app.models
```

The adapter is detected from the imported modules; add `--orm peewee` to choose it explicitly. Models referenced by foreign keys and `ManyToManyField` join tables are included automatically. The database is never opened: SQL types and expressions are rendered with the model database's SQL context. Models without a usable database fall back to Peewee's generic field types.

## What is extracted

| Peewee declaration | Snapshot |
| --- | --- |
| `Meta.table_name`, `Meta.schema` | Table name and schema |
| Fields | Columns with SQL types, nullability, Python defaults, and `SQL("DEFAULT ...")` server defaults |
| `primary_key`, `CompositeKey` | Primary key, including composite keys |
| `ForeignKeyField` | Database foreign keys with `on_delete`, `on_update`, `deferrable`, and `constraint_name` |
| `ForeignKeyField` and `backref` | ORM relationships; `backref="+"` hides the reverse side, and a unique foreign key is one-to-one |
| `ManyToManyField` | Many-to-many relationships through the generated join table, which is listed as a table but not as a model |
| `index`, `unique`, `Meta.indexes`, `add_index()` | Indexes, including descending order, expressions, and partial `where` conditions |
| Field `constraints` and `Meta.constraints` | `CHECK` and `UNIQUE` constraints, with optional `CONSTRAINT name` |

Peewee implements `unique=True` as a unique index, so it appears in the index list.

## Reported limitations

- Peewee cannot mark a model as abstract. A base class whose only field is the implicit `id` and that has subclasses is skipped with a `skipped_base_model` diagnostic; other base classes are extracted as tables.
- Raw SQL indexes added with `add_index(SQL(...))` are reported as `unsupported_index`, and raw constraints other than `CHECK` or `UNIQUE` as `unsupported_constraint`.
- Peewee subclasses copy their parents' fields into separate tables; there is no multi-table inheritance.
- `--target` is not used for Peewee.
