# Security policy

This page covers what is specific to the Python package. The cross-framework policy for the shared viewer is the [Laranex Schema Viewer UI security page](https://laranex.github.io/schema-viewer-ui/security.html).

## Supported versions

Laranex Schema Viewer is alpha software. Security fixes are made on the `main` branch only and ship in the next release on PyPI; earlier releases are not patched.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately through [GitHub private vulnerability reporting](https://github.com/laranex/python-schema-viewer/security/advisories/new) and include the affected version or commit, reproduction steps, and the impact you observed. If that form is not available to you, open a public issue that asks for a private contact, without any details of the vulnerability.

Expect an acknowledgement within seven days. Please allow time for a fix before public disclosure.

Reports in scope include path traversal or arbitrary file reads through the viewer, script injection from schema strings in the ERD, bypasses of host or bounds checks, MCP behavior beyond read-only document queries, credentials reaching the extraction worker, writes through database introspection, and snapshots the scanner should detect but misses.

## Security model

- `build` imports the application modules you name and runs their mapper callbacks in a separate extraction worker. At the default `process` level, the worker gets an allowlisted environment without credentials, a timeout, and cleanup of its process group. Its network, process and file-write guards are best-effort, not a sandbox. `--isolation container` adds no network and a read-only project. See [build security](docs/security.md). Build only from code you trust at the `process` level.
- `serve` and `mcp` read a saved laranex-schema document only. They do not import application code, connect to a database, run SQL or migrations, or read data rows.
- The viewer serves only packaged assets and the selected document, with `Cache-Control: no-store` on every response, and checks the `Host` header. It has no authentication and no TLS, and the `Host` check is not access control: it is the one exception to authentication in development, meant for a single developer on their own machine, and must never be shared. It binds to loopback only: `serve` refuses a host that is not loopback unless `--unsafe-allow-remote` is given, and then warns on every start. That flag is unsafe, not a supported deployment. For remote access, keep the server on `127.0.0.1` behind an authenticated HTTPS reverse proxy or an access-controlled tunnel ([CLI reference](docs/cli.md#serve)).

| Concern | The package enforces | You (the application or repository owner) must |
| --- | --- | --- |
| Built-in viewer (`serve`) | Loopback bind; refuses another host without `--unsafe-allow-remote`, which warns on every start; `Host` check; only the files the viewer manifest lists, plus the document; `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and a restrictive Content-Security-Policy on every response, errors included | Keep it on loopback, for yourself only; put an authenticated HTTPS reverse proxy or an access-controlled tunnel in front of it for anything else |
| The viewer or a document served any other way (your own server, a proxy, static hosting, a CDN, object storage) | Nothing: the viewer UI has no authentication | Authenticate the page, every asset, the document and every download server-side; send `no-store`; use HTTPS beyond localhost; never deploy it to production |
| MCP (`mcp`) | stdio only, read-only tools over one saved document, validated inputs, responses bounded to 64 KiB; it reads no other file and connects to no database | Never expose it over a network without your own authentication |
| Build isolation (`build`) | Extraction worker with an allowlisted environment (credential-like names refused, database URLs never passed), a timeout, process-group cleanup, a strictly validated result; `container`: no network, read-only project, dropped capabilities. The in-process guards are best-effort | Build only code you trust at the `process` level; use `container` in CI and for unreviewed code, with an image that holds no credentials |
| Database introspection | A separate worker that never imports application code, gets the URL only on stdin, runs read-only sessions the database confirms, refuses a role with write privileges unless `--allow-write-privileges` | Use a dedicated read-only role, or a disposable database in CI |
| Snapshots in Git | `scan`; `init` writes a `.gitignore` block; `build` stops when Git tracks its output and warns when the output is not ignored | Run `scan` in a pre-commit hook and in CI, fail on every non-zero exit, and review allowlist changes |
| Viewer downloads and exports | Warnings in the documentation | Store and share exported files responsibly (see [responsibility boundary](#responsibility-boundary)) |
| Gate and scan reports (`check --output`, `scan --report`) | The scan report holds paths, commits, digests and fixed codes, never file content | Keep both as access-restricted CI artifacts with a short retention |
| Package distribution | sdists and wheels hold only the package source, the runtime viewer assets and JSON Schemas, and the top-level notices: no tests, fixtures, docs or demo documents (`scripts/check_wheel.py`); CI scans the unpacked sdist and wheel | Nothing |

## Schema snapshots are sensitive

A laranex-schema document of a private schema reveals table, column, index, constraint and relationship names, defaults, comments and connection names. Treat as equally sensitive every copy and export of it: the JSON document, the viewer's offline HTML page, DBML, Mermaid, Markdown, SVG and PNG downloads, and the gate report of `check`. Review them before sharing, and never commit them, even to a private repository. Never attach them to public issues or upload them as artifacts of a public repository's workflows.

## Protecting the page alone is insufficient

When the viewer or a document is served by anything other than `serve` on loopback, protect every delivery path server-side with the same authentication: the page, every viewer asset, `snapshot.json` or an inline document, downloads, embedding in an iframe, caches and CDNs, and object storage. A static or CDN path must not bypass it. Send `Cache-Control: no-store` on every sensitive response, errors included, and use HTTPS beyond localhost. Authentication is enforced only by the server or the integrating application, never by the browser UI.

## Development-only viewer

The viewer is a development tool. Never expose it in production. Authentication is mandatory, even in local development, with one exception: `serve` on loopback, for a single developer on their own machine, never shared. `--unsafe-allow-remote` is unsafe, as its name says; reach the viewer from elsewhere only through an authenticated HTTPS reverse proxy or an access-controlled tunnel, with `serve` still on loopback. In a container, publish the port to the host's loopback only (`-p 127.0.0.1:8765:8765`).

## MCP

`laranex-schema-viewer mcp` runs on local stdio only and is read-only; there is no remote transport. Any HTTP bridge you put in front of it must add its own authentication. Responses contain only the saved document's data: no credentials, environment, database URLs or file system paths. See [MCP](docs/mcp.md).

## Git protection

`laranex-schema-viewer scan` finds laranex-schema documents by content and judges them against an allowlist ([CLI reference](docs/cli.md#scan)):

```bash
laranex-schema-viewer scan --tracked --untracked --artifacts dist --report scan-report.json
```

- **Exit codes:** 0 clean, 1 a confirmed exposure, 2 the scan failed or is incomplete, 3 an uncertain exposure (2 wins over 1, 1 over 3). CI must fail on every non-zero exit.
- **Allowlist:** `.laranex-schema-allowlist.json` lists approved synthetic fixtures, or snapshots committed on purpose, by exact path and SHA-256. Adding an entry is an explicit review decision, not proof that the content is harmless.
- **`.gitignore`:** `init` appends a block that ignores `/schema-viewer.json`, `/schema-viewer.html` and the configured output. `build` stops with exit 1 when Git tracks its output, and warns when the output is not ignored.
- **Pre-commit:** a plain `.git/hooks/pre-commit` running `laranex-schema-viewer scan --tracked`, or the `laranex-schema-scan` hook this repository publishes for the pre-commit framework ([before each commit](docs/cli.md#before-each-commit)).

## Limitations

- Detection is by content, for the supported formats and file extensions only: `.json`, `.html` and `.htm` in the repository, and also JavaScript and source maps in artifact directories.
- Compressed or encoded payloads, for example base64+gzip offline HTML pages, are not scanned.
- Renamed files with other extensions, HTML-entity-escaped documents, and SVG, PNG, DBML, Mermaid and Markdown exports are not detectable by content. Keeping them out of version control is your responsibility.
- The scanner guards against accidents, not adversaries.
- The extraction worker's in-process guards are best-effort, not a sandbox; only the `container` level adds operating-system limits ([remaining risks](docs/security.md#remaining-risks)).
- A file over 64 MiB, or history in a shallow clone, cannot be scanned and fails the scan (exit 2).
- A document records what was declared or inspected when it was built; it does not prove the deployed schema.

## Incident response

When a snapshot or an export reached Git or was published:

1. Stop tracking it with `git rm --cached -- <path>` (the file is kept), add the ignore rule (`laranex-schema-viewer init` adds the default paths), and commit.
2. Treat anything already pushed or shared as disclosed. Find every copy in the history with `laranex-schema-viewer scan --history --report scan-report.json` (it needs a full clone).
3. Rewrite history (for example with `git filter-repo`) only with the repository owner's approval, and coordinate the force-push and fresh clones with everyone who has one.
4. Purge CI artifacts, caches, CDN and GitHub Pages copies, and forks where possible.
5. Rotate anything the schema revealed, such as secrets in column defaults or comments. Rotation is the fix; rewriting history is cleanup.

## Responsibility boundary

Laranex Schema Viewer is responsible for safe defaults, protection of the snapshots it generates, scanner checks for the supported formats, and secure serving of the viewer. Once you deliberately download or export a standalone file (offline HTML, JSON, DBML, Markdown, Mermaid, SVG or PNG), storing, sharing and distributing it is the responsibility of you and the repository owner; the package does not track or control such files.

## Disclaimer

The software is provided under the [MIT license](LICENSE), including its "AS IS" disclaimer of warranty. Nothing on this page adds a warranty or changes the license.
