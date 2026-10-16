# SQLModel integration

SQLModel uses SQLAlchemy metadata and mappers, so it shares the SQLAlchemy adapter. Install the `sqlmodel` extra and build inside an environment with your application's dependencies.

## Table declarations

```python
from sqlmodel import Field, SQLModel

class Team(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str

class Member(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)
```

These definitions produce a foreign-key edge even though they declare no `Relationship()` attributes.

## Discover your model package

```bash
laranex-schema-viewer build \
  --project-root /absolute/path/to/backend \
  --module app.models --recursive \
  --output /absolute/path/to/schema-viewer.json
```

Only classes declared with `table=True` become SQLModel tables. Request/response classes and other validation models are not database tables.

## Relationship declarations

SQLModel `Relationship()` attributes are extracted from the underlying SQLAlchemy mappers. Their presence is independent of physical FK constraints. Inspect both layers when reviewing a join or an association table.

Avoid importing application entry points that initialize services when a dedicated model package is sufficient.
