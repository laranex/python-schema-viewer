# Schema drift gate in CI

`laranex-schema-viewer check` fails a CI job when a saved document shows schema drift. It reads only the document (and `pyproject.toml`): it never connects to a database, imports application code, starts a process, runs migrations or rewrites the document. Run it after a `build` that inspected the databases and computed the [comparisons](./comparison.md) you care about.

```bash
laranex-schema-viewer build --source database      # with [[tool.laranex-schema-viewer.comparisons]]
laranex-schema-viewer check                        # exit 0, 1 or 2
```

## What is checked

The gate follows the shared [gate report](https://laranex.github.io/schema-viewer-ui/contract#gate-reports) rules. Every one of these is a **finding** with a severity and a certainty:

| Source | Finding | Severity, certainty |
| --- | --- | --- |
| `comparison` | Every listed difference of every stored comparison, kind `<object_type>_<change>` (`column_changed`, `primary_key_missing`) | The difference's own (see [severity](./comparison.md)) |
| `migration` | A migration track `behind`, `diverged` or `uninitialized`, kind `migration_<status>` | `behind` and `diverged` error, `uninitialized` warning; certain |
| `diagnostic` | Every diagnostic in the document, kind as recorded (drift between declared and database metadata, unresolved foreign keys, lint, ...) | The diagnostic's own |

These are **errors**: they mean the check could not be completed, and the gate exits 2 whatever the findings.

| Error | When |
| --- | --- |
| `comparison_incomplete` | A comparison is incomplete: differences were omitted by its limit, or a side's inspection did not cover a compared feature or its schema |
| `migration_unknown` | A migration track's status is `unknown` |
| `inspection_failed` | An `inspection` diagnostic with severity `error` |
| `validation_incomplete` | The document is a newer laranex-schema 2.x minor, validated only leniently; upgrade the package |
| `invalid_document` | The document is missing, unreadable, not laranex-schema 2.x, or fails validation (2.0 documents are validated strictly) |

This package writes migration tracks empty, so its own documents have no migration findings yet; documents from other producers may have them.

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
| `0` | Passed: no error and no qualifying finding |
| `1` | Drift: at least one finding qualifies, and nothing kept the check from completing |
| `2` | Incomplete or invalid: any error above (it wins over `1`), invalid arguments (including argparse usage errors), invalid configuration, an unknown comparison scope, or a failure writing `--output` |

An incomplete comparison never passes. A document with no comparisons is checked for its other findings; the summary says how many comparisons were checked.

## Options

| Option | Default |
| --- | --- |
| `BASELINE=TARGET` | None: check the stored comparisons. Named scopes are compared first, as [`compare`](./comparison.md#compare-a-saved-document) does (replacing a stored comparison of the same scopes), without writing the document; repeatable |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json` |
| `--severity`, `--certainty` | See [thresholds](#thresholds) |
| `--limit N` | `500`, for comparisons named on the command line |
| `--json` | Print the `laranex-schema-gate` 1.0 report instead of the summary |
| `--output FILE` | Also write the JSON report to `FILE`, atomically |

## Output

The default output is a plain-text summary: the verdict and exit code, what was checked, the errors, and the qualifying findings (most severe first), each with its id. It has no colors, timestamps or absolute paths, so identical inputs print identical bytes.

`--json` prints the report, which validates strictly against the shared [gate report JSON Schema](https://laranex.github.io/schema-viewer-ui/contract#gate-reports) (`laranex_schema_viewer/contract/gate-report.schema.json` in the package), in canonical JSON:

```json
{
  "counts": { "error": 13, "findings": 26, "info": 1, "qualifying": 23, "warning": 12 },
  "errors": [],
  "exitCode": 1,
  "findings": [
    {
      "certainty": "certain",
      "id": "x62b27f513574b3a0",
      "kind": "column_missing",
      "label": "tenant_a.users.legacy_code",
      "message": "Only in the baseline",
      "severity": "error",
      "source": "comparison"
    }
  ],
  "format": "laranex-schema-gate",
  "passed": false,
  "thresholds": { "certainty": "certain", "severity": "warning" },
  "version": "1.0"
}
```

(Shortened.) `--output gate-report.json` keeps the report as a CI artifact while the log shows the summary.

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
          path: |
            schema-viewer.json
            gate-report.json
```

The job fails on exit 1 (drift) and on exit 2 (an incomplete or invalid check). For a production-like comparison, inspect with a read-only role ([database introspection](./database.md)); the gate itself needs no database access at all. To check a document committed earlier instead, skip the database and run `check --snapshot path/to/schema-viewer.json`.
