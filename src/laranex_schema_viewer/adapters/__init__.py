"""Adapters only run while building. Each ORM adapter module provides:

- ``defines_models(loaded)``: whether imported modules declare models for that ORM.
- ``document_from_modules(loaded, *, project_root, targets)``: the declared schema as a ``model.Document``.

The helpers here turn what an ORM declares into the schema model, the same way for every ORM. They must not import
any ORM, and they never write JSON: ``contract.serialize`` does.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .. import model as m
from ..contract import finalize

GENERATOR = "python-schema-viewer"
ACTIONS = {"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"}
LITERAL = re.compile(r"^(?:'(?:[^']|'')*'(?:::[\w ]+)?|-?\d+(?:\.\d+)?|true|false|null)$", re.IGNORECASE)
CARDINALITY = {("MANYTOONE", False): "many-to-one", ("MANYTOONE", True): "many-to-one",
               ("ONETOMANY", True): "one-to-many", ("ONETOMANY", False): "one-to-one",
               ("MANYTOMANY", True): "many-to-many", ("MANYTOMANY", False): "many-to-many"}
# Dialect names ORMs use for the contract's canonical ones.
DIALECTS = {"postgres": "postgresql", "pgsql": "postgresql", "mssql": "sqlsrv"}

# (table id, column) pairs an ORM joins on: (local, remote).
JoinPair = tuple[tuple[str, str], tuple[str, str]]


def identifier(*parts: str | None) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def connection_id(name: str) -> str:
    return f"connection:{name}"


def callable_name(value: object) -> str:
    """Name a callable default without calling it; builtins such as datetime.now have no module."""
    name = getattr(value, "__qualname__", type(value).__name__)
    module = getattr(value, "__module__", None)
    return f"{module}.{name}" if module else name


def class_source(cls: type, project_root: Path | None) -> m.Source | None:
    """Source location relative to the project; never publish paths outside it."""
    try:
        filename = inspect.getsourcefile(cls)
        if filename is None or project_root is None:
            return None
        path = Path(filename).resolve()
        if not path.is_relative_to(project_root):
            return None
        return m.Source(file=path.relative_to(project_root).as_posix(), line=inspect.getsourcelines(cls)[1])
    except (OSError, TypeError):
        return None


def sql_default(text: str) -> m.DefaultValue:
    """A database default as SQL text: a literal, or an expression the database evaluates."""
    return m.DefaultValue(kind="literal" if LITERAL.fullmatch(text.strip()) else "expression", value=text)


def python_default(value: Any) -> m.OrmDefault:
    """A default the ORM applies in Python: a callable (named, never called) or a scalar."""
    if callable(value):
        return m.OrmDefault(kind="callable", value=callable_name(value))
    return m.OrmDefault(kind="scalar", value=str(value))


def referential_action(value: str | None) -> tuple[str | None, str | None]:
    """A referential action as the contract spells it, or (None, original) when it is not an SQL action."""
    if value is None:
        return None, None
    normal = str(value).strip().upper().replace("_", " ")
    return (normal, None) if normal in ACTIONS else (None, str(value))


def foreign_key(*, id: str, name: str | None, columns: Sequence[str], target_table: str,
                target_columns: Sequence[str], orm: str, on_delete: str | None = None, on_update: str | None = None,
                deferrable: bool | None = None, initially: str | None = None) -> m.ForeignKey:
    """A declared foreign key. ORM-only actions (not SQL) are kept in the ORM's extension, never as SQL."""
    delete, raw_delete = referential_action(on_delete)
    update, raw_update = referential_action(on_update)
    kept = {k: v for k, v in (("onDelete", raw_delete), ("onUpdate", raw_update)) if v is not None}
    timing = initially.upper() if initially and initially.upper() in ("DEFERRED", "IMMEDIATE") else None
    return m.ForeignKey(id=id, name=name, columns=tuple(columns), foreign_table=target_table,
                        foreign_columns=tuple(target_columns), on_delete=delete, on_update=update,
                        deferrable=deferrable, initially=timing, provenance=m.DECLARED,
                        extensions={orm: kept} if kept else None)


def column_part(column: str, order: str | None = None) -> m.IndexPart:
    return m.IndexPart(column=column, order=order.lower() if order else None)


def expression_part(expression: str) -> m.IndexPart:
    return m.IndexPart(expression=expression)


def dialect_options(predicates: Mapping[str, str] | None = None, included: Mapping[str, Sequence[str]] | None = None,
                    methods: Mapping[str, str] | None = None) -> dict[str, m.IndexOptions] | None:
    """Index options an ORM declares for specific dialects, keyed by the contract's dialect names."""
    predicates, included, methods = predicates or {}, included or {}, methods or {}
    options = {}
    for dialect in sorted({*predicates, *included, *methods}):
        options[DIALECTS.get(dialect, dialect)] = m.IndexOptions(
            method=methods.get(dialect), predicate=predicates.get(dialect),
            included=tuple(included[dialect]) if dialect in included else None)
    return options or None


@dataclass(frozen=True)
class IndexDeclaration:
    """An index as an ORM declares it, before it gets its id from its name or its place in the table."""

    name: str | None
    parts: tuple[m.IndexPart, ...]
    unique: bool
    dialect_options: Mapping[str, m.IndexOptions] | None = None

    def order(self) -> tuple[Any, ...]:
        return (self.name is None, self.name or "", [(p.column or "", p.expression or "", p.order or "")
                                                      for p in self.parts], self.unique)


