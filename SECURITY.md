# Security policy

## Supported versions

Laranex Schema Viewer is alpha software. Security fixes are made on the `main` branch only.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately through [GitHub private vulnerability reporting](https://github.com/laranex/python-schema-viewer/security/advisories/new) and include the affected version or commit, reproduction steps, and the impact you observed.

Expect an acknowledgement within seven days. Please allow time for a fix before public disclosure.

## Security model

- `build` imports the application modules you name and runs their mapper callbacks in a separate extraction worker. At the default `process` level, the worker gets an allowlisted environment without credentials, a timeout, and cleanup of its process group. Its network, process and file-write guards are best-effort, not a sandbox. `--isolation container` adds no network and a read-only project. See [docs/security.md](docs/security.md). Build only from code you trust at the `process` level.
- `serve` and `mcp` read a saved laranex-schema document only. They do not import application code, connect to a database, run SQL or migrations, or read data rows.
- The viewer binds to `127.0.0.1` by default, serves only packaged assets and the selected document, and checks the `Host` header. It has no authentication. Do not expose it on a shared network.
- Documents can reveal table, column, and index names. Review them before sharing, and do not commit documents of private applications.

Reports in scope include path traversal or arbitrary file reads through the viewer, script injection from schema strings in the ERD, bypasses of host or bounds checks, and MCP behavior beyond read-only document queries.
