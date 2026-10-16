# Laranex Schema Viewer

Framework-independent schema snapshots, an interactive ERD, and read-only MCP tools for **SQLAlchemy 2.x and SQLModel**. Inspired by [Laravel Schema Viewer](https://github.com/laranex/laravel-schema-viewer).

Build once from model definitions, then browse or query the saved JSON without starting your application or accessing its database. Foreign keys and declared ORM relationships remain separate throughout extraction, viewing, and MCP queries.

## Quick start

Requires Python 3.10+. This is an alpha release that is not yet published to PyPI; install from GitHub in an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp] @ git+https://github.com/laranex/python-schema-viewer.git'

laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --module myapp.models --recursive \
  --output schema-viewer.json

laranex-schema-viewer serve --snapshot schema-viewer.json
```

Open **http://127.0.0.1:8765** to search tables, inspect indexes and relationships, and navigate the graph. The viewer needs no Node build or web framework.

Build only trusted modules: imports and mapper callbacks execute application code. Snapshots describe declared metadata, not deployed database state; review them before sharing.

## Documentation

- [Installation](docs/installation.md) and [quick start](docs/quick-start.md)
- [SQLAlchemy](docs/sqlalchemy.md), [SQLModel](docs/sqlmodel.md), and [model discovery](docs/discovery.md)
- [CLI reference](docs/cli.md) and [interactive viewer](docs/viewer.md)
- [MCP for Codex and Claude Code](docs/mcp.md)
- [Agent skills](docs/agent-skills.md) and [troubleshooting](docs/troubleshooting.md)

Install agent skills with `npx skills add laranex/python-schema-viewer`; see the [skills guide](docs/agent-skills.md).

## Contributing

See [AGENTS.md](AGENTS.md) for architecture, development commands, testing, and documentation maintenance. [Validation](docs/validation.md) and [skill verification](docs/agent-skills-validation.md) describe test coverage and limitations. Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

Documentation Markdown lives in `docs/`; VitePress tooling lives in `.gh-page/`:

```bash
cd .gh-page
npm install
npm run dev -- --port 5173 --strictPort
```

Preview at **http://127.0.0.1:5173/python-schema-viewer/**.

## License

[MIT](LICENSE). See [NOTICE](NOTICE) for attribution.
