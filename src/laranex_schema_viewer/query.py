"""Bounded, read-only queries shared by MCP tools; no ORM dependencies.

Queries run over one view of the pinned document. A version 1 snapshot's view is its own JSON, so its responses are
exactly what they always were. A laranex-schema document (declared, database or combined) is projected onto the same
fields, plus fields only it has: provenance, drift, coverage, index parts and relationship cardinality.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .snapshot import load_document
from .snapshot import revision as v1_revision

MAX_RESPONSE_BYTES = 64 * 1024
SCOPES = {"declared": "declared_metadata", "database": "database_metadata", "combined": "combined_metadata"}
DIALECTS = {"pgsql": "postgresql"}


class SnapshotQuery:
    """Pin one validated snapshot/revision for a server's lifetime."""

    def __init__(self, path: str | Path) -> None:
        loaded = load_document(path)
        self.format = loaded.format
        if loaded.v1 is not None:
            self.view = loaded.v1.model_dump(mode="json")
            self.revision = v1_revision(loaded.v1)
        else:
            from .contract import revision

            self.view = unified_view(loaded.unified)
            self.revision = revision(loaded.unified)
        self.tables = {t["id"]: t for t in self.view["tables"]}
        self.models = {m["id"]: m for m in self.view["models"]}

    def page(self, items: list[Any], *, limit: int = 25, offset: int = 0,
             revision: str | None = None, **context: Any) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if revision is not None and revision != self.revision:
            raise ValueError("Snapshot revision differs; restart pagination without revision")
        result = dict(context, revision=self.revision, total=len(items), items=[], next_offset=None)
        for item in items[offset:offset + limit]:
            result["items"].append(item)
            if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES - 128:
                result["items"].pop()
                if not result["items"]:
                    raise ValueError("One result exceeds the response byte limit")
                break
        end = offset + len(result["items"])
        result["next_offset"] = end if end < len(items) else None
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
            raise ValueError("Response metadata exceeds the response byte limit")
        return result

    def overview(self, **pagination: Any) -> dict[str, Any]:
        view = self.view
        tables = view["tables"]
        extra = {k: view[k] for k in ("source", "connections") if k in view}
        counts = dict(tables=len(tables), models=len(view["models"]),
                      columns=sum(len(t["columns"]) for t in tables),
                      indexes=sum(len(t["indexes"]) for t in tables),
                      foreign_keys=sum(len(t["foreign_keys"]) for t in tables),
                      relationships=len(view["relationships"]), diagnostics=len(view["diagnostics"]))
        if "source" in view:
            counts["drift"] = sum(1 for d in view["diagnostics"] if d["category"] == "drift")
        return self.page([
            dict(id=t["id"], namespace=t["namespace"], schema_name=t["schema_name"], name=t["name"],
                 columns=len(t["columns"]), foreign_keys=len(t["foreign_keys"]),
                 **{k: t[k] for k in ("provenance",) if k in t}) for t in tables
        ], counts=counts, scope=view["scope"], **extra, **pagination)

    def get_table(self, table: str, section: str = "columns", **pagination: Any) -> dict[str, Any]:
        if table not in self.tables:
            raise ValueError("Unknown table ID; use schema_overview or schema_search")
        value = self.tables[table]
        sections = {
            "columns": value["columns"], "indexes": value["indexes"],
            "foreign_keys": value["foreign_keys"], "constraints": value["constraints"],
            "models": [m for m in self.view["models"] if table in m["table_ids"]],
            "relationships": [r for r in self.view["relationships"]
                              if table in (r["source_table"], r["target_table"])],
        }
        if section not in sections:
            raise ValueError(f"Unknown table section: {section}")
        summary = dict(id=value["id"], name=value["name"], namespace=value["namespace"],
                       schema_name=value["schema_name"], primary_key=value["primary_key"])
        summary.update({k: value[k] for k in ("provenance", "drift", "owner") if k in value})
        return self.page(sections[section], table=summary, section=section,
                         counts={key: len(items) for key, items in sections.items()}, **pagination)

    def get_model(self, model: str, **pagination: Any) -> dict[str, Any]:
        if model not in self.models:
            raise ValueError("Unknown model ID; use schema_search")
        return self.page([r for r in self.view["relationships"] if r["model"] == model],
                         model=self.models[model], **pagination)

    def relationships(self, table: str | None = None, kind: str = "orm",
                      **pagination: Any) -> dict[str, Any]:
        if table is not None and table not in self.tables:
            raise ValueError("Unknown table ID")
        if kind == "orm":
            items = [r for r in self.view["relationships"]
                     if table is None or table in (r["source_table"], r["target_table"])]
        elif kind == "foreign_key":
            items = [dict(source_table=t["id"], **fk) for t in self.view["tables"] for fk in t["foreign_keys"]
                     if table is None or table in (t["id"], fk["target_table"])]
        else:
            raise ValueError("kind must be orm or foreign_key")
        return self.page(items, kind=kind, **pagination)

    def search(self, query: str, **pagination: Any) -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("query must contain 1 to 200 characters")
        needle = query.casefold()
        items = []
        for table in self.view["tables"]:
            if needle in f"{table['namespace']}.{table['schema_name'] or ''}.{table['name']}".casefold():
                items.append(dict(kind="table", id=table["id"], name=table["name"]))
            for column in table["columns"]:
                if needle in column["name"].casefold():
                    items.append(dict(kind="column", table=table["id"], name=column["name"]))
        for model in self.view["models"]:
            if needle in model["class_name"].casefold():
                items.append(dict(kind="model", id=model["id"], name=model["class_name"]))
        for relation in self.view["relationships"]:
            if needle in f"{relation['name']} {relation['direction']}".casefold():
                items.append(dict(kind="relationship", id=relation["id"], name=relation["name"]))
        return self.page(items, **pagination)


