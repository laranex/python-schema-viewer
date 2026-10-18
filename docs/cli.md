# CLI and configuration

Every option has a default. Command-line options take precedence over the `[tool.laranex-schema-viewer]` section of `pyproject.toml`, which takes precedence over built-in defaults.

## Init

```bash
laranex-schema-viewer init
```

Asks for model modules, recursive import, and the output path (or, in a Django project with `manage.py`, the settings module and optional apps), then appends a `[tool.laranex-schema-viewer]` section to `pyproject.toml`, creating the file if needed. Suggested values come from model detection; press Enter to accept them. `--yes` accepts every suggestion without prompting. `init` never overwrites an existing section and does not import application code.

`init` also appends a commented block to the project root's `.gitignore` (creating it if needed) with anchored patterns for the default snapshot `/schema-viewer.json`, the viewer's offline download `/schema-viewer.html` and, when it differs and lies inside the project root, the configured output (for example `/out/schema.json`). It adds only the patterns the file does not already contain, prints what it added, and running it again adds nothing.

```toml
[tool.laranex-schema-viewer]
modules = ["app.models"]
recursive = true
output = "schema-viewer.json"
```

| Key | Purpose |
| --- | --- |
| `modules` | Modules to import, like repeated `--module` |
| `targets` | `module:attribute` targets, like repeated `--target` |
| `recursive` | Import every submodule of the selected packages |
| `output` | Document path, relative to the project root; also the default for `serve` and `mcp` |
| `orm` | `auto`, `sqlalchemy`, `django`, `tortoise`, or `peewee`, like `--orm` |
| `settings` | Django settings module, like `--settings` |
| `source` | `declared`, `database` or `combined`, like `--source` |
| `[[comparisons]]` | Scope pairs `build` and `compare` compare, with `baseline`, `target`, `limit` and `overrides`; see [schema comparison](./comparison.md#configure-comparisons) |
| `[check]` | `severity` (default `warning`) and `certainty` (default `certain`) thresholds for `check`; see [schema drift gate](./check.md#thresholds) |

## Build

```bash
laranex-schema-viewer build
```

Every build writes one [laranex-schema 2.0](https://laranex.github.io/schema-viewer-ui/contract) document in canonical JSON (sorted keys, two-space indentation, no timestamps), validated strictly before it replaces the previous file. A failed build leaves the previous file in place.

A schema snapshot must never be committed. Before extracting anything, `build` asks Git about its output path: when the Git index tracks it, `build` stops with exit code 1 and writes nothing, and tells you to untrack it with `git rm --cached -- PATH` (the file is kept) and ignore it; when the path is in a Git work tree but not ignored (`git check-ignore`), `build` writes it and prints a warning on stderr. Outside a Git work tree, or without Git, nothing is checked.

| Option | Default | Purpose |
| --- | --- | --- |
| `--project-root PATH` | Current directory | Application import root and location of `pyproject.toml` |
| `--module MODULE` | Config `modules`, else detected | Import a module; repeatable |
| `--target MODULE:ATTRIBUTE` | Config `targets` | SQLAlchemy metadata, base, or registry; or a Tortoise `TORTOISE_ORM` config |
| `--orm NAME` | Config `orm`, else `auto` | `sqlalchemy`, `django`, `tortoise`, or `peewee`; `auto` detects it |
| `--settings MODULE` | Config `settings`, else `manage.py` | Django settings module; selects Django |
| `--recursive` | Config `recursive`, else off | Discover selected package submodules |
| `--output PATH` | Config `output`, else `schema-viewer.json` | Write the document |
| `--isolation LEVEL` | Config `isolation`, else `process` | Where application code runs: `process` or `container` |
| `--timeout SECONDS` | Config `timeout`, else `120` | Stop extraction after this long |
| `--container-image IMAGE` | Config `container_image` | Image for `--isolation container` |
| `--source SOURCE` | Config `source`, else `declared` | `declared`, `database` or `combined`; see [database introspection](./database.md) |
| `--database-url-env NAME` | Config `database_url_env`, else `LARANEX_SCHEMA_DATABASE_URL` | Environment variable holding the database URL |
| `--schema NAME` | Config `database_schemas`, else the default schema | Database schema to inspect; repeatable |
| `--database-connection NAME` | Config `database_connection`, else the only declared connection | Connection the database belongs to |
| `--database NAME=ENV_VAR` | Config `[tool.laranex-schema-viewer.databases.NAME]` | Inspect connection `NAME`, whose URL is in `ENV_VAR`; repeatable. Replaces the three options above; see [several connections](./database.md#several-connections) |
| `--allow-write-privileges` | Off | Inspect with a role that could change the database, in read-only sessions |
| `--compare BASELINE=TARGET` | Config `[[tool.laranex-schema-viewer.comparisons]]` | Compare two inspected scopes, each `CONNECTION` or `CONNECTION:SCHEMA` (a target schema `*` expands to every inspected schema); repeatable. Replaces the configured comparisons; needs `--source database` or `combined`. See [schema comparison](./comparison.md) |
| `--compare-limit N` | `500` | Differences listed per `--compare` comparison; the rest are counted as omitted |

Application code is imported in a separate extraction worker with a filtered environment: database URLs, passwords and tokens are not passed to it. `env_passthrough`, `[tool.laranex-schema-viewer.env]`, `allow_writes` and `container_runtime` configure it. Operations the worker blocks are printed as warnings. See [build security](./security.md) for what each level guarantees.

When neither options nor configuration select a module, `build` looks up to four directories deep for exactly one `models` package or `models.py` file, skipping hidden, virtual-environment, test, documentation, and build directories. It imports a detected package recursively and prints which module it chose. If it finds none or several, it stops and asks for `--module` or configuration.

Before that, if a `manage.py` in the project root sets `DJANGO_SETTINGS_MODULE`, `build` uses Django with that settings module. See [supported ORMs](./orm-support.md) for how each adapter selects models.

## Serve

```bash
laranex-schema-viewer serve
```

| Option | Default |
| --- | --- |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json`; a laranex-schema 2.x document |
| `--host HOST` | `127.0.0.1`; a loopback host only (`127.0.0.0/8`, `::1`, `localhost`) unless `--unsafe-allow-remote` |
| `--port PORT` | `8765` |
| `--unsafe-allow-remote` | Off |

The server serves allowlisted viewer assets and the selected document, which it validates on startup and on each request. A file of another format or version stops `serve` with an error; rebuild it. Every response, errors included, is sent with `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and a restrictive Content-Security-Policy, and error responses carry only their status.

`serve` is the one exception to authentication in development: it has none, and is meant for a single developer on their own machine. Never share it.

The viewer has no authentication and no TLS, so `serve` binds only to loopback. Another `--host` (`0.0.0.0`, a LAN address, a host name) is refused with exit code 2. To reach the viewer from another machine, keep `serve` on loopback and put an authenticated HTTPS reverse proxy or an access-controlled tunnel in front of it. In a container, the container's loopback is not reachable through a published port, so `serve` there needs `--host 0.0.0.0 --unsafe-allow-remote` (and warns); publish the port to the host's loopback only (`-p 127.0.0.1:8765:8765`), so that nothing beyond the host can reach it.

`--unsafe-allow-remote` binds a non-loopback host anyway. It is never on by default, has no configuration key, and prints a warning to stderr on every start: anyone who can reach the address can read the whole schema, and the `Host` check is not access control. It is not a secure way to share the viewer.

The `Host` check accepts `localhost`, `127.0.0.1`, `::1` and the bound host, so a reverse proxy on the same machine must forward to `http://127.0.0.1:8765` with a loopback `Host` header (nginx's default for `proxy_pass` is `127.0.0.1:8765`). An SSH tunnel works the same way: `ssh -L 8765:127.0.0.1:8765 host`, then open `http://127.0.0.1:8765` locally.

## Compare

```bash
laranex-schema-viewer compare app:tenant_template=app:*
```

Compares scopes of a saved document without a database or application imports, and prints a summary (`--json`: the comparison objects). `--write` stores them in the document atomically, with a `comparison_incomplete` diagnostic for each incomplete one; a failed write leaves the document as it was. A request that cannot be expanded (an unknown connection, a `*` target that matches no schema, a comparison listed twice) or an invalid override fails with the shared contract's message and writes nothing. See [schema comparison](./comparison.md#compare-a-saved-document).

| Option | Default |
| --- | --- |
| `BASELINE=TARGET` | Config `[[tool.laranex-schema-viewer.comparisons]]`; repeatable |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json` |
| `--limit N` | `500`, for comparisons named on the command line |
| `--json` | Off |
| `--write` | Off |

## Check

```bash
laranex-schema-viewer check --output gate-report.json
```

The CI schema drift gate, a thin layer over the port of the shared reference gate: checks a saved document's comparisons, drift and reconciliation diagnostics and migration tracks against severity and certainty thresholds, without a database or application imports, and never writes the document. It checks the comparisons named on the command line (computed in memory first), else the configured `[[tool.laranex-schema-viewer.comparisons]]` (which must be stored in the document), else every stored one. Prints a summary (`--json`: the `laranex-schema-gate` 1.0 report). Exit code 0 passed, 1 a finding meets the thresholds, 2 the check is incomplete or invalid (an invalid or unreadable document, a requested comparison not found, an incomplete comparison or database inspection, a failed inspection, an unknown migration status, invalid arguments or configuration); 2 wins over 1. See [schema drift gate](./check.md).

| Option | Default |
| --- | --- |
| `BASELINE=TARGET` | The configured comparisons, else every stored one; named scopes are compared in memory, then checked; repeatable |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json` |
| `--severity LEVEL` | Config `[check] severity`, else `warning` |
| `--certainty LEVEL` | Config `[check] certainty`, else `certain` |
| `--limit N` | `500`, for comparisons named on the command line |
| `--json` | Off |
| `--output FILE` | None |

## Scan

```bash
laranex-schema-viewer scan --tracked --untracked --artifacts dist
```

Finds schema snapshots by content, not by file name, so a private schema is not committed or published by accident: laranex-schema documents at the top level or nested up to six levels deep, documents embedded in HTML (and, in artifact directories, in JavaScript bundles and source maps), and the legacy formats. It is a port of the shared scanner (`contract/scan.mjs` of laranex-schema-viewer-ui), with the same options, scopes, summary line, report and exit codes; the rules are the [snapshot scanning](https://laranex.github.io/schema-viewer-ui/contract#snapshot-scanning) section of the contract, and the 45 shared conformance cases pass. It reads only files and Git objects (Git runs as a subprocess, without a shell): no network, database or application code. Name at least one scope; without one, `scan` prints "name at least one scope" and exits 2.

| Option | Scans |
| --- | --- |
| `--tracked` | Every `.json`, `.html` and `.htm` file in the Git index, read from the index: the staged bytes, also of a file tracked before it was ignored |
| `--untracked` | The same files that Git would add: untracked and not ignored |
| `--artifacts DIR` | Every file under a build output directory, ignore rules not applied, JavaScript (`.js`, `.mjs`, `.cjs`) and source maps (`.map`) included; repeatable |
| `--history` | Every version of a candidate file reachable from any ref; a shallow clone fails the scan |
| `--allowlist FILE` | The allowlist; default `.laranex-schema-allowlist.json` at the Git top level, if present |
| `--report FILE` | Also write the `laranex-schema-scan` 1.0 report (JSON) |
| `--json` | Print the report instead of the summary |

The command prints a one-line count summary, never file content; paths and reasons are only in the report, which holds paths, commits, SHA-256 digests and fixed codes. Exit code 0 clean, 1 a confirmed exposure (a snapshot, an allowlisted file whose content changed, or allowlisted content at another path), 2 the scan failed or is incomplete (not a Git work tree, Git failed, an invalid or unreadable allowlist, a missing artifact directory, an unreadable file or one over 64 MiB, a symbolic link to a directory in an artifact directory, no history, a usage error, a report that cannot be written), 3 an uncertain finding (for example a truncated document). 2 wins over 1, 1 over 3. CI should fail on every non-zero exit.

An approved synthetic fixture, or a snapshot committed on purpose, is listed in the allowlist by exact path and SHA-256 (of the bytes with CRLF read as LF); nothing inside a document marks it as synthetic, and listing a file is a review decision, not proof that it is harmless. An entry that matches no file is reported as stale (a warning). Allowlists may include others under a `root`, such as laranex-schema-viewer-ui's `contract/synthetic.json`:

```json
{
  "format": "laranex-schema-allowlist",
  "version": "1.0",
  "entries": [{"path": "tests/fixtures/blog.json", "sha256": "…"}]
}
```

Not detected, and so still your responsibility to keep out of version control: SVG, PNG, DBML, Mermaid and Markdown exports, compressed or encoded payloads (such as base64+gzip offline pages), and files with other extensions. Gate reports (`check --output`) are not snapshots, but they describe the schema too: keep them as access-restricted CI artifacts with a short retention ([schema drift gate](./check.md#github-actions)). If a snapshot was committed or published, follow the [incident response](https://github.com/laranex/python-schema-viewer/blob/main/SECURITY.md#incident-response) in the security policy, starting with `scan --history`.

### Before each commit

A plain Git hook, `.git/hooks/pre-commit` (executable), scans what is staged:

```sh
#!/bin/sh
exec laranex-schema-viewer scan --tracked
```

With the [pre-commit](https://pre-commit.com) framework, add the hook this repository publishes to `.pre-commit-config.yaml`. It runs the scanner from the pinned release's source with the `python3` on `PATH` (3.10 or later) and Git, so it needs neither the package installed nor network access at commit time:

```yaml
repos:
  - repo: https://github.com/laranex/python-schema-viewer
    rev: vX.Y.Z  # a release that has the hook
    hooks:
      - id: laranex-schema-scan
        # args: [--tracked, --untracked]  # the default is [--tracked]
```

### In CI

Scan the checkout and everything you publish, fail on any non-zero exit, and keep the report only when it fails:

```yaml
- run: laranex-schema-viewer scan --tracked --untracked --artifacts dist --report scan-report.json
- if: failure()
  uses: actions/upload-artifact@v7
  with:
    name: scan-report
    path: scan-report.json
    if-no-files-found: ignore
    retention-days: 7
```

## MCP

```bash
laranex-schema-viewer mcp --snapshot /absolute/path/to/schema-viewer.json
```

`--snapshot` defaults to the configured `output`, else `schema-viewer.json`, read from the current directory's `pyproject.toml`. MCP clients may start the server from another directory, so use an absolute path in client configuration. The server uses stdio and reads a validated laranex-schema 2.x document once at startup; any other format or version fails startup with an error.

## Help and version

```bash
laranex-schema-viewer --version
laranex-schema-viewer --help
laranex-schema-viewer init --help
laranex-schema-viewer build --help
laranex-schema-viewer compare --help
laranex-schema-viewer check --help
laranex-schema-viewer scan --help
```
