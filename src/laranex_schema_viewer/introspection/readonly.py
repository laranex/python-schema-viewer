"""Read-only database sessions for introspection.

Guaranteed by the database: PostgreSQL and MySQL/MariaDB sessions run every transaction READ ONLY (set when the
connection opens and verified before any catalog is read), and SQLite files are opened with ``mode=ro`` plus
``PRAGMA query_only``. The transaction is rolled back, never committed.

Checked before inspecting: the role's privileges. A role that could change the schema or data fails the inspection
unless the caller accepts it explicitly; read-only transactions still apply then.

Defense in depth: only catalog statements (SELECT, SHOW, read-only PRAGMA, session SET) are sent; anything else raises
before it reaches the driver.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from . import IntrospectionError

DRIVERS = {"postgresql": {"psycopg", "psycopg2"}, "mysql": {"pymysql", "mysqldb"}, "sqlite": {"pysqlite"}}
STATEMENT = re.compile(r"^\s*(SELECT|WITH|SHOW|SET\s+(SESSION|TRANSACTION|LOCAL|NAMES)|BEGIN|START\s+TRANSACTION|ROLLBACK|"
                       r"PRAGMA\s+query_only\s*=\s*(1|ON)\s*$|PRAGMA\s+[\w.]+\s*(\(\s*[^)=;]*\))?\s*;?\s*$)", re.IGNORECASE)
# Privileges that only read. Everything else (INSERT, CREATE, ALTER, ALL PRIVILEGES, GRANT OPTION...) is a problem.
MYSQL_READ_ONLY = {"SELECT", "SHOW VIEW", "USAGE", "SHOW DATABASES", "PROCESS", "REPLICATION CLIENT",
                   "BINLOG MONITOR", "SLAVE MONITOR", "REPLICA MONITOR"}
MYSQL_GRANT = re.compile(r"^GRANT\s+(.+?)\s+ON\s+(\S+)\s+TO\s", re.IGNORECASE)


def guard_statements(engine: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def only_catalog_reads(conn, cursor, statement, parameters, context, executemany):
        if not STATEMENT.match(statement):
            raise IntrospectionError(f"Refused a statement that is not a catalog read: {statement.strip()[:80]!r}")
        return statement, parameters


def create_engine(url: str, statement_timeout: float) -> Any:
    """An engine whose every connection is read-only; the URL's password never appears in errors."""
    from sqlalchemy import create_engine as sa_create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError, NoSuchModuleError
    from sqlalchemy.pool import NullPool

    try:
        parsed = make_url(url)
    except ArgumentError:
        raise IntrospectionError("The database URL is not a valid SQLAlchemy URL") from None
    backend = parsed.get_backend_name()
    if backend not in DRIVERS:
        raise IntrospectionError(f"Inspecting {backend} databases is not supported; use PostgreSQL, MySQL, MariaDB, "
                                 "or SQLite")
    milliseconds = int(statement_timeout * 1000)
    kwargs: dict[str, Any] = {"poolclass": NullPool}
    if backend == "postgresql":
        kwargs["connect_args"] = {
            "connect_timeout": 10,
            "options": f"-c default_transaction_read_only=on -c statement_timeout={milliseconds} "
                       f"-c lock_timeout={min(milliseconds, 5000)} -c idle_in_transaction_session_timeout=60000",
            "application_name": "laranex-schema-viewer",
        }
    elif backend == "mysql":
        kwargs["connect_args"] = {"connect_timeout": 10, "read_timeout": int(statement_timeout) + 5,
                                  "init_command": "SET SESSION TRANSACTION READ ONLY"}
    else:
        database = parsed.database or ""
        if database in ("", ":memory:") or database.startswith("file:"):
            raise IntrospectionError("SQLite introspection needs the path of an existing database file")

        def connect() -> sqlite3.Connection:
            # mode=ro: SQLite refuses every write and never creates the file.
            return sqlite3.connect(f"file:{quote(database)}?mode=ro", uri=True, timeout=5)

        kwargs["creator"] = connect
    try:
        engine = sa_create_engine(parsed, **kwargs)
    except (NoSuchModuleError, ImportError, ModuleNotFoundError) as error:
        raise IntrospectionError(f"The {backend} driver for {parsed.drivername} is not installed ({error}); install "
                                 "laranex-schema-viewer[postgresql] or [mysql]") from None
    if engine.dialect.driver not in DRIVERS[backend]:
        raise IntrospectionError(f"Driver {engine.dialect.driver} is not supported for introspection; use "
                                 f"{' or '.join(sorted(DRIVERS[backend]))}")
    guard_statements(engine)
    return engine


