# CLI and configuration

The MVP is configured through command-line arguments. It does not require a framework plugin or a dedicated configuration file.

## Build

```bash
laranex-schema-viewer build --module app.models --recursive
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--project-root PATH` | Current directory | Application import root |
| `--module MODULE` | None | Import a module; repeatable |
| `--target MODULE:ATTRIBUTE` | None | Select metadata, base, or registry; repeatable |
| `--recursive` | Off | Discover selected package submodules |
| `--output PATH` | `schema-viewer.json` | Write the snapshot |

## Serve

```bash
laranex-schema-viewer serve --snapshot schema-viewer.json --port 8765
```

| Option | Default |
| --- | --- |
| `--snapshot PATH` | `schema-viewer.json` |
| `--host HOST` | `127.0.0.1` |
| `--port PORT` | `8765` |

Keep the loopback default for local inspection. The server serves allowlisted viewer assets and the selected snapshot.

## MCP

```bash
laranex-schema-viewer mcp --snapshot /absolute/path/to/schema-viewer.json
```

`--snapshot` defaults to `schema-viewer.json`. Use an absolute path in client configuration. The server uses stdio and reads a validated snapshot once at startup.

## Help and version

```bash
laranex-schema-viewer --version
laranex-schema-viewer --help
laranex-schema-viewer build --help
```
