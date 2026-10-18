# Installation

## Requirements

Use Python 3.10 or newer and an isolated virtual environment. Each supported ORM is an optional installation extra.

CI tests Python 3.10 and 3.14 on every pull request and 3.10 to 3.15 before each release, with PostgreSQL, MySQL and MariaDB on both SQLAlchemy 2.0 (Python 3.10) and 2.1 (Python 3.11 and later). On Python 3.10 and 3.11 the `django` extra installs Django 5.2; on 3.12 and later, Django 6.

Check your interpreter first with `python3 --version`. The `python3` bundled with macOS is Python 3.9, which pip skips this package for; use a newer Python from [python.org](https://www.python.org/downloads/), Homebrew, or [uv](#install-with-uv).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp]'
laranex-schema-viewer --version
```

## Install with uv

[uv](https://docs.astral.sh/uv/) can download a suitable Python for you. Add the package to a uv-managed application as a development dependency:

```bash
uv add --dev 'laranex-schema-viewer[sqlmodel,mcp]'
uv run laranex-schema-viewer --version
```

Or create a standalone environment:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install 'laranex-schema-viewer[sqlmodel,mcp]'
```

To use only the viewer and MCP server on saved documents, install the command globally:

```bash
uv tool install 'laranex-schema-viewer[mcp]'
```

Building a document imports your models, so run `build` in an environment that has your application's dependencies, such as `uv run` inside the application.

## Choose your extras

| Extra | Purpose |
| --- | --- |
| `sqlalchemy` | SQLAlchemy 2.x metadata and mapper extraction |
| `sqlmodel` | SQLModel extraction through the SQLAlchemy adapter |
| `django` | Django model metadata through `Model._meta` |
| `tortoise` | Tortoise ORM 1.x models through `Tortoise.init_models` |
| `peewee` | Peewee 3.19+ and 4.x models |
| `mcp` | Read-only stdio MCP server |
| `postgresql`, `mysql` | Drivers for [database introspection](./database.md); SQLite needs none |
| `dev` | ORM, MCP, lint, and Python build dependencies for contributors |

Install only the extras you need. For example, SQLAlchemy without MCP:

```bash
python -m pip install 'laranex-schema-viewer[sqlalchemy]'
```

Build using an environment that can import your application's dependencies. Once a document exists, the viewer can run without importing the application.

## Install from source

To contribute or try unreleased changes, install an editable checkout. The prebuilt viewer and the shared contract files are not in the repository: `scripts/fetch_ui.py` downloads the `laranex-schema-viewer-ui` npm tarball that `ui.lock.json` pins and checks its SHA-512 first. Builds never download anything and stop with "run python scripts/fetch_ui.py" until it has run.

```bash
git clone https://github.com/laranex/python-schema-viewer.git
cd python-schema-viewer
python scripts/fetch_ui.py
python -m pip install -e '.[dev]'
```

Installing from the PyPI sdist needs neither the fetch step nor Node.js: the sdist contains the verified assets.

Continue with [Quick start](./quick-start.md).
