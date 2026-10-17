"""A database's catalogs as a laranex-schema 1.0 document (source: database).

SQLAlchemy's Inspector reads columns, keys, constraints and comments. Native type names and the index details it
misses come from catalog queries: pg_index for methods and key collations; information_schema.STATISTICS for MySQL and
MariaDB key parts (functional parts, DESC, prefix lengths, visibility); SQLite's index PRAGMAs and CREATE INDEX text.
Everything runs on one read-only connection (readonly.py). Never imports application code.
"""

from __future__ import annotations

import re
from typing import Any

from ..adapters import identifier
from ..contract import VERSION, assert_document, build_graph
from ..contract.models import FEATURES
from ..unified import GENERATOR, action, connection_id, sort_diagnostics

LITERAL = re.compile(r"^(?:'(?:[^']|'')*'(?:::[\w .\[\]\"]+)?|\(?-?\d+(?:\.\d+)?\)?(?:::[\w .\"]+)?|true|false|null)$",
                     re.IGNORECASE)
TYPE_NAMES = {"character varying": "varchar", "character": "char", "timestamp with time zone": "timestamptz",
              "timestamp without time zone": "timestamp", "time with time zone": "timetz",
              "time without time zone": "time", "double precision": "double", "bit varying": "varbit"}
OWNERS = {"alembic_version": ("package", "alembic"), "django_migrations": ("framework", "django"),
          "aerich": ("package", "aerich")}
IDENTIFIER = re.compile(r"[A-Za-z_][\w$]*|`[^`]+`|\"[^\"]+\"")


def inspect_database(connection: Any, *, name: str, schemas: list[str], diagnostics: list[dict[str, Any]]
                     ) -> dict[str, Any]:
    from sqlalchemy import inspect

    inspector = inspect(connection)
    dialect = connection.dialect.name
    mariadb = bool(getattr(connection.dialect, "is_mariadb", False))
    default = inspector.default_schema_name if dialect != "sqlite" else None
    reader = Reader(connection, inspector, name=name, default=default, diagnostics=diagnostics)
    selected = [None] if dialect == "sqlite" else (schemas or [default])
    tables = [t for schema in selected for t in reader.schema_tables(schema)]
    driver = "mariadb" if mariadb else {"postgresql": "pgsql"}.get(dialect, dialect)
    connection_entry = {
        "id": connection_id(name), "name": name, "driver": driver, "defaultSchema": default,
        "coverage": {"database": coverage(dialect, mariadb, connection, None if dialect == "sqlite" else selected,
                                          reader.unavailable), "declared": None},
        "tables": sorted(tables, key=lambda t: t["id"]),
    }
    doc: dict[str, Any] = {
        "format": "laranex-schema", "version": VERSION, "source": "database",
        "generator": {"name": GENERATOR, "orm": None}, "note": None, "connections": [connection_entry],
        "models": [], "relationships": [], "links": [], "derivedLinks": [], "diagnostics": sort_diagnostics(diagnostics),
    }
    doc.update(build_graph(doc))
    return assert_document(doc, strict=True).to_json()


def coverage(dialect: str, mariadb: bool, connection: Any, schemas: list[str] | None,
             unavailable: set[str]) -> dict[str, Any]:
    features = {f: "complete" for f in FEATURES}
    features.update(models="not_applicable", relationships="not_applicable")
    if dialect == "mysql":
        features.update(includedColumns="not_applicable", indexPredicates="not_applicable")
        version = connection.dialect.server_version_info or ()
        if mariadb:
            features["indexExpressions"] = "not_applicable"
        elif version < (8, 0, 13):
            features["indexExpressions"] = "not_applicable"
    elif dialect == "sqlite":
        features.update(comments="not_applicable", includedColumns="not_applicable", checkConstraints="partial")
    for feature in unavailable:
        features[feature] = "unavailable"
    complete = all(v in ("complete", "not_applicable") for v in features.values())
    return {"complete": complete, "schemas": schemas, "features": features}


def type_name(native: str, sa_type: Any) -> str:
    if type(sa_type).__name__.upper() == "ENUM" or native.lower().startswith("enum("):
        return "enum"
    base = re.sub(r"\(.*?\)", "", native).strip().lower()
    base = re.sub(r"\s+(unsigned|zerofill|signed)\b", "", base)
    return TYPE_NAMES.get(base, base)


def default_value(text: str | None) -> dict[str, str] | None:
    if text is None:
        return None
    value = str(text).strip()
    if value.lower().startswith("nextval("):
        return {"kind": "sequence", "value": value}
    return {"kind": "literal" if LITERAL.fullmatch(value) else "expression", "value": value}


