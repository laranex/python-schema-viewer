# Read-only MCP integration

Use an absolute executable path from the environment where the package and its `mcp` extra are installed. Use an absolute snapshot path. Neither client needs access to the application's environment after the snapshot has been built. The tools read the same snapshot format for every supported ORM, so MCP needs no ORM extras.

## Codex

Add to your Codex `config.toml`:

```toml
[mcp_servers.laranex_schema]
command = "/absolute/path/to/.venv/bin/laranex-schema-viewer"
args = ["mcp", "--snapshot", "/absolute/path/to/schema-viewer.json"]
```

This follows the [official Codex MCP configuration documentation](https://developers.openai.com/codex/mcp/). No client configuration was modified automatically.

## Claude Code

Project `.mcp.json` example:

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

Alternatively:

```bash
claude mcp add --transport stdio laranex_schema -- \
  /absolute/path/to/.venv/bin/laranex-schema-viewer \
  mcp --snapshot /absolute/path/to/schema-viewer.json
```

See the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Tool usage

| Tool | Input and result |
| --- | --- |
| `schema_overview` | Counts and paginated table summaries with opaque IDs |
| `schema_search` | `query_text`: literal case-insensitive substring, 1–200 characters |
| `schema_get_table` | Exact `table` ID and section: `columns`, `indexes`, `foreign_keys`, `constraints`, `models`, or `relationships` |
| `schema_get_model` | Exact `model` ID; model details and paginated outgoing declarations |
| `schema_get_relationships` | Optional table ID; `kind` is `orm` or `foreign_key`, never implicitly mixed |

Every tool accepts `limit` (1–100, default 25), `offset` (default 0), and an optional `revision`. Follow `next_offset` until it is null, preserving the other arguments and revision. Responses stop before 64 KiB; a single oversized item produces an explicit error. Inputs are validated; IDs are looked up, never evaluated or treated as paths. Unknown IDs and revisions return tool errors.

Each server pins a validated snapshot for its lifetime. Restart after rebuilding; the revision prevents pagination against a different snapshot after a restart. A missing, invalid, oversized, or unsupported-version snapshot fails startup. These tools cannot rebuild snapshots or connect to a database.

The implementation uses [the official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk), currently the 2.x `MCPServer` API. Tests initialize a genuine SDK client over subprocess stdio, discover all tools, call each, and verify read-only annotations and invalid inputs. Interactive Codex and Claude Code client sessions were not run in the implementation environment.


## Example agent workflow

Ask: “Find the tasks table, show its indexes, and explain foreign keys separately from ORM relationships.” Search first, use the returned opaque table ID, and inspect both relationship kinds independently.

MCP exposes diagnostic counts, not diagnostic messages. Read top-level `diagnostics` from the selected saved JSON for message details.

## Pair MCP with agent skills

Install the application-facing [agent skills](./agent-skills.md) for discovery, relationship analysis, diagnostics, and MCP setup. Skills prefer read-only tools and use JSON as a fallback. Installing skills does not register an MCP server; configure the client separately as described above.
