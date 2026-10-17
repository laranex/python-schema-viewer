"""Reconciliation: a declared and a database laranex-schema document as one combined document with drift.

Each database connection is reconciled with one declared connection (by name, or an explicit namespace mapping);
nothing is compared across connections, so an ORM relationship between connections never calls for a foreign key.
Within a connection, tables match by (schema, name), treating the database's default schema as unqualified; columns by name; foreign keys
by their column mapping, then by name; indexes by name, then by key parts; constraints by name, then by columns or
expression. Where both sides describe something the database's facts win, the declared side keeps what only it knows
(ORM defaults, notes, models and relationships), and differences become drift entries plus diagnostics.

Comparisons that depend on normalizing type names or SQL text are reported with certainty "likely"; structural ones
(missing columns, keys, nullability, referential actions) with "certain". Never imports an ORM or a database driver.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .contract import assert_document, build_graph
from .contract.models import FEATURES
from .unified import connection_id, sort_diagnostics

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


class Reconciler:
    """Reconciles one database connection with the declared connection it belongs to."""

    def __init__(self, declared: dict[str, Any], db_connection: dict[str, Any], declared_connection: dict[str, Any],
                 name: str, ambiguous: frozenset[str] | set[str] = frozenset()) -> None:
        self.declared = declared
        self.db_connection, self.declared_connection = db_connection, declared_connection
        self.name = name
        self.dialect = db_connection["driver"]
        self.default = db_connection["defaultSchema"]
        # Table names that occur in more than one connection: their diagnostics name this connection.
        self.ambiguous, self.scope = ambiguous, None
        self.diagnostics: list[dict[str, Any]] = []
        self.ids: dict[str, str] = {}  # database table id -> combined table id

    def key(self, table: dict[str, Any]) -> tuple[str | None, str]:
        schema = table["schema"]
        return (None if schema in (None, self.default) else schema, table["name"])

    def report(self, kind: str, category: str, severity: str, certainty: str, subject: dict[str, Any],
               message: str) -> None:
        if self.scope in self.ambiguous:
            message = f"Connection {self.name}: {message}"
        self.diagnostics.append({"kind": kind, "category": category, "severity": severity, "certainty": certainty,
                                 "subject": subject, "message": message})

    def combine(self) -> dict[str, Any]:
        """The combined connection; its diagnostics are left in self.diagnostics."""
        declared_connection = self.declared_connection
        declared_tables = {self.key(t): t for t in declared_connection["tables"]}
        database_tables = {self.key(t): t for t in self.db_connection["tables"]}
        for key, table in database_tables.items():
            self.ids[table["id"]] = declared_tables[key]["id"] if key in declared_tables else table["id"]
        mapped = {tid for m in self.declared["models"] for tid in m["tables"]}
        tables = []
        for key, table in declared_tables.items():
            self.scope = table["name"]
            if key in database_tables:
                tables.append(self.table(table, database_tables[key]))
                continue
            tables.append(copy.deepcopy(table))
            owners = [m for m in self.declared["models"] if table["id"] in m["tables"]]
            for model in owners:
                self.report("model_without_table", "reconciliation", "warning", "certain",
                            {"type": "model", "id": model["id"], "columns": None, "label": model["className"]},
                            f"{model['className']} declares table {table['name']}, which the database does not have.")
            if not owners:
                self.report("declared_only", "reconciliation", "warning", "certain", self.table_subject(table),
                            f"The models declare table {table['name']}, which the database does not have.")
        for key, table in database_tables.items():
            if key in declared_tables:
                continue
            self.scope = table["name"]
            tables.append(self.remap(copy.deepcopy(table), table["id"]))
            if table["owner"]["kind"] == "application" and table["id"] not in mapped:
                label = ".".join(filter(None, [table["schema"], table["name"]]))
                self.report("table_without_model", "reconciliation", "info", "certain", self.table_subject(table),
                            f"No model maps {label}.")
        return {
            **{k: v for k, v in declared_connection.items() if k != "tables"},
            "id": connection_id(self.name), "name": self.name,
            "driver": self.db_connection["driver"], "defaultSchema": self.default,
            "coverage": {"database": self.db_connection["coverage"]["database"],
                         "declared": declared_connection["coverage"]["declared"]},
            "tables": sorted(tables, key=lambda t: t["id"]),
        }

    def table_subject(self, table: dict[str, Any]) -> dict[str, Any]:
        return {"type": "table", "id": self.ids.get(table["id"], table["id"]), "columns": None, "label": table["name"]}

    def remap(self, table: dict[str, Any], database_id: str) -> dict[str, Any]:
        """Point a database table's ids and foreign keys at combined table ids."""
        tid = self.ids[database_id]
        table["id"] = tid
        for item in [*table["indexes"], *table["constraints"]]:
            item["id"] = tid + item["id"][len(database_id):]
        for index in table["indexes"]:
            if index["backs"] and index["backs"]["constraint"]:
                index["backs"]["constraint"] = tid + index["backs"]["constraint"][len(database_id):]
        for fk in table["foreignKeys"]:
            fk["foreignTable"] = self.ids.get(fk["foreignTable"], fk["foreignTable"])
        return table

    def table(self, declared: dict[str, Any], database: dict[str, Any]) -> dict[str, Any]:
        db = self.remap(copy.deepcopy(database), database["id"])
        tid = declared["id"]
        out = {**db, "id": tid, "note": declared["note"], "owner": declared["owner"],
               "provenance": ["database", "declared"]}
        if declared.get("extensions"):
            out["extensions"] = declared["extensions"]
        drift = []
        if declared["primaryKey"] != db["primaryKey"]:
            drift.append({"field": "primaryKey", "database": db["primaryKey"], "declared": declared["primaryKey"]})
            self.report("table_drift", "drift", "warning", "certain", self.table_subject(declared),
                        f"The primary key of {declared['name']} is ({', '.join(db['primaryKey'])}) in the database "
                        f"but ({', '.join(declared['primaryKey'])}) in the models.")
        if declared["comment"] is not None and declared["comment"] != db["comment"]:
            drift.append({"field": "comment", "database": db["comment"], "declared": declared["comment"]})
            self.report("table_drift", "drift", "info", "certain", self.table_subject(declared),
                        f"The comment on {declared['name']} differs between the database and the models.")
        if drift:
            out["drift"] = drift
        out["columns"] = self.columns(declared, db)
        out["foreignKeys"] = self.foreign_keys(declared, db)
        out["constraints"], unique_columns, renamed = self.constraints(declared, db)
        for index in db["indexes"]:
            if index["backs"] and index["backs"]["constraint"] in renamed:
                index["backs"] = {**index["backs"], "constraint": renamed[index["backs"]["constraint"]]}
        out["indexes"] = self.indexes(declared, db, unique_columns)
        return out

    def column_subject(self, table: dict[str, Any], column: str) -> dict[str, Any]:
        return {"type": "column", "id": table["id"], "columns": [column], "label": f"{table['name']}.{column}"}

    def columns(self, declared: dict[str, Any], db: dict[str, Any]) -> list[dict[str, Any]]:
        declared_columns = {c["name"]: c for c in declared["columns"]}
        out = []
        for column in db["columns"]:
            mine = declared_columns.pop(column["name"], None)
            if mine is None:
                out.append(column)
                self.report("database_only", "reconciliation", "info", "certain",
                            self.column_subject(declared, column["name"]),
                            f"The database has {declared['name']}.{column['name']}; the models do not declare it.")
                continue
            merged = {**column, "ormDefault": mine["ormDefault"], "note": mine["note"],
                      "provenance": ["database", "declared"]}
            if mine.get("extensions"):
                merged["extensions"] = mine["extensions"]
            drift = self.column_drift(declared, mine, column, column["name"] in db["primaryKey"])
            if drift:
                merged["drift"] = drift
            out.append(merged)
        for column in declared_columns.values():
            out.append(copy.deepcopy(column))
            self.report("declared_only", "reconciliation", "warning", "certain",
                        self.column_subject(declared, column["name"]),
                        f"The models declare {declared['name']}.{column['name']}; the database has no such column.")
        return out

    def column_drift(self, table: dict[str, Any], mine: dict[str, Any], db: dict[str, Any],
                     primary: bool) -> list[dict[str, Any]]:
        drift = []
        subject = self.column_subject(table, db["name"])
        label = f"{table['name']}.{db['name']}"
        declared_type = normalize_type(mine["type"], mine["timezone"])
        database_type = normalize_type(db["type"], db["timezone"])
        comparable = declared_type and database_type and db["typeName"] != "enum"
        if comparable and (declared_type[0] != database_type[0] or (
                declared_type[1] and database_type[1] and declared_type[1] != database_type[1])):
            drift.append({"field": "type", "database": db["type"], "declared": mine["type"]})
            self.report("column_drift", "drift", "warning", "likely", subject,
                        f"The database stores {label} as {db['type']}; the models declare {mine['type']}.")
        if mine["nullable"] != db["nullable"] and not primary:
            drift.append({"field": "nullable", "database": db["nullable"], "declared": mine["nullable"]})
            self.report("column_drift", "drift", "warning", "certain", subject,
                        f"{label} is {'nullable' if db['nullable'] else 'NOT NULL'} in the database but "
                        f"{'nullable' if mine['nullable'] else 'NOT NULL'} in the models.")
        declared_default, database_default = mine["default"], db["default"]
        if declared_default and declared_default["kind"] in ("literal", "expression") and (
                database_default is None or normalize_sql(declared_default["value"]) !=
                normalize_sql(database_default["value"])):
            drift.append({"field": "default", "database": database_default["value"] if database_default else None,
                          "declared": declared_default["value"]})
            self.report("column_drift", "drift", "warning", "likely", subject,
                        f"The database default of {label} is "
                        f"{database_default['value'] if database_default else 'none'}; the models declare "
                        f"{declared_default['value']}.")
        if mine["comment"] is not None and mine["comment"] != db["comment"]:
            drift.append({"field": "comment", "database": db["comment"], "declared": mine["comment"]})
            self.report("column_drift", "drift", "info", "certain", subject,
                        f"The comment on {label} differs between the database and the models.")
        return drift

    def foreign_keys(self, declared: dict[str, Any], db: dict[str, Any]) -> list[dict[str, Any]]:
        def mapping(fk: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(fk["columns"]), fk["foreignTable"], tuple(fk["foreignColumns"])

        remaining = list(db["foreignKeys"])
        out = []
        for mine in declared["foreignKeys"]:
            match = next((fk for fk in remaining if mapping(fk) == mapping(mine)), None) or next(
                (fk for fk in remaining if mine["name"] and fk["name"] == mine["name"]), None)
            subject = {"type": "foreignKey", "id": mine["id"], "columns": mine["columns"], "label": mine["name"]}
            if match is None:
                out.append(copy.deepcopy(mine))
                self.report("declared_only", "reconciliation", "warning", "certain", subject,
                            f"The models declare a foreign key on {declared['name']}.({', '.join(mine['columns'])}); "
                            "the database does not enforce one.")
                continue
            remaining.remove(match)
            merged = {**match, "id": mine["id"], "provenance": ["database", "declared"]}
            if mine.get("extensions"):
                merged["extensions"] = mine["extensions"]
            drift = []
            if mapping(match) != mapping(mine):
                drift.append({"field": "columns", "database": [match["columns"], match["foreignColumns"]],
                              "declared": [mine["columns"], mine["foreignColumns"]]})
            for field in ("onDelete", "onUpdate"):
                if normalize_action(mine[field], self.dialect) != normalize_action(match[field], self.dialect):
                    drift.append({"field": field, "database": match[field], "declared": mine[field]})
            for field in ("deferrable", "initially"):
                if mine[field] is not None and match[field] is not None and mine[field] != match[field]:
                    drift.append({"field": field, "database": match[field], "declared": mine[field]})
            if drift:
                merged["drift"] = drift
                fields = ", ".join(d["field"] for d in drift)
                self.report("foreign_key_drift", "drift", "warning", "certain", subject,
                            f"Foreign key {match['name'] or mine['name'] or ''} on {declared['name']} differs "
                            f"between the database and the models ({fields}).".replace("key  on", "key on"))
            out.append(merged)
        for fk in remaining:
            out.append(fk)
            self.report("database_only", "reconciliation", "info", "certain",
                        {"type": "foreignKey", "id": fk["id"], "columns": fk["columns"], "label": fk["name"]},
                        f"The database enforces a foreign key on {declared['name']}.({', '.join(fk['columns'])}) "
                        "that the models do not declare.")
        return sorted(out, key=lambda f: f["id"])

    def constraints(self, declared: dict[str, Any], db: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], set[tuple[str, ...]], dict[str, str]]:
        """Constraints, the column sets of declared unique constraints the database enforces, and the database
        constraint ids that now carry declared ids."""
        renamed: dict[str, str] = {}
        remaining = list(db["constraints"])
        unique_indexes = {tuple(i["columns"]) for i in db["indexes"]
                          if i["unique"] and not i["expression"] and not i["partial"]}
        enforced: set[tuple[str, ...]] = set()
        out = []
        for mine in declared["constraints"]:
            subject = {"type": "constraint", "id": mine["id"], "columns": mine["columns"] or None,
                       "label": mine["name"]}
            if mine["kind"] == "unique":
                match = next((c for c in remaining if c["kind"] == "unique" and c["columns"] == mine["columns"]), None)
            else:
                match = next((c for c in remaining if c["kind"] == "check" and mine["name"] and
                              c["name"] == mine["name"]), None) or next(
                    (c for c in remaining if c["kind"] == "check" and
                     normalize_sql(c["expression"], self.dialect) == normalize_sql(mine["expression"], self.dialect)),
                    None)
            if match is not None:
                remaining.remove(match)
                renamed[match["id"]] = mine["id"]
                merged = {**match, "id": mine["id"], "provenance": ["database", "declared"]}
                if mine["kind"] == "check" and (normalize_sql(match["expression"], self.dialect) !=
                                                normalize_sql(mine["expression"], self.dialect)):
                    merged["drift"] = [{"field": "expression", "database": match["expression"],
                                        "declared": mine["expression"]}]
                    self.report("constraint_drift", "drift", "warning", "likely", subject,
                                f"Check {mine['name'] or ''} on {declared['name']} reads {match['expression']} in the "
                                f"database but {mine['expression']} in the models.")
                if mine["kind"] == "unique":
                    enforced.add(tuple(mine["columns"]))
                out.append(merged)
            elif mine["kind"] == "unique" and tuple(mine["columns"]) in unique_indexes:
                # MySQL and MariaDB enforce unique constraints as unique indexes, listed with the indexes.
                enforced.add(tuple(mine["columns"]))
                out.append({**copy.deepcopy(mine), "provenance": ["database", "declared"]})
            else:
                out.append(copy.deepcopy(mine))
                self.report("declared_only", "reconciliation", "warning", "certain", subject,
                            f"The models declare a {mine['kind']} constraint on {declared['name']}"
                            f"{' (' + ', '.join(mine['columns']) + ')' if mine['columns'] else ''}; the database "
                            "does not enforce it.")
        for constraint in remaining:
            out.append(constraint)
            self.report("database_only", "reconciliation", "info", "certain",
                        {"type": "constraint", "id": constraint["id"], "columns": constraint["columns"] or None,
                         "label": constraint["name"]},
                        f"The database enforces a {constraint['kind']} constraint "
                        f"{constraint['name'] or ''} on {declared['name']} that the models do not declare.")
        return sorted(out, key=lambda c: c["id"]), enforced, renamed

    def indexes(self, declared: dict[str, Any], db: dict[str, Any],
                unique_columns: set[tuple[str, ...]]) -> list[dict[str, Any]]:
        def keys(index: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(p["column"] or normalize_sql(p["expression"]) for p in index["parts"])

        remaining = list(db["indexes"])
        out = []
        for mine in declared["indexes"]:
            match = next((i for i in remaining if mine["name"] and i["name"] == mine["name"]), None) or next(
                (i for i in remaining if keys(i) == keys(mine) and i["unique"] == mine["unique"]), None)
            subject = {"type": "index", "id": mine["id"], "columns": None, "label": mine["name"]}
            if match is None:
                out.append(copy.deepcopy(mine))
                self.report("declared_only", "reconciliation", "warning", "certain", subject,
                            f"The models declare index {mine['name'] or ', '.join(mine['columns'])} on "
                            f"{declared['name']}; the database has no such index.")
                continue
            remaining.remove(match)
            merged = {**match, "provenance": ["database", "declared"]}
            drift = []
            if match["unique"] != mine["unique"]:
                drift.append({"field": "unique", "database": match["unique"], "declared": mine["unique"]})
            if keys(match) != keys(mine):
                drift.append({"field": "parts", "database": list(keys(match)), "declared": list(keys(mine))})
            if drift:
                merged["drift"] = drift
                what = ("is unique in the database but not in the models" if match["unique"] and not mine["unique"]
                        else "is unique in the models but not in the database" if mine["unique"] and not match["unique"]
                        else "has different key parts in the database and the models")
                self.report("index_drift", "drift", "warning", "certain",
                            {"type": "index", "id": match["id"], "columns": None, "label": match["name"]},
                            f"{match['name']} {what}.")
            out.append(merged)
        for index in remaining:
            backs = index["backs"] or {}
            implied = (backs.get("kind") == "primary_key" and index["columns"] == declared["primaryKey"]) or (
                backs.get("kind") == "unique_constraint" and tuple(index["columns"]) in unique_columns) or (
                index["unique"] and tuple(index["columns"]) in unique_columns)
            if implied:
                # The index behind a declared primary key or unique constraint.
                out.append({**index, "provenance": ["database", "declared"]})
                continue
            out.append(index)
            supporting = self.dialect in ("mysql", "mariadb") and not index["unique"] and any(
                fk["columns"] == index["columns"] for fk in db["foreignKeys"])
            if not backs and not supporting:  # MySQL and MariaDB create an index for every foreign key.
                self.report("database_only", "reconciliation", "info", "certain",
                            {"type": "index", "id": index["id"], "columns": None, "label": index["name"]},
                            f"The database has index {index['name']} on {declared['name']}; the models do not "
                            "declare it.")
        return sorted(out, key=lambda i: i["id"])


def unavailable() -> dict[str, Any]:
    """Coverage of a source that was not read for a connection."""
    return {"complete": False, "schemas": None, "features": {f: "unavailable" for f in FEATURES}}


def connection_subject(name: str) -> dict[str, Any]:
    return {"type": "connection", "id": connection_id(name), "columns": None, "label": name}


def combine(declared: dict[str, Any], database: dict[str, Any], *, connection: str | None = None,
            namespaces: dict[str, str] | None = None) -> dict[str, Any]:
    """A combined laranex-schema document.

    Each database connection is reconciled with the declared connection of the same name, or with the declared
    namespace ``namespaces`` maps its name to; the combined connection takes the database connection's name. A
    database connection the models do not declare stays database-only, and a declared connection no database was
    inspected for stays declared-only; each gets a connection diagnostic, and the side that was not read has
    ``unavailable`` coverage. A single database connection belongs to the only declared namespace, whatever their
    names; when no database connection matches any declared namespace, this fails and lists them. ``connection`` is
    the single-database form: it names the declared connection the
    document's only database connection belongs to, and fails when the models do not declare it.
    """
    if declared["source"] != "declared" or database["source"] != "database":
        raise ValueError("combine() takes a declared document and a database document")
    declared_by_name = {c["name"]: c for c in declared["connections"]}
    found = ", ".join(declared_by_name) or "none"
    if connection is not None:
        if len(database["connections"]) != 1:
            raise ValueError("connection= applies to a database document with one connection; use namespaces=")
        if connection not in declared_by_name:
            raise ValueError(f"The models declare no connection named {connection!r} (found: {found}); choose one "
                             "with --database-connection")
        pairs = [(database["connections"][0], connection, connection)]
    else:
        namespaces = dict(namespaces or {})
        only = database["connections"][0]["name"] if len(database["connections"]) == 1 else None
        if only and only not in namespaces and only not in declared_by_name and len(declared_by_name) == 1:
            namespaces[only] = next(iter(declared_by_name))  # One database, one declared namespace: they belong.
        pairs = []
        for db in database["connections"]:
            target = namespaces.get(db["name"], db["name"])
            if db["name"] in namespaces and target not in declared_by_name:
                raise ValueError(f"Connection {db['name']} maps to the declared namespace {target!r}, which the "
                                 f"models do not declare (found: {found})")
            pairs.append((db, target if target in declared_by_name else None, db["name"]))
    if declared_by_name and pairs and all(target is None for _, target, _ in pairs):
        raise ValueError(f"No database connection ({', '.join(db['name'] for db, _, _ in pairs)}) matches a declared "
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
        names[name] = {t["name"] for t in db["tables"]} | (
            {t["name"] for t in declared_by_name[target]["tables"]} if target else set())
    for name, c in declared_by_name.items():
        if name not in targets:
            names[name] = {t["name"] for t in c["tables"]}
    counts: dict[str, int] = {}
    for tables in names.values():
        for table in tables:
            counts[table] = counts.get(table, 0) + 1
    ambiguous = {table for table, n in counts.items() if n > 1}

    connections, diagnostics = [], []
    for db, target, name in pairs:
        if target is None:
            connections.append({**copy.deepcopy(db), "coverage": {"database": db["coverage"]["database"],
                                                                  "declared": unavailable()}})
            diagnostics.append({
                "kind": "database_only", "category": "reconciliation", "severity": "info", "certainty": "certain",
                "subject": connection_subject(name),
                "message": f"The models declare no connection named {name} (found: {found}); its tables come from "
                           "the database only. Map it to a declared namespace to reconcile it."})
            continue
        reconciler = Reconciler(declared, db, declared_by_name[target], name, ambiguous)
        connections.append(reconciler.combine())
        diagnostics += reconciler.diagnostics
    for name, c in declared_by_name.items():
        if name in targets:
            continue
        connections.append({**copy.deepcopy(c), "coverage": {"database": unavailable(),
                                                             "declared": c["coverage"]["declared"]}})
        diagnostics.append({
            "kind": "inspection_unavailable", "category": "inspection", "severity": "info", "certainty": "certain",
            "subject": connection_subject(name),
            "message": f"No database was inspected for connection {name}; its tables come from the models only."})
    doc = {
        **{k: copy.deepcopy(v) for k, v in declared.items() if k not in ("links", "derivedLinks")},
        "source": "combined",
        "connections": sorted(connections, key=lambda c: c["name"]),
        "diagnostics": sort_diagnostics([*declared["diagnostics"], *database["diagnostics"], *diagnostics]),
        "links": [], "derivedLinks": [],
    }
    doc.update(build_graph(doc))
    return assert_document(doc, strict=True).to_json()
