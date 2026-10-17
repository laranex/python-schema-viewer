"""Unified serialization: the adapters' declared-metadata document (laranex-schema-viewer version 1) as a
laranex-schema 1.0 document. Also the explicit conversion of version 1 snapshots. Never imports an ORM."""

from __future__ import annotations

import json
import re
from typing import Any

from .contract import VERSION, assert_document, build_graph
from .contract.models import FEATURES
from .document import Default, Index, SchemaDocument
from .document import Column as V1Column
from .document import Table as V1Table

GENERATOR = "python-schema-viewer"
ACTIONS = {"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"}
LITERAL = re.compile(r"^(?:'(?:[^']|'')*'(?:::[\w ]+)?|-?\d+(?:\.\d+)?|true|false|null)$", re.IGNORECASE)
IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
CARDINALITY = {("MANYTOONE", False): "many-to-one", ("MANYTOONE", True): "many-to-one",
               ("ONETOMANY", True): "one-to-many", ("ONETOMANY", False): "one-to-one",
               ("MANYTOMANY", True): "many-to-many", ("MANYTOMANY", False): "many-to-many"}


def connection_id(namespace: str) -> str:
    return f"connection:{namespace}"


def declared_coverage() -> dict[str, Any]:
    """What ORM metadata extraction reads: every feature, from the declarations."""
    return {"complete": True, "schemas": None, "features": {f: "complete" for f in FEATURES}}


def database_default(value: Default | None) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """A server-side default as (default, generated column). Never invents SQL from a class name."""
    if value is None:
        return None, None
    if value.kind == "sequence":
        return {"kind": "sequence", "value": value.value}, None
    if value.kind == "generated":
        if IDENTIFIER.fullmatch(value.value):
            # Identity / FetchedValue: the database supplies the value; the adapter recorded only its kind.
            return {"kind": "identity" if value.value == "Identity" else "raw", "value": value.value}, None
        return None, {"kind": None, "expression": value.value}
    if value.kind == "sql":
        return {"kind": "literal" if LITERAL.fullmatch(value.value.strip()) else "expression", "value": value.value}, None
    return {"kind": "raw", "value": value.value}, None


def orm_default(value: Default | None) -> dict[str, str] | None:
    if value is None:
        return None
    kind = {"scalar": "scalar", "callable": "callable", "sequence": "sequence"}.get(value.kind, "expression")
    return {"kind": kind, "value": value.value}


def action(value: str | None) -> tuple[str | None, str | None]:
    """A referential action as the contract spells it, or (None, original) when it is not an SQL action."""
    if value is None:
        return None, None
    normal = str(value).strip().upper().replace("_", " ")
    return (normal, None) if normal in ACTIONS else (None, str(value))


def column(c: V1Column, extensions_namespace: str) -> dict[str, Any]:
    default, generated = database_default(c.server_default)
    out: dict[str, Any] = {
        "name": c.name, "type": c.type, "typeName": None, "nullable": c.nullable,
        "autoIncrement": c.autoincrement if isinstance(c.autoincrement, bool) else None,
        "timezone": c.timezone, "collation": None, "enum": None, "default": default,
        "ormDefault": orm_default(c.default), "generated": generated, "comment": c.comment, "note": None,
        "provenance": ["declared"],
    }
    if c.autoincrement == "ignore_fk":
        out["extensions"] = {extensions_namespace: {"autoincrement": "ignore_fk"}}
    return out


def index_parts(index: Index) -> list[dict[str, Any]]:
    """Key parts in order. SQLAlchemy lists every key part as an expression, plain columns included."""
    keys = index.expressions or index.columns
    columns = list(index.columns)
    parts = []
    for key in keys:
        is_column = bool(columns) and key == columns[0]
        if is_column:
            columns.pop(0)
        parts.append({"column": key if is_column else None, "expression": None if is_column else key,
                      "order": None, "nulls": None, "length": None, "collation": None, "opclass": None})
    parts += [{"column": c, "expression": None, "order": None, "nulls": None, "length": None,
               "collation": None, "opclass": None} for c in columns]
    return parts


