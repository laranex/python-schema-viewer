"""Validation of laranex-schema 2.x documents, a port of laranex-schema-viewer-ui's validate.ts with its messages.

First the format and major version (one clear error for anything else, 1.x included), then the shape (shape.py),
then the reference rules of docs/contract.md, checked on the model the document deserializes to. Strict validation
(producers and tests) accepts exactly version 2.0 and rejects unknown properties; lenient validation (readers)
accepts any 2.x and ignores what it does not know.
"""

from __future__ import annotations

import json
from typing import Any

from .. import model as m
from .graph import in_scope
from .ids import (by_list, comparison_id, difference_id, inferred_association_id, link_id, migration_track_id,
                  sorted_strings)
from .migration import migration_history, migration_state
from .serialize import FORMAT, MAJOR, VERSION, document_from_json
from .shape import CHANGES, OBJECT_TYPES, Context, document

MAX_ERRORS = 100
DRIVER_ALIASES = {"pgsql": "postgresql", "postgres": "postgresql", "mssql": "sqlsrv", "sqlserver": "sqlsrv"}


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
        return (f"Unsupported {FORMAT} version {json.dumps(version, ensure_ascii=False)}; "
                f"this reader supports {MAJOR}.x")
    return None


def validate_document(data: Any, *, strict: bool = False) -> tuple[m.Document | None, list[str]]:
    """The document as the model, or None and the errors (at most 100)."""
    unsupported = supported(data)
    if unsupported:
        return None, [unsupported]
    errors: list[str] = []
    if strict and data.get("version") != VERSION:
        errors.append(f"$.version: strict validation is for version {VERSION}, got "
                      f"{json.dumps(data.get('version'), ensure_ascii=False)}")
    document.check(data, "$", Context(errors, strict))
    if errors:
        return None, errors[:MAX_ERRORS]
    doc = document_from_json(data)
    rules(doc, data, errors)
    return (None, errors[:MAX_ERRORS]) if errors else (doc, [])


def assert_document(data: Any, *, strict: bool = False) -> m.Document:
    unsupported = supported(data)
    if unsupported:
        raise UnsupportedDocumentError(unsupported)
    doc, errors = validate_document(data, strict=strict)
    if doc is None:
        raise InvalidDocumentError(errors)
    return doc


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    for v in values:
        if v in seen and v not in found:
            found.append(v)
        seen.add(v)
    return found


def _is_sorted(values: list[str] | tuple[str, ...]) -> bool:
    return list(values) == sorted_strings(values)


def _sorted_by_id(items: tuple[Any, ...]) -> bool:
    return _is_sorted([i.id for i in items])


