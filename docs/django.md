# Django integration

Install the `django` extra in an environment that can import your project and its installed apps:

```bash
python -m pip install 'laranex-schema-viewer[django,mcp]'
```

## Build from a Django project

Run `build` from the directory that contains `manage.py`. The settings module is read from `manage.py` without running it:

```bash
laranex-schema-viewer build
```

Or name the settings explicitly:

```bash
laranex-schema-viewer build --orm django --settings mysite.settings --output schema-viewer.json
```

`build` calls `django.setup()` and extracts every installed model, including Django's own apps such as `auth`. To focus on your apps, pass app or module names; models they reference are added automatically so every relationship resolves:

```bash
laranex-schema-viewer build --orm django --settings mysite.settings --module shop --module accounts
```

`laranex-schema-viewer init` detects `manage.py` and saves `orm = "django"` and `settings` in `pyproject.toml`.

## No database access

Database connections are blocked while settings load and models are read. Column types come from the configured backend's `db_type()`, and index conditions, expression indexes, and check constraints are compiled to that backend's SQL without running a query. If an app's `ready()` hook tries to connect, the build stops with an error instead of connecting.

## What is extracted

| Django declaration | Snapshot |
| --- | --- |
| `Meta.db_table`, `db_table_comment`, `db_comment` | Table name and comments |
| Concrete fields | Columns with backend types, nullability, Python and `db_default` defaults |
| `primary_key=True`, `CompositePrimaryKey` (Django 5.2+) | Primary key, including composite keys |
| `ForeignKey`, `OneToOneField` with `db_constraint=True` | Database foreign keys; delete actions only for Django 6.1+ `DB_CASCADE`, `DB_SET_NULL`, and `DB_SET_DEFAULT` |
| `ForeignKey`, `OneToOneField`, reverse accessors | ORM relationships; one-to-one has `uselist: false` |
| `ManyToManyField` | Many-to-many relationships through the automatic or explicit `through` table |
| `unique`, `unique_together`, `UniqueConstraint`, `CheckConstraint` | Unique and check constraints; conditional or expression `UniqueConstraint` becomes a unique index, as Django creates it |
| `db_index`, `Meta.indexes` | Indexes with descending order, expressions, conditions, `include`, and PostgreSQL index types |
| Multi-table inheritance | Child table with a `*_ptr` primary key and foreign key to the parent; the model lists both tables |
| Proxy models | Models that share their concrete model's table |

Django's usual `on_delete=CASCADE` runs in Python, not in the database, so it is not reported as a foreign-key action. A `ForeignKey` with `db_constraint=False` appears as an ORM relationship without a database foreign key.

## Reported limitations

- `managed = False` models are extracted with an `unmanaged_model` diagnostic: Django does not create or migrate their tables.
- `GenericForeignKey` and `GenericRelation` have no fixed target table and are reported as `unsupported_relation`.
- Exclusion constraints and other custom constraint classes are reported as `unsupported_constraint`.
- Database routers choose the backend used to render types; tables are not split by database alias.
