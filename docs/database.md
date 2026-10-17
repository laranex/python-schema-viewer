# Database introspection

`build` can read a live database's catalogs as well as your models. It can also compare the two and report where they drift apart. The output is a [laranex-schema 1.0](https://laranex.github.io/schema-viewer-ui/contract) document, which [laranex-schema-viewer-ui](https://laranex.github.io/schema-viewer-ui/) displays.

| `--source` | Reads | Writes |
| --- | --- | --- |
| `declared` (default) | Your models, in the [extraction worker](./security.md) | A version 1 snapshot, or laranex-schema with `--format laranex-schema` |
| `database` | The database catalogs, in the introspection worker | laranex-schema, with database provenance |
| `combined` | Both, then reconciles them | laranex-schema, with provenance, drift and diagnostics |

`serve` and `mcp` still read version 1 snapshots, so keep a declared build for them.

## Quick start

```bash
pip install 'laranex-schema-viewer[postgresql]'   # or [mysql]; SQLite needs no driver
export LARANEX_SCHEMA_DATABASE_URL='postgresql+psycopg://schema_reader:...@db.internal/app'
laranex-schema-viewer build --source combined --output schema-combined.json
```

The URL is read only from the environment variable named by `--database-url-env` or `database_url_env` (default `LARANEX_SCHEMA_DATABASE_URL`). It is never accepted as an argument or from `pyproject.toml`, so it stays out of shell history and version control. Only the introspection worker receives it, on stdin. The extraction worker, which imports your models, never sees it.

| Option | Config key | Purpose |
| --- | --- | --- |
| `--source` | `source` | `declared`, `database` or `combined` |
| `--format` | `format` | `v1` or `laranex-schema`; database and combined builds always write laranex-schema |
| `--database-url-env NAME` | `database_url_env` | The variable holding the URL |
| `--schema NAME` (repeatable) | `database_schemas` | Schemas to read; default: the connection's default schema. Ignored by SQLite |
| `--database-connection NAME` | `database_connection` | The declared connection the database belongs to; default: the only one, else `default` |
| `--allow-write-privileges` | none | Inspect with a role that could change the database; see below |
| `--timeout SECONDS` | `timeout` | Also bounds introspection |

## Several connections

When the models span several databases, name each connection in `pyproject.toml`. The table name is the connection name, and `url_env` names the environment variable that holds its URL:

```toml
[tool.laranex-schema-viewer.databases.default]
url_env = "APP_DATABASE_URL"
schemas = ["public", "billing"]          # optional; default: the connection's default schema

[tool.laranex-schema-viewer.databases.analytics]
url_env = "ANALYTICS_DATABASE_URL"
namespace = "app.models:AnalyticsBase"   # optional; the declared namespace it reconciles with
```

Or select them on the command line with `--database NAME=ENV_VAR`, repeated once per connection. When `--database` is given, only the named connections are inspected; `schemas` and `namespace` still come from `[tool.laranex-schema-viewer.databases.NAME]` when that table exists.

```bash
laranex-schema-viewer build --source combined \
  --database default=APP_DATABASE_URL --database analytics=ANALYTICS_DATABASE_URL
```

- Each connection is read by its own introspection worker, one after the other. A worker receives only its own connection's URL, on stdin, and every connection gets the same read-only session and privilege check. If one connection fails, the build fails and writes nothing.
- Connections may use different drivers: PostgreSQL, MySQL, MariaDB and SQLite can be mixed.
- The output has one connection per name, each with its own driver, default schema and coverage. Table ids are qualified by connection, so a `users` table on two connections stays two tables, with separate links and diagnostics.
- URLs are never accepted in the configuration or as arguments. A `url` key, or an `url_env` variable listed in `env_passthrough` or `[tool.laranex-schema-viewer.env]`, stops the build.
- The single-database options (`--database-url-env`, `--schema`, `--database-connection` and their configuration keys) keep working unchanged, but cannot be mixed with `--database` or `[tool.laranex-schema-viewer.databases]`; the build stops and says which options conflict.

### Mapping connections to declared namespaces

Each table in a declared build belongs to a namespace, and in a combined build each database connection is reconciled with one of them: the namespace named by its `namespace` key, else the namespace with the connection's own name. When there is exactly one database connection and one declared namespace, they belong together whatever their names. The combined connection takes the database connection's name. Table ids keep the declared namespace; ids are opaque, so this changes nothing a reader relies on.

Run `laranex-schema-viewer build --format laranex-schema` and read the `connections[].name` values to see your declared namespaces. They depend on the ORM:

| ORM | Declared namespace | What to map |
| --- | --- | --- |
| SQLAlchemy, SQLModel | `default` with one `MetaData`. With several, the `module:attribute` that first holds each one: the `--target` you gave, or, for discovered modules, the first attribute in sorted order (often a model class rather than the base) | Set `namespace` for every connection. Prefer `targets = ["app.models:Base", "app.models:AnalyticsBase"]`, which keeps the names stable. Flask-SQLAlchemy bind keys are not read; map each bind's metadata |
| Django | Always `default`, for every app | One connection only. Database routers and per-model databases in `DATABASES` are not represented; inspecting a second database reports it as database-only |
| Tortoise ORM | The app label (`apps` keys in `TORTOISE_ORM`), not the connection name | Set `namespace` to the app label when the connection is named differently, for example `[...databases.default]` with `namespace = "models"` |
| Peewee | Always `default` | One connection only; models bound to other `Database` objects are not told apart |