def rules(doc: m.Document, data: dict[str, Any], errors: list[str]) -> None:
    tables = doc.tables()
    table_by_id = {t.id: t for t in tables}
    connection_of = {t.id: c.id for c in doc.connections for t in c.tables}

    def columns_of(tid: str) -> set[str]:
        return {c.name for c in table_by_id[tid].columns} if tid in table_by_id else set()

    foreign_keys = [f for t in tables for f in t.foreign_keys]
    indexes = [i for t in tables for i in t.indexes]
    constraints = [c for t in tables for c in t.constraints]
    fk_by_id = {f.id: f for f in foreign_keys}
    models = {x.id for x in doc.models}
    relationship_ids = {r.id for r in doc.relationships}
    links = {x.id for x in doc.links}
    # A diagnostic's stored id, which the model recomputes from its content.
    stored_ids = [d.get("id") for d in data.get("diagnostics", [])]

    def unresolved(kind: str, subject: str) -> bool:
        return any(d.kind == kind and d.subject.id == subject for d in doc.diagnostics)

    for label, values in [
        ("connection", [c.id for c in doc.connections]), ("table", [t.id for t in tables]),
        ("index", [i.id for i in indexes]), ("foreign-key", [f.id for f in foreign_keys]),
        ("constraint", [c.id for c in constraints]), ("model", [x.id for x in doc.models]),
        ("relationship", [r.id for r in doc.relationships]),
        ("link", [x.id for x in doc.links] + [x.id for x in doc.derived_links]),
        ("diagnostic", stored_ids), ("comparison", [c.id for c in doc.comparisons]),
        ("difference", [d.id for c in doc.comparisons for d in c.differences]),
        ("migration", [x.id for x in doc.migrations]),
        ("inferred-association", [a.id for a in doc.inferred_associations]),
    ]:
        errors += [f"Duplicate {label} id: {v}" for v in _duplicates(values)]
    errors += [f"Duplicate connection name: {v}" for v in _duplicates([c.name for c in doc.connections])]

    allowed = {"declared": {"declared"}, "database": {"database"}, "combined": {"database", "declared"}}[doc.source]
    physical: list[tuple[str, Any]] = []
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
        canonical = DRIVER_ALIASES.get(connection.driver) if connection.driver is not None else None
        if canonical:
            errors.append(f"Driver {connection.driver} is spelled {canonical}: connection {connection.id}")
        coverage = connection.coverage
        if (coverage.database is not None, coverage.declared is not None) != needs:
            errors.append(f"Coverage does not match source {doc.source}: connection {connection.id}")
        for name, entry in (("database", coverage.database), ("declared", coverage.declared)):
            if entry is not None and entry.complete and any(
                    v in ("partial", "unavailable") for v in entry.features.values()):
                errors.append(f"Coverage is marked complete with partial or unavailable features: "
                              f"connection {connection.id} ({name})")

    for t in tables:
        _table_rules(t, errors)
        own = columns_of(t.id)
        for fk in t.foreign_keys:
            if len(fk.columns) != len(fk.foreign_columns):
                errors.append(f"Foreign-key column counts differ: {fk.id}")
            if not set(fk.columns) <= own:
                errors.append(f"Unknown foreign-key column: {fk.id}")
            target = table_by_id.get(fk.foreign_table)
            excused = unresolved("unresolved_foreign_key", fk.id)
            if target is None and not excused:
                errors.append(f"Unknown foreign-key target table: {fk.id}")
            elif target is not None and connection_of[target.id] != connection_of[t.id]:
                errors.append(f"Foreign key crosses connections: {fk.id}")
            elif target is not None and not set(fk.foreign_columns) <= columns_of(target.id) and not excused:
                errors.append(f"Unknown foreign-key target column: {fk.id}")

    for x in doc.models:
        if not set(x.tables) <= set(table_by_id):
            errors.append(f"Unknown model table: {x.id}")

    def endpoint_ok(e: m.Endpoint) -> bool:
        return e.table in table_by_id and set(e.columns) <= columns_of(e.table)

    for r in doc.relationships:
        if r.model not in models:
            errors.append(f"Unknown relationship model: {r.id}")
        if (r.related_model is None and r.polymorphic is None and not unresolved("unresolved_relationship", r.id)) \
                or (r.related_model is not None and r.related_model not in models):
            errors.append(f"Unknown related model: {r.id}")
        if r.table not in table_by_id or (r.related_table is not None and r.related_table not in table_by_id):
            errors.append(f"Unknown relationship table: {r.id}")
        if r.keys and (not endpoint_ok(r.keys.source) or not endpoint_ok(r.keys.target)
                       or len(r.keys.source.columns) != len(r.keys.target.columns)):
            errors.append(f"Invalid relationship keys: {r.id}")
        if r.pivot:
            p = r.pivot
            pairs = (len(p.parent_columns) == len(p.parent_pivot_columns)
                     and len(p.related_columns) == len(p.related_pivot_columns))
            if p.table not in table_by_id or not pairs or not set(
                    p.parent_pivot_columns + p.related_pivot_columns + p.columns) <= columns_of(p.table):
                errors.append(f"Invalid relationship pivot: {r.id}")
        if r.through and not set(r.through) <= set(table_by_id):
            errors.append(f"Unknown relationship through table: {r.id}")
        if r.inverse is not None and r.inverse not in relationship_ids:
            errors.append(f"Unknown inverse relationship: {r.id}")

    for x in doc.links:
        if not endpoint_ok(x.source) or not endpoint_ok(x.target):
            errors.append(f"Unknown link endpoint: {x.id}")
        if x.foreign_key is not None and x.foreign_key not in fk_by_id:
            errors.append(f"Unknown link foreign key: {x.id}")
        if not set(x.relationships) <= relationship_ids:
            errors.append(f"Unknown link relationship: {x.id}")
        state = (("both" if x.relationships else "foreign_key") if x.foreign_key is not None
                 else "relationship" if x.relationships else None)
        if state != x.state:
            errors.append(f"Link state {x.state} does not match its foreign key and relationships: {x.id}")
    derived = {x.id for x in doc.derived_links}
    for x in doc.derived_links:
        if not {x.source, x.target, *x.via} <= set(table_by_id) or x.relationship not in relationship_ids:
            errors.append(f"Unknown derived link reference: {x.id}")

    exists = {
        "document": lambda i: False,
        "connection": lambda i: any(c.id == i for c in doc.connections),
        "table": lambda i: i in table_by_id, "column": lambda i: i in table_by_id,
        "index": lambda i: any(x.id == i for x in indexes), "foreignKey": lambda i: i in fk_by_id,
        "constraint": lambda i: any(x.id == i for x in constraints), "model": lambda i: i in models,
        "relationship": lambda i: i in relationship_ids, "link": lambda i: i in links or i in derived,
        "comparison": lambda i: any(c.id == i for c in doc.comparisons),
        "migration": lambda i: any(x.id == i for x in doc.migrations),
        "inferredAssociation": lambda i: any(a.id == i for a in doc.inferred_associations),
    }
    for n, d in enumerate(doc.diagnostics):
        s = d.subject
        if stored_ids[n] != d.id:
            errors.append(f"Diagnostic id is not {d.id}: diagnostics[{n}]")
        if s.type == "column" and not s.columns:
            errors.append(f"A column subject names its columns: diagnostics[{n}]")
        if s.type == "document" and s.id is not None:
            errors.append(f"A document diagnostic has no subject id: diagnostics[{n}]")
        unresolved_kind = d.kind in ("unresolved_foreign_key", "unresolved_relationship")
        if s.id is not None and s.type != "document" and not exists[s.type](s.id) and not unresolved_kind:
            errors.append(f"Unknown diagnostic subject {s.type} {s.id}: diagnostics[{n}]")
        if s.type == "column" and s.id is not None and s.id in table_by_id and not set(
                s.columns or ()) <= columns_of(s.id):
            errors.append(f"Unknown diagnostic subject column: diagnostics[{n}]")

    connection_by_id = {c.id: c for c in doc.connections}
    _comparison_rules(doc, connection_by_id, table_by_id, errors)
    _migration_rules(doc, connection_by_id, table_by_id, errors)
    _inference_rules(doc, table_by_id, fk_by_id, errors)