def unified_view(doc: dict[str, Any]) -> dict[str, Any]:
    """A laranex-schema document in the version 1 response fields, keeping what only the unified contract has."""
    tables = [table_view(t, c) for c in doc["connections"] for t in c["tables"]]
    relationships = {r["id"]: r for r in doc["relationships"]}
    return {
        "scope": SCOPES[doc["source"]], "source": doc["source"],
        "connections": [{"id": c["id"], "name": c["name"], "driver": c["driver"],
                         "default_schema": c["defaultSchema"], "coverage": c["coverage"]} for c in doc["connections"]],
        "tables": tables,
        "models": [{"id": m["id"], "class_name": m["className"], "table_ids": m["tables"],
                    "primary_key": m["primaryKey"], "source": m["source"]} for m in doc["models"]],
        "relationships": [relationship_view(r, relationships) for r in doc["relationships"]],
        "diagnostics": [{"kind": d["kind"], "subject": d["subject"]["id"] or d["subject"]["label"] or "",
                         "message": d["message"], "category": d["category"], "severity": d["severity"],
                         "certainty": d["certainty"], "subject_type": d["subject"]["type"],
                         "columns": d["subject"]["columns"]} for d in doc["diagnostics"]],
    }


def physical(entity: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    out["provenance"] = entity["provenance"]
    if entity.get("drift"):
        out["drift"] = entity["drift"]
    return out


def server_default(column: dict[str, Any]) -> dict[str, str] | None:
    if column["generated"]:
        return {"kind": "generated", "value": column["generated"]["expression"]}
    default = column["default"]
    if default is None:
        return None
    kind = {"literal": "sql", "expression": "sql", "sequence": "sequence"}.get(default["kind"], "generated")
    return {"kind": kind, "value": default["value"]}


def orm_default(column: dict[str, Any]) -> dict[str, str] | None:
    default = column["ormDefault"]
    if default is None:
        return None
    return {"kind": {"expression": "sql"}.get(default["kind"], default["kind"]), "value": default["value"]}


def table_view(t: dict[str, Any], connection: dict[str, Any]) -> dict[str, Any]:
    dialect = DIALECTS.get(connection["driver"], connection["driver"])
    columns = []
    for c in t["columns"]:
        extension = next((e.get("autoincrement") for e in (c.get("extensions") or {}).values()), None)
        columns.append(physical(c, {
            "name": c["name"], "type": c["type"], "timezone": c["timezone"], "nullable": c["nullable"],
            "primary_key": c["name"] in t["primaryKey"],
            "autoincrement": extension or ("auto" if c["autoIncrement"] is None else c["autoIncrement"]),
            "default": orm_default(c), "server_default": server_default(c), "comment": c["comment"],
            "type_name": c["typeName"], "collation": c["collation"], "enum": c["enum"],
        }))
    indexes = []
    for i in t["indexes"]:
        options = dict(i.get("dialectOptions") or {})
        predicates = {d: o["predicate"] for d, o in options.items() if o.get("predicate")}
        included = {d: o["included"] for d, o in options.items() if o.get("included")}
        methods = {d: o["method"] for d, o in options.items() if o.get("method")}
        if dialect:
            if i["predicate"]:
                predicates.setdefault(dialect, i["predicate"])
            if i["included"]:
                included.setdefault(dialect, i["included"])
            if i["method"]:
                methods.setdefault(dialect, i["method"])
        indexes.append(physical(i, {
            "name": i["name"], "columns": i["columns"],
            "expressions": [p["expression"] or p["column"] for p in i["parts"]] if i["expression"] else [],
            "unique": i["unique"], "predicates": predicates, "included_columns": included, "methods": methods,
            "id": i["id"], "parts": i["parts"], "primary": i["primary"], "backs": i["backs"],
            "partial": i["partial"],
        }))
    foreign_keys = []
    for fk in t["foreignKeys"]:
        raw = next(iter((fk.get("extensions") or {}).values()), {})
        foreign_keys.append(physical(fk, {
            "id": fk["id"], "name": fk["name"], "columns": fk["columns"], "target_table": fk["foreignTable"],
            "target_columns": fk["foreignColumns"], "on_delete": raw.get("onDelete", fk["onDelete"]),
            "on_update": raw.get("onUpdate", fk["onUpdate"]), "deferrable": fk["deferrable"],
            "initially": fk["initially"],
        }))
    return physical(t, {
        "id": t["id"], "namespace": connection["name"], "name": t["name"], "schema_name": t["schema"],
        "comment": t["comment"], "columns": columns, "primary_key": t["primaryKey"], "indexes": indexes,
        "foreign_keys": foreign_keys,
        "constraints": [physical(c, {"kind": c["kind"], "name": c["name"], "columns": c["columns"],
                                     "expression": c["expression"], "id": c["id"]}) for c in t["constraints"]],
        "owner": t["owner"],
    })


def relationship_view(r: dict[str, Any], relationships: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    if r["keys"]:
        local, remote = ((r["keys"]["from"], r["keys"]["to"]) if r["type"] == "MANYTOONE"
                         else (r["keys"]["to"], r["keys"]["from"]))
        pairs = [{"local": {"table": local["table"], "column": a}, "remote": {"table": remote["table"], "column": b}}
                 for a, b in zip(local["columns"], remote["columns"])]
    elif r["pivot"]:
        pivot = r["pivot"]
        pairs = [{"local": {"table": r["table"], "column": a}, "remote": {"table": pivot["table"], "column": b}}
                 for a, b in zip(pivot["parentColumns"], pivot["parentPivotColumns"])]
        pairs += [{"local": {"table": r["relatedTable"], "column": a}, "remote": {"table": pivot["table"], "column": b}}
                  for a, b in zip(pivot["relatedColumns"], pivot["relatedPivotColumns"])]
    inverse = relationships.get(r["inverse"]) if r["inverse"] else None
    join = r["join"] or {}
    return {
        "id": r["id"], "name": r["name"], "model": r["model"], "target_model": r["relatedModel"],
        "source_table": r["table"], "target_table": r["relatedTable"], "direction": r["type"],
        "uselist": r["collection"], "viewonly": r["readOnly"],
        "back_populates": inverse["name"] if inverse else None,
        "secondary": r["pivot"]["table"] if r["pivot"] else None, "primary_join": join.get("primary") or "",
        "secondary_join": join.get("secondary"), "join_pairs": pairs,
        "cardinality": r["cardinality"], "polymorphic": r["polymorphic"], "through": r["through"],
        "constraints": r["constraints"], "note": r["note"],
    }
