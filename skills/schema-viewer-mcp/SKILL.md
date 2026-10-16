---
name: schema-viewer-mcp
description: Configure or query Laranex Schema Viewer's read-only snapshot MCP server with Codex, Claude Code, or another stdio MCP client. Use for MCP setup, pagination, stale revisions, and tool errors.
license: MIT
---

# Use schema snapshots over MCP

Inspect existing client configuration and installed executables before changing setup. Skills do not install the Python package, register MCP, grant permissions, or authorize application imports.

## Configure a client

Use Python 3.10+ and an isolated environment with the `mcp` extra:

```bash
python -m pip install 'laranex-schema-viewer[mcp]'
```

The viewer and MCP need no ORM extras after a snapshot has been built. Use absolute executable and snapshot paths, preserving unrelated client settings.

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

Other clients must support stdio MCP and launch the same executable/arguments. Respect the client's trust settings. Never print application secrets or send logs to MCP stdout. No FastAPI server or database connection is required.

## Query the snapshot

Discover available tools; client prefixes vary. Start with `schema_overview`, then `schema_search` using `query_text`. Use exact returned opaque table/model IDs rather than guessing their encoding.

```json
{"tool":"schema_search","arguments":{"query_text":"users","limit":25}}
```

- `schema_get_table`: exact `table` ID; `section` is `columns`, `indexes`, `foreign_keys`, `constraints`, `models`, or `relationships`.
- `schema_get_model`: exact `model` ID; details and outgoing declarations.
- `schema_get_relationships`: optional `table`; `kind` is `orm` or `foreign_key`. Query both separately when comparing enforcement with declarations.

All tools accept `limit` (1–100, default 25), nonnegative `offset`, and optional `revision`. Pass the returned revision on subsequent pages. Follow `next_offset` until null with unchanged filters. On revision mismatch restart at offset 0 without a revision; do not combine results across revisions. Responses are bounded to 64 KiB; a single oversized item fails explicitly.

## Fallback and failures

If MCP is unavailable or an item is too large, read the user-selected saved JSON as data. Prefer `laranex_schema_viewer.read_snapshot` when installed; otherwise check `format="laranex-schema-viewer"`, `version=1`, and `scope="declared_metadata"`, disclosing limited validation. Collections are top-level `tables`, `models`, `relationships`, and `diagnostics`. Physical FKs are in `tables[].foreign_keys`; ORM declarations are in `relationships`.

MCP exposes diagnostic counts, not a diagnostic-message tool. Inspect JSON for `kind`, `subject`, and `message`. Do not invent `schema_get_hints`, diff/check/export commands, database query tools, or automatic rebuild behavior.

A server pins one validated snapshot for its lifetime. Restart after an explicitly authorized rebuild. Missing or invalid files fail startup; verify paths and package extras rather than starting an application or importing models to repair a query. Snapshot freshness is unknown without build evidence, and declared metadata cannot prove deployed database state.
