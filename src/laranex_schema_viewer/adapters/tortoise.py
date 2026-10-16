"""Tortoise ORM metadata through ``Model._meta`` after ``Tortoise.init_models``.

``init_models`` resolves relations without opening a connection; ``Tortoise.init``,
which connects, is never called. Column types come from Tortoise's field SQL types for
the configured dialect, or the generic types when no Tortoise config is supplied.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from tortoise import Tortoise
from tortoise.models import Model as TortoiseModel

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
)
from ..document import Table as DocumentTable
from . import class_source, identifier

DIALECTS = {"postgres": "postgres", "postgresql": "postgres", "asyncpg": "postgres",
            "psycopg": "postgres", "sqlite": "sqlite", "mysql": "mysql", "mssql": "mssql",
            "oracle": "oracle"}


def is_model(value: Any) -> bool:
    return (isinstance(value, type) and issubclass(value, TortoiseModel)
            and value is not TortoiseModel and not value._meta.abstract)


def defines_models(loaded: dict[str, ModuleType]) -> bool:
    return any(is_model(value) for module in loaded.values() for value in vars(module).values())


def related_labels(loaded: dict[str, ModuleType]) -> set[str]:
    """App labels named in "label.Model" relation references, read before initialisation."""
    labels = set()
    for module in loaded.values():
        for value in vars(module).values():
            if is_model(value):
                for field in value._meta.fields_map.values():
                    reference = getattr(field, "model_name", None)
                    if isinstance(reference, str) and "." in reference:
                        labels.add(reference.split(".", 1)[0])
    return labels


def dialect_from_config(config: dict) -> str | None:
    """Read only the URL scheme or engine name of the default connection."""
    connections = config.get("connections", {})
    connection = connections.get("default", next(iter(connections.values()), None))
    if isinstance(connection, str):
        scheme = connection.split(":", 1)[0]
    elif isinstance(connection, dict):
        scheme = str(connection.get("engine", "")).rsplit(".", 1)[-1]
    else:
        return None
    return DIALECTS.get(scheme)


def callable_name(value: Any) -> str:
    return f"{getattr(value, '__module__', '')}.{getattr(value, '__qualname__', type(value).__name__)}"


class TortoiseAdapter:
    def __init__(self, models: list[type], *, dialect: str | None = None,
                 project_root: Path | None = None) -> None:
        self.models = models
        self.dialect = dialect
        self.project_root = project_root
        self.diagnostics: list[Diagnostic] = []

    def table_id(self, model: type, table: str | None = None) -> str:
        meta = model._meta
        return identifier(meta.app, meta.schema, table or meta.db_table)

    def model_id(self, model: type) -> str:
        return identifier("model", model.__module__, model.__qualname__, self.table_id(model))

    def sql_type(self, field: Any) -> str:
        sql_type = field.get_for_dialect(self.dialect or "", "SQL_TYPE")
        return sql_type or type(field).__name__

    def column(self, model: type, name: str) -> str:
        return model._meta.fields_db_projection.get(name, name)

    def target_column(self, field: Any) -> str:
        target = field.related_model
        if field.to_field:
            return self.column(target, field.to_field)
        return target._meta.db_pk_column

    def extract(self) -> SchemaDocument:
        self.diagnostics = []
        model_tables = {(m._meta.app, m._meta.schema, m._meta.db_table) for m in self.models}
        tables, models, relationships = [], [], []
        for model in sorted(self.models, key=self.model_id):
            meta = model._meta
            tables.append(self.extract_table(model))
            for name in sorted(meta.m2m_fields):
                field = meta.fields_map[name]
                if not field._generated and (meta.app, meta.schema, field.through) not in model_tables:
                    tables.append(self.through_table(model, field))
            models.append(Model(
                id=self.model_id(model), class_name=f"{model.__module__}.{model.__qualname__}",
                table_ids=[self.table_id(model)], primary_key=[meta.db_pk_column],
                source=class_source(model, self.project_root),
            ))
            relationships.extend(self.relationships(model))
        return SchemaDocument(
            tables=sorted(tables, key=lambda t: t.id), models=models,
            relationships=sorted(relationships, key=lambda r: r.id),
            diagnostics=sorted(self.diagnostics, key=lambda d: (d.kind, d.subject, d.message)),
        )

    def extract_table(self, model: type) -> DocumentTable:
        meta = model._meta
        tid = self.table_id(model)
        columns, indexes, constraints, foreign_keys = [], [], [], []
        for name, column in meta.fields_db_projection.items():
            field = meta.fields_map[name]
            default = None
            if field.default is not None:
                default = (Default(kind="callable", value=callable_name(field.default))
                           if callable(field.default) else Default(kind="scalar", value=str(field.default)))
            db_default = getattr(field, "db_default", None)
            columns.append(Column(
                name=column, type=self.sql_type(field), nullable=field.null, primary_key=field.pk,
                autoincrement=bool(field.generated), default=default,
                server_default=Default(kind="sql", value=str(db_default)) if db_default is not None else None,
                comment=field.description,
            ))
            if field.unique and not field.pk:
                constraints.append(Constraint(kind="unique", name=None, columns=[column]))
            elif field.index and not field.pk:
                indexes.append(Index(name=None, columns=[column], expressions=[f"{meta.db_table}.{column}"],
                                     unique=False))
        for names in meta.unique_together:
            constraints.append(Constraint(kind="unique", name=None,
                                          columns=[self.column(model, n) for n in names]))
        for index in meta.indexes:
            indexes.append(self.index(model, index))
        for constraint in getattr(meta, "constraints", ()):
            kind = type(constraint).__name__
            if kind == "UniqueConstraint" and getattr(constraint, "condition", None) is None:
                constraints.append(Constraint(kind="unique", name=constraint.name,
                                              columns=[self.column(model, n) for n in constraint.fields]))
            elif kind == "UniqueConstraint":
                cols = [self.column(model, n) for n in constraint.fields]
                indexes.append(Index(
                    name=constraint.name, columns=cols, unique=True,
                    expressions=[f"{meta.db_table}.{c}" for c in cols],
                    predicates={self.dialect or "tortoise": constraint.condition},
                ))
            elif kind == "CheckConstraint":
                constraints.append(Constraint(kind="check", name=constraint.name, expression=constraint.check))
            else:
                self.diagnostics.append(Diagnostic(
                    kind="unsupported_constraint", subject=tid,
                    message=f"{kind} {getattr(constraint, 'name', '')} is not represented",
                ))
        for name in sorted(meta.fk_fields | meta.o2o_fields):
            field = meta.fields_map[name]
            if not field.db_constraint:
                continue
            target = self.table_id(field.related_model)
            source, target_column = field.source_field, self.target_column(field)
            foreign_keys.append(ForeignKey(
                id=identifier("foreign_key", tid, None, identifier(source), target, identifier(target_column)),
                name=None, columns=[source], target_table=target, target_columns=[target_column],
                on_delete=str(field.on_delete),
            ))
        return DocumentTable(
            id=tid, namespace=meta.app, name=meta.db_table, schema_name=meta.schema,
            comment=meta.table_description or None, columns=columns, primary_key=[meta.db_pk_column],
            indexes=sorted(indexes, key=lambda i: i.model_dump_json()),
            foreign_keys=sorted(foreign_keys, key=lambda f: f.id),
            constraints=sorted(constraints, key=lambda c: c.model_dump_json()),
        )

    def index(self, model: type, index: Any) -> Index:
        meta = model._meta
        description = index.describe()
        columns = [self.column(model, name) for name in description.get("fields") or []]
        extra = (description.get("extra") or "").strip()
        index_type = (description.get("type") or "").strip()
        key = self.dialect or "tortoise"
        return Index(
            name=description.get("name"), columns=columns, unique=False,
            expressions=[f"{meta.db_table}.{c}" for c in columns] + list(description.get("expressions") or []),
            predicates={key: extra.removeprefix("WHERE").strip()} if extra.startswith("WHERE") else {},
            methods={key: index_type.lower()} if index_type else {},
        )

    def through_table(self, model: type, field: Any) -> DocumentTable:
        """The join table Tortoise generates for a ManyToManyField without a through model."""
        meta, target = model._meta, field.related_model
        tid = self.table_id(model, field.through)
        source_type = self.sql_type(meta.fields_map[meta.pk_attr])
        target_type = self.sql_type(target._meta.fields_map[target._meta.pk_attr])
        foreign_keys = []
        if field.db_constraint:
            for column, other, other_column in [(field.backward_key, model, meta.db_pk_column),
                                                (field.forward_key, target, target._meta.db_pk_column)]:
                other_id = self.table_id(other)
                foreign_keys.append(ForeignKey(
                    id=identifier("foreign_key", tid, None, identifier(column), other_id, identifier(other_column)),
                    name=None, columns=[column], target_table=other_id, target_columns=[other_column],
                    on_delete=str(field.on_delete),
                ))
        keys = [field.backward_key, field.forward_key]
        return DocumentTable(
            id=tid, namespace=meta.app, name=field.through, schema_name=meta.schema,
            comment=field.description or None, primary_key=[],
            columns=[Column(name=field.backward_key, type=source_type, nullable=False),
                     Column(name=field.forward_key, type=target_type, nullable=False)],
            indexes=[Index(name=None, columns=keys, unique=True,
                           expressions=[f"{field.through}.{k}" for k in keys])] if field.unique else [],
            foreign_keys=sorted(foreign_keys, key=lambda f: f.id),
        )

    def through_id(self, owner: type, name: str) -> str:
        """An explicit through model's table, else the table Tortoise generates."""
        for model in self.models:
            if model._meta.db_table == name and model._meta.app == owner._meta.app:
                return self.table_id(model)
        return self.table_id(owner, name)

    def relationships(self, model: type) -> list[Relationship]:
        meta = model._meta
        mid = self.model_id(model)
        result = []

        def relation(name: str, target: type, direction: str, uselist: bool, back: str | None,
                     pairs: list[tuple[str, str, str, str]], secondary: str | None = None) -> None:
            def text(table_id: str, column: str) -> str:
                return f"{json.loads(table_id)[-1]}.{column}"

            joins = [f"{text(a, ac)} = {text(b, bc)}" for a, ac, b, bc in pairs]
            result.append(Relationship(
                id=identifier("relationship", mid, name), name=name, model=mid,
                target_model=self.model_id(target), source_table=self.table_id(model),
                target_table=self.table_id(target), direction=direction, uselist=uselist,
                viewonly=False, back_populates=back, secondary=secondary,
                primary_join=joins[0], secondary_join=joins[1] if len(joins) > 1 else None,
                join_pairs=[JoinPair(local=Endpoint(table=a, column=ac), remote=Endpoint(table=b, column=bc))
                            for a, ac, b, bc in pairs],
            ))

        own = self.table_id(model)
        for name in sorted(meta.fk_fields | meta.o2o_fields):
            field = meta.fields_map[name]
            target = field.related_model
            relation(name, target, "MANYTOONE", False, field.related_name or None,
                     [(own, field.source_field, self.table_id(target), self.target_column(field))])
        for name in sorted(meta.backward_fk_fields | meta.backward_o2o_fields):
            field = meta.fields_map[name]
            source = field.related_model
            forward = next((n for n in sorted(source._meta.fk_fields | source._meta.o2o_fields)
                            if source._meta.fields_map[n].related_name == name
                            and source._meta.fields_map[n].related_model is model), None)
            local = (self.target_column(source._meta.fields_map[forward]) if forward
                     else meta.db_pk_column)
            relation(name, source, "ONETOMANY", name in meta.backward_fk_fields, forward,
                     [(own, local, self.table_id(source), field.relation_source_field)])
        for name in sorted(meta.m2m_fields):
            field = meta.fields_map[name]
            target = field.related_model
            through = self.through_id(target if field._generated else model, field.through)
            relation(name, target, "MANYTOMANY", True, field.related_name or None,
                     [(own, meta.db_pk_column, through, field.backward_key),
                      (self.table_id(target), target._meta.db_pk_column, through, field.forward_key)],
                     secondary=through)
        return result


