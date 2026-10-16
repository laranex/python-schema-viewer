# Agent skills

Install the application-facing skills with the [Skills CLI](https://github.com/vercel-labs/skills):

```bash
npx skills add laranex/python-schema-viewer
```

This remote command reads the published GitHub repository. New skills in an uncommitted local checkout become remotely available only after a maintainer publishes them. To inspect this checkout now, use:

```bash
npx skills add /absolute/path/to/python-schema-viewer/skills --list
```

### Available skills

| Skill | Use it for |
| --- | --- |
| `python-schema-viewer` | Install the package, build snapshots, and open the ERD |
| `schema-discovery` | Inspect tables, columns, indexes, and mapped models |
| `relationship-analysis` | Analyze foreign keys separately from ORM declarations |
| `schema-diagnostics` | Investigate extraction diagnostics and missing coverage |
| `schema-viewer-mcp` | Configure and query the read-only stdio MCP server |

Canonical distributable sources live under `skills/<name>/SKILL.md`. Each folder is self-contained; schema discovery includes a bundled snapshot reference. Contributor architecture, testing, and security instructions belong in root `AGENTS.md`.

### Choose agents and skills

List the remotely available catalog:

```bash
npx skills add laranex/python-schema-viewer --list
```

Install a selected skill for Codex and Claude Code in the current project:

```bash
npx skills add laranex/python-schema-viewer \
  --skill schema-discovery --agent codex claude-code --yes
```

For an unpublished checkout, install all five distributable skills from the local source directory:

```bash
npx skills add /absolute/path/to/python-schema-viewer/skills \
  --skill '*' --agent codex claude-code --yes
```

The CLI manages agent installation paths: Codex uses `.agents/skills/`; Claude Code uses `.claude/skills/`. Other supported agents can be selected through `--agent` or the interactive installer. Standard Markdown/YAML skills do not depend on Codex-specific execution features. These are project-scoped commands; use `--global` only when intentionally installing for your user account. The CLI can create compatibility symlinks; `--copy` requests independent copies.

### Use skills with MCP

See [MCP client configuration](./mcp.md) for Codex and Claude Code settings.

1. Install the Python package with the `mcp` extra in an isolated environment:

   ```bash
   python -m pip install 'laranex-schema-viewer[mcp]'
   ```

2. Select an existing valid snapshot. Build separately with the appropriate ORM extra only when importing the application's models is part of the task.
3. Configure your client's stdio MCP server with the absolute executable and `mcp --snapshot /absolute/path/to/schema-viewer.json` arguments.
4. Install a skill and ask the agent to use it. Discover actual tool names from the client; prefixes can vary.

| Skill | MCP workflow |
| --- | --- |
| `python-schema-viewer` | Configure the package and snapshot server, then verify `schema_overview` |
| `schema-discovery` | `schema_overview` → `schema_search` → `schema_get_table` / `schema_get_model` |
| `relationship-analysis` | `schema_get_relationships` with `kind="foreign_key"` and `kind="orm"` separately |
| `schema-diagnostics` | Inspect tool evidence; read diagnostic messages from JSON because MCP exposes only their count |
| `schema-viewer-mcp` | Configure clients, discover tools, paginate results, and troubleshoot revisions or startup errors |

For example, ask: “Use schema-discovery with my MCP server to find tasks, then use relationship-analysis to compare its foreign keys with its ORM declarations.” The agent must use returned opaque IDs, not guess IDs from table names.

Tools return `revision` and `next_offset`. Keep filters and revision unchanged while paging until `next_offset` is null. The server pins one snapshot until restart; rebuild separately and restart to load a new revision. Missing MCP access falls back to the selected JSON snapshot without starting the application or rebuilding automatically.

### Usage examples

After installation, ask your agent to use a skill by name:

- “Use schema-discovery to list the tables and indexes in my saved snapshot.”
- “Use relationship-analysis to explain the tasks-to-users foreign key and ORM declarations separately.”
- “Use schema-diagnostics to investigate missing tables and recorded extraction errors.”
- “Use schema-viewer-mcp to configure the snapshot server and find the users table.”

Codex also supports explicit `$schema-discovery` invocation; Claude Code exposes skills through its skill interface. Installation is not MCP registration: configure the server separately, and install the Python package separately when needed.

Skills prefer available read-only MCP tools and fall back to the selected JSON snapshot. They do not authorize database access, migrations, automatic model imports, or publishing snapshots. A snapshot describes declared metadata and can be stale.

See [MCP integration](./mcp.md) for client configuration.
