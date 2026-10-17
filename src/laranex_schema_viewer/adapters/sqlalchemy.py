"""SQLAlchemy 2 public metadata/mapper APIs, shared by SQLModel."""

from __future__ import annotations

import importlib
import inspect as python_inspect
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import CheckConstraint, MetaData, Table, UniqueConstraint, inspect
from sqlalchemy import Column as SAColumn
from sqlalchemy.exc import NoReferencedColumnError, NoReferencedTableError
from sqlalchemy.orm import Mapper, registry
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import UnaryExpression

from .. import model as m
from . import (ConstraintDeclaration, IndexDeclaration, callable_name, class_source, declared_document,
               declared_table, diagnostic, dialect_options, expression_part, foreign_key, identifier, relationship,
               sql_default)

__all__ = ["SQLAlchemyAdapter", "defines_models", "document_from_modules", "identifier"]

ORM = "sqlalchemy"
ORDERS = {operators.desc_op: ("order", "desc"), operators.asc_op: ("order", "asc"),
          operators.nulls_first_op: ("nulls", "first"), operators.nulls_last_op: ("nulls", "last")}


def sql(expression: Any) -> str:
    if hasattr(expression, "compile"):
        return str(expression.compile(compile_kwargs={"literal_binds": True}))
    return str(expression)


def orm_default(value: Any) -> m.OrmDefault | None:
    """``Column.default``: applied by SQLAlchemy, never called here."""
    if value is None:
        return None
    if getattr(value, "is_callable", False):
        return m.OrmDefault(kind="callable", value=callable_name(value.arg))
    if getattr(value, "is_sequence", False):
        return m.OrmDefault(kind="sequence", value=value.name)
    if getattr(value, "is_scalar", False):
        return m.OrmDefault(kind="scalar", value=str(value.arg))
    if hasattr(value, "arg"):
        return m.OrmDefault(kind="expression", value=sql(value.arg))
    return None


def server_default(value: Any) -> tuple[m.DefaultValue | None, m.Generated | None]:
    """``Column.server_default`` as (database default, generated column). Never invents SQL from a class name."""
    if value is None:
        return None, None
    if hasattr(value, "arg"):
        return sql_default(sql(value.arg)), None
    if hasattr(value, "sqltext"):
        return None, m.Generated(kind=None, expression=sql(value.sqltext))
    # Identity / FetchedValue: the database supplies the value. Never use repr(), which can embed object addresses
    # or execute a user callable.
    name = type(value).__name__
    return m.DefaultValue(kind="identity" if name == "Identity" else "raw", value=name), None


def index_part(expression: Any) -> m.IndexPart:
    """A key part: a column of the table (with its order), or SQL text."""
    element, flags = expression, {}
    while isinstance(element, UnaryExpression) and element.modifier in ORDERS:
        key, value = ORDERS[element.modifier]
        flags.setdefault(key, value)
        element = element.element
    if isinstance(element, SAColumn):
        return m.IndexPart(column=element.name, order=flags.get("order"), nulls=flags.get("nulls"))
    return expression_part(sql(expression))


