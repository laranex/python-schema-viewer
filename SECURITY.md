# Security policy

## Supported versions

Laranex Schema Viewer is alpha software. Security fixes are made on the `main` branch only.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately through [GitHub private vulnerability reporting](https://github.com/laranex/python-schema-viewer/security/advisories/new) and include the affected version or commit, reproduction steps, and the impact you observed.

Expect an acknowledgement within seven days. Please allow time for a fix before public disclosure.

## Security model

- `build` imports the application modules you name and runs their mapper callbacks. Import suppression is not a sandbox. Build only from trusted code.
- `serve` and `mcp` read a saved snapshot only. They do not import application code, connect to a database, run SQL or migrations, or read data rows.
- The viewer binds to `127.0.0.1` by default, serves only packaged assets and the selected snapshot, and checks the `Host` header. It has no authentication. Do not expose it on a shared network.
- Snapshots can reveal table, column, and index names. Review them before sharing, and do not commit snapshots of private applications.

Reports in scope include path traversal or arbitrary file reads through the viewer, script injection from schema strings in the ERD, bypasses of host or bounds checks, and MCP behavior beyond read-only snapshot queries.
