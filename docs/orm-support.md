# Supported ORMs

Each ORM has its own adapter and optional extra. All adapters produce the same laranex-schema 2.0 document, so the viewer, MCP tools, and agent skills work the same for every ORM.

| ORM | Extra | Tested versions | Selection |
| --- | --- | --- | --- |
| [SQLAlchemy](./sqlalchemy.md) | `sqlalchemy` | 2.0 | Detected from modules, or `--orm sqlalchemy` |
| [SQLModel](./sqlmodel.md) | `sqlmodel` | 0.0.22+ | Uses the SQLAlchemy adapter |
| [Django](./django.md) | `django` | 4.2, 5.2, 6.1 | Settings from `--settings`, configuration, or `manage.py` |
| [Tortoise ORM](./tortoise.md) | `tortoise` | 1.1 | Detected from modules, or `--orm tortoise` |
| [Peewee](./peewee.md) | `peewee` | 3.19, 4.5 | Detected from modules, or `--orm peewee` |

When imported modules declare models for more than one ORM, `build` stops and asks for `--orm`.

## Feature matrix

| Feature | SQLAlchemy / SQLModel | Django | Tortoise | Peewee |
| --- | --- | --- | --- | --- |
| Composite primary keys | Yes | Django 5.2+ | Not in Tortoise | Yes |
| Database foreign keys | Yes, with actions | Yes; actions only for `DB_*` options | Yes, with `on_delete` | Yes, with actions and deferrable |
| ORM relationships | Declared `relationship()` | Forward and reverse accessors | Forward and backward relations | Foreign keys and backrefs |
| Many-to-many join tables | Association tables | Automatic and explicit `through` | Generated and explicit `through` | Generated join tables |
| Expression and partial indexes | Yes | Yes | Partial and type-specific | Yes |
| Check constraints | Yes | Yes | `Meta.constraints` | Field and table `CHECK` |
| Inheritance | Standard declarative mappings | Multi-table and proxy | Abstract only | Field copying only |
| Schema names | Yes | No | `Meta.schema` | `Meta.schema` |
| Database connection during build | None | Blocked | None | None |

Foreign keys and ORM relationships stay separate for every ORM: a relationship without a database constraint never creates a foreign key, and a foreign key without a declared relationship never creates an ORM edge.

Anything an adapter cannot represent is recorded in the document's `diagnostics` instead of being guessed. See each ORM's page for the details.