class SQLAlchemyAdapter:
    def __init__(
        self,
        metadata: dict[str, MetaData],
        models: list[type] | None = None,
        project_root: Path | None = None,
    ) -> None:
        if not metadata or len({id(m) for m in metadata.values()}) != len(metadata):
            raise ValueError("Supply distinct metadata objects with unique namespace names")
        self.metadata = metadata
        self.models = models or []
        self.project_root = project_root
        self.diagnostics: list[m.Diagnostic] = []

    def table_id(self, table: Any) -> str:
        for namespace, metadata in self.metadata.items():
            if getattr(table, "metadata", None) is metadata:
                return identifier(namespace, table.schema, table.name)
        raise ValueError(f"Table outside selected metadata: {table}")

    def extract(self) -> m.Document:
        self.diagnostics = []
        mappers = {inspect(cls) for cls in self.models}
        # Include related and inherited models registered in selected metadata.
        for mapper in list(mappers):
            mappers.update(x for x in mapper.registry.mappers
                           if any(t.metadata in self.metadata.values() for t in x.tables))
        try:
            for found in {x.registry for x in mappers}:
                found.configure(cascade=True)
        except Exception as error:
            raise ValueError(f"ORM mapper configuration failed: {error}") from error

        def model_id(mapper: Any) -> str:
            cls = mapper.class_
            return identifier("model", cls.__module__, cls.__qualname__,
                              *sorted(self.table_id(t) for t in mapper.tables))

        tables = [(namespace, self.extract_table(namespace, table))
                  for namespace, metadata in sorted(self.metadata.items())
                  for table in sorted(metadata.tables.values(), key=lambda t: t.key)]
        models = []
        relationships = []
        for mapper in sorted(mappers, key=model_id):
            cls = mapper.class_
            mid = model_id(mapper)
            models.append(m.Model(
                id=mid, class_name=f"{cls.__module__}.{cls.__qualname__}",
                tables=tuple(sorted(self.table_id(t) for t in mapper.tables)),
                primary_key=tuple(c.name for c in mapper.primary_key),
                source=class_source(cls, self.project_root),
            ))
            for relation in mapper.relationships:
                if relation.mapper not in mappers:
                    raise ValueError(f"Relationship target outside selected metadata: {mid}.{relation.key}")
                rid = identifier("relationship", mid, relation.key)
                subject = m.Subject(type="relationship", id=rid, label=relation.key)
                secondary = relation.secondary
                secondary_id = None
                if isinstance(secondary, Table):
                    secondary_id = self.table_id(secondary)
                elif secondary is not None:
                    self.diagnostics.append(diagnostic(
                        "secondary_selectable", subject,
                        "Secondary is a selectable; joins are preserved without a table identity."))
                pairs = []
                for local, remote in relation.local_remote_pairs or []:
                    try:
                        pairs.append(((self.table_id(local.table), local.name),
                                      (self.table_id(remote.table), remote.name)))
                    except (ValueError, AttributeError):
                        self.diagnostics.append(diagnostic(
                            "unresolved_join_pair", subject,
                            "Join includes an alias or expression; see the relationship's join conditions."))
                relationships.append(relationship(
                    id=rid, name=relation.key, model=mid, related_model=model_id(relation.mapper),
                    table=self.table_id(mapper.local_table), related_table=self.table_id(relation.mapper.local_table),
                    direction=relation.direction.name, collection=bool(relation.uselist),
                    read_only=relation.viewonly, inverse=relation.back_populates, secondary=secondary_id,
                    primary_join=sql(relation.primaryjoin),
                    secondary_join=sql(relation.secondaryjoin) if relation.secondaryjoin is not None else None,
                    pairs=pairs,
                ))
        return declared_document(tables, orm=ORM, models=models, relationships=relationships,
                                 diagnostics=self.diagnostics)

    def extract_table(self, namespace: str, table: Table) -> m.Table:
        tid = self.table_id(table)
        foreign_keys = []
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            targets = [e.target_fullname for e in elements]
            columns = [e.parent.name for e in elements]
            unresolved = None
            try:
                target_table = self.table_id(elements[0].column.table)
                target_columns = [e.column.name for e in elements]
            except (NoReferencedTableError, NoReferencedColumnError) as error:
                target_name = targets[0].rsplit(".", 1)[0]
                target_schema, _, name = target_name.rpartition(".")
                target_table = identifier(namespace, target_schema or table.metadata.schema, name)
                target_columns = [t.rsplit(".", 1)[1] for t in targets]
                unresolved = str(error)
            fid = identifier("foreign_key", tid, constraint.name, identifier(*columns), target_table,
                             identifier(*target_columns))
            if unresolved is not None:
                self.diagnostics.append(diagnostic(
                    "unresolved_foreign_key",
                    m.Subject(type="foreignKey", id=fid, columns=tuple(columns), label=constraint.name), unresolved))
            foreign_keys.append(foreign_key(
                id=fid, name=constraint.name, columns=columns, target_table=target_table,
                target_columns=target_columns, orm=ORM, on_delete=constraint.ondelete, on_update=constraint.onupdate,
                deferrable=constraint.deferrable, initially=constraint.initially,
            ))
        indexes = []
        for index in table.indexes:
            predicates, included, methods = {}, {}, {}
            for dialect, options in index.dialect_options.items():
                if options.get("where") is not None:
                    predicates[dialect] = sql(options["where"])
                if options.get("include"):
                    included[dialect] = list(options["include"])
                if options.get("using"):
                    methods[dialect] = options["using"]
            indexes.append(IndexDeclaration(
                name=index.name, parts=tuple(index_part(e) for e in index.expressions), unique=bool(index.unique),
                dialect_options=dialect_options(predicates, included, methods)))
        constraints = []
        for constraint in table.constraints:
            if isinstance(constraint, (UniqueConstraint, CheckConstraint)):
                constraints.append(ConstraintDeclaration(
                    kind="unique" if isinstance(constraint, UniqueConstraint) else "check",
                    name=constraint.name, columns=tuple(c.name for c in constraint.columns),
                    expression=sql(constraint.sqltext) if isinstance(constraint, CheckConstraint) else None,
                ))
        columns = []
        for c in table.columns:
            default, generated = server_default(c.server_default)
            columns.append(m.Column(
                name=c.name, type=str(c.type), timezone=getattr(c.type, "timezone", None), nullable=bool(c.nullable),
                auto_increment=c.autoincrement if isinstance(c.autoincrement, bool) else None,
                default=default, generated=generated, orm_default=orm_default(c.default), comment=c.comment,
                provenance=m.DECLARED,
                extensions={ORM: {"autoincrement": "ignore_fk"}} if c.autoincrement == "ignore_fk" else None,
            ))
        return declared_table(
            id=tid, name=table.name, schema=table.schema, comment=table.comment,
            primary_key=[c.name for c in table.primary_key], columns=columns, indexes=indexes,
            foreign_keys=foreign_keys, constraints=constraints,
        )


