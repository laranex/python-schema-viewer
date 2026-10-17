# Read-only MCP integration

Use an absolute executable path from the environment where the package and its `mcp` extra are installed. Use an absolute path to the saved document (`--snapshot`). Neither client needs access to the application's environment or database after the document has been built. The tools read one [laranex-schema 2.x](https://laranex.github.io/schema-viewer-ui/contract) document, declared, database or combined, the same for every supported ORM, so MCP needs no ORM extras.

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

| Tool | Arguments | Result items |
| --- | --- | --- |
| `schema_overview` | — | Table summaries |
| `schema_search` | `query_text`: literal, case-insensitive substring, 1–200 characters | Search hits |
| `schema_get_table` | Exact `table` ID; `section`: `columns` (default), `indexes`, `foreign_keys`, `constraints`, `models`, `relationships`, `links`, `derived_links`, or `diagnostics` | The section's objects |
| `schema_get_model` | Exact `model` ID | The relationships the model declares |
| `schema_get_relationships` | Optional `table` ID; `kind`: `orm` (default) or `foreign_key`, never mixed | Relationships, or foreign-key entries |

Every tool also accepts `limit` (1–100, default 25), `offset` (default 0), and an optional `revision`. Follow `nextOffset` until it is null, keeping the other arguments and passing the returned `revision`. Inputs are validated; IDs are looked up, never evaluated or treated as paths. Unknown IDs and revisions return tool errors.

Each server pins one validated document for its lifetime. Restart after rebuilding; the revision prevents pagination against a different document after a restart. A missing, invalid or oversized file, or one of another format or version, fails startup. These tools cannot rebuild documents or connect to a database. Schema comparison, migration and inferred-association tools are planned with the features that compute them; this version has none.

The implementation uses [the official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk), currently the 2.x `MCPServer` API. Tests initialize a genuine SDK client over subprocess stdio, discover all tools, call each, and verify read-only annotations and invalid inputs. Interactive Codex and Claude Code client sessions were not run in the implementation environment.

## Response format

