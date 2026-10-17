# Quick start

Build a small schema without creating an engine, opening a database, or running migrations.

## Define two models

Save the following as `models.py` in a scratch directory:

```python
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    tasks: Mapped[list["Task"]] = relationship(back_populates="owner")

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    owner: Mapped[User] = relationship(back_populates="tasks")
```

## Build a document

Run in that scratch directory, using the environment where you installed the package:

```bash
laranex-schema-viewer build
```

With no options, `build` finds the single `models.py` or `models` package in the current directory and writes `schema-viewer.json`, a laranex-schema 2.0 document. The output reports actual table, model, ORM relationship, and diagnostic counts. This example declares two tables, two models, one physical FK, and two ORM relationships.

## Open the viewer

```bash
laranex-schema-viewer serve
```

Open **http://127.0.0.1:8765**. Select a table for details, search for `owner_id`, and toggle the foreign-key and ORM layers independently.

## Connect an agent

```bash
laranex-schema-viewer mcp --snapshot /absolute/path/to/schema-viewer.json
```

This command speaks MCP over stdio, so configure it as a client subprocess rather than opening it in a browser. See [MCP integration](./mcp.md).
