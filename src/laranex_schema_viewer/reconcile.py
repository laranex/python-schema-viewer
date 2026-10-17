"""Reconciliation: a declared and a database document as one combined document with drift, on the schema model.

Each database connection is reconciled with one declared connection (by name, or an explicit namespace mapping);
nothing is compared across connections, so an ORM relationship between connections never calls for a foreign key.
Within a connection, tables match by (schema, name), treating the database's default schema as unqualified; columns
by name; foreign keys by their column mapping, then by name; indexes by name, then by key parts; constraints by name,
then by columns or expression. Where both sides describe something the database's facts win, the declared side keeps
what only it knows (ORM defaults, notes, models and relationships), and differences become drift entries plus
diagnostics.

Comparisons that depend on normalizing type names or SQL text are reported with certainty "likely"; structural ones
(missing columns, keys, nullability, referential actions) with "certain". Never imports an ORM or a database driver.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from . import model as m
from .adapters import connection_id
from .contract import finalize
from .contract.serialize import camel
from .model import replace

TYPE_SYNONYMS = {
    "int": "integer", "int4": "integer", "integer": "integer", "mediumint": "integer", "serial": "integer",
    "bigint": "bigint", "int8": "bigint", "bigserial": "bigint", "smallint": "smallint", "int2": "smallint",
    "smallserial": "smallint", "tinyint": "tinyint", "bool": "boolean", "boolean": "boolean",
    "character varying": "varchar", "varchar": "varchar", "nvarchar": "varchar", "character": "char", "char": "char",
    "bpchar": "char", "nchar": "char", "text": "text", "clob": "text", "datetime": "timestamp", "timestamp": "timestamp",
    "timestamp without time zone": "timestamp", "timestamptz": "timestamptz", "timestamp with time zone": "timestamptz",
    "time without time zone": "time", "time with time zone": "timetz", "timetz": "timetz", "float": "double",
    "float8": "double", "double": "double", "double precision": "double", "real": "real", "float4": "real",
    "numeric": "numeric", "decimal": "numeric", "blob": "blob", "bytea": "blob", "varbinary": "blob",
    "largebinary": "blob",
}
INTEGERS = {"integer", "bigint", "smallint", "tinyint"}


def normalize_type(text: str | None, timezone: bool | None = None) -> tuple[str, str] | None:
    """(base, arguments) of a column type, or None when it cannot be compared."""
    if not text:
        return None
    value = re.sub(r"\s+", " ", text.strip().lower())
    value = re.sub(r"\b(unsigned|zerofill|signed)\b", "", value).strip()
    match = re.fullmatch(r"([a-z_][\w ]*?)\s*(\((.*)\))?\s*(\[\])?\s*(with(out)? time zone)?", value)
    if not match:
        return None
    base = f"{match.group(1)} {match.group(5)}" if match.group(5) else match.group(1)
    base = TYPE_SYNONYMS.get(base.strip(), base.strip())
    if base == "timestamp" and timezone:
        base = "timestamptz"
    arguments = re.sub(r"\s+", "", match.group(3) or "")
    if base == "tinyint" and arguments == "1":
        base, arguments = "boolean", ""  # MySQL's BOOLEAN
    if base in INTEGERS or (base == "double" and arguments == "53"):
        arguments = ""  # Display widths (int(11)) and float(53) carry no storage difference.
    return base + ("[]" if match.group(4) else ""), arguments


def normalize_sql(text: str | None, dialect: str | None = None) -> str:
    value = (text or "").lower()
    if dialect == "mariadb":
        value = value.replace("octet_length(", "length(")  # MariaDB stores LENGTH() as its synonym
    value = re.sub(r"::[\w .\[\]\"]+", "", value)
    value = re.sub(r"[\s()`\"]", "", value)
    return value


def normalize_action(value: str | None, dialect: str | None) -> str:
    value = (value or "NO ACTION").upper()
    return "NO ACTION" if value == "RESTRICT" and dialect in ("mysql", "mariadb") else value


def index_keys(index: m.Index) -> tuple[str, ...]:
    return tuple(p.column or normalize_sql(p.expression) for p in index.parts)


def table_subject(table: m.Table, tid: str | None = None) -> m.Subject:
    return m.Subject(type="table", id=tid or table.id, label=table.name)


def column_subject(table: m.Table, column: str) -> m.Subject:
    return m.Subject(type="column", id=table.id, columns=(column,), label=f"{table.name}.{column}")


class Reconciler:
    """Reconciles one database connection with the declared connection it belongs to."""

    def __init__(self, declared: m.Document, db_connection: m.Connection, declared_connection: m.Connection,
                 name: str, ambiguous: frozenset[str] | set[str] = frozenset()) -> None:
        self.declared = declared
        self.db_connection, self.declared_connection = db_connection, declared_connection
        self.name = name
        self.dialect = db_connection.driver
        self.default = db_connection.default_schema
        # Table names that occur in more than one connection: their diagnostics name this connection.
        self.ambiguous, self.scope = ambiguous, None
        self.diagnostics: list[m.Diagnostic] = []
        self.ids: dict[str, str] = {}  # database table id -> combined table id

    def key(self, table: m.Table) -> tuple[str | None, str]:
        return (None if table.schema in (None, self.default) else table.schema, table.name)

    def report(self, kind: str, category: str, severity: str, certainty: str, subject: m.Subject,
               message: str) -> None:
        if self.scope in self.ambiguous:
            message = f"Connection {self.name}: {message}"
        self.diagnostics.append(m.Diagnostic(kind=kind, category=category, severity=severity, certainty=certainty,
                                             subject=subject, message=message))

    def combine(self) -> m.Connection:
        """The combined connection; its diagnostics are left in self.diagnostics."""
        declared_tables = {self.key(t): t for t in self.declared_connection.tables}
        database_tables = {self.key(t): t for t in self.db_connection.tables}
        for key, table in database_tables.items():
            self.ids[table.id] = declared_tables[key].id if key in declared_tables else table.id
        mapped = {tid for x in self.declared.models for tid in x.tables}
        tables = []
        for key, table in declared_tables.items():
            self.scope = table.name
            if key in database_tables:
                tables.append(self.table(table, database_tables[key]))
                continue
            tables.append(table)
            owners = [x for x in self.declared.models if table.id in x.tables]
            for owner in owners:
                self.report("model_without_table", "reconciliation", "warning", "certain",
                            m.Subject(type="model", id=owner.id, label=owner.class_name),
                            f"{owner.class_name} declares table {table.name}, which the database does not have.")
            if not owners:
                self.report("declared_only", "reconciliation", "warning", "certain", table_subject(table),
                            f"The models declare table {table.name}, which the database does not have.")
        for key, table in database_tables.items():
            if key in declared_tables:
                continue
            self.scope = table.name
            tables.append(self.remap(table))
            if table.owner.kind == "application" and table.id not in mapped:
                label = ".".join(filter(None, [table.schema, table.name]))
                self.report("table_without_model", "reconciliation", "info", "certain",
                            table_subject(table, self.ids[table.id]), f"No model maps {label}.")
        return replace(self.declared_connection, id=connection_id(self.name), name=self.name,
                       driver=self.db_connection.driver, default_schema=self.default,
                       coverage=m.ConnectionCoverage(database=self.db_connection.coverage.database,
                                                     declared=self.declared_connection.coverage.declared),
                       tables=tuple(tables))

    def remap(self, table: m.Table) -> m.Table:
        """Point a database table's ids and foreign keys at combined table ids."""
        old, tid = table.id, self.ids[table.id]

        def moved(value: str) -> str:
            return tid + value[len(old):]

        return replace(
            table, id=tid,
            indexes=tuple(replace(i, id=moved(i.id), backs=replace(i.backs, constraint=moved(i.backs.constraint))
                                  if i.backs and i.backs.constraint else i.backs) for i in table.indexes),
            constraints=tuple(replace(c, id=moved(c.id)) for c in table.constraints),
            foreign_keys=tuple(replace(f, foreign_table=self.ids.get(f.foreign_table, f.foreign_table))
                               for f in table.foreign_keys),
        )

    def table(self, declared: m.Table, database: m.Table) -> m.Table:
        db = self.remap(database)
        drift = []
        if declared.primary_key != db.primary_key:
            drift.append(m.Drift(field="primary_key", database=list(db.primary_key),
                                 declared=list(declared.primary_key)))
            self.report("table_drift", "drift", "warning", "certain", table_subject(declared),
                        f"The primary key of {declared.name} is ({', '.join(db.primary_key)}) in the database "
                        f"but ({', '.join(declared.primary_key)}) in the models.")
        if declared.comment is not None and declared.comment != db.comment:
            drift.append(m.Drift(field="comment", database=db.comment, declared=declared.comment))
            self.report("table_drift", "drift", "info", "certain", table_subject(declared),
                        f"The comment on {declared.name} differs between the database and the models.")
        constraints, unique_columns, renamed = self.constraints(declared, db)
        db = replace(db, indexes=tuple(
            replace(i, backs=replace(i.backs, constraint=renamed[i.backs.constraint]))
            if i.backs and i.backs.constraint in renamed else i for i in db.indexes))
        return replace(
            db, id=declared.id, note=declared.note, owner=declared.owner, provenance=m.BOTH, drift=tuple(drift),
            extensions=declared.extensions or db.extensions, columns=self.columns(declared, db),
            foreign_keys=self.foreign_keys(declared, db), constraints=constraints,
            indexes=self.indexes(declared, db, unique_columns),
        )

    def columns(self, declared: m.Table, db: m.Table) -> tuple[m.Column, ...]:
        declared_columns = {c.name: c for c in declared.columns}
        out = []
        for column in db.columns:
            mine = declared_columns.pop(column.name, None)
            if mine is None:
                out.append(column)
                self.report("database_only", "reconciliation", "info", "certain", column_subject(declared, column.name),
                            f"The database has {declared.name}.{column.name}; the models do not declare it.")
                continue
            out.append(replace(column, orm_default=mine.orm_default, note=mine.note, provenance=m.BOTH,
                               extensions=mine.extensions or column.extensions,
                               drift=self.column_drift(declared, mine, column, column.name in db.primary_key)))
        for column in declared_columns.values():
            out.append(column)
            self.report("declared_only", "reconciliation", "warning", "certain", column_subject(declared, column.name),
                        f"The models declare {declared.name}.{column.name}; the database has no such column.")
        return tuple(out)

    def column_drift(self, table: m.Table, mine: m.Column, db: m.Column, primary: bool) -> tuple[m.Drift, ...]:
        drift = []
        subject = column_subject(table, db.name)
        label = f"{table.name}.{db.name}"
        declared_type = normalize_type(mine.type, mine.timezone)
        database_type = normalize_type(db.type, db.timezone)
        comparable = declared_type and database_type and db.type_name != "enum"
        if comparable and (declared_type[0] != database_type[0] or (
                declared_type[1] and database_type[1] and declared_type[1] != database_type[1])):
            drift.append(m.Drift(field="type", database=db.type, declared=mine.type))
            self.report("column_drift", "drift", "warning", "likely", subject,
                        f"The database stores {label} as {db.type}; the models declare {mine.type}.")
        if mine.nullable != db.nullable and not primary:
            drift.append(m.Drift(field="nullable", database=db.nullable, declared=mine.nullable))
            self.report("column_drift", "drift", "warning", "certain", subject,
                        f"{label} is {'nullable' if db.nullable else 'NOT NULL'} in the database but "
                        f"{'nullable' if mine.nullable else 'NOT NULL'} in the models.")
        declared_default, database_default = mine.default, db.default
        if declared_default and declared_default.kind in ("literal", "expression") and (
                database_default is None or normalize_sql(declared_default.value) !=
                normalize_sql(database_default.value)):
            drift.append(m.Drift(field="default", database=database_default.value if database_default else None,
                                 declared=declared_default.value))
            self.report("column_drift", "drift", "warning", "likely", subject,
                        f"The database default of {label} is "
                        f"{database_default.value if database_default else 'none'}; the models declare "
                        f"{declared_default.value}.")
        if mine.comment is not None and mine.comment != db.comment:
            drift.append(m.Drift(field="comment", database=db.comment, declared=mine.comment))
            self.report("column_drift", "drift", "info", "certain", subject,
                        f"The comment on {label} differs between the database and the models.")
        return tuple(drift)

    def foreign_keys(self, declared: m.Table, db: m.Table) -> tuple[m.ForeignKey, ...]:
        def mapping(fk: m.ForeignKey) -> tuple[object, ...]:
            return fk.columns, fk.foreign_table, fk.foreign_columns

        remaining = list(db.foreign_keys)
        out = []
        for mine in declared.foreign_keys:
            match = next((fk for fk in remaining if mapping(fk) == mapping(mine)), None) or next(
                (fk for fk in remaining if mine.name and fk.name == mine.name), None)
            subject = m.Subject(type="foreignKey", id=mine.id, columns=mine.columns, label=mine.name)
            if match is None:
                out.append(mine)
                self.report("declared_only", "reconciliation", "warning", "certain", subject,
                            f"The models declare a foreign key on {declared.name}.({', '.join(mine.columns)}); "
                            "the database does not enforce one.")
                continue
            remaining.remove(match)
            drift = []
            if mapping(match) != mapping(mine):
                drift.append(m.Drift(field="columns", database=[list(match.columns), list(match.foreign_columns)],
                                     declared=[list(mine.columns), list(mine.foreign_columns)]))
            for name in ("on_delete", "on_update"):
                if normalize_action(getattr(mine, name), self.dialect) != normalize_action(getattr(match, name),
                                                                                           self.dialect):
                    drift.append(m.Drift(field=name, database=getattr(match, name), declared=getattr(mine, name)))
            for name in ("deferrable", "initially"):
                ours, theirs = getattr(mine, name), getattr(match, name)
                if ours is not None and theirs is not None and ours != theirs:
                    drift.append(m.Drift(field=name, database=theirs, declared=ours))
            if drift:
                fields = ", ".join(camel(d.field) for d in drift)  # as the document names them
                self.report("foreign_key_drift", "drift", "warning", "certain", subject,
                            f"Foreign key {match.name or mine.name or ''} on {declared.name} differs "
                            f"between the database and the models ({fields}).".replace("key  on", "key on"))
            out.append(replace(match, id=mine.id, provenance=m.BOTH, drift=tuple(drift),
                               extensions=mine.extensions or match.extensions))
        for fk in remaining:
            out.append(fk)
            self.report("database_only", "reconciliation", "info", "certain",
                        m.Subject(type="foreignKey", id=fk.id, columns=fk.columns, label=fk.name),
                        f"The database enforces a foreign key on {declared.name}.({', '.join(fk.columns)}) "
                        "that the models do not declare.")
        return tuple(sorted(out, key=lambda f: f.id))

    def constraints(self, declared: m.Table, db: m.Table
                    ) -> tuple[tuple[m.Constraint, ...], set[tuple[str, ...]], dict[str, str]]:
        """Constraints, the column sets of declared unique constraints the database enforces, and the database
        constraint ids that now carry declared ids."""
        renamed: dict[str, str] = {}
        remaining = list(db.constraints)
        unique_indexes = {i.columns for i in db.indexes if i.unique and not i.expression and not i.partial}
        enforced: set[tuple[str, ...]] = set()
        out = []
        for mine in declared.constraints:
            subject = m.Subject(type="constraint", id=mine.id, columns=mine.columns or None, label=mine.name)
            if mine.kind == "unique":
                match = next((c for c in remaining if c.kind == "unique" and c.columns == mine.columns), None)
            else:
                match = next((c for c in remaining if c.kind == "check" and mine.name and c.name == mine.name),
                             None) or next(
                    (c for c in remaining if c.kind == "check" and
                     normalize_sql(c.expression, self.dialect) == normalize_sql(mine.expression, self.dialect)), None)
            if match is not None:
                remaining.remove(match)
                renamed[match.id] = mine.id
                drift = ()
                if mine.kind == "check" and (normalize_sql(match.expression, self.dialect) !=
                                             normalize_sql(mine.expression, self.dialect)):
                    drift = (m.Drift(field="expression", database=match.expression, declared=mine.expression),)
                    self.report("constraint_drift", "drift", "warning", "likely", subject,
                                f"Check {mine.name or ''} on {declared.name} reads {match.expression} in the "
                                f"database but {mine.expression} in the models.")
                if mine.kind == "unique":
                    enforced.add(mine.columns)
                out.append(replace(match, id=mine.id, provenance=m.BOTH, drift=drift))
            elif mine.kind == "unique" and mine.columns in unique_indexes:
                # MySQL and MariaDB enforce unique constraints as unique indexes, listed with the indexes.
                enforced.add(mine.columns)
                out.append(replace(mine, provenance=m.BOTH))
            else:
                out.append(mine)
                self.report("declared_only", "reconciliation", "warning", "certain", subject,
                            f"The models declare a {mine.kind} constraint on {declared.name}"
                            f"{' (' + ', '.join(mine.columns) + ')' if mine.columns else ''}; the database "
                            "does not enforce it.")
        for constraint in remaining:
            out.append(constraint)
            self.report("database_only", "reconciliation", "info", "certain",
                        m.Subject(type="constraint", id=constraint.id, columns=constraint.columns or None,
                                  label=constraint.name),
                        f"The database enforces a {constraint.kind} constraint "
                        f"{constraint.name or ''} on {declared.name} that the models do not declare.")
        return tuple(sorted(out, key=lambda c: c.id)), enforced, renamed

    def indexes(self, declared: m.Table, db: m.Table, unique_columns: set[tuple[str, ...]]) -> tuple[m.Index, ...]:
        remaining = list(db.indexes)
        out = []
        for mine in declared.indexes:
            match = next((i for i in remaining if mine.name and i.name == mine.name), None) or next(
                (i for i in remaining if index_keys(i) == index_keys(mine) and i.unique == mine.unique), None)
            if match is None:
                out.append(mine)
                self.report("declared_only", "reconciliation", "warning", "certain",
                            m.Subject(type="index", id=mine.id, label=mine.name),
                            f"The models declare index {mine.name or ', '.join(index_keys(mine))} on "
                            f"{declared.name}; the database has no such index.")
                continue
            remaining.remove(match)
            drift = []
            if match.unique != mine.unique:
                drift.append(m.Drift(field="unique", database=match.unique, declared=mine.unique))
            if index_keys(match) != index_keys(mine):
                drift.append(m.Drift(field="parts", database=list(index_keys(match)),
                                     declared=list(index_keys(mine))))
            if drift:
                what = ("is unique in the database but not in the models" if match.unique and not mine.unique
                        else "is unique in the models but not in the database" if mine.unique and not match.unique
                        else "has different key parts in the database and the models")
                self.report("index_drift", "drift", "warning", "certain",
                            m.Subject(type="index", id=match.id, label=match.name), f"{match.name} {what}.")
            out.append(replace(match, provenance=m.BOTH, drift=tuple(drift)))
        for index in remaining:
            backs = index.backs
            implied = (backs is not None and backs.kind == "primary_key" and index.columns == declared.primary_key) or (
                backs is not None and backs.kind == "unique_constraint" and index.columns in unique_columns) or (
                index.unique and index.columns in unique_columns)
            if implied:
                # The index behind a declared primary key or unique constraint.
                out.append(replace(index, provenance=m.BOTH))
                continue
            out.append(index)
            supporting = self.dialect in ("mysql", "mariadb") and not index.unique and any(
                fk.columns == index.columns for fk in db.foreign_keys)
            if backs is None and not supporting:  # MySQL and MariaDB create an index for every foreign key.
                self.report("database_only", "reconciliation", "info", "certain",
                            m.Subject(type="index", id=index.id, label=index.name),
                            f"The database has index {index.name} on {declared.name}; the models do not "
                            "declare it.")
        return tuple(sorted(out, key=lambda i: i.id))