def table(t: V1Table, extensions_namespace: str) -> dict[str, Any]:
    indexes = []
    for n, i in enumerate(t.indexes):
        parts = index_parts(i)
        options = {}
        for dialect in sorted({*i.predicates, *i.included_columns, *i.methods}):
            entry = {k: v for k, v in (("method", i.methods.get(dialect)), ("predicate", i.predicates.get(dialect)),
                                       ("included", i.included_columns.get(dialect))) if v is not None}
            options[dialect] = entry
        index = {
            "id": f"{t.id}:index:{i.name if i.name is not None else n}", "name": i.name, "parts": parts,
            "columns": [p["column"] for p in parts if p["column"] is not None],
            "expression": any(p["expression"] is not None for p in parts), "unique": i.unique, "primary": False,
            "backs": None, "method": None, "partial": False, "predicate": None, "included": [],
            "provenance": ["declared"],
        }
        if options:
            index["dialectOptions"] = options
        indexes.append(index)
    foreign_keys = []
    for fk in t.foreign_keys:
        on_delete, raw_delete = action(fk.on_delete)
        on_update, raw_update = action(fk.on_update)
        entry: dict[str, Any] = {
            "id": fk.id, "name": fk.name, "columns": fk.columns, "foreignTable": fk.target_table,
            "foreignColumns": fk.target_columns, "onUpdate": on_update, "onDelete": on_delete,
            "deferrable": fk.deferrable,
            "initially": fk.initially.upper() if fk.initially and fk.initially.upper() in ("DEFERRED", "IMMEDIATE") else None,
            "provenance": ["declared"],
        }
        kept = {k: v for k, v in (("onDelete", raw_delete), ("onUpdate", raw_update)) if v is not None}
        if kept:
            entry["extensions"] = {extensions_namespace: kept}
        foreign_keys.append(entry)
    return {
        "id": t.id, "name": t.name, "schema": t.schema_name, "comment": t.comment, "note": None,
        "owner": {"kind": "application", "package": None}, "primaryKey": t.primary_key,
        "columns": [column(c, extensions_namespace) for c in t.columns], "indexes": indexes,
        "foreignKeys": foreign_keys,
        "constraints": [{"id": f"{t.id}:constraint:{c.name if c.name is not None else n}", "kind": c.kind,
                         "name": c.name, "columns": c.columns, "expression": c.expression,
                         "provenance": ["declared"]} for n, c in enumerate(t.constraints)],
        "provenance": ["declared"],
    }


def relationship(r: Any, relationships: dict[tuple[str, str], str]) -> dict[str, Any]:
    keys = pivot = None
    if r.secondary is None and r.join_pairs:
        # Keys run like a foreign key: from the columns that refer to the others.
        local = {"table": r.join_pairs[0].local.table, "columns": [p.local.column for p in r.join_pairs]}
        remote = {"table": r.join_pairs[0].remote.table, "columns": [p.remote.column for p in r.join_pairs]}
        consistent = all(p.local.table == local["table"] and p.remote.table == remote["table"] for p in r.join_pairs)
        if consistent:
            keys = {"from": local, "to": remote} if r.direction == "MANYTOONE" else {"from": remote, "to": local}
    elif r.secondary is not None:
        primary = r.primary_join or ""
        parent = [p for p in r.join_pairs if p.remote.table == r.secondary and p.local.table == r.source_table
                  and f"{p.remote.column}" in primary]
        related = [p for p in r.join_pairs if p.remote.table == r.secondary and p not in parent]
        pivot = {"table": r.secondary, "parentColumns": [p.local.column for p in parent],
                 "parentPivotColumns": [p.remote.column for p in parent],
                 "relatedColumns": [p.local.column for p in related],
                 "relatedPivotColumns": [p.remote.column for p in related], "columns": []}
    return {
        "id": r.id, "name": r.name, "model": r.model, "table": r.source_table, "relatedModel": r.target_model,
        "relatedTable": r.target_table, "type": r.direction,
        "cardinality": CARDINALITY[(r.direction, bool(r.uselist))], "collection": r.uselist, "readOnly": r.viewonly,
        "keys": keys, "pivot": pivot, "polymorphic": None, "through": None,
        "join": {"primary": r.primary_join or None, "secondary": r.secondary_join},
        "constraints": [], "inverse": relationships.get((r.target_model, r.back_populates)) if r.back_populates else None,
        "note": None, "source": None,
    }


