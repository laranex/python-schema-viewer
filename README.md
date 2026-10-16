# Laranex Schema Viewer

Framework-independent schema snapshots, an interactive ERD, and read-only MCP tools for **SQLAlchemy 2.x and SQLModel**. Inspired by [Laravel Schema Viewer](https://github.com/laranex/laravel-schema-viewer).

Build once from model definitions, then browse or query the saved JSON without starting your application or accessing its database. Foreign keys and declared ORM relationships remain separate throughout extraction, viewing, and MCP queries.

## Quick start

Requires Python 3.10+. Install from PyPI in an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp]'

laranex-schema-viewer build \
  --project-root /absolute/path/to/application \
  --module myapp.models --recursive \
  --output schema-viewer.json

laranex-schema-viewer serve --snapshot schema-viewer.json
```

Open **http://127.0.0.1:8765** to search tables, inspect indexes and relationships, and navigate the graph. The viewer needs no Node build or web framework.

Build only trusted modules: imports and mapper callbacks execute application code. Snapshots describe declared metadata, not deployed database state; review them before sharing.

## Documentation

- [Installation](https://laranex.github.io/python-schema-viewer/installation.html) and [quick start](https://laranex.github.io/python-schema-viewer/quick-start.html)
- [SQLAlchemy](https://laranex.github.io/python-schema-viewer/sqlalchemy.html), [SQLModel](https://laranex.github.io/python-schema-viewer/sqlmodel.html), and [model discovery](https://laranex.github.io/python-schema-viewer/discovery.html)
- [CLI reference](https://laranex.github.io/python-schema-viewer/cli.html) and [interactive viewer](https://laranex.github.io/python-schema-viewer/viewer.html)
- [MCP for Codex and Claude Code](https://laranex.github.io/python-schema-viewer/mcp.html)
- [Agent skills](https://laranex.github.io/python-schema-viewer/agent-skills.html) and [troubleshooting](https://laranex.github.io/python-schema-viewer/troubleshooting.html)

Install agent skills with `npx skills add laranex/python-schema-viewer`; see the [skills guide](https://laranex.github.io/python-schema-viewer/agent-skills.html).

## Contributing

See [AGENTS.md](https://github.com/laranex/python-schema-viewer/blob/main/AGENTS.md) for architecture, development commands, testing, and documentation maintenance. [Validation](https://github.com/laranex/python-schema-viewer/blob/main/docs/validation.md) and [skill verification](https://github.com/laranex/python-schema-viewer/blob/main/docs/agent-skills-validation.md) describe test coverage and limitations. Report vulnerabilities as described in [SECURITY.md](https://github.com/laranex/python-schema-viewer/blob/main/SECURITY.md).

Documentation Markdown lives in `docs/`; VitePress tooling lives in `.gh-page/`:

```bash
cd .gh-page
npm install
npm run dev -- --port 5173 --strictPort
```

Preview at **http://127.0.0.1:5173/python-schema-viewer/**.

## License

[MIT](https://github.com/laranex/python-schema-viewer/blob/main/LICENSE). See [NOTICE](https://github.com/laranex/python-schema-viewer/blob/main/NOTICE) for attribution.