@dataclass(frozen=True)
class ConstraintDeclaration:
    kind: str
    name: str | None
    columns: tuple[str, ...] = ()
    expression: str | None = None

    def order(self) -> tuple[Any, ...]:
        return self.kind, self.name is None, self.name or "", self.columns, self.expression or ""


def declared_table(*, id: str, name: str, schema: str | None, primary_key: Sequence[str],
                   columns: Sequence[m.Column], comment: str | None = None,
                   indexes: Iterable[IndexDeclaration] = (), foreign_keys: Iterable[m.ForeignKey] = (),
                   constraints: Iterable[ConstraintDeclaration] = ()) -> m.Table:
    """A declared table. Unnamed indexes and constraints are identified by their place in a deterministic order."""
    return m.Table(
        id=id, name=name, schema=schema, comment=comment, primary_key=tuple(primary_key), columns=tuple(columns),
        indexes=tuple(m.Index(id=f"{id}:index:{i.name if i.name is not None else n}", name=i.name, parts=i.parts,
                              expression=any(p.expression is not None for p in i.parts), unique=i.unique,
                              dialect_options=i.dialect_options, provenance=m.DECLARED)
                      for n, i in enumerate(sorted(indexes, key=IndexDeclaration.order))),
        foreign_keys=tuple(sorted(foreign_keys, key=lambda f: f.id)),
        constraints=tuple(m.Constraint(id=f"{id}:constraint:{c.name if c.name is not None else n}", kind=c.kind,
                                       name=c.name, columns=c.columns, expression=c.expression,
                                       provenance=m.DECLARED)
                          for n, c in enumerate(sorted(constraints, key=ConstraintDeclaration.order))),
        provenance=m.DECLARED,
    )


def relationship(*, id: str, name: str, model: str, related_model: str, table: str, related_table: str,
                 direction: str, collection: bool, pairs: Sequence[JoinPair], primary_join: str | None,
                 secondary_join: str | None = None, secondary: str | None = None, read_only: bool = False,
                 inverse: str | None = None) -> m.Relationship:
    """An ORM relationship from the column pairs it joins on. ``direction`` is MANYTOONE, ONETOMANY or MANYTOMANY;
    ``inverse`` names the relationship on the related model that mirrors this one, if any.

    Keys run like a foreign key: from the referencing columns. A secondary (association) table becomes a pivot whose
    parent pairs are the ones the primary join states."""
    keys = pivot = None
    if secondary is None and pairs:
        (local_table, _), (remote_table, _) = pairs[0]
        if all(a[0] == local_table and b[0] == remote_table for a, b in pairs):
            local = m.Endpoint(table=local_table, columns=tuple(a[1] for a, _ in pairs))
            remote = m.Endpoint(table=remote_table, columns=tuple(b[1] for _, b in pairs))
            keys = (m.Keys(source=local, target=remote) if direction == "MANYTOONE"
                    else m.Keys(source=remote, target=local))
    elif secondary is not None:
        parent = [(a, b) for a, b in pairs if b[0] == secondary and a[0] == table and b[1] in (primary_join or "")]
        related = [(a, b) for a, b in pairs if b[0] == secondary and (a, b) not in parent]
        pivot = m.Pivot(table=secondary, parent_columns=tuple(a[1] for a, _ in parent),
                        parent_pivot_columns=tuple(b[1] for _, b in parent),
                        related_columns=tuple(a[1] for a, _ in related),
                        related_pivot_columns=tuple(b[1] for _, b in related))
    return m.Relationship(
        id=id, name=name, model=model, table=table, related_model=related_model, related_table=related_table,
        type=direction, cardinality=CARDINALITY[(direction, collection)], collection=collection,
        read_only=read_only, keys=keys, pivot=pivot,
        join=m.Join(primary=primary_join or None, secondary=secondary_join),
        inverse=identifier("relationship", related_model, inverse) if inverse else None,
    )


def diagnostic(kind: str, subject: m.Subject, message: str) -> m.Diagnostic:
    """What extraction could not represent."""
    return m.Diagnostic(kind=kind, category="extraction", severity="warning", certainty="certain", subject=subject,
                        message=message)


def table_subject(table_id: str) -> m.Subject:
    return m.Subject(type="table", id=table_id)


def declared_coverage() -> m.Coverage:
    """ORM metadata extraction reads every feature from the declarations."""
    return m.coverage({f: "complete" for f in m.FEATURES})


def declared_document(tables: Iterable[tuple[str, m.Table]], *, orm: str, models: Iterable[m.Model] = (),
                      relationships: Iterable[m.Relationship] = (),
                      diagnostics: Iterable[m.Diagnostic] = ()) -> m.Document:
    """A declared document: one connection per namespace (``tables`` are (namespace, table) pairs), with inverses
    that name no extracted relationship dropped, in canonical form."""
    grouped: dict[str, list[m.Table]] = {}
    for namespace, table in tables:
        grouped.setdefault(namespace, []).append(table)
    relationships = list(relationships)
    known = {r.id for r in relationships}
    return finalize(m.Document(
        source="declared", generator=m.Generator(name=GENERATOR, orm=orm),
        connections=tuple(m.Connection(id=connection_id(name), name=name, tables=tuple(found),
                                       coverage=m.ConnectionCoverage(declared=declared_coverage()))
                          for name, found in grouped.items()),
        models=tuple(models),
        relationships=tuple(r if r.inverse is None or r.inverse in known else replace(r, inverse=None)
                            for r in relationships),
        diagnostics=tuple(diagnostics),
    ))
