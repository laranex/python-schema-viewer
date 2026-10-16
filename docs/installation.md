# Installation

## Requirements

Use Python 3.10 or newer and an isolated virtual environment. SQLAlchemy 2.x and SQLModel support are optional installation extras.

This MVP is installed from source; these instructions do not assume a published PyPI release.

```bash
git clone https://github.com/laranex/python-schema-viewer.git
cd python-schema-viewer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[sqlmodel,mcp]'
laranex-schema-viewer --version
```

## Choose your extras

| Extra | Purpose |
| --- | --- |
| `sqlalchemy` | SQLAlchemy 2.x metadata and mapper extraction |
| `sqlmodel` | SQLModel extraction through the SQLAlchemy adapter |
| `mcp` | Read-only stdio MCP server |
| `dev` | ORM, MCP, lint, and Python build dependencies for contributors |

For an existing application's isolated environment, install from an absolute checkout path:

```bash
python -m pip install -e '/absolute/path/to/python-schema-viewer[sqlmodel,mcp]'
```

Build using an environment that can import your application's dependencies. Once a snapshot exists, the viewer can run without importing the application.

Continue with [Quick start](./quick-start.md).
