"""Validation of laranex-schema documents: shape (Pydantic), unknown properties (strict mode) and the reference
rules of docs/contract.md, with the same messages as the TypeScript validator."""

from __future__ import annotations

import json
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .models import FORMAT, MAJOR, Coverage, Document, Table

MAX_ERRORS = 100


class UnsupportedDocumentError(ValueError):
    pass


class InvalidDocumentError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        more = f"; and {len(errors) - 5} more" if len(errors) > 5 else ""
        super().__init__(f"Invalid laranex-schema document: {'; '.join(errors[:5])}{more}")
        self.errors = errors


def supported(data: Any) -> str | None:
    """The format and major version, checked first so a wrong document gets one clear error."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return f"Not a {FORMAT} document"
    version = data.get("version")
    major = version.split(".")[0] if isinstance(version, str) else None
    if major != str(MAJOR):
        return f"Unsupported {FORMAT} version {json.dumps(version)}; this reader supports {MAJOR}.x"
    return None


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The model a field holds, through list, dict and optional wrappers."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        found = _model_of(arg)
        if found is not None:
            return found
    return None


def unknown_properties(model: type[BaseModel], data: Any, path: str = "$") -> list[str]:
    """Properties this version does not define, which strict (producer) validation rejects."""
    if not isinstance(data, dict):
        return []
    fields = {(f.alias or name): f for name, f in model.model_fields.items()}
    errors = [f"{path}: unknown property {key}" for key in data if key not in fields]
    for key, field in fields.items():
        value = data.get(key)
        inner = _model_of(field.annotation)
        if inner is None or value is None:
            continue
        origin = get_origin(field.annotation)
        if isinstance(value, list):
            for i, item in enumerate(value):
                errors += unknown_properties(inner, item, f"{path}.{key}[{i}]")
        elif isinstance(value, dict) and (origin is dict or any(get_origin(a) is dict for a in get_args(field.annotation))):
            for k, item in value.items():
                errors += unknown_properties(inner, item, f"{path}.{key}.{k}")
        else:
            errors += unknown_properties(inner, value, f"{path}.{key}")
    return errors


def _shape_errors(error: ValidationError) -> list[str]:
    out = []
    for e in error.errors():
        location = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e["loc"])
        out.append(f"${location}: {e['msg']}")
    return out


def validate_document(data: Any, *, strict: bool = False) -> tuple[Document | None, list[str]]:
    unsupported = supported(data)
    if unsupported:
        return None, [unsupported]
    try:
        document = Document.model_validate(data)
    except ValidationError as error:
        return None, _shape_errors(error)[:MAX_ERRORS]
    errors = unknown_properties(Document, data) if strict else []
    if not errors:
        errors = rules(document)
    return (None, errors[:MAX_ERRORS]) if errors else (document, [])


def assert_document(data: Any, *, strict: bool = False) -> Document:
    unsupported = supported(data)
    if unsupported:
        raise UnsupportedDocumentError(unsupported)
    document, errors = validate_document(data, strict=strict)
    if document is None:
        raise InvalidDocumentError(errors)
    return document


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    for v in values:
        if v in seen and v not in found:
            found.append(v)
        seen.add(v)
    return found


def rules(doc: Document) -> list[str]:
    errors: list[str] = []
    tables = doc.tables()
    table_by_id = {t.id: t for t in tables}

    def columns_of(tid: str) -> set[str]:
        return {c.name for c in table_by_id[tid].columns} if tid in table_by_id else set()

    foreign_keys = [f for t in tables for f in t.foreign_keys]
    indexes = [i for t in tables for i in t.indexes]
    constraints = [c for t in tables for c in t.constraints]
    fk_ids = {f.id for f in foreign_keys}
    models = {m.id for m in doc.models}
    relationships = {r.id for r in doc.relationships}
    links = {link.id for link in doc.links}

    def unresolved(kind: str, subject: str) -> bool:
        return any(d.kind == kind and d.subject.id == subject for d in doc.diagnostics)

    for label, values in [
        ("connection", [c.id for c in doc.connections]), ("table", [t.id for t in tables]),
        ("index", [i.id for i in indexes]), ("foreign-key", [f.id for f in foreign_keys]),
        ("constraint", [c.id for c in constraints]), ("model", [m.id for m in doc.models]),
        ("relationship", [r.id for r in doc.relationships]),
        ("link", [x.id for x in doc.links] + [x.id for x in doc.derived_links]),
    ]:
        errors += [f"Duplicate {label} id: {v}" for v in _duplicates(values)]
    errors += [f"Duplicate connection name: {v}" for v in _duplicates([c.name for c in doc.connections])]

    allowed = {"declared": {"declared"}, "database": {"database"}, "combined": {"database", "declared"}}[doc.source]
    physical = []
    for t in tables:
        physical.append((f"table {t.id}", t))
        physical += [(f"column {t.id}.{c.name}", c) for c in t.columns]
        physical += [(f"index {i.id}", i) for i in t.indexes]
        physical += [(f"foreign key {f.id}", f) for f in t.foreign_keys]
        physical += [(f"constraint {c.id}", c) for c in t.constraints]
    for what, entity in physical:
        if not set(entity.provenance) <= allowed:
            errors.append(f"Provenance {'+'.join(entity.provenance)} is not possible in a {doc.source} document: {what}")
        if entity.drift and not {"database", "declared"} <= set(entity.provenance):
            errors.append(f"Drift needs both database and declared provenance: {what}")
    needs = {"declared": (False, True), "database": (True, False), "combined": (True, True)}[doc.source]
    for connection in doc.connections:
        coverage = connection.coverage
        if (coverage.database is not None, coverage.declared is not None) != needs:
            errors.append(f"Coverage does not match source {doc.source}: connection {connection.id}")
        for name, entry in (("database", coverage.database), ("declared", coverage.declared)):
            if isinstance(entry, Coverage) and entry.complete and any(
                    v in ("partial", "unavailable") for v in entry.features.values()):
                errors.append(f"Coverage is marked complete with partial or unavailable features: "
                              f"connection {connection.id} ({name})")

    for t in tables:
        errors += _table_rules(t)
        own = columns_of(t.id)
        for fk in t.foreign_keys:
            if len(fk.columns) != len(fk.foreign_columns):
                errors.append(f"Foreign-key column counts differ: {fk.id}")
            if not set(fk.columns) <= own:
                errors.append(f"Unknown foreign-key column: {fk.id}")
            excused = unresolved("unresolved_foreign_key", fk.id)
            if fk.foreign_table not in table_by_id and not excused:
                errors.append(f"Unknown foreign-key target table: {fk.id}")
            elif fk.foreign_table in table_by_id and not set(fk.foreign_columns) <= columns_of(fk.foreign_table) \
                    and not excused:
                errors.append(f"Unknown foreign-key target column: {fk.id}")

    for m in doc.models:
        if not set(m.tables) <= set(table_by_id):
            errors.append(f"Unknown model table: {m.id}")

    def endpoint_ok(e: Any) -> bool:
        return e.table in table_by_id and set(e.columns) <= columns_of(e.table)

    for r in doc.relationships:
        if r.model not in models:
            errors.append(f"Unknown relationship model: {r.id}")
        if (r.related_model is None and r.polymorphic is None
                and not unresolved("unresolved_relationship", r.id)) or (
                r.related_model is not None and r.related_model not in models):
            errors.append(f"Unknown related model: {r.id}")
        if r.table not in table_by_id or (r.related_table is not None and r.related_table not in table_by_id):
            errors.append(f"Unknown relationship table: {r.id}")
        if r.keys and (not endpoint_ok(r.keys.from_) or not endpoint_ok(r.keys.to)
                       or len(r.keys.from_.columns) != len(r.keys.to.columns)):
            errors.append(f"Invalid relationship keys: {r.id}")
        if r.pivot:
            p = r.pivot
            pivot_columns = columns_of(p.table)
            pairs = (len(p.parent_columns) == len(p.parent_pivot_columns)
                     and len(p.related_columns) == len(p.related_pivot_columns))
            if p.table not in table_by_id or not pairs or not set(
                    p.parent_pivot_columns + p.related_pivot_columns + p.columns) <= pivot_columns:
                errors.append(f"Invalid relationship pivot: {r.id}")
        if r.through is not None and not set(r.through) <= set(table_by_id):
            errors.append(f"Unknown relationship through table: {r.id}")
        if r.inverse is not None and r.inverse not in relationships:
            errors.append(f"Unknown inverse relationship: {r.id}")

    for link in doc.links:
        if not endpoint_ok(link.from_) or not endpoint_ok(link.to):
            errors.append(f"Unknown link endpoint: {link.id}")
        if link.foreign_key is not None and link.foreign_key not in fk_ids:
            errors.append(f"Unknown link foreign key: {link.id}")
        if not set(link.relationships) <= relationships:
            errors.append(f"Unknown link relationship: {link.id}")
        state = (("both" if link.relationships else "foreign_key") if link.foreign_key is not None
                 else "relationship" if link.relationships else None)
        if state != link.state:
            errors.append(f"Link state {link.state} does not match its foreign key and relationships: {link.id}")
    for d in doc.derived_links:
        if not {d.from_, d.to, *d.via} <= set(table_by_id) or d.relationship not in relationships:
            errors.append(f"Unknown derived link reference: {d.id}")

    exists = {
        "connection": lambda i: any(c.id == i for c in doc.connections), "table": lambda i: i in table_by_id,
        "column": lambda i: i in table_by_id, "index": lambda i: any(x.id == i for x in indexes),
        "foreignKey": lambda i: i in fk_ids, "constraint": lambda i: any(x.id == i for x in constraints),
        "model": lambda i: i in models, "relationship": lambda i: i in relationships, "link": lambda i: i in links,
    }
    for n, d in enumerate(doc.diagnostics):
        s = d.subject
        if s.type == "document" and s.id is not None:
            errors.append(f"A document diagnostic has no subject id: diagnostics[{n}]")
        unresolved_kind = d.kind in ("unresolved_foreign_key", "unresolved_relationship")
        if s.id is not None and s.type != "document" and not exists[s.type](s.id) and not unresolved_kind:
            errors.append(f"Unknown diagnostic subject {s.type} {s.id}: diagnostics[{n}]")
        if s.type == "column" and s.id in table_by_id and not set(s.columns or []) <= columns_of(s.id):
            errors.append(f"Unknown diagnostic subject column: diagnostics[{n}]")
    return errors


def _table_rules(table: Table) -> list[str]:
    errors = []
    names = [c.name for c in table.columns]
    columns = set(names)
    errors += [f"Duplicate column {c}: {table.id}" for c in _duplicates(names)]
    if not set(table.primary_key) <= columns:
        errors.append(f"Unknown primary-key column: {table.id}")
    for index in table.indexes:
        at = f"index {index.id}"
        if any((p.column is None) == (p.expression is None) for p in index.parts):
            errors.append(f"An index part is exactly one of a column or an expression: {at}")
        if [p.column for p in index.parts if p.column is not None] != index.columns:
            errors.append(f"Index columns must list its column parts in order: {at}")
        if not set(index.columns + index.included) <= columns:
            errors.append(f"Unknown index column: {at}")
        if any(p.expression is not None for p in index.parts) and not index.expression:
            errors.append(f"An index with expression parts has expression true: {at}")
        if not index.partial and index.predicate is not None:
            errors.append(f"Only a partial index has a predicate: {at}")
        if index.primary and (not index.unique or index.backs is None or index.backs.kind != "primary_key"):
            errors.append(f"A primary index is unique and backs the primary key: {at}")
        if index.backs and index.backs.kind == "primary_key" and (
                not index.primary or index.columns != table.primary_key):
            errors.append(f"An index backing the primary key is primary and has its columns: {at}")
        if index.backs and index.backs.kind == "unique_constraint":
            backed = next((c for c in table.constraints if c.id == index.backs.constraint), None)
            if not index.unique or (index.backs.constraint is not None and (backed is None or backed.kind != "unique")):
                errors.append(f"An index backing a unique constraint is unique and names a unique constraint of "
                              f"its table: {at}")
    for c in table.constraints:
        if not set(c.columns) <= columns:
            errors.append(f"Unknown constraint column: constraint {c.id}")
        if c.kind == "unique" and not c.columns and c.expression is None:
            errors.append(f"A unique constraint has columns or an expression: constraint {c.id}")
    return errors
