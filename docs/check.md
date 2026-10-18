# Schema drift gate in CI

`laranex-schema-viewer check` fails a CI job when a saved document shows schema drift. It reads only the document (and `pyproject.toml`): it never connects to a database, imports application code, starts a process, runs migrations or rewrites the document. Run it after a `build` that inspected the databases and computed the [comparisons](./comparison.md) you configured; `check` then checks exactly those.

```bash
laranex-schema-viewer build --source database      # with [[tool.laranex-schema-viewer.comparisons]]
laranex-schema-viewer check                        # exit 0, 1 or 2
```

## What is checked

The command is a thin layer over `contract.gate.gate_document`, a port of the shared reference gate (`gateDocument`): the [gate report](https://laranex.github.io/schema-viewer-ui/contract#gate-reports) rules are normative, and the 23 shared gate conformance cases give byte-identical reports both through `gate_document` and through `check --json`. The gate validates the document itself (2.0 strictly, a newer 2.x leniently), so a missing, unreadable or invalid snapshot still gets a report.

These are **findings**, each with a severity and a certainty:

| Source | Finding | Severity, certainty | Label, message |
| --- | --- | --- | --- |
| `comparison` | Every listed difference of every checked comparison, kind `<object_type>_<change>` (`column_changed`, `primary_key_missing`) | The difference's own (see [severity](./comparison.md)) | `app:tenant_a → app:tenant_b · tenant_a.orders.total`; `Only in the baseline`, `Only in the target` or `Differs in type, nullable` |
| `diagnostic` | Every diagnostic of category `drift` or `reconciliation` (declared and database metadata disagree), kind as recorded | The diagnostic's own | The subject's label, else what its id names (`app · users.email`); the diagnostic's message |
| `migration` | A migration track `behind`, `diverged` or `uninitialized`, kind `migration_<status>` | `behind` and `diverged` error, `uninitialized` warning; certain | `app · tenant_b` (the connection alone for an unqualified track); for example `alembic revisions not applied: 3c4d5e6f7a8b` |

Nothing else is a finding: `extraction` diagnostics (such as `unresolved_foreign_key`), `lint` and `inspection` diagnostics never fail the gate, whatever the thresholds.

These are **errors**: the check could not be completed, and the gate exits 2 whatever the findings.

| Error | When |
| --- | --- |
| `invalid_document` | The snapshot is missing, unreadable or not JSON (`The input could not be parsed as JSON.`), not a laranex-schema document, not 2.x, or fails validation. The report then has no other error and no findings; the reason (the file error or the first validation errors) is printed on stderr beside it, never in the report |
| `validation_incomplete` | A newer laranex-schema 2.x minor, validated only leniently; upgrade the package |
| `comparison_not_found` | A [requested comparison](#which-comparisons-are-checked) names a connection the document does not have, has a `*` target that expands to no schema, or is not stored in the document |
| `comparison_incomplete` | A checked comparison is incomplete: differences were omitted by its limit, or a side's inspection did not cover a compared feature or its schema |
| `coverage_incomplete` | In a `database` or `combined` document, a connection whose database inspection read `tables` or `columns` other than completely, or left any feature `unavailable`. A `partial` feature that is a dialect's known limit (SQLite check constraints) never fails the gate by itself; the comparisons that read it are judged by their own completeness |
| `inspection_failed` | An `inspection` diagnostic with severity `error` |
| `migration_unknown` | A migration track's status is `unknown` |

This package writes migration tracks empty, so its own documents have no migration findings yet; documents from other producers may have them.

## Which comparisons are checked

| Command | Requested comparisons | Checked |
| --- | --- | --- |
| `check` without `[[tool.laranex-schema-viewer.comparisons]]` | None | Every stored comparison; zero comparisons pass |
| `check` with configured comparisons | The configured entries, as written (connection names, `*` targets unexpanded) | Only those, which `build` must have stored; others in the document are ignored |
| `check BASELINE=TARGET ...` | The named comparisons, replacing the configured ones | Only those, computed in memory first |

Requests use connection names; a target schema `*` is expanded by the gate to every schema the target connection's inspection read except the baseline's own scope, with the same shared [expansion](./comparison.md#requested-comparisons) as `build` and `compare`. A request that cannot be resolved is a `comparison_not_found` error (exit 2) with the expansion's message: a configured comparison the build did not store (a declared build stores none), an unknown connection, a `*` whose inspection lists no schemas, or a `*` that matches no schema. A comparison configured or named twice is a configuration error (exit 2, no report): `Requested comparison app:tenant_a → app:tenant_b is listed more than once.`

Comparisons named on the command line are computed from the saved document, as [`compare`](./comparison.md#compare-a-saved-document) does (replacing a stored comparison of the same scopes, with `--limit`), and then requested, so the gate checks exactly what was computed. Nothing is written. A named comparison whose connection is unknown, or whose `*` cannot be expanded, is not computed and is reported by the gate as not found; on an invalid document nothing is computed.

## Thresholds

A finding **qualifies** (fails the check) when its severity is at least the severity threshold and, with certainty `certain`, it is certain; with `likely`, likely findings qualify too.

| Setting | Flag | `[tool.laranex-schema-viewer.check]` key | Default |
| --- | --- | --- | --- |
| Lowest failing severity: `error`, `warning`, `info` | `--severity` | `severity` | `warning` |
| `certain` (only certain findings fail) or `likely` | `--certainty` | `certainty` | `certain` |

```toml
[tool.laranex-schema-viewer.check]
severity = "error"
certainty = "likely"
```

Flags take precedence over the configuration. The section accepts only these two keys; anything else, or another value, is a configuration error (exit 2).

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Passed: no error and no qualifying finding (findings below the thresholds are still listed) |
| `1` | Drift: at least one finding qualifies, and nothing kept the check from completing |
| `2` | Incomplete or invalid: any error above (it wins over `1`), or, without a report, invalid arguments (including argparse usage errors), invalid configuration, a comparison configured or named twice, or a failure writing `--output` (the previous file is left as it was) |

## Options

| Option | Default |
| --- | --- |
| `BASELINE=TARGET` | The configured comparisons, else every stored one. Each named comparison is computed in memory, then checked; see [which comparisons are checked](#which-comparisons-are-checked); repeatable |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json` |
| `--severity`, `--certainty` | See [thresholds](#thresholds) |
| `--limit N` | `500`, for comparisons named on the command line |
| `--json` | Print the `laranex-schema-gate` 1.0 report instead of the summary |
| `--output FILE` | Also write the JSON report to `FILE`, atomically |

## Output

The default output is a plain-text summary: the verdict and exit code, how many comparisons were checked and which were requested, the errors, and the qualifying findings (most severe first), each with its id. It has no colors, timestamps or absolute paths, so identical inputs print identical bytes.

`--json` prints the report, which validates strictly against the shared [gate report JSON Schema](https://laranex.github.io/schema-viewer-ui/contract#gate-reports) (`laranex_schema_viewer/contract/gate-report.schema.json` in the package), in canonical JSON:

```json
{
  "comparisons": ["c69f5b58e94575f1b"],
  "counts": { "error": 13, "findings": 26, "info": 1, "qualifying": 23, "warning": 12 },
  "errors": [],
  "exitCode": 1,
  "findings": [
    {
      "certainty": "certain",
      "id": "x62b27f513574b3a0",
      "kind": "column_missing",
      "label": "app:tenant_a → app:tenant_b · tenant_a.users.legacy_code",
      "message": "Only in the baseline",
      "severity": "error",
      "source": "comparison"
    }
  ],
  "format": "laranex-schema-gate",
  "passed": false,
  "requested": [
    { "baseline": { "connection": "app", "schema": "tenant_a" }, "target": { "connection": "app", "schema": "*" } }
  ],
  "thresholds": { "certainty": "certain", "severity": "warning" },
  "version": "1.0"
}
```

(Shortened.) `requested` lists the requested comparisons, sorted, and `comparisons` the ids of the stored comparisons that were checked. `--output gate-report.json` keeps the report as a CI artifact while the log shows the summary.

## GitHub Actions

Build against a disposable database that your migrations create, then gate the document. The URL is read from an environment variable named in the configuration, never from arguments or `pyproject.toml`.

```toml
[tool.laranex-schema-viewer]
source = "database"
output = "schema-viewer.json"

[tool.laranex-schema-viewer.databases.app]
url_env = "APP_DATABASE_URL"
schemas = ["tenant_template", "tenant_acme", "tenant_globex"]

[[tool.laranex-schema-viewer.comparisons]]
baseline = { connection = "app", schema = "tenant_template" }
target = { connection = "app", schema = "*" }

[tool.laranex-schema-viewer.check]
severity = "warning"
certainty = "certain"
```

```yaml
name: schema
on: [pull_request]
jobs:
  drift:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: postgres
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      APP_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/postgres
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install 'laranex-schema-viewer[postgresql]' -r requirements.txt
      - run: ./scripts/create-tenants-and-migrate.sh   # your own setup of the disposable database
      - run: laranex-schema-viewer build
      - name: Schema drift gate
        run: laranex-schema-viewer check --output gate-report.json
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: schema
          retention-days: 7
          path: |
            schema-viewer.json
            gate-report.json
```

The job fails on exit 1 (drift) and on exit 2 (an incomplete or invalid check). For a production-like comparison, inspect with a read-only role ([database introspection](./database.md)); the gate itself needs no database access at all. To check a document saved earlier instead (for example, one another job uploaded as an artifact), skip the database and run `check --snapshot path/to/schema-viewer.json`. Never commit the document. It and the gate report describe the schema, so treat both as sensitive CI artifacts:

- **Access.** Upload them only where just the people who may see the schema can download them. A public repository's workflow artifacts can be downloaded by anyone who can read the repository, so do not upload the document or the report there; print the summary in the log instead.
- **Retention.** Keep them only as long as a failing job needs investigating (`retention-days: 7` above), and delete them sooner when a job has been resolved.

The gate report is not a schema snapshot, so `scan` does not flag it, but it carries table, column and schema names in its labels. See the [security policy](https://github.com/laranex/python-schema-viewer/blob/main/SECURITY.md#schema-snapshots-are-sensitive).
