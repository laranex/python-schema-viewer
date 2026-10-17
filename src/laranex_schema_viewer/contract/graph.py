"""The framework-neutral graph builder (docs/contract.md, "Graph"), a port of laranex-schema-viewer-ui's graph.ts on
the schema model, and ``finalize``: the canonical arrays, ids and links every producer's document gets before it is
written. The shared conformance cases (tests/fixtures/contract/conformance/graph) keep the two implementations equal.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from .. import model as m
from .ids import derived_link_id, link_id


def in_scope(table: m.Table, connection: m.Connection, schema: str | None) -> bool:
    """Whether a table is in a scope: its schema, or unqualified while the connection's default is that schema."""
    if schema is None:
        return table.schema is None or table.schema == connection.default_schema
    return table.schema == schema or (table.schema is None and connection.default_schema == schema)


@dataclass
class _Link:
    id: str
    source: m.Endpoint
    target: m.Endpoint
    foreign_key: str | None = None
    relationships: list[str] = field(default_factory=list)
    polymorphic: bool = False
    notes: list[tuple[str, str]] = field(default_factory=list)


def build_graph(connections: Sequence[m.Connection], relationships: Iterable[m.Relationship]
                ) -> tuple[tuple[m.Link, ...], tuple[m.DerivedLink, ...]]:
    """Links (foreign keys within a connection and relationship keys) and derived links (through relationships)."""
    columns = {t.id: {c.name for c in t.columns} for c in connections for t in c.tables}
    connection_of = {t.id: c.id for c in connections for t in c.tables}

    def resolved(e: m.Endpoint) -> bool:
        return bool(e.columns) and all(c in columns.get(e.table, ()) for c in e.columns)

    links: dict[str, _Link] = {}

    def add(source: m.Endpoint, target: m.Endpoint, foreign_key: str | None = None,
            relationship: m.Relationship | None = None) -> None:
        if not resolved(source) or not resolved(target) or len(source.columns) != len(target.columns):
            return
        pairs = sorted([c, target.columns[i]] for i, c in enumerate(source.columns))
        lid = link_id(source.table, target.table, pairs)
        link = links.setdefault(lid, _Link(lid, source, target))
        if foreign_key is not None and (link.foreign_key is None or foreign_key < link.foreign_key):
            # Duplicate constraints on the same columns: the smallest id stands for them; the endpoints follow it.
            link.foreign_key, link.source, link.target = foreign_key, source, target
        if relationship is not None and relationship.id not in link.relationships:
            link.relationships.append(relationship.id)
            link.polymorphic = link.polymorphic or relationship.polymorphic is not None
            if relationship.note:
                link.notes.append((relationship.id, relationship.note))

    # Foreign keys first, so a link's endpoints follow the constraint's column order.
    for connection in connections:
        for table in connection.tables:
            for fk in sorted(table.foreign_keys, key=lambda f: f.id):
                if connection_of.get(fk.foreign_table) != connection.id:
                    continue
                add(m.Endpoint(table=table.id, columns=fk.columns),
                    m.Endpoint(table=fk.foreign_table, columns=fk.foreign_columns), foreign_key=fk.id)

    derived = []
    for r in relationships:
        if r.through:
            if r.related_table is not None and {r.table, r.related_table, *r.through} <= columns.keys():
                derived.append(m.DerivedLink(id=derived_link_id(r.id), source=r.table, target=r.related_table,
                                             via=r.through, relationship=r.id))
        elif r.pivot:
            p = r.pivot
            add(m.Endpoint(table=p.table, columns=p.parent_pivot_columns),
                m.Endpoint(table=r.table, columns=p.parent_columns), relationship=r)
            if r.related_table is not None:
                add(m.Endpoint(table=p.table, columns=p.related_pivot_columns),
                    m.Endpoint(table=r.related_table, columns=p.related_columns), relationship=r)
        elif r.keys:
            add(r.keys.source, r.keys.target, relationship=r)

    result = []
    for lid in sorted(links):
        link = links[lid]
        state = ("both" if link.relationships else "foreign_key") if link.foreign_key is not None else "relationship"
        result.append(m.Link(id=lid, source=link.source, target=link.target, foreign_key=link.foreign_key,
                             relationships=tuple(sorted(link.relationships)), state=state,
                             polymorphic=link.polymorphic, note=min(link.notes)[1] if link.notes else None))
    return tuple(result), tuple(sorted(derived, key=lambda d: d.id))


def diagnostic_order(d: m.Diagnostic) -> tuple[str, str, str, str, str]:
    return d.category, d.kind, d.subject.type, d.subject.id or "", d.message


def finalize(document: m.Document) -> m.Document:
    """The document in canonical form: tables, models and relationships sorted by id, connections by name, diagnostics
    by (category, kind, subject type, subject id, message) with equal ones (one id) listed once, and the links the
    shared graph rules build."""
    connections = tuple(sorted((replace(c, tables=tuple(sorted(c.tables, key=lambda t: t.id)))
                                for c in document.connections), key=lambda c: c.name))
    diagnostics: dict[str, m.Diagnostic] = {}
    for d in sorted(document.diagnostics, key=diagnostic_order):
        diagnostics.setdefault(d.id, d)
    relationships = tuple(sorted(document.relationships, key=lambda r: r.id))
    links, derived = build_graph(connections, relationships)
    return replace(document, connections=connections, models=tuple(sorted(document.models, key=lambda x: x.id)),
                   relationships=relationships, links=links, derived_links=derived,
                   diagnostics=tuple(diagnostics.values()))