def connection_subject(name: str) -> m.Subject:
    return m.Subject(type="connection", id=connection_id(name), label=name)


def combine(declared: m.Document, database: m.Document, *, connection: str | None = None,
            namespaces: dict[str, str] | None = None) -> m.Document:
    """A combined document.

    Each database connection is reconciled with the declared connection of the same name, or with the declared
    namespace ``namespaces`` maps its name to; the combined connection takes the database connection's name. A
    database connection the models do not declare stays database-only, and a declared connection no database was
    inspected for stays declared-only; each gets a connection diagnostic, and the side that was not read has
    ``unavailable`` coverage. A single database connection belongs to the only declared namespace, whatever their
    names; when no database connection matches any declared namespace, this fails and lists them. ``connection`` is
    the single-database form: it names the declared connection the document's only database connection belongs to,
    and fails when the models do not declare it.
    """
    if declared.source != "declared" or database.source != "database":
        raise ValueError("combine() takes a declared document and a database document")
    declared_by_name = {c.name: c for c in declared.connections}
    found = ", ".join(declared_by_name) or "none"
    pairs: list[tuple[m.Connection, str | None, str]]
    if connection is not None:
        if len(database.connections) != 1:
            raise ValueError("connection= applies to a database document with one connection; use namespaces=")
        if connection not in declared_by_name:
            raise ValueError(f"The models declare no connection named {connection!r} (found: {found}); choose one "
                             "with --database-connection")
        pairs = [(database.connections[0], connection, connection)]
    else:
        namespaces = dict(namespaces or {})
        only = database.connections[0].name if len(database.connections) == 1 else None
        if only and only not in namespaces and only not in declared_by_name and len(declared_by_name) == 1:
            namespaces[only] = next(iter(declared_by_name))  # One database, one declared namespace: they belong.
        pairs = []
        for db in database.connections:
            target = namespaces.get(db.name, db.name)
            if db.name in namespaces and target not in declared_by_name:
                raise ValueError(f"Connection {db.name} maps to the declared namespace {target!r}, which the "
                                 f"models do not declare (found: {found})")
            pairs.append((db, target if target in declared_by_name else None, db.name))
    if declared_by_name and pairs and all(target is None for _, target, _ in pairs):
        raise ValueError(f"No database connection ({', '.join(db.name for db, _, _ in pairs)}) matches a declared "
                         f"namespace (found: {found}); map each connection to one with namespace in "
                         "[tool.laranex-schema-viewer.databases.NAME]")
    targets: dict[str, str] = {}
    for db, target, name in pairs:
        if target is None:
            continue
        if target in targets:
            raise ValueError(f"Connections {targets[target]} and {name} both map to the declared namespace "
                             f"{target!r}")
        targets[target] = name
        if target != name and name in declared_by_name:
            raise ValueError(f"Connection {name} maps to the declared namespace {target!r}, but the models also "
                             f"declare a namespace named {name!r}; give the connection another name")
    names: dict[str, set[str]] = {}
    for db, target, name in pairs:
        names[name] = {t.name for t in db.tables} | (
            {t.name for t in declared_by_name[target].tables} if target else set())
    for name, c in declared_by_name.items():
        if name not in targets:
            names[name] = {t.name for t in c.tables}
    counts: dict[str, int] = {}
    for tables in names.values():
        for table in tables:
            counts[table] = counts.get(table, 0) + 1
    ambiguous = {table for table, n in counts.items() if n > 1}

    connections: list[m.Connection] = []
    diagnostics: list[m.Diagnostic] = []
    for db, target, name in pairs:
        if target is None:
            connections.append(replace(db, coverage=m.ConnectionCoverage(database=db.coverage.database,
                                                                         declared=m.unavailable_coverage())))
            diagnostics.append(m.Diagnostic(
                kind="database_only", category="reconciliation", severity="info", certainty="certain",
                subject=connection_subject(name),
                message=f"The models declare no connection named {name} (found: {found}); its tables come from "
                        "the database only. Map it to a declared namespace to reconcile it."))
            continue
        reconciler = Reconciler(declared, db, declared_by_name[target], name, ambiguous)
        connections.append(reconciler.combine())
        diagnostics += reconciler.diagnostics
    for name, c in declared_by_name.items():
        if name in targets:
            continue
        connections.append(replace(c, coverage=m.ConnectionCoverage(database=m.unavailable_coverage(),
                                                                    declared=c.coverage.declared)))
        diagnostics.append(m.Diagnostic(
            kind="inspection_unavailable", category="inspection", severity="info", certainty="certain",
            subject=connection_subject(name),
            message=f"No database was inspected for connection {name}; its tables come from the models only."))
    return finalize(replace(declared, source="combined", connections=tuple(connections),
                            diagnostics=_all(declared.diagnostics, database.diagnostics, diagnostics)))


def _all(*groups: Iterable[m.Diagnostic]) -> tuple[m.Diagnostic, ...]:
    return tuple(d for group in groups for d in group)
