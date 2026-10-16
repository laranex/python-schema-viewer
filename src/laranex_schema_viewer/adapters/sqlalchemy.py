"""SQLAlchemy 2 public metadata/mapper APIs, shared by SQLModel."""

from __future__ import annotations

import importlib
import inspect as python_inspect
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import CheckConstraint, MetaData, Table, UniqueConstraint, inspect
from sqlalchemy.exc import NoReferencedColumnError, NoReferencedTableError
from sqlalchemy.orm import Mapper, registry

from ..document import (
    Column,
    Constraint,
    Default,
    Diagnostic,
    Endpoint,
    ForeignKey,
    Index,
    JoinPair,
    Model,
    Relationship,
    SchemaDocument,
    Source,
)
from ..document import Table as DocumentTable
from . import callable_name, class_source, identifier

__all__ = ["SQLAlchemyAdapter", "defines_models", "document_from_modules", "identifier"]


def sql(expression: Any) -> str:
    if hasattr(expression, "compile"):
        return str(expression.compile(compile_kwargs={"literal_binds": True}))
    return str(expression)


def describe_default(value: Any) -> Default | None:
    if value is None:
        return None
    if getattr(value, "is_callable", False):
        function = value.arg
        return Default(kind="callable", value=callable_name(function))
    if getattr(value, "is_sequence", False):
        return Default(kind="sequence", value=value.name)
    if getattr(value, "is_scalar", False):
        return Default(kind="scalar", value=str(value.arg))
    if hasattr(value, "arg"):
        return Default(kind="sql", value=sql(value.arg))
    if hasattr(value, "sqltext"):
        return Default(kind="generated", value=sql(value.sqltext))
    # FetchedValue / Identity have no scalar argument. Never use repr(), which
    # can embed object addresses or execute a user callable.
    return Default(kind="generated", value=type(value).__name__)


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
        self.diagnostics: list[Diagnostic] = []

    def table_id(self, table: Any) -> str:
        for namespace, metadata in self.metadata.items():
            if getattr(table, "metadata", None) is metadata:
                return identifier(namespace, table.schema, table.name)
        raise ValueError(f"Table outside selected metadata: {table}")

    def source(self, cls: type) -> Source | None:
        return class_source(cls, self.project_root)

    def extract(self) -> SchemaDocument:
        self.diagnostics = []
        mappers = {inspect(cls) for cls in self.models}
        # Include related and inherited models registered in selected metadata.
        for mapper in list(mappers):
            mappers.update(m for m in mapper.registry.mappers
                           if any(t.metadata in self.metadata.values() for t in m.tables))
        try:
            for registry in {m.registry for m in mappers}:
                registry.configure(cascade=True)
        except Exception as error:
            raise ValueError(f"ORM mapper configuration failed: {error}") from error

        def model_id(mapper: Any) -> str:
            cls = mapper.class_
            return identifier("model", cls.__module__, cls.__qualname__,
                              *sorted(self.table_id(t) for t in mapper.tables))

        tables = [self.extract_table(namespace, table)
                  for namespace, metadata in sorted(self.metadata.items())
                  for table in sorted(metadata.tables.values(), key=lambda t: t.key)]
        models = []
        relationships = []
        for mapper in sorted(mappers, key=model_id):
            cls = mapper.class_
            mid = model_id(mapper)
            models.append(Model(
                id=mid, class_name=f"{cls.__module__}.{cls.__qualname__}",
                table_ids=sorted(self.table_id(t) for t in mapper.tables),
                primary_key=[c.name for c in mapper.primary_key], source=self.source(cls),
            ))
            for relation in mapper.relationships:
                if relation.mapper not in mappers:
                    raise ValueError(f"Relationship target outside selected metadata: {mid}.{relation.key}")
                secondary = relation.secondary
                secondary_id = None
                if isinstance(secondary, Table):
                    secondary_id = self.table_id(secondary)
                elif secondary is not None:
                    self.diagnostics.append(Diagnostic(
                        kind="secondary_selectable", subject=f"{mid}.{relation.key}",
                        message="Secondary is a selectable; joins are preserved without a table identity.",
                    ))
                pairs = []
                for local, remote in relation.local_remote_pairs or []:
                    try:
                        pairs.append(JoinPair(
                            local=Endpoint(table=self.table_id(local.table), column=local.name),
                            remote=Endpoint(table=self.table_id(remote.table), column=remote.name),
                        ))
                    except (ValueError, AttributeError):
                        self.diagnostics.append(Diagnostic(
                            kind="unresolved_join_pair", subject=f"{mid}.{relation.key}",
                            message="Join includes an alias or expression; see primary_join/secondary_join.",
                        ))
                relationships.append(Relationship(
                    id=identifier("relationship", mid, relation.key), name=relation.key,
                    model=mid, target_model=model_id(relation.mapper),
                    source_table=self.table_id(mapper.local_table),
                    target_table=self.table_id(relation.mapper.local_table),
                    direction=relation.direction.name, uselist=bool(relation.uselist),
                    viewonly=relation.viewonly, back_populates=relation.back_populates,
                    secondary=secondary_id, primary_join=sql(relation.primaryjoin),
                    secondary_join=sql(relation.secondaryjoin) if relation.secondaryjoin is not None else None,
                    join_pairs=pairs,
                ))
        return SchemaDocument(
            tables=tables, models=models,
            relationships=sorted(relationships, key=lambda r: r.id),
            diagnostics=sorted(self.diagnostics, key=lambda d: (d.kind, d.subject, d.message)),
        )

    def extract_table(self, namespace: str, table: Table) -> DocumentTable:
        tid = self.table_id(table)
        foreign_keys = []
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            targets = [e.target_fullname for e in elements]
            try:
                target_table = self.table_id(elements[0].column.table)
                target_columns = [e.column.name for e in elements]
            except (NoReferencedTableError, NoReferencedColumnError) as error:
                target_name = targets[0].rsplit(".", 1)[0]
                target_schema, _, name = target_name.rpartition(".")
                target_table = identifier(namespace, target_schema or table.metadata.schema, name)
                target_columns = [t.rsplit(".", 1)[1] for t in targets]
                self.diagnostics.append(Diagnostic(
                    kind="unresolved_foreign_key", subject=tid, message=str(error),
                ))
            columns = [e.parent.name for e in elements]
            foreign_keys.append(ForeignKey(
                id=identifier("foreign_key", tid, constraint.name,
                              identifier(*columns), target_table, identifier(*target_columns)),
                name=constraint.name, columns=columns, target_table=target_table,
                target_columns=target_columns, on_delete=constraint.ondelete,
                on_update=constraint.onupdate, deferrable=constraint.deferrable,
                initially=constraint.initially,
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
            indexes.append(Index(
                name=index.name, columns=[c.name for c in index.columns],
                expressions=[sql(e) for e in index.expressions], unique=bool(index.unique),
                predicates=predicates, included_columns=included, methods=methods,
            ))
        constraints = []
        for constraint in table.constraints:
            if isinstance(constraint, (UniqueConstraint, CheckConstraint)):
                constraints.append(Constraint(
                    kind="unique" if isinstance(constraint, UniqueConstraint) else "check",
                    name=constraint.name, columns=[c.name for c in constraint.columns],
                    expression=sql(constraint.sqltext) if isinstance(constraint, CheckConstraint) else None,
                ))
        return DocumentTable(
            id=tid, namespace=namespace, name=table.name, schema_name=table.schema,
            comment=table.comment, primary_key=[c.name for c in table.primary_key],
            columns=[Column(
                name=c.name, type=str(c.type), timezone=getattr(c.type, "timezone", None),
                nullable=c.nullable, primary_key=c.primary_key,
                autoincrement=c.autoincrement, default=describe_default(c.default),
                server_default=describe_default(c.server_default), comment=c.comment,
            ) for c in table.columns],
            indexes=sorted(indexes, key=lambda i: i.model_dump_json()),
            foreign_keys=sorted(foreign_keys, key=lambda f: f.id),
            constraints=sorted(constraints, key=lambda c: c.model_dump_json()),
        )



def is_mapped_class(value: Any) -> bool:
    return python_inspect.isclass(value) and isinstance(inspect(value, raiseerr=False), Mapper)


def defines_models(loaded: dict[str, ModuleType]) -> bool:
    return any(isinstance(value, MetaData) or is_mapped_class(value)
               for module in loaded.values() for value in vars(module).values())


def document_from_modules(
    loaded: dict[str, ModuleType], *, project_root: Path, targets: list[str],
) -> SchemaDocument:
    """Collect metadata and mapped classes from imported modules and explicit targets."""
    metadata: dict[str, MetaData] = {}
    classes: set[type] = set()

    def add_metadata(value: MetaData, name: str) -> None:
        if not any(m is value for m in metadata.values()):
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
            classes.update(m.class_ for m in value.mappers)
        elif isinstance(value, MetaData):
            add_metadata(value, target)
        elif python_inspect.isclass(value) and isinstance(getattr(value, "metadata", None), MetaData):
            add_metadata(value.metadata, target)
            if hasattr(value, "registry"):
                classes.update(m.class_ for m in value.registry.mappers)
            if is_mapped_class(value):
                classes.add(value)
        else:
            raise ValueError(f"Unsupported metadata/base/registry target: {target}")
    if not metadata:
        raise ValueError("No SQLAlchemy metadata or mapped classes found")
    # Stable names independent of which module first imports a shared registry.
    named = {"default" if len(metadata) == 1 else name: value for name, value in metadata.items()}
    return SQLAlchemyAdapter(named, list(classes), project_root).extract()