def subject(value: str, doc: dict[str, Any], v1: SchemaDocument) -> dict[str, Any]:
    tables = {t.id for t in v1.tables}
    models = {m.id for m in v1.models}
    relationships = {r["id"] for r in doc["relationships"]}
    if value in tables:
        return {"type": "table", "id": value, "columns": None, "label": None}
    if value in models:
        return {"type": "model", "id": value, "columns": None, "label": None}
    owner, dot, key = value.rpartition(".")
    if dot and owner in models:
        rid = json.dumps(["relationship", owner, key], ensure_ascii=False, separators=(",", ":"))
        if rid in relationships:
            return {"type": "relationship", "id": rid, "columns": None, "label": key}
        return {"type": "model", "id": owner, "columns": None, "label": value}
    return {"type": "document", "id": None, "columns": None, "label": value}


def from_v1(v1: SchemaDocument, *, orm: str | None = None) -> dict[str, Any]:
    """A laranex-schema 1.0 document (declared source) from the adapters' output or a version 1 snapshot."""
    namespace = orm or "python"
    connections: dict[str, dict[str, Any]] = {}
    for t in v1.tables:
        connections.setdefault(t.namespace, {
            "id": connection_id(t.namespace), "name": t.namespace, "driver": None, "defaultSchema": None,
            "coverage": {"database": None, "declared": declared_coverage()}, "tables": [],
        })["tables"].append(table(t, namespace))
    for c in connections.values():
        c["tables"].sort(key=lambda t: t["id"])
    by_name = {(r.model, r.name): r.id for r in v1.relationships}
    doc: dict[str, Any] = {
        "format": "laranex-schema", "version": VERSION, "source": "declared",
        "generator": {"name": GENERATOR, "orm": orm}, "note": None,
        "connections": [connections[name] for name in sorted(connections)],
        "models": sorted([{"id": m.id, "className": m.class_name, "tables": m.table_ids, "primaryKey": m.primary_key,
                           "source": m.source.model_dump() if m.source else None} for m in v1.models],
                         key=lambda m: m["id"]),
        "relationships": sorted([relationship(r, by_name) for r in v1.relationships], key=lambda r: r["id"]),
        "links": [], "derivedLinks": [], "diagnostics": [],
    }
    diagnostics = []
    fk_by_table = {t.id: t.foreign_keys for t in v1.tables}
    columns = {t.id: {c.name for c in t.columns} for t in v1.tables}
    for d in v1.diagnostics:
        if d.kind == "unresolved_foreign_key" and d.subject in fk_by_table:
            # Version 1 names the table; the contract names each foreign key it could not resolve.
            for fk in fk_by_table[d.subject]:
                if fk.target_table not in columns or not set(fk.target_columns) <= columns[fk.target_table]:
                    diagnostics.append({"kind": d.kind, "category": "extraction", "severity": "warning",
                                        "certainty": "certain",
                                        "subject": {"type": "foreignKey", "id": fk.id, "columns": fk.columns,
                                                    "label": fk.name}, "message": d.message})
            continue
        diagnostics.append({"kind": d.kind, "category": "extraction", "severity": "warning", "certainty": "certain",
                            "subject": subject(d.subject, doc, v1), "message": d.message})
    doc["diagnostics"] = sort_diagnostics(diagnostics)
    doc.update(build_graph(doc))
    return assert_document(doc, strict=True).to_json()


def sort_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(diagnostics, key=lambda d: (d["category"], d["kind"], d["subject"]["type"],
                                             d["subject"]["id"] or "", d["message"]))
