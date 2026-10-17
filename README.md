# Laranex Schema Viewer

Framework-independent schema documents, an interactive ERD, and read-only MCP tools for **SQLAlchemy 2.x, SQLModel, Django, Tortoise ORM, and Peewee**. Inspired by [Laravel Schema Viewer](https://github.com/laranex/laravel-schema-viewer).

Build once from model definitions, a live database's catalogs (read-only), or both reconciled with drift, then browse or query the saved JSON without starting your application. Every build writes a [laranex-schema 2.0](https://laranex.github.io/schema-viewer-ui/contract) document, the format the Laravel and Node.js schema viewers share. Foreign keys and declared ORM relationships remain separate throughout extraction, viewing, and MCP queries.

## Quick start

Requires Python 3.10+. Install from PyPI in an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp]'

cd /absolute/path/to/application
laranex-schema-viewer init     # optional: save defaults in pyproject.toml
laranex-schema-viewer build    # finds your models package; writes schema-viewer.json
laranex-schema-viewer serve
```

Replace `sqlmodel` with your ORM's extra: `sqlalchemy`, `django`, `tortoise`, or `peewee`. In a Django project, `build` reads the settings module from `manage.py`.

With [uv](https://docs.astral.sh/uv/), add it to your application instead: `uv add --dev 'laranex-schema-viewer[sqlmodel,mcp]'`, then prefix commands with `uv run`. The `python3` bundled with macOS is 3.9 and is not supported; see [installation](https://laranex.github.io/python-schema-viewer/installation.html).

Open **http://127.0.0.1:8765** to search tables, inspect indexes and relationships, and navigate the graph. The viewer is the shared [Laranex Schema Viewer UI](https://laranex.github.io/python-schema-viewer/viewer.html), vendored prebuilt: it needs no Node.js or web framework and loads nothing from the internet.

Build only trusted modules: imports and mapper callbacks execute application code. A declared document describes model metadata, not deployed database state; review documents before sharing.

## Documentation

- [Installation](https://laranex.github.io/python-schema-viewer/installation.html) and [quick start](https://laranex.github.io/python-schema-viewer/quick-start.html)
- [Supported ORMs](https://laranex.github.io/python-schema-viewer/orm-support.html): [SQLAlchemy](https://laranex.github.io/python-schema-viewer/sqlalchemy.html), [SQLModel](https://laranex.github.io/python-schema-viewer/sqlmodel.html), [Django](https://laranex.github.io/python-schema-viewer/django.html), [Tortoise ORM](https://laranex.github.io/python-schema-viewer/tortoise.html), [Peewee](https://laranex.github.io/python-schema-viewer/peewee.html), and [model discovery](https://laranex.github.io/python-schema-viewer/discovery.html)
- [CLI reference](https://laranex.github.io/python-schema-viewer/cli.html), [database introspection](https://laranex.github.io/python-schema-viewer/database.html), and [interactive viewer](https://laranex.github.io/python-schema-viewer/viewer.html)
- [MCP for Codex and Claude Code](https://laranex.github.io/python-schema-viewer/mcp.html)
- [Agent skills](https://laranex.github.io/python-schema-viewer/agent-skills.html) and [troubleshooting](https://laranex.github.io/python-schema-viewer/troubleshooting.html)

Install agent skills with `npx skills add laranex/python-schema-viewer`; see the [skills guide](https://laranex.github.io/python-schema-viewer/agent-skills.html).

## Contributing

See [AGENTS.md](https://github.com/laranex/python-schema-viewer/blob/main/AGENTS.md) for architecture, development commands, testing, and documentation maintenance. [Validation](https://github.com/laranex/python-schema-viewer/blob/main/docs/validation.md) and [skill verification](https://github.com/laranex/python-schema-viewer/blob/main/docs/agent-skills-validation.md) describe test coverage and limitations. Report vulnerabilities as described in [SECURITY.md](https://github.com/laranex/python-schema-viewer/blob/main/SECURITY.md). Release notes are in the [changelog](https://github.com/laranex/python-schema-viewer/blob/main/CHANGELOG.md).

Documentation Markdown lives in `docs/`; VitePress tooling lives in `.gh-page/`:

```bash
cd .gh-page
npm install
npm run dev -- --port 5173 --strictPort
```

Preview at **http://127.0.0.1:5173/python-schema-viewer/**.

## License

[MIT](https://github.com/laranex/python-schema-viewer/blob/main/LICENSE). See [NOTICE](https://github.com/laranex/python-schema-viewer/blob/main/NOTICE) for attribution.