def owner(table: str) -> dict[str, Any]:
    kind, package = OWNERS.get(table, ("application", None))
    return {"kind": kind, "package": package}


def part(column: str | None = None, expression: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"column": column, "expression": expression, "order": kw.get("order"), "nulls": kw.get("nulls"),
            "length": kw.get("length"), "collation": kw.get("collation"), "opclass": kw.get("opclass")}


def index_entry(tid: str, name: str, parts: list[dict[str, Any]], *, unique: bool, primary: bool = False,
                backs: dict[str, Any] | None = None, method: str | None = None, predicate: str | None = None,
                included: list[str] | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = {
        "id": f"{tid}:index:{name}", "name": name, "parts": parts,
        "columns": [p["column"] for p in parts if p["column"] is not None],
        "expression": any(p["expression"] is not None for p in parts), "unique": unique, "primary": primary,
        "backs": backs, "method": method, "partial": predicate is not None, "predicate": predicate,
        "included": included or [], "provenance": ["database"],
    }
    if options:
        entry["dialectOptions"] = options
    return entry


def referenced_columns(expression: str, columns: list[str]) -> list[str]:
    found = {token.strip('`"') for token in IDENTIFIER.findall(expression)}
    return [c for c in columns if c in found]


def split_top_level(text: str, separator: str = ",") -> list[str]:
    parts, depth, quote, current = [], 0, None, []
    for char in text:
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"`[":
            quote = "]" if char == "[" else char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return parts


def sqlite_index_sql(sql: str) -> tuple[list[str], str | None]:
    """The key list and WHERE clause of a CREATE INDEX statement."""
    start = sql.index("(", re.search(r"\bON\b", sql, re.IGNORECASE).end())
    depth, end = 0, start
    for end, char in enumerate(sql[start:], start):
        depth += char == "("
        depth -= char == ")"
        if depth == 0:
            break
    rest = sql[end + 1:].strip()
    where = re.match(r"WHERE\s+(.*)$", rest, re.IGNORECASE | re.DOTALL)
    return split_top_level(sql[start + 1:end]), where.group(1).strip().rstrip(";").strip() if where else None


class Reader:
    def __init__(self, connection: Any, inspector: Any, *, name: str, default: str | None,
                 diagnostics: list[dict[str, Any]]) -> None:
        self.connection, self.inspector, self.name, self.default = connection, inspector, name, default
        self.dialect = connection.dialect.name
        self.diagnostics = diagnostics
        self.unavailable: set[str] = set()

    def table_id(self, schema: str | None, table: str) -> str:
        return identifier(self.name, None if schema in (None, self.default) else schema, table)

    def note(self, kind: str, message: str, subject: dict[str, Any] | None = None, severity: str = "warning") -> None:
        self.diagnostics.append({
            "kind": kind, "category": "inspection", "severity": severity, "certainty": "certain",
            "subject": subject or {"type": "connection", "id": connection_id(self.name), "columns": None,
                                   "label": self.name},
            "message": message})

    def multi(self, method: str, schema: str | None, feature: str | None = None) -> dict[Any, Any]:
        try:
            return dict(getattr(self.inspector, method)(schema=schema))
        except NotImplementedError:
            if feature:
                self.unavailable.add(feature)
                self.note("inspection_unavailable", f"{feature} could not be read from this database")
            return {}

    def schema_tables(self, schema: str | None) -> list[dict[str, Any]]:
        if self.dialect != "sqlite" and schema not in self.inspector.get_schema_names():
            from . import IntrospectionError

            raise IntrospectionError(f"Schema {schema!r} does not exist or the role cannot see it")
        partitions = self.partitions(schema)
        names = [t for t in self.inspector.get_table_names(schema=schema) if t not in partitions]
        columns = self.multi("get_multi_columns", schema)
        primary = self.multi("get_multi_pk_constraint", schema)
        foreign = self.multi("get_multi_foreign_keys", schema, "foreignKeys")
        uniques = self.multi("get_multi_unique_constraints", schema, "uniqueConstraints")
        checks = self.multi("get_multi_check_constraints", schema, "checkConstraints")
        comments = self.multi("get_multi_table_comment", schema) if self.dialect != "sqlite" else {}
        natives = self.native_types(schema)
        indexes = self.indexes(schema, names, primary, uniques)
        tables = []
        for table in names:
            key = (schema, table)
            tid = self.table_id(schema, table)
            tables.append(self.table(schema, table, tid, columns.get(key, []), primary.get(key) or {},
                                     foreign.get(key, []), uniques.get(key, []), checks.get(key, []),
                                     (comments.get(key) or {}).get("text"), natives.get(table, {}),
                                     indexes.get(table, [])))
        return tables

    def partitions(self, schema: str | None) -> set[str]:
        if self.dialect != "postgresql":
            return set()
        from sqlalchemy import text

        return set(self.connection.execute(text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relispartition"), {"schema": schema}).scalars())

    def native_types(self, schema: str | None) -> dict[str, dict[str, str]]:
        from sqlalchemy import text

        out: dict[str, dict[str, str]] = {}
        if self.dialect == "postgresql":
            rows = self.connection.execute(text(
                "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND a.attnum > 0 AND NOT a.attisdropped"), {"schema": schema})
        elif self.dialect == "mysql":
            rows = self.connection.execute(text(
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = :schema"), {"schema": schema})
        else:
            rows = [(t, c[1], c[2]) for t in self.inspector.get_table_names()
                    for c in self.connection.exec_driver_sql(f"PRAGMA table_xinfo({quote_sqlite(t)})")]
        for table, column, native in rows:
            out.setdefault(table, {})[column] = native
        return out

    def column(self, c: dict[str, Any], native: str | None, tid: str, rowid: bool = False) -> dict[str, Any]:
        # rowid: SQLite's INTEGER PRIMARY KEY aliases the rowid, which the database assigns.
        sa_type = c["type"]
        if native is None or native == "":
            native = "" if type(sa_type).__name__ == "NullType" else sa_type.compile(dialect=self.connection.dialect)
        computed, identity = c.get("computed"), c.get("identity")
        default = None if computed else default_value(c.get("default"))
        if identity:
            default = {"kind": "identity",
                       "value": f"GENERATED {'ALWAYS' if identity.get('always') else 'BY DEFAULT'} AS IDENTITY"}
        enum = None
        if type(sa_type).__name__.upper() == "ENUM":
            enum = {"name": getattr(sa_type, "name", None) if self.dialect == "postgresql" else None,
                    "values": list(sa_type.enums)}
        return {
            "name": c["name"], "type": native, "typeName": type_name(native, sa_type),
            "nullable": bool(c["nullable"]), "autoIncrement": rowid or c.get("autoincrement") is True,
            "timezone": getattr(sa_type, "timezone", None) if hasattr(sa_type, "timezone") else None,
            "collation": getattr(sa_type, "collation", None), "enum": enum, "default": default,
            "ormDefault": None,
            "generated": {"kind": ("stored" if computed.get("persisted") else "virtual")
                          if computed.get("persisted") is not None else None,
                          "expression": str(computed["sqltext"])} if computed else None,
            "comment": c.get("comment"), "note": None, "provenance": ["database"],
        }

    def table(self, schema: str | None, table: str, tid: str, columns: list[dict[str, Any]], pk: dict[str, Any],
              foreign: list[dict[str, Any]], uniques: list[dict[str, Any]], checks: list[dict[str, Any]],
              comment: str | None, natives: dict[str, str], indexes: list[dict[str, Any]]) -> dict[str, Any]:
        names = [c["name"] for c in columns]
        actions = self.sqlite_actions(table) if self.dialect == "sqlite" else {}
        foreign_keys = []
        for fk in foreign:
            target = self.table_id(fk.get("referred_schema") or schema if self.dialect == "mysql"
                                   else fk.get("referred_schema") or self.default, fk["referred_table"])
            options = {**(fk.get("options") or {}), **actions.get(tuple(fk["constrained_columns"]), {})}
            on_delete, _ = action(options.get("ondelete") or "NO ACTION")
            on_update, _ = action(options.get("onupdate") or "NO ACTION")
            deferrable = None if self.dialect == "mysql" else bool(options.get("deferrable", False))
            initially = None if self.dialect == "mysql" else (options.get("initially") or "IMMEDIATE").upper()
            foreign_keys.append({
                "id": identifier("foreign_key", tid, fk["name"], identifier(*fk["constrained_columns"]), target,
                                 identifier(*fk["referred_columns"])),
                "name": fk["name"], "columns": fk["constrained_columns"], "foreignTable": target,
                "foreignColumns": fk["referred_columns"], "onUpdate": on_update, "onDelete": on_delete,
                "deferrable": deferrable, "initially": initially, "provenance": ["database"],
            })
        constraints = []
        if self.dialect != "mysql":  # MySQL and MariaDB unique constraints are unique indexes; listed there.
            for n, u in enumerate(uniques):
                constraints.append({"id": f"{tid}:constraint:{u['name'] if u['name'] else n}", "kind": "unique",
                                    "name": u["name"], "columns": u["column_names"], "expression": None,
                                    "provenance": ["database"]})
        for n, ck in enumerate(checks):
            expression = str(ck["sqltext"])
            constraints.append({"id": f"{tid}:constraint:{ck['name'] if ck.get('name') else f'check{n}'}",
                                "kind": "check", "name": ck.get("name"),
                                "columns": referenced_columns(expression, names), "expression": expression,
                                "provenance": ["database"]})
        return {
            "id": tid, "name": table, "schema": schema, "comment": comment, "note": None, "owner": owner(table),
            "primaryKey": list(pk.get("constrained_columns") or []),
            "columns": [self.column(c, natives.get(c["name"]), tid, rowid=self.dialect == "sqlite" and
                                    [c["name"]] == pk.get("constrained_columns") and
                                    (natives.get(c["name"]) or "").upper() == "INTEGER") for c in columns],
            "indexes": sorted(indexes, key=lambda i: i["id"]),
            "foreignKeys": sorted(foreign_keys, key=lambda f: f["id"]),
            "constraints": sorted(constraints, key=lambda c: c["id"]), "provenance": ["database"],
        }

    def sqlite_actions(self, table: str) -> dict[tuple[str, ...], dict[str, str]]:
        """Referential actions from PRAGMA foreign_key_list; the Inspector misses column-level clauses."""
        grouped: dict[int, list[Any]] = {}
        for row in self.connection.exec_driver_sql(f"PRAGMA foreign_key_list({quote_sqlite(table)})").all():
            grouped.setdefault(row[0], []).append(row)
        return {tuple(r[3] for r in sorted(rows, key=lambda r: r[1])): {"onupdate": rows[0][5], "ondelete": rows[0][6]}
                for rows in grouped.values()}

    def indexes(self, schema: str | None, names: list[str], primary: dict[Any, Any],
                uniques: dict[Any, Any]) -> dict[str, list[dict[str, Any]]]:
        if self.dialect == "postgresql":
            return self.postgresql_indexes(schema, primary)
        if self.dialect == "mysql":
            return self.mysql_indexes(schema)
        return {t: self.sqlite_indexes(t, uniques.get((None, t), [])) for t in names}

    def postgresql_indexes(self, schema: str | None, primary: dict[Any, Any]) -> dict[str, list[dict[str, Any]]]:
        from sqlalchemy import text

        details: dict[tuple[str, str], dict[str, Any]] = {}
        for table, index, method, position, collation, is_primary in self.connection.execute(text(
            "SELECT t.relname, c.relname, am.amname, k.n, "
            "CASE WHEN k.coll <> 0 AND k.coll IS DISTINCT FROM a.attcollation AND co.collname <> 'default' "
            "THEN co.collname END, ix.indisprimary "
            "FROM pg_index ix JOIN pg_class c ON c.oid = ix.indexrelid JOIN pg_class t ON t.oid = ix.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace JOIN pg_am am ON am.oid = c.relam "
            "CROSS JOIN LATERAL unnest(ix.indcollation::oid[], (ix.indkey::int2[])[0:ix.indnkeyatts - 1]) "
            "WITH ORDINALITY AS k(coll, attnum, n) "
            "LEFT JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum AND k.attnum > 0 "
            "LEFT JOIN pg_collation co ON co.oid = k.coll WHERE n.nspname = :schema ORDER BY 1, 2, 4"),
                {"schema": schema}):
            entry = details.setdefault((table, index), {"method": method, "collations": [], "primary": is_primary})
            entry["collations"].append(collation)
        out: dict[str, list[dict[str, Any]]] = {}
        for (key_schema, table), found in self.multi("get_multi_indexes", schema, "indexes").items():
            tid = self.table_id(key_schema, table)
            for ix in found:
                options = ix.get("dialect_options") or {}
                info = details.get((table, ix["name"]), {"method": None, "collations": []})
                sorting = ix.get("column_sorting") or {}
                ops = options.get("postgresql_ops") or {}
                keys = ix.get("expressions") or ix["column_names"]
                parts = []
                for n, (column, key) in enumerate(zip(ix["column_names"], keys)):
                    flags = sorting.get(key, ())
                    parts.append(part(
                        column if column is not None else None, None if column is not None else key,
                        order="desc" if "desc" in flags else None,
                        nulls="last" if "nulls_last" in flags else "first" if "nulls_first" in flags else None,
                        collation=info["collations"][n] if n < len(info["collations"]) else None,
                        opclass=ops.get(key)))
                extra = {"nullsNotDistinct": True} if options.get("postgresql_nulls_not_distinct") else None
                constraint = ix.get("duplicates_constraint")
                out.setdefault(table, []).append(index_entry(
                    tid, ix["name"], parts, unique=bool(ix["unique"]),
                    backs={"kind": "unique_constraint", "constraint": f"{tid}:constraint:{constraint}"}
                    if constraint else None,
                    method=info["method"], predicate=options.get("postgresql_where"),
                    included=list(options.get("postgresql_include") or []),
                    options={"postgresql": {"options": extra}} if extra else None))
        for (key_schema, table), pk in primary.items():
            if pk and pk.get("name") and pk.get("constrained_columns"):
                tid = self.table_id(key_schema, table)
                info = details.get((table, pk["name"]), {"method": "btree"})
                out.setdefault(table, []).append(index_entry(
                    tid, pk["name"], [part(c) for c in pk["constrained_columns"]], unique=True, primary=True,
                    backs={"kind": "primary_key", "constraint": None}, method=info["method"]))
        return out

    def mysql_indexes(self, schema: str | None) -> dict[str, list[dict[str, Any]]]:
        from sqlalchemy import text

        columns = set(self.connection.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'information_schema' "
            "AND TABLE_NAME = 'STATISTICS'")).scalars())
        expression = "EXPRESSION" if "EXPRESSION" in columns else "NULL"
        visible = "IS_VISIBLE" if "IS_VISIBLE" in columns else "NULL"
        ignored = "IGNORED" if "IGNORED" in columns else "NULL"
        grouped: dict[tuple[str, str], list[Any]] = {}
        for row in self.connection.execute(text(
            f"SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME, {expression}, COLLATION, SUB_PART, INDEX_TYPE, "
            f"{visible}, {ignored} FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = :schema "
            "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"), {"schema": schema}):
            grouped.setdefault((row[0], row[1]), []).append(row)
        out: dict[str, list[dict[str, Any]]] = {}
        for (table, name), rows in grouped.items():
            tid = self.table_id(schema, table)
            method = (rows[0][7] or "").lower() or None
            # STATISTICS escapes quotes inside functional key parts.
            parts = [part(r[3], None if r[3] is not None else (r[4] or '').replace("\\'", "'") or None,
                          order="desc" if r[5] == "D" else None,
                          length=r[6] if r[6] and method != "spatial" else None) for r in rows]
            options = {}
            if rows[0][8] == "NO":
                options["visible"] = False
            if rows[0][9] == "YES":
                options["ignored"] = True
            primary = name == "PRIMARY"
            out.setdefault(table, []).append(index_entry(
                tid, name, parts, unique=not rows[0][2], primary=primary,
                backs={"kind": "primary_key", "constraint": None} if primary else None, method=method,
                options={"mysql": {"options": options}} if options else None))
        return out

    def sqlite_indexes(self, table: str, uniques: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tid = self.table_id(None, table)
        unique_by_columns = {tuple(u["column_names"]): u for u in uniques}
        out = []
        for _, name, unique, origin, partial in self.connection.exec_driver_sql(
                f"PRAGMA index_list({quote_sqlite(table)})").all():
            keys = [r for r in self.connection.exec_driver_sql(f"PRAGMA index_xinfo({quote_sqlite(name)})").all()
                    if r[5]]
            texts, predicate = [], None
            if origin == "c":
                sql = self.connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)).scalar()
                if sql:
                    texts, predicate = sqlite_index_sql(sql)
            parts = []
            for n, (_, cid, column, desc, _, _) in enumerate(keys):
                expression = None
                if cid == -2:
                    raw = texts[n] if n < len(texts) else ""
                    expression = re.sub(r"\s+(ASC|DESC)$", "", re.sub(r"\s+COLLATE\s+\w+", "", raw, flags=re.I),
                                        flags=re.I).strip() or None
                # index_xinfo also reports collations inherited from the column; only an explicit COLLATE is the
                # index's own.
                explicit = re.search(r"\bCOLLATE\s+(\w+)", texts[n], re.I) if n < len(texts) else None
                shown = explicit.group(1) if explicit else None
                parts.append(part(column if cid != -2 else None, expression, order="desc" if desc else None,
                                  collation=shown))
            backs = None
            if origin == "pk":
                backs = {"kind": "primary_key", "constraint": None}
            elif origin == "u":
                constraint = unique_by_columns.get(tuple(p["column"] for p in parts))
                if constraint and constraint.get("name"):
                    backs = {"kind": "unique_constraint", "constraint": f"{tid}:constraint:{constraint['name']}"}
            out.append(index_entry(tid, name, parts, unique=bool(unique), primary=origin == "pk", backs=backs,
                                   predicate=predicate if partial else None))
        return out


def quote_sqlite(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