This section is normative: other Laranex packages serve the same responses, and `tests/fixtures/mcp-conformance/` records them (see [Conformance](#conformance)). Field names are camelCase throughout. Items that are document entities are the contract objects exactly as the document serializes them, with every field and nothing renamed; the other item kinds are defined below.

### Envelope

A result is a JSON object with the tool's context fields (below), then:

| Field | Value |
| --- | --- |
| `revision` | The SHA-256 (hex) of the document's canonical JSON |
| `total` | The number of items the call matches |
| `offset` | The requested offset |
| `limit` | The requested limit |
| `items` | The page: at most `limit` items from `offset`, in the order given below |
| `nextOffset` | `offset` + the number of items returned, or null when that reaches `total` |

**Size.** A result's size is the number of UTF-8 bytes of its compact JSON: no whitespace, non-ASCII characters unescaped (JavaScript's `JSON.stringify`, Python's `json.dumps(value, separators=(",", ":"), ensure_ascii=False)`). The page is filled one item at a time, with `nextOffset` null while filling: an item that makes the result larger than 65,408 bytes (64 KiB less 128, kept for `nextOffset`) ends the page and is left for the next one. If it is the page's first item, the call fails with `One result exceeds the response byte limit`. If the finished result exceeds 65,536 bytes, the call fails with `Response metadata exceeds the response byte limit`.

**Errors.** A failed call is a tool error with one of these messages, checked in this order: the tool's own arguments (unknown table, section, model or kind; the search text), then `limit`, `offset` and `revision`.

| Message | When |
| --- | --- |
| `Unknown table ID; use schema_overview or schema_search` | `schema_get_table` with a table ID the document does not have |
| `Unknown table section: SECTION` | `schema_get_table` with another section |
| `Unknown model ID; use schema_search` | `schema_get_model` with a model ID the document does not have |
| `Unknown table ID` | `schema_get_relationships` with a table ID the document does not have |
| `kind must be orm or foreign_key` | `schema_get_relationships` with another kind |
| `query must contain 1 to 200 characters` | `schema_search` with an empty or longer `query_text` |
| `limit must be an integer from 1 to 100` | Any tool |
| `offset must be a nonnegative integer` | Any tool |
| `Document revision differs; restart pagination without revision` | `revision` is not the pinned document's |
| `One result exceeds the response byte limit`, `Response metadata exceeds the response byte limit` | See Size |

The MCP SDK also rejects out-of-range `limit` and `offset` values from the tool schema before the call reaches the query service.

### `schema_overview`

Context fields:

- `version`: the document's version, such as `"2.0"`; `source`: `declared`, `database` or `combined`; `generator` and `note`, as in the document.
- `connections`: per connection, in document order, `{id, name, driver, defaultSchema, coverage, tables}`, where `coverage` is the connection's coverage object and `tables` its number of tables.
- `counts`: `connections`, `tables`, `columns`, `indexes`, `foreignKeys`, `constraints`, `models`, `relationships`, `links`, `derivedLinks`, `diagnostics`, `comparisons`, `migrations`, `inferredAssociations` (the number of each), and `drift`: the number of tables, columns, indexes, foreign keys and constraints that carry drift.
- `diagnosticCounts`: diagnostics counted by `category` (`extraction`, `inspection`, `reconciliation`, `drift`, `lint`), `severity` (`error`, `warning`, `info`) and `certainty` (`certain`, `likely`), every key present.

Items are table summaries, one per table in document order (connections in order, then their tables): `{id, connection, name, schema, provenance, counts}`, where `connection` is the connection's id and `counts` has `columns`, `indexes`, `foreignKeys`, `constraints`, `drift` (the table and its columns, indexes, foreign keys and constraints that carry drift) and `diagnostics` (the table's diagnostics, as in the `diagnostics` section).

### `schema_search`

Context: `query`, the search text. Items are hits `{kind, id, table, name}`. Matching is a case-insensitive substring test after Unicode case folding (Python's `str.casefold`). For each table in document order, in this order:

- a `table` hit (`id` and `table` are the table's id) when the text matches the connection name, schema and table name joined by `.`, leaving out a null schema;
- a `column` hit (`id` null, `table` the table's id) for each column whose name matches;
- `index`, `foreignKey` and `constraint` hits (`id` the entity's id) for each named entity of the table whose name matches.

Then a `model` hit (`table` null, `name` the class name) for each model whose `className` matches, and a `relationship` hit (`table` the relationship's `table`) for each relationship whose `name`, a space and `type` match.

### `schema_get_table`

Context fields: `table`, the Table object without `columns`, `indexes`, `foreignKeys` and `constraints`; `connection`, `{id, name, driver, defaultSchema}` of its connection; `section`; and `counts`, the number of items in every section, keyed by section name. Sections, all in document order:

| Section | Items |
| --- | --- |
| `columns`, `indexes`, `foreign_keys`, `constraints` | The table's Column, Index, ForeignKey and Constraint objects |
| `models` | Models whose `tables` include the table |
| `relationships` | Relationships whose `table` or `relatedTable` is the table, whose pivot table is the table, or whose `through` lists it |
| `links` | Links whose `from` or `to` endpoint is on the table |
| `derived_links` | Derived links whose `from`, `to` or `via` names the table |
| `diagnostics` | Diagnostics whose subject is the table or one of its columns (`type` `table` or `column`, `id` the table's id), or one of its indexes, foreign keys or constraints |

### `schema_get_model`

Context: `model`, the Model object. Items: the Relationship objects whose `model` is that model.

### `schema_get_relationships`

Context: `kind` and `table` (the requested table ID, or null). With `kind` `orm`, items are Relationship objects: every relationship, or with a table, those the table's `relationships` section lists. With `kind` `foreign_key`, items are `{table, foreignKey}` for every foreign key in document order (`table` is the id of the table that declares it), or with a table, those declared on the table or referencing it (`foreignTable`).

### Conformance

`tests/fixtures/mcp-conformance/NAME.json` has, for each valid fixture of the shared contract (`tests/fixtures/contract/fixtures/valid/NAME.json`), `{document, revision, calls}`; `calls` lists `{tool, arguments, result}` or `{tool, arguments, error}` for every tool, every table and section, every model, pagination with the pinned revision, and invalid arguments. `byte-limit.json` embeds a synthetic document whose pages the size limit cuts. `python scripts/record_mcp_conformance.py` rewrites them, and `--check` reports stale ones.

## Example agent workflow

Ask: “Find the tasks table, show its indexes, and explain foreign keys separately from ORM relationships.” Search first, use the returned opaque table ID, and inspect both relationship kinds independently.

A table's diagnostics, with their messages, are its `diagnostics` section. Diagnostics about connections, models, relationships or the document are in the saved JSON's top-level `diagnostics`.

## Pair MCP with agent skills

Install the application-facing [agent skills](./agent-skills.md) for discovery, relationship analysis, diagnostics, and MCP setup. Skills prefer read-only tools and use JSON as a fallback. Installing skills does not register an MCP server; configure the client separately as described above.