@contextmanager
def read_only(engine: Any, statement_timeout: float) -> Iterator[Any]:
    """A connection inside a verified read-only transaction, rolled back afterwards."""
    from sqlalchemy import text

    try:
        connection = engine.connect()
    except IntrospectionError:
        raise
    except Exception as error:  # noqa: BLE001 - driver errors differ; the worker scrubs the URL from messages
        raise IntrospectionError(f"Could not connect to the database: {type(error).__name__}: {error}") from None
    try:
        dialect = engine.dialect.name
        if dialect == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
            verified = connection.execute(text("SHOW transaction_read_only")).scalar() == "on"
        elif dialect == "mysql":
            if getattr(engine.dialect, "is_mariadb", False):
                connection.execute(text(f"SET SESSION max_statement_time = {statement_timeout:g}"))
            else:
                connection.execute(text(f"SET SESSION max_execution_time = {int(statement_timeout * 1000)}"))
            state = _first(connection, ("SELECT @@session.transaction_read_only", "SELECT @@session.tx_read_only"))
            verified = int(state or 0) == 1
        else:
            connection.execute(text("PRAGMA query_only = 1"))
            verified = connection.execute(text("PRAGMA query_only")).scalar() == 1
        if not verified:
            raise IntrospectionError("The database did not confirm a read-only transaction; nothing was inspected")
        yield connection
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def _first(connection: Any, statements: tuple[str, ...]) -> Any:
    """The first of several equivalent queries the server knows (variable names differ across versions)."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    for statement in statements:
        try:
            return connection.execute(text(statement)).scalar()
        except DBAPIError:
            continue
    return None


def privilege_problems(connection: Any, schemas: list[str]) -> list[str]:
    """What the role could do beyond reading the catalogs. An empty list means a read-only role."""
    dialect = connection.dialect.name
    if dialect == "postgresql":
        return _postgresql(connection, schemas)
    if dialect == "mysql":
        return _mysql(connection)
    return []  # SQLite has no roles; the file is opened read-only.


def _postgresql(connection: Any, schemas: list[str]) -> list[str]:
    from sqlalchemy import bindparam, text

    problems = []
    role = connection.execute(text(
        "SELECT r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls, r.rolreplication, "
        "has_database_privilege(current_database(), 'CREATE') FROM pg_roles r WHERE r.rolname = current_user"
    )).one()
    for flag, label in zip(role[1:], ("is a superuser", "can create roles", "can create databases",
                                      "bypasses row security", "can replicate", "can create schemas")):
        if flag:
            problems.append(f"role {role[0]} {label}")
    rows = connection.execute(text(
        "SELECT n.nspname FROM pg_namespace n WHERE n.nspname IN :schemas "
        "AND (has_schema_privilege(n.oid, 'CREATE') OR pg_has_role(n.nspowner, 'USAGE')) ORDER BY 1"
    ).bindparams(bindparam("schemas", expanding=True)), {"schemas": schemas}).scalars().all()
    problems += [f"can create objects in or owns schema {name}" for name in rows]
    rows = connection.execute(text(
        "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN :schemas AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND (pg_has_role(c.relowner, 'USAGE') "
        "OR has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')) ORDER BY 1 LIMIT 6"
    ).bindparams(bindparam("schemas", expanding=True)), {"schemas": schemas}).scalars().all()
    if rows:
        problems.append(f"can change or owns tables ({', '.join(rows[:5])}{', ...' if len(rows) > 5 else ''})")
    return problems


def _mysql(connection: Any) -> list[str]:
    from sqlalchemy import text

    problems = []
    for (grant,) in connection.execute(text("SHOW GRANTS")).all():
        match = MYSQL_GRANT.match(grant)
        if not match:
            # A role granted to the user ("GRANT `role` TO ..."): its privileges are not listed here.
            if grant.upper().startswith("GRANT ") and " TO " in grant.upper():
                problems.append(f"holds a role whose privileges cannot be verified ({grant.split(' TO ')[0][6:]})")
            continue
        privileges = {re.sub(r"\s*\(.*?\)", "", p).strip().upper() for p in match.group(1).split(",")}
        extra = sorted(privileges - MYSQL_READ_ONLY)
        if "WITH GRANT OPTION" in grant.upper():
            extra.append("GRANT OPTION")
        if extra:
            problems.append(f"has {', '.join(extra)} on {match.group(2)}")
    return problems
