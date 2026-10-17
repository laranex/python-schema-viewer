# Schema comparison

A comparison lists how one scope's schema differs from another's: two tenant schemas of one PostgreSQL database, a primary and a replica, or the same application on PostgreSQL and MySQL. Python Schema Viewer computes comparisons with the shared [laranex-schema 2.0 algorithm](https://laranex.github.io/schema-viewer-ui/contract#comparisons) (the same as the Laravel and Node.js packages, checked against the shared conformance cases) and stores them in the document's `comparisons` array. The viewer and MCP display stored comparisons; they never compute them.

Comparisons read only what a document already holds. They never open a database connection, run SQL or import your application: `build` compares what its read-only [database introspection](./database.md) just read, and `compare` works on a saved document.

## Scopes

A scope is a connection, by name, and optionally a schema of it, spelled `CONNECTION` or `CONNECTION:SCHEMA` on the command line:

| Scope | Tables |
| --- | --- |
| `app:tenant_a` | The tables of schema `tenant_a` of connection `app` (and unqualified tables, if `tenant_a` is the connection's default schema) |
| `app` | The connection's unqualified tables and those in its default schema (PostgreSQL `public`, the MySQL database) |
| `app:*` (target only) | One comparison per schema the connection's inspection read (`coverage.schemas`), except the baseline's own scope |

Tables match by name, so a schema name is never part of a difference: `tenant_a.orders` and `tenant_b.orders` are the same table, and a sequence, index expression or check that names its own schema (`nextval('tenant_a.orders_id_seq')`) compares equal to the other tenant's. Differences are keyed by table, column and entity names only.

## Configure comparisons

```toml
[tool.laranex-schema-viewer.databases.app]
url_env = "APP_DATABASE_URL"
schemas = ["tenant_template", "tenant_acme", "tenant_globex"]

[[tool.laranex-schema-viewer.comparisons]]
baseline = { connection = "app", schema = "tenant_template" }
target = { connection = "app", schema = "*" }
limit = 500
overrides = [
  { object_type = "index", change = "extra", severity = "info" },
  { object_type = "column", change = "changed", field = "comment", severity = "warning" },
]

[[tool.laranex-schema-viewer.comparisons]]
baseline = { connection = "primary" }
target = { connection = "replica" }
```

| Key | Meaning |
| --- | --- |
| `baseline` | `{ connection, schema }`: a connection name and an optional schema |
| `target` | The same; `schema = "*"` compares the baseline with every schema the target connection's inspection read |
| `limit` | Differences listed in the document, default **500**; the rest are counted in `summary` and `omitted`, and the comparison is incomplete. `0` keeps only the counts |
| `overrides` | Severities for `(object_type, change)`, or for one `field` of a `changed` difference. A field override wins over a general one, and a changed difference takes its most severe field |

`object_type` is `table`, `column`, `primaryKey`, `index`, `foreignKey` or `constraint`; `change` is `missing` (in the baseline only), `extra` (in the target only) or `changed`; `severity` is `error`, `warning` or `info`. The fields each object type can list are the contract's: `comment` (table); `type`, `nullable`, `default`, `collation`, `enum`, `generated`, `comment` (column); `columns` (primary key); `name`, `unique`, `parts`, `method`, `predicate`, `included` (index); `name`, `columns`, `foreignTable`, `foreignColumns`, `onDelete`, `onUpdate`, `deferrable`, `initially` (foreign key); `name`, `columns`, `expression` (constraint).

Unknown keys, values and fields are errors. The configuration names connections and schemas only, never URLs. A pair named explicitly takes precedence over the same pair from a `*` expansion; naming one pair twice otherwise is an error.

Default severities:

| Object | missing / extra | changed |
| --- | --- | --- |
| table | error | info (`comment`) |
| column | error | warning; `comment`: info |
| primaryKey | error | error |
| index, foreignKey, constraint | warning | warning |

## Compare during a build

`build --source database` and `build --source combined` compute the configured comparisons after inspecting every connection, and store them in the document they write, validated strictly first:

```bash
APP_DATABASE_URL=... laranex-schema-viewer build --source database
laranex-schema-viewer build --source database --database primary=PRIMARY_URL --database replica=REPLICA_URL \
  --compare primary=replica --compare-limit 200
```

`--compare BASELINE=TARGET` (repeatable) replaces the configured comparisons for that build, with the default severities; `--compare-limit` sets their limit. A declared build reads no database, so it skips configured comparisons (and says so) and rejects `--compare`.

In a combined document only what the database reported takes part: tables, columns, keys and constraints that only your models declare are left out.

## Compare a saved document

```bash
laranex-schema-viewer compare app:tenant_template=app:*
laranex-schema-viewer compare --snapshot /path/to/schema-viewer.json primary=replica --json
laranex-schema-viewer compare --write
```

| Option | Default | Purpose |
| --- | --- | --- |
| `BASELINE=TARGET` | Configured comparisons | Scopes to compare; repeatable |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json` | The document to read |
| `--limit N` | `500` | Differences listed per comparison named on the command line |
| `--json` | Off | Print the comparison objects as laranex-schema JSON instead of the summary |
| `--write` | Off | Store the comparisons in the document atomically, replacing those of the same scopes (and their diagnostics) and keeping the others |

`compare` reads the document and `pyproject.toml` only: no database, no application import, no ORM. The summary has one line per comparison and one per listed difference:

```text
app:tenant_template -> app:tenant_initech (complete): 3 missing, 2 extra, 5 changed
  error   certain missing table      tenant_template.audit_log
  error   certain extra   table      tenant_initech.coupons
  warning certain changed column     tenant_template.orders.total  type "numeric(10,2)" -> "numeric(12,2)"
  warning certain changed foreignKey tenant_template.orders.orders_user_id_fkey  onDelete "CASCADE" -> "RESTRICT"
  warning likely  changed constraint tenant_template.orders.orders_total_check  expression "total >= 0::numeric" -> "total > 0::numeric"
  ...
```

`--write` rewrites only laranex-schema 2.0 documents, so nothing a newer minor version added is lost. `compare` always exits 0 when it could compare; failing a check on differences is not part of this version.

## Reading the result

Each comparison has `baseline`, `target`, a `summary` counting every difference found (`missing`, `extra`, `changed`), `omitted` (left out by the limit), `complete`, and its `differences` sorted by object type, key and change. Each difference has an `objectType`, a `change`, a `key` both sides share (`["orders", "total"]`, `["orders", "name", "orders_open_idx"]`), `baseline` and `target` references with readable labels, the differing `fields` with both values, a `severity` and a `certainty`.

- **certain**: missing and extra entities, and changed fields compared exactly: same-driver types, nullability, primary keys, index uniqueness and parts, foreign keys and referential actions.
- **likely**: a changed field compared as normalized text, which can be wrong: types across different drivers (`character varying(255)` and `varchar(255)`, `int4` and `int(11)`, `tinyint(1)` and `boolean`), defaults, generated expressions, index predicates, check expressions and expression index parts.

`complete` is false when differences were omitted, or when either side's coverage of a compared feature is not complete (SQLite's check constraints, for example) or did not read the scope's schema. An incomplete comparison gets a `comparison_incomplete` diagnostic that says why. It is never a passing result.

## Accuracy and limits

The tests compare real catalogs: PostgreSQL tenant schemas with every kind of planted drift (all detected, an identical tenant with no difference, no schema name in any key), and the same application on PostgreSQL, MySQL and MariaDB. What they show about cross-driver comparisons:

- Defaults compare as normalized SQL text, not values: `now()` and `CURRENT_TIMESTAMP`, or `0` and `'0.00'` (how MySQL reads back a decimal default), are reported as `likely` changes.
- MySQL and MariaDB keep a unique constraint as a unique index. Compared with PostgreSQL, the constraint is reported missing and the index extra.
- MySQL creates an index for a foreign key that has none; compared with PostgreSQL, that index is extra unless the PostgreSQL side declares the same index.
- Features a database does not have are differences: partial indexes and included columns on MySQL and MariaDB, functional key parts on MariaDB.
- Types across drivers are normalized with the contract's synonyms only; types it does not know (`json` and `jsonb`, `uuid` and `char(36)`) are compared as written.

Comparisons describe the catalogs at build time. Tenant schemas that were not inspected are not compared: list them in `schemas`. MCP pages through stored differences only; those left out by the limit are counted, not listed, so raise the limit and rebuild (or run `compare --write`) to list them.