def is_mapped_class(value: Any) -> bool:
    return python_inspect.isclass(value) and isinstance(inspect(value, raiseerr=False), Mapper)


def defines_models(loaded: dict[str, ModuleType]) -> bool:
    return any(isinstance(value, MetaData) or is_mapped_class(value)
               for module in loaded.values() for value in vars(module).values())


def document_from_modules(
    loaded: dict[str, ModuleType], *, project_root: Path, targets: list[str],
) -> m.Document:
    """Collect metadata and mapped classes from imported modules and explicit targets."""
    metadata: dict[str, MetaData] = {}
    classes: set[type] = set()

    def add_metadata(value: MetaData, name: str) -> None:
        if not any(x is value for x in metadata.values()):
            metadata[name] = value

    for name, module in sorted(loaded.items()):
        for attribute, value in sorted(vars(module).items()):
            if isinstance(value, MetaData):
                add_metadata(value, f"{name}:{attribute}")
            elif is_mapped_class(value):
                classes.add(value)
                for table in inspect(value).tables:
                    add_metadata(table.metadata, f"{name}:{attribute}")
    for target in targets:
        module_name, separator, attribute = target.partition(":")
        if not separator or not attribute:
            raise ValueError("Targets use module:attribute syntax")
        value = getattr(importlib.import_module(module_name), attribute)
        if isinstance(value, registry):
            add_metadata(value.metadata, target)
            classes.update(x.class_ for x in value.mappers)
        elif isinstance(value, MetaData):
            add_metadata(value, target)
        elif python_inspect.isclass(value) and isinstance(getattr(value, "metadata", None), MetaData):
            add_metadata(value.metadata, target)
            if hasattr(value, "registry"):
                classes.update(x.class_ for x in value.registry.mappers)
            if is_mapped_class(value):
                classes.add(value)
        else:
            raise ValueError(f"Unsupported metadata/base/registry target: {target}")
    if not metadata:
        raise ValueError("No SQLAlchemy metadata or mapped classes found")
    # Stable names independent of which module first imports a shared registry.
    named = {"default" if len(metadata) == 1 else name: value for name, value in metadata.items()}
    return SQLAlchemyAdapter(named, list(classes), project_root).extract()
