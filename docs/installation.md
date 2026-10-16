# Installation

## Requirements

Use Python 3.10 or newer and an isolated virtual environment. SQLAlchemy 2.x and SQLModel support are optional installation extras.

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

To use only the viewer and MCP server on saved snapshots, install the command globally:

```bash
uv tool install 'laranex-schema-viewer[mcp]'
```

Building a snapshot imports your models, so run `build` in an environment that has your application's dependencies, such as `uv run` inside the application.

## Choose your extras

| Extra | Purpose |
| --- | --- |
| `sqlalchemy` | SQLAlchemy 2.x metadata and mapper extraction |
| `sqlmodel` | SQLModel extraction through the SQLAlchemy adapter |
| `mcp` | Read-only stdio MCP server |
| `dev` | ORM, MCP, lint, and Python build dependencies for contributors |

Install only the extras you need. For example, SQLAlchemy without MCP:

```bash
python -m pip install 'laranex-schema-viewer[sqlalchemy]'
```

Build using an environment that can import your application's dependencies. Once a snapshot exists, the viewer can run without importing the application.

## Install from source

To contribute or try unreleased changes, install an editable checkout:

```bash
git clone https://github.com/laranex/python-schema-viewer.git
cd python-schema-viewer
python -m pip install -e '.[dev]'
```

Continue with [Quick start](./quick-start.md).