def document_from_modules(
    loaded: dict[str, ModuleType], *, project_root: Path, targets: list[str],
) -> SchemaDocument:
    """Initialise models from modules (one app label) or from a TORTOISE_ORM config target."""
    dialect = None
    if targets:
        if len(targets) != 1:
            raise ValueError("Tortoise accepts one --target pointing at a TORTOISE_ORM config dict")
        module_name, _, attribute = targets[0].partition(":")
        config = getattr(importlib.import_module(module_name), attribute or "TORTOISE_ORM")
        if not isinstance(config, dict) or not isinstance(config.get("apps"), dict):
            raise ValueError(f"{targets[0]} is not a Tortoise config with an 'apps' mapping")
        dialect = dialect_from_config(config)
        apps = {label: list(app["models"]) for label, app in config["apps"].items()}
    else:
        labels = related_labels(loaded)
        if len(labels) > 1:
            raise ValueError(f"Models reference several Tortoise apps ({', '.join(sorted(labels))}); "
                             "pass --target module:TORTOISE_ORM")
        modules = [name for name, module in loaded.items()
                   if any(is_model(v) and v.__module__ == name for v in vars(module).values())]
        if not modules:
            raise ValueError("No Tortoise models found in the selected modules")
        apps = {labels.pop() if labels else "models": modules}
    labels = list(apps)
    for index, label in enumerate(labels):
        Tortoise.init_models(apps[label], label, _init_relations=index == len(labels) - 1)
    models = [model for label in labels for model in Tortoise.apps[label].values()]
    return TortoiseAdapter(models, dialect=dialect, project_root=project_root).extract()