def _owned(table_by_id: dict[str, m.Table], connection: m.Connection, schema: str | None, tid: str) -> bool:
    table = table_by_id.get(tid)
    return table is not None and any(t is table for t in connection.tables) and in_scope(table, connection, schema)


def _comparison_rules(doc: m.Document, connections: dict[str, m.Connection], tables: dict[str, m.Table],
                      errors: list[str]) -> None:
    if not _sorted_by_id(doc.comparisons):
        errors.append("Comparisons are sorted by id")
    for c in doc.comparisons:
        at = f"comparison {c.id}"
        if c.baseline.connection not in connections or c.target.connection not in connections:
            errors.append(f"Unknown comparison connection: {at}")
            continue
        if c.baseline.connection == c.target.connection and c.baseline.schema == c.target.schema:
            errors.append(f"A comparison compares two different scopes: {at}")
        expected = comparison_id(c.baseline.connection, c.baseline.schema, c.target.connection, c.target.schema)
        if c.id != expected:
            errors.append(f"Comparison id is not {expected}: {at}")

        if not all(_difference_before(a, b) for a, b in zip(c.differences, c.differences[1:])):
            errors.append(f"Differences are sorted by object type, key and change: {at}")
        tenant = ([c.baseline.schema, c.target.schema] if c.baseline.schema is not None and c.target.schema is not None
                  and c.baseline.schema != c.target.schema else [])
        counts = {"missing": 0, "extra": 0, "changed": 0}
        for d in c.differences:
            where = f"difference {d.id}"
            counts[d.change] += 1
            expected = difference_id(c.id, d.object_type, d.key)
            if d.id != expected:
                errors.append(f"Difference id is not {expected}: {where}")
            sides = {"missing": (True, False), "extra": (False, True), "changed": (True, True)}[d.change]
            if (d.baseline is not None, d.target is not None) != sides:
                has = ("both sides" if d.change == "changed" else "only a baseline" if d.change == "missing"
                       else "only a target")
                errors.append(f"A {d.change} difference has {has}: {where}")
            if (d.change == "changed") != (len(d.fields) > 0):
                errors.append(f"Only a changed difference lists fields, and it lists at least one: {where}")
            if not _is_sorted([f.field for f in d.fields]):
                errors.append(f"Difference fields are unique and sorted by field: {where}")
            if any(k in tenant for k in d.key):
                errors.append(f"A difference key never contains a compared schema name: {where}")
            for scope, ref in ((c.baseline, d.baseline), (c.target, d.target)):
                if ref is None:
                    continue
                connection = connections[scope.connection]
                if not _owned(tables, connection, scope.schema, ref.table):
                    errors.append(f"A difference side names a table of its own scope: {where}")
                    continue
                table = tables[ref.table]
                entities = {"index": table.indexes, "foreignKey": table.foreign_keys,
                            "constraint": table.constraints}.get(d.object_type)
                if (not any(e.id == ref.id for e in entities)) if entities is not None else ref.id is not None:
                    errors.append(f"A difference side's id is its {d.object_type}'s, of its table: {where}")
        total = c.summary.missing + c.summary.extra + c.summary.changed
        if c.omitted > 0 and c.complete:
            errors.append(f"A comparison with omitted differences is incomplete: {at}")
        if total != len(c.differences) + c.omitted or any(getattr(c.summary, k) < counts[k] for k in CHANGES):
            errors.append(f"Comparison summary does not count its differences and omitted ones: {at}")


