"""Live database introspection: reads a database's catalogs into a laranex-schema 2.0 database document.

It runs in its own worker process (worker.py), separate from the extraction worker that imports application code:

- the introspection worker is started with ``python -I`` from an empty directory and never imports application
  modules; it reports, and fails on, any module loaded from the project;
- it is the only process given the database URL, which travels on stdin, never in the environment or arguments;
- it connects with a read-only session (readonly.py): database-enforced read-only transactions, statement timeouts,
  an allowlist of catalog statements, and a check of the role's privileges that fails when the role could change the
  schema or data, unless the caller explicitly accepts that.

See docs/security.md and docs/database.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class IntrospectionError(RuntimeError):
    """The database could not be inspected safely, or at all. Never answered by falling back to declared metadata."""


@dataclass(frozen=True)
class DatabaseRequest:
    url: str = field(repr=False)
    # Schemas to read; empty means the connection's default schema. Ignored by SQLite.
    schemas: tuple[str, ...] = ()
    # The contract connection name, matched against the declared namespaces when combining.
    connection: str = "default"
    # Inspect even when the role holds privileges beyond reading the catalogs (still in read-only transactions).
    allow_write_privileges: bool = False
    timeout: float = 120.0
    statement_timeout: float = 30.0