- A database connection that matches no declared namespace stays database-only: its tables have database provenance, its declared coverage is `unavailable`, and one `database_only` diagnostic names the connection, instead of a `table_without_model` for every table.
- A declared namespace with no database connection stays declared-only: its database coverage is `unavailable`, with an `inspection_unavailable` diagnostic.
- The build stops when no connection matches any declared namespace (it lists the declared names), when a `namespace` does not exist, when two connections map to one namespace, or when a connection is renamed onto another declared namespace's name.

Nothing is compared across connections. An ORM relationship from a model on one connection to a table on another (SQLAlchemy `primaryjoin` with `foreign()`, for example) is kept as a relationship-only link; it never calls for a foreign key, and it is not reported as a missing one. Where a table name occurs on more than one connection, drift and reconciliation messages start with `Connection NAME:`.

Supported databases: PostgreSQL (psycopg 3 or psycopg2), MySQL and MariaDB (PyMySQL or mysqlclient), and SQLite files. The test suite runs against PostgreSQL 17.6, MySQL 8.4.6, MariaDB 11.4.8 and SQLite 3.53; older versions may report less.

## Use a read-only role

Introspection only reads the catalogs, but you should still give it a role that can do nothing else. The build checks the role's privileges first. If the role could change the schema or data, the build stops, unless you pass `--allow-write-privileges`. In that case it still runs every statement in a read-only transaction, and records a `write_privileges` diagnostic.

**PostgreSQL**: the catalogs are readable without any table privileges.

```sql
CREATE ROLE schema_reader LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE app TO schema_reader;
GRANT USAGE ON SCHEMA public TO schema_reader;          -- each schema you inspect
ALTER ROLE schema_reader SET default_transaction_read_only = on;
```

The check refuses superusers and roles that can create roles, databases or schemas. It also refuses roles that own, or can insert into, update, delete from, truncate, reference or add triggers to, any table in the inspected schemas.

**MySQL and MariaDB**: `information_schema` shows only the objects a user has a privilege on, so grant `SELECT` on the schema. The build never reads rows.

```sql
CREATE USER 'schema_reader'@'%' IDENTIFIED BY '...';
GRANT SELECT, SHOW VIEW ON app.* TO 'schema_reader'@'%';
```

The check reads `SHOW GRANTS`. It refuses any privilege other than `SELECT`, `SHOW VIEW`, `USAGE`, `SHOW DATABASES`, `PROCESS` and the replication monitoring privileges. It also refuses `GRANT OPTION`, and roles whose privileges it cannot list.

**SQLite** has no roles. The file is opened with `mode=ro` and `PRAGMA query_only`, and a missing file is reported, never created.

## What is read

The document records the following:

- **Tables and columns:** tables and their comments; columns with their native types, nullability, defaults (literal, expression, sequence or identity), generated expressions, enum values, collations and comments.
- **Keys and constraints:** composite primary keys; foreign keys across schemas, with their referential actions and deferral; unique and check constraints.
- **Indexes:** key columns and expressions, order, nulls order, prefix lengths, collations, operator classes, methods (`btree`, `gin`, `fulltext`, `spatial`...), predicates, included columns, and the primary key or constraint each one backs. PostgreSQL `NULLS NOT DISTINCT` and MySQL invisible indexes appear under `dialectOptions`.

Coverage records what each database can express. MySQL and MariaDB have no partial indexes or included columns, and MariaDB has no functional key parts. SQLite has no comments, and its check constraints are parsed from the table SQL, so their coverage is `partial`. Views and PostgreSQL partitions (the partitioned parent is kept) are not listed.

## Drift

In a combined build, tables match by schema and name; the default schema counts as unqualified. Columns match by name. Foreign keys match by their column mapping, then by name. Indexes match by name, then by key parts, and constraints by name, then by columns or expression. The database's facts win. Your models contribute ORM defaults, notes, models and relationships, and each element records its provenance (`database`, `declared`, or both).

| Diagnostic | Meaning | Certainty |
| --- | --- | --- |
| `column_drift` | Type, nullability, default or comment differs | Types and defaults: `likely`; the rest: `certain` |
| `index_drift` | Uniqueness or key parts differ | `certain` |
| `foreign_key_drift` | Columns, referential actions or deferral differ | `certain` |
| `constraint_drift` | A check expression differs | `likely` |
| `table_drift` | Primary key or comment differs | `certain` |
| `declared_only` | The models declare something the database lacks | `certain` |
| `database_only` | The database has something the models do not declare | `certain` |
| `model_without_table` / `table_without_model` | A model's table is missing, or a table has no model | `certain` |

`likely` means the comparison normalizes type names or SQL text, for example `VARCHAR(255)` and `character varying(255)`, or casts in a default, and could be wrong. The indexes MySQL creates for foreign keys, and the indexes behind primary keys and unique constraints, are matched to their keys and are not reported as extra.

## Upgrading

- Version 1 snapshots are unchanged and remain the default output. Existing `build`, `serve` and `mcp` usage keeps working.
- `laranex-schema-viewer convert schema-viewer.json --output schema.json [--orm sqlalchemy]` converts a saved snapshot to laranex-schema 1.0.
- The isolated extraction worker is new in this release. See [build security](./security.md#upgrading) for what changes for existing builds.