def _difference_before(a: m.Difference, b: m.Difference) -> bool:
    """Differences are sorted by object type, then key element-wise, then change."""
    order = by_list([str(OBJECT_TYPES.index(a.object_type)).zfill(2), *a.key],
                    [str(OBJECT_TYPES.index(b.object_type)).zfill(2), *b.key])
    return order < 0 or (order == 0 and CHANGES.index(a.change) < CHANGES.index(b.change))


def _migration_rules(doc: m.Document, connections: dict[str, m.Connection], tables: dict[str, m.Table],
                     errors: list[str]) -> None:
    if not _sorted_by_id(doc.migrations):
        errors.append("Migration tracks are sorted by id")
    for track in doc.migrations:
        at = f"migration {track.id}"
        connection = connections.get(track.connection)
        if connection is None:
            errors.append(f"Unknown migration connection: {at}")
            continue
        expected = migration_track_id(track.provider, track.connection, track.schema)
        if track.id != expected:
            errors.append(f"Migration track id is not {expected}: {at}")

        def own(tid: str) -> bool:
            return _owned(tables, connection, track.schema, tid)

        if track.version_table is not None and not own(track.version_table):
            errors.append(f"The version table is a table of the track's scope: {at}")
        if track.version_table is None and (track.current is None or len(track.current) > 0):
            errors.append(f"Without a version table no revision is recorded (current is []): {at}")
        history = track.history
        if history is not None:
            expected_history = migration_history(history.revisions)
            if not _sorted_by_id(history.revisions) or any(
                    r.parents != e.parents or r.branch_labels != e.branch_labels
                    for r, e in zip(history.revisions, expected_history.revisions)):
                errors.append(f"Revisions are unique and sorted by id, with sorted parents and branch labels: {at}")
            if history.heads != expected_history.heads:
                errors.append(f"History heads are the revisions no revision names as a parent: {at}")
            if history.missing != expected_history.missing:
                errors.append(f"History missing lists the parents not in revisions: {at}")
        if track.current is not None and not _is_sorted(track.current):
            errors.append(f"Current revisions are unique and sorted: {at}")
        state = migration_state(history, track.current)
        if (track.status != state.status or track.pending != state.pending
                or track.unknown_revisions != state.unknown_revisions):
            errors.append(f"Migration status, pending and unknownRevisions do not follow the status rules "
                          f"(expected {state.status}): {at}")
        keys = [[r.name, r.table or ""] for r in track.coverage]
        if not all(by_list(a, b) < 0 for a, b in zip(keys, keys[1:])):
            errors.append(f"Coverage rows are unique and sorted by name and table: {at}")
        for row in track.coverage:
            if row.table is not None and (not own(row.table) or tables[row.table].name != row.name):
                errors.append(f"A coverage row's table is a table of the track's scope with its name: {at} ({row.name})")
            if "unknown" in (row.autogenerate, row.migration_managed) and row.certainty != "likely":
                errors.append(f"A coverage row with an unknown answer is likely, not certain: {at} ({row.name})")
            if not _is_sorted([f"{e.kind}\0{e.detail}" for e in row.evidence]):
                errors.append(f"Coverage evidence is unique and sorted by kind and detail: {at} ({row.name})")


