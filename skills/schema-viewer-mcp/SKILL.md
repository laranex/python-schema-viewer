---
name: schema-viewer-mcp
description: Configure Laranex Schema Viewer's read-only MCP server (over a saved laranex-schema 2.0 document) for Codex, Claude Code, or another stdio MCP client, and troubleshoot its tools - pagination, revision mismatches, oversized items, stored schema comparisons, and startup errors.
license: MIT
---

# Use schema documents over MCP

The server serves one saved laranex-schema 2.x document over stdio. It needs no web server, database, or ORM extra. Skills do not install the package, register servers, grant permissions, or authorize model imports.

## Configure a client

Inspect the existing client configuration first and preserve unrelated settings. Install the `mcp` extra in an isolated Python 3.10+ environment:

```bash
python -m pip install 'laranex-schema-viewer[mcp]'
```

Use absolute paths for the executable and the document.

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
| `schema_overview` | — | source, generator, connections (driver, coverage), counts, and table summaries |
| `schema_search` | `query_text` | hits `{kind, id, table, name}` for tables, columns, indexes, foreign keys, constraints, models, and relationships |
| `schema_get_table` | `table`, `section` (`columns`, `indexes`, `foreign_keys`, `constraints`, `models`, `relationships`, `links`, `derived_links`, `diagnostics`) | the table, its connection, and one section's objects |
| `schema_get_model` | `model` | the model and the relationships it declares |
| `schema_get_relationships` | optional `table`, `kind` (`orm` or `foreign_key`) | relationship objects, or `{table, foreignKey}` entries |
| `schema_compare` | `section` (`comparisons` or `differences`), optional `comparison`, `baseline` and `target` (`CONNECTION` or `CONNECTION:SCHEMA`), and for differences `object_type`, `change`, `severity`, `certainty` | stored comparison summaries, or `{comparison, difference}` entries |

Items are the document's own objects with its camelCase field names. Client prefixes vary; discover the actual tool names and pass only IDs the tools returned. All tools accept `limit` (1–100, default 25), `offset`, and `revision`, and every response has `revision`, `total`, `offset`, `limit`, `items`, and `nextOffset`; pass the returned `revision` and follow `nextOffset` until null with unchanged filters. Never combine results across revisions.

Stored comparisons (between tenant schemas or connections) are read, never computed:

```json
{"tool":"schema_compare","arguments":{"section":"differences","severity":"error","limit":25}}
```

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Revision mismatch | The server restarted on a new document; restart paging at offset 0 without `revision`. |
| Item too large (responses are capped at 64 KiB) | Read that item from the saved JSON. |
| Need diagnostics | A table's are its `diagnostics` section; the rest are top-level `diagnostics[]` in the JSON. |
| Server fails to start | Check the executable, the `mcp` extra, and the document path. A file of another format or version is refused; an authorized `laranex-schema-viewer build` writes laranex-schema 2.0. Do not start the application or import models to repair it. |
| Results look stale | The server pins one document for its lifetime; restart it after an authorized rebuild. |
| No comparisons, or `omitted` above zero | Comparisons are computed by an authorized database build or `laranex-schema-viewer compare --write`; differences beyond the configured limit are counted, not listed. An incomplete comparison is partial evidence. |

For the JSON fallback, prefer `laranex_schema_viewer.read_document`; otherwise check `format="laranex-schema"` and a `version` of `2.x`, and disclose limited validation.

There are no hints, check, export, SQL, or rebuild tools, and migration status and inferred associations are not produced yet. Freshness is unknown without build evidence, and declared metadata does not prove deployed state.
