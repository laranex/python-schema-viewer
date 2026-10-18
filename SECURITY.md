# Security policy

## Supported versions

Laranex Schema Viewer is alpha software. Security fixes are made on the `main` branch only.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately through [GitHub private vulnerability reporting](https://github.com/laranex/python-schema-viewer/security/advisories/new) and include the affected version or commit, reproduction steps, and the impact you observed.

Expect an acknowledgement within seven days. Please allow time for a fix before public disclosure.

## Security model

- `build` imports the application modules you name and runs their mapper callbacks in a separate extraction worker. At the default `process` level, the worker gets an allowlisted environment without credentials, a timeout, and cleanup of its process group. Its network, process and file-write guards are best-effort, not a sandbox. `--isolation container` adds no network and a read-only project. See [docs/security.md](docs/security.md). Build only from code you trust at the `process` level.
- `serve` and `mcp` read a saved laranex-schema document only. They do not import application code, connect to a database, run SQL or migrations, or read data rows.
- The viewer serves only packaged assets and the selected document, with `Cache-Control: no-store` on every response, and checks the `Host` header. It has no authentication and no TLS, and the `Host` check is not access control: it is the one exception to authentication in development, meant for a single developer on their own machine, and must never be shared. It binds to loopback only: `serve` refuses a host that is not loopback unless `--unsafe-allow-remote` is given, and then warns on every start. That flag is unsafe, not a supported deployment. For remote access, keep the server on `127.0.0.1` behind an authenticated HTTPS reverse proxy or an access-controlled tunnel ([CLI reference](docs/cli.md#serve)).
- Documents can reveal table, column, and index names. Review them before sharing, and never commit them, even to a private repository.

Reports in scope include path traversal or arbitrary file reads through the viewer, script injection from schema strings in the ERD, bypasses of host or bounds checks, and MCP behavior beyond read-only document queries.
