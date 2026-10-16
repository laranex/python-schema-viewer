---
name: schema-viewer-mcp
description: Configure Laranex Schema Viewer's read-only snapshot MCP server for Codex, Claude Code, or another stdio MCP client, and troubleshoot its tools - pagination, revision mismatches, oversized items, and startup errors.
license: MIT
---

# Use schema snapshots over MCP

The server serves one saved snapshot over stdio. It needs no web server, database, or ORM extra. Skills do not install the package, register servers, grant permissions, or authorize model imports.

## Configure a client

Inspect the existing client configuration first and preserve unrelated settings. Install the `mcp` extra in an isolated Python 3.10+ environment:

```bash
python -m pip install 'laranex-schema-viewer[mcp]'
```

Use absolute paths for the executable and the snapshot.

Codex `config.toml`:

```toml
[mcp_servers.laranex_schema]
command = "/absolute/path/to/.venv/bin/laranex-schema-viewer"
args = ["mcp", "--snapshot", "/absolute/path/to/schema-viewer.json"]
```

Claude Code project `.mcp.json`:

```json
{
  "mcpServers": {
    "laranex_schema": {
      "type": "stdio",
      "command": "/absolute/path/to/.venv/bin/laranex-schema-viewer",
      "args": ["mcp", "--snapshot", "/absolute/path/to/schema-viewer.json"]
    }
  }
}
```

Other stdio clients launch the same command and arguments. Respect the client's trust settings, and never print secrets or logs to stdout.

## Tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `schema_overview` | — | counts and table IDs |
| `schema_search` | `query_text` | matching tables, columns, and models |
| `schema_get_table` | `table`, `section` (`columns`, `indexes`, `foreign_keys`, `constraints`, `models`, `relationships`) | one section |
| `schema_get_model` | `model` | model details and outgoing relationships |
| `schema_get_relationships` | optional `table`, `kind` (`orm` or `foreign_key`) | relationship records |

Client prefixes vary; discover the actual tool names and pass only IDs the tools returned. All tools accept `limit` (1–100, default 25), `offset`, and `revision`; pass the returned `revision` and follow `next_offset` until null with unchanged filters. Never combine results across revisions.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Revision mismatch | The server restarted on a new snapshot; restart paging at offset 0 without `revision`. |
| Item too large (responses are capped at 64 KiB) | Read that item from the saved JSON. |
| Need diagnostic messages | MCP only counts diagnostics; read `diagnostics[]` (`kind`, `subject`, `message`) from the JSON. |
| Server fails to start | Check the executable, the `mcp` extra, and the snapshot path. Do not start the application or import models to repair it. |
| Results look stale | The server pins one snapshot for its lifetime; restart it after an authorized rebuild. |

For the JSON fallback, prefer `laranex_schema_viewer.read_snapshot`; otherwise check `format="laranex-schema-viewer"`, `version=1`, and `scope="declared_metadata"` and disclose limited validation.

There are no hints, diff, check, export, SQL, or rebuild tools. Snapshot freshness is unknown without build evidence, and declared metadata does not prove deployed state.