def _inference_rules(doc: m.Document, tables: dict[str, m.Table], fks: dict[str, m.ForeignKey],
                     errors: list[str]) -> None:
    if not _sorted_by_id(doc.inferred_associations):
        errors.append("Inferred associations are sorted by id")
    link_by_id = {x.id: x for x in doc.links}
    table_of = {f.id: t.id for t in tables.values() for f in t.foreign_keys}
    models = {x.id: x for x in doc.models}

    def link_of(fk: m.ForeignKey) -> m.Link | None:
        pairs = sorted([c, fk.foreign_columns[i]] for i, c in enumerate(fk.columns))
        return link_by_id.get(link_id(table_of[fk.id], fk.foreign_table, pairs))

    for a in doc.inferred_associations:
        at = f"inferred association {a.id}"
        expected = inferred_association_id(a.kind, a.foreign_keys, a.via)
        if a.id != expected:
            errors.append(f"Inferred association id is not {expected}: {at}")
        association = a.kind == "association_table"
        if association != (a.via is not None) or association != (a.cardinality == "many-to-many") or \
                len(a.foreign_keys) != (2 if association else 1):
            errors.append("An association-table inference has via, two foreign keys and many-to-many; a foreign-key "
                          f"inference has none of them and one foreign key: {at}")
        if not all(t in tables for t in (a.table, a.related_table, *(() if a.via is None else (a.via,)))):
            errors.append(f"Unknown inferred association table: {at}")
        owner, related = models.get(a.model), models.get(a.related_model)
        if owner is None or a.table not in owner.tables or related is None or a.related_table not in related.tables:
            errors.append(f"An inferred association's models map its tables: {at}")
        if not _is_sorted(a.foreign_keys) or not _is_sorted(a.links) or not _is_sorted(a.ambiguities):
            errors.append(f"Inferred association foreign keys, links and ambiguities are unique and sorted: {at}")
        fk_links = []
        for fid in a.foreign_keys:
            fk = fks.get(fid)
            found = link_of(fk) if fk is not None else None
            if fk is None or found is None:
                errors.append(f"An inferred association's foreign key exists and has a link: {at}")
            elif found.relationships:
                errors.append(f"A declared relationship covers the inferred association's foreign key {fid}: {at}")
            else:
                fk_links.append(found.id)
        if list(a.links) != sorted_strings(fk_links) and len(fk_links) == len(a.foreign_keys):
            errors.append(f"An inferred association lists the links of its foreign keys: {at}")
        resolves = {
            "foreign_key": lambda i: i in a.foreign_keys,
            "primary_key": lambda i: i in tables,
            "unique_constraint": lambda i: any(c.id == i and c.kind == "unique"
                                               for t in tables.values() for c in t.constraints),
            "unique_index": lambda i: any(x.id == i and x.unique for t in tables.values() for x in t.indexes),
            "model_mapping": lambda i: i in models,
        }
        if not all(resolves[e.kind](e.id) for e in a.evidence) or not all(
                any(e.kind == "foreign_key" and e.id == fid for e in a.evidence) for fid in a.foreign_keys):
            errors.append(f"Inferred association evidence names existing entities, including each of its foreign "
                          f"keys: {at}")
        if not _is_sorted([f"{e.kind}\0{e.id}" for e in a.evidence]):
            errors.append(f"Inferred association evidence is unique and sorted by kind and id: {at}")


def _table_rules(table: m.Table, errors: list[str]) -> None:
    names = [c.name for c in table.columns]
    columns = set(names)
    errors += [f"Duplicate column {c}: {table.id}" for c in _duplicates(names)]
    if not set(table.primary_key) <= columns:
        errors.append(f"Unknown primary-key column: {table.id}")
    for index in table.indexes:
        at = f"index {index.id}"
        if any((p.column is None) == (p.expression is None) for p in index.parts):
            errors.append(f"An index part is exactly one of a column or an expression: {at}")
        part_columns = [p.column for p in index.parts if p.column is not None]
        if not set(part_columns + list(index.included)) <= columns:
            errors.append(f"Unknown index column: {at}")
        if any(p.expression is not None for p in index.parts) and not index.expression:
            errors.append(f"An index with expression parts has expression true: {at}")
        if not index.partial and index.predicate is not None:
            errors.append(f"Only a partial index has a predicate: {at}")
        backs = index.backs
        if backs is not None and backs.kind == "primary_key" and (
                not index.unique or index.expression or tuple(part_columns) != table.primary_key):
            errors.append(f"An index backing the primary key is unique and has exactly its columns: {at}")
        if backs is not None and backs.kind == "unique_constraint":
            backed = None if backs.constraint is None else next(
                (c for c in table.constraints if c.id == backs.constraint), None)
            if not index.unique or (backs.constraint is not None and (backed is None or backed.kind != "unique")):
                errors.append(f"An index backing a unique constraint is unique and names a unique constraint of its "
                              f"table: {at}")
    for c in table.constraints:
        if not set(c.columns) <= columns:
            errors.append(f"Unknown constraint column: constraint {c.id}")
        if c.kind == "unique" and not c.columns and c.expression is None:
            errors.append(f"A unique constraint has columns or an expression: constraint {c.id}")
