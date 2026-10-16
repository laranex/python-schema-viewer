"""Bounded, read-only queries shared by MCP tools; no ORM dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .document import SchemaDocument
from .snapshot import read_snapshot, revision

MAX_RESPONSE_BYTES = 64 * 1024


class SnapshotQuery:
    """Pin one validated snapshot/revision for a server's lifetime."""

    def __init__(self, path: str | Path) -> None:
        self.document: SchemaDocument = read_snapshot(path)
        self.revision = revision(self.document)
        self.tables = {t.id: t for t in self.document.tables}
        self.models = {m.id: m for m in self.document.models}

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
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
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
        doc = self.document
        return self.page([
            dict(id=t.id, namespace=t.namespace, schema_name=t.schema_name, name=t.name,
                 columns=len(t.columns), foreign_keys=len(t.foreign_keys)) for t in doc.tables
        ], counts=dict(tables=len(doc.tables), models=len(doc.models),
                       columns=sum(len(t.columns) for t in doc.tables),
                       indexes=sum(len(t.indexes) for t in doc.tables),
                       foreign_keys=sum(len(t.foreign_keys) for t in doc.tables),
                       relationships=len(doc.relationships), diagnostics=len(doc.diagnostics)),
            scope=doc.scope, **pagination)

    def get_table(self, table: str, section: str = "columns", **pagination: Any) -> dict[str, Any]:
        if table not in self.tables:
            raise ValueError("Unknown table ID; use schema_overview or schema_search")
        value = self.tables[table]
        sections = {
            "columns": value.columns, "indexes": value.indexes,
            "foreign_keys": value.foreign_keys, "constraints": value.constraints,
            "models": [m for m in self.document.models if table in m.table_ids],
            "relationships": [r for r in self.document.relationships
                              if table in (r.source_table, r.target_table)],
        }
        if section not in sections:
            raise ValueError(f"Unknown table section: {section}")
        return self.page(sections[section], table=dict(
            id=value.id, name=value.name, namespace=value.namespace,
            schema_name=value.schema_name, primary_key=value.primary_key,
        ), section=section, counts={key: len(items) for key, items in sections.items()}, **pagination)

    def get_model(self, model: str, **pagination: Any) -> dict[str, Any]:
        if model not in self.models:
            raise ValueError("Unknown model ID; use schema_search")
        return self.page([r for r in self.document.relationships if r.model == model],
                         model=self.models[model].model_dump(mode="json"), **pagination)

    def relationships(self, table: str | None = None, kind: str = "orm",
                      **pagination: Any) -> dict[str, Any]:
        if table is not None and table not in self.tables:
            raise ValueError("Unknown table ID")
        if kind == "orm":
            items = [r.model_dump(mode="json") for r in self.document.relationships
                     if table is None or table in (r.source_table, r.target_table)]
        elif kind == "foreign_key":
            items = [dict(source_table=t.id, **fk.model_dump(mode="json"))
                     for t in self.document.tables for fk in t.foreign_keys
                     if table is None or table in (t.id, fk.target_table)]
        else:
            raise ValueError("kind must be orm or foreign_key")
        return self.page(items, kind=kind, **pagination)

    def search(self, query: str, **pagination: Any) -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("query must contain 1 to 200 characters")
        needle = query.casefold()
        items = []
        for table in self.document.tables:
            if needle in f"{table.namespace}.{table.schema_name or ''}.{table.name}".casefold():
                items.append(dict(kind="table", id=table.id, name=table.name))
            for column in table.columns:
                if needle in column.name.casefold():
                    items.append(dict(kind="column", table=table.id, name=column.name))
        for model in self.document.models:
            if needle in model.class_name.casefold():
                items.append(dict(kind="model", id=model.id, name=model.class_name))
        for relation in self.document.relationships:
            if needle in f"{relation.name} {relation.direction}".casefold():
                items.append(dict(kind="relationship", id=relation.id, name=relation.name))
        return self.page(items, **pagination)

