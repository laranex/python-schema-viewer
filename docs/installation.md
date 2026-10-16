# Installation

## Requirements

Use Python 3.10 or newer and an isolated virtual environment. SQLAlchemy 2.x and SQLModel support are optional installation extras.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'laranex-schema-viewer[sqlmodel,mcp]'
laranex-schema-viewer --version
```

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
