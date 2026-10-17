"""Bounded, read-only queries over one saved laranex-schema 2.x document, shared by the MCP tools; no ORM dependencies.

The response format is specified in docs/mcp.md, and tests/fixtures/mcp-conformance records it for every shared valid
contract fixture. Items are the document's own contract objects (camelCase, exactly as serialized), apart from the
overview's table summaries, search hits and foreign-key entries, whose shapes docs/mcp.md defines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import model as m
from .contract import to_json
from .snapshot import read_document

MAX_RESPONSE_BYTES = 64 * 1024
# Room kept for nextOffset, which is set after the items that fit are chosen.
RESERVED_BYTES = 128
SECTIONS = ("columns", "indexes", "foreign_keys", "constraints", "models", "relationships", "links",
            "derived_links", "diagnostics")
KINDS = ("orm", "foreign_key")
CATEGORIES = ("extraction", "inspection", "reconciliation", "drift", "lint")
SEVERITIES = ("error", "warning", "info")
CERTAINTIES = ("certain", "likely")
COMPARE_SECTIONS = ("comparisons", "differences")
OBJECT_TYPES = ("table", "column", "primaryKey", "index", "foreignKey", "constraint")
CHANGES = ("missing", "extra", "changed")


def size(value: Any) -> int:
    """UTF-8 bytes of compact JSON (no whitespace, non-ASCII unescaped), as JavaScript's JSON.stringify writes it."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class SnapshotQuery:
    """Pin one validated document and its revision for a server's lifetime."""

    def __init__(self, path: str | Path) -> None:
        snapshot = read_document(path)
        self.document = snapshot.document
        self.version = snapshot.data["version"]
        self.revision = snapshot.revision
        doc = self.document
        self.connection_of = {t.id: c for c in doc.connections for t in c.tables}
        self.tables = {t.id: t for t in doc.tables()}
        self.models = {x.id: x for x in doc.models}
        self.comparisons = {c.id: c for c in doc.comparisons}

    def page(self, items: list[Any], *, limit: int = 25, offset: int = 0,
             revision: str | None = None, **context: Any) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if revision is not None and revision != self.revision:
            raise ValueError("Document revision differs; restart pagination without revision")
        result = dict(context, revision=self.revision, total=len(items), offset=offset, limit=limit, items=[],
                      nextOffset=None)
        for item in items[offset:offset + limit]:
            result["items"].append(item)
            if size(result) > MAX_RESPONSE_BYTES - RESERVED_BYTES:
                result["items"].pop()
                if not result["items"]:
                    raise ValueError("One result exceeds the response byte limit")
                break
        end = offset + len(result["items"])
        result["nextOffset"] = end if end < len(items) else None
        if size(result) > MAX_RESPONSE_BYTES:
            raise ValueError("Response metadata exceeds the response byte limit")
        return result

    def overview(self, **pagination: Any) -> dict[str, Any]:
        doc = self.document
        tables = doc.tables()
        physical = [e for t in tables for e in (t, *t.columns, *t.indexes, *t.foreign_keys, *t.constraints)]
        counts = {
            "connections": len(doc.connections), "tables": len(tables),
            "columns": sum(len(t.columns) for t in tables), "indexes": sum(len(t.indexes) for t in tables),
            "foreignKeys": sum(len(t.foreign_keys) for t in tables),
            "constraints": sum(len(t.constraints) for t in tables), "models": len(doc.models),
            "relationships": len(doc.relationships), "links": len(doc.links), "derivedLinks": len(doc.derived_links),
            "diagnostics": len(doc.diagnostics), "drift": sum(1 for e in physical if e.drift),
            "comparisons": len(doc.comparisons), "migrations": len(doc.migrations),
            "inferredAssociations": len(doc.inferred_associations),
        }
        by = {name: {value: sum(1 for d in doc.diagnostics if getattr(d, name) == value) for value in values}
                       for name, values in (("category", CATEGORIES), ("severity", SEVERITIES),
                                            ("certainty", CERTAINTIES))}
        connections = [{"id": c.id, "name": c.name, "driver": c.driver, "defaultSchema": c.default_schema,
                        "coverage": to_json(c.coverage), "tables": len(c.tables)} for c in doc.connections]
        return self.page([self.summary(t) for t in tables], version=self.version, source=doc.source,
                         generator=to_json(doc.generator), note=doc.note, connections=connections, counts=counts,
                         diagnosticCounts=by, **pagination)

    def summary(self, table: m.Table) -> dict[str, Any]:
        entities = (table, *table.columns, *table.indexes, *table.foreign_keys, *table.constraints)
        return {
            "id": table.id, "connection": self.connection_of[table.id].id, "name": table.name,
            "schema": table.schema, "provenance": list(table.provenance),
            "counts": {"columns": len(table.columns), "indexes": len(table.indexes),
                       "foreignKeys": len(table.foreign_keys), "constraints": len(table.constraints),
                       "drift": sum(1 for e in entities if e.drift),
                       "diagnostics": len(self.table_diagnostics(table))},
        }

    def table_diagnostics(self, table: m.Table) -> list[m.Diagnostic]:
        """Diagnostics about the table, its columns, or its indexes, foreign keys and constraints."""
        own = {table.id, *(x.id for x in (*table.indexes, *table.foreign_keys, *table.constraints))}
        return [d for d in self.document.diagnostics
                if d.subject.type in ("table", "column", "index", "foreignKey", "constraint") and d.subject.id in own]

    def table_relationships(self, table: str) -> list[m.Relationship]:
        """Relationships declared from, to, through or over a pivot of the table."""
        return [r for r in self.document.relationships
                if table in (r.table, r.related_table) or (r.pivot is not None and r.pivot.table == table)
                or table in (r.through or ())]

    def get_table(self, table: str, section: str = "columns", **pagination: Any) -> dict[str, Any]:
        if table not in self.tables:
            raise ValueError("Unknown table ID; use schema_overview or schema_search")
        if section not in SECTIONS:
            raise ValueError(f"Unknown table section: {section}")
        value = self.tables[table]
        doc = self.document
        sections: dict[str, list[Any]] = {
            "columns": list(value.columns), "indexes": list(value.indexes),
            "foreign_keys": list(value.foreign_keys), "constraints": list(value.constraints),
            "models": [x for x in doc.models if table in x.tables],
            "relationships": self.table_relationships(table),
            "links": [x for x in doc.links if table in (x.source.table, x.target.table)],
            "derived_links": [x for x in doc.derived_links if table in (x.source, x.target, *x.via)],
            "diagnostics": self.table_diagnostics(value),
        }
        header = {k: v for k, v in to_json(value).items()
                  if k not in ("columns", "indexes", "foreignKeys", "constraints")}
        connection = self.connection_of[table]
        return self.page([to_json(x) for x in sections[section]], table=header,
                         connection={"id": connection.id, "name": connection.name, "driver": connection.driver,
                                     "defaultSchema": connection.default_schema},
                         section=section, counts={key: len(items) for key, items in sections.items()}, **pagination)

    def get_model(self, model: str, **pagination: Any) -> dict[str, Any]:
        if model not in self.models:
            raise ValueError("Unknown model ID; use schema_search")
        return self.page([to_json(r) for r in self.document.relationships if r.model == model],
                         model=to_json(self.models[model]), **pagination)

    def relationships(self, table: str | None = None, kind: str = "orm", **pagination: Any) -> dict[str, Any]:
        if table is not None and table not in self.tables:
            raise ValueError("Unknown table ID")
        if kind == "orm":
            items = [to_json(r) for r in (self.document.relationships if table is None
                                          else self.table_relationships(table))]
        elif kind == "foreign_key":
            items = [{"table": t.id, "foreignKey": to_json(fk)} for t in self.document.tables()
                     for fk in t.foreign_keys if table is None or table in (t.id, fk.foreign_table)]
        else:
            raise ValueError("kind must be orm or foreign_key")
        return self.page(items, kind=kind, table=table, **pagination)

    def search(self, query: str, **pagination: Any) -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("query must contain 1 to 200 characters")
        needle = query.casefold()
        items = []

        def hit(kind: str, id: str | None, table: str | None, name: str) -> None:
            items.append({"kind": kind, "id": id, "table": table, "name": name})

        for table in self.document.tables():
            qualified = ".".join(filter(None, [self.connection_of[table.id].name, table.schema, table.name]))
            if needle in qualified.casefold():
                hit("table", table.id, table.id, table.name)
            for column in table.columns:
                if needle in column.name.casefold():
                    hit("column", None, table.id, column.name)
            for kind, entities in (("index", table.indexes), ("foreignKey", table.foreign_keys),
                                   ("constraint", table.constraints)):
                for entity in entities:
                    if entity.name is not None and needle in entity.name.casefold():
                        hit(kind, entity.id, table.id, entity.name)
        for x in self.document.models:
            if needle in x.class_name.casefold():
                hit("model", x.id, None, x.class_name)
        for r in self.document.relationships:
            if needle in f"{r.name} {r.type}".casefold():
                hit("relationship", r.id, r.table, r.name)
        return self.page(items, query=query, **pagination)

    def scope_label(self, scope: m.Scope) -> str:
        """A scope as the command line spells it: the connection's name, then ``:schema``."""
        name = next((c.name for c in self.document.connections if c.id == scope.connection), scope.connection)
        return name if scope.schema is None else f"{name}:{scope.schema}"

    def scope_filter(self, value: str | None, side: str) -> m.Scope | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{side} must be CONNECTION or CONNECTION:SCHEMA")
        name, separator, schema = value.partition(":")
        if separator and not schema:
            raise ValueError(f"{side} must be CONNECTION or CONNECTION:SCHEMA")
        connection = next((c for c in self.document.connections if c.name == name), None)
        if connection is None:
            raise ValueError(f"Unknown {side} connection; use schema_overview")
        return m.Scope(connection=connection.id, schema=schema if separator else None)

    def compare(self, section: str = "comparisons", comparison: str | None = None, baseline: str | None = None,
                target: str | None = None, object_type: str | None = None, change: str | None = None,
                severity: str | None = None, certainty: str | None = None, **pagination: Any) -> dict[str, Any]:
        """Stored comparisons, or their differences, selected by comparison id or scopes and difference filters."""
        if section not in COMPARE_SECTIONS:
            raise ValueError("section must be comparisons or differences")
        if comparison is not None and comparison not in self.comparisons:
            raise ValueError("Unknown comparison ID; use schema_compare")
        scopes = {"baseline": self.scope_filter(baseline, "baseline"), "target": self.scope_filter(target, "target")}
        filters = {"objectType": object_type, "change": change, "severity": severity, "certainty": certainty}
        for (name, value), allowed in zip(filters.items(), (OBJECT_TYPES, CHANGES, SEVERITIES, CERTAINTIES)):
            if value is not None and value not in allowed:
                argument = "object_type" if name == "objectType" else name
                raise ValueError(f"{argument} must be one of {', '.join(allowed)}")
        if section == "comparisons" and any(v is not None for v in filters.values()):
            raise ValueError("object_type, change, severity and certainty filter differences; use section differences")
        selected = [c for c in self.document.comparisons
                    if (comparison is None or c.id == comparison)
                    and (scopes["baseline"] is None or c.baseline == scopes["baseline"])
                    and (scopes["target"] is None or c.target == scopes["target"])]
        context: dict[str, Any] = {"section": section, "filters": {"comparison": comparison, "baseline": baseline,
                                                                    "target": target, **filters}}
        if section == "comparisons":
            return self.page([self.comparison_summary(c) for c in selected], **context, **pagination)
        items = [{"comparison": c.id, "difference": to_json(d)} for c in selected for d in c.differences
                 if all(v is None or getattr(d, attribute) == v for attribute, v in
                        (("object_type", object_type), ("change", change), ("severity", severity),
                         ("certainty", certainty)))]
        return self.page(items, **context, comparisons=len(selected), omitted=sum(c.omitted for c in selected),
                         **pagination)

    def comparison_summary(self, comparison: m.Comparison) -> dict[str, Any]:
        """The Comparison object without its differences, with readable scope labels and counts of the listed ones."""
        summary = {k: v for k, v in to_json(comparison).items() if k != "differences"}
        listed = comparison.differences
        summary["labels"] = {"baseline": self.scope_label(comparison.baseline),
                             "target": self.scope_label(comparison.target)}
        summary["counts"] = {"differences": len(listed),
                             **{v: sum(1 for d in listed if d.severity == v) for v in SEVERITIES},
                             **{v: sum(1 for d in listed if d.certainty == v) for v in CERTAINTIES}}
        return summary
