"""Peewee model metadata through the ``Model._meta`` API.

Types and SQL text are rendered with the model database's SQL context, which never
connects; models without a usable database fall back to Peewee's generic field types.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType
from typing import Any

import peewee
from peewee import SCOPE_VALUES, SQL, AutoField, CompositeKey, Field, ForeignKeyField, Ordering

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
from . import callable_name, class_source, identifier

NAMESPACE = "default"
DIALECTS = [(peewee.PostgresqlDatabase, "postgresql"), (peewee.MySQLDatabase, "mysql"),
            (peewee.SqliteDatabase, "sqlite")]
TABLE_CONSTRAINT = re.compile(
    r'^\s*(?:CONSTRAINT\s+"?(?P<name>[^"\s]+)"?\s+)?(?P<kind>CHECK|UNIQUE)\s*\((?P<body>.*)\)\s*$',
    re.IGNORECASE | re.DOTALL,
)


def is_model(value: Any) -> bool:
    return isinstance(value, type) and issubclass(value, peewee.Model) and value is not peewee.Model


def defines_models(loaded: dict[str, ModuleType]) -> bool:
    return any(is_model(value) for module in loaded.values() for value in vars(module).values())


def is_generated_through(model: type) -> bool:
    """ManyToManyField creates its through model inside peewee unless one is supplied."""
    return model.__module__ == peewee.__name__


def literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


class PeeweeAdapter:
    def __init__(self, models: list[type], project_root: Path | None = None) -> None:
        self.models = models
        self.project_root = project_root
        self.diagnostics: list[Diagnostic] = []

    def table_id(self, model: type) -> str:
        return identifier(NAMESPACE, model._meta.schema, model._meta.table_name)

    def model_id(self, model: type) -> str:
        return identifier("model", model.__module__, model.__qualname__, self.table_id(model))

    def database(self, model: type) -> Any:
        database = model._meta.database
        try:
            database.get_sql_context()
            return database
        except Exception:
            return None

    def dialect(self, model: type) -> str:
        database = self.database(model)
        return next((name for kind, name in DIALECTS if isinstance(database, kind)), "peewee")

    def render(self, model: type, node: Any) -> str:
        database = self.database(model)
        if database is None:
            self.diagnostics.append(Diagnostic(
                kind="unrendered_expression", subject=self.table_id(model),
                message="Model has no usable database; kept Peewee's expression text",
            ))
            return str(getattr(node, "sql", node))
        context = database.get_sql_context()
        with context(scope=SCOPE_VALUES):
            sql, params = context.sql(node).query()
        for value in params:
            sql = sql.replace(database.param, literal(value), 1)
        return sql

    def sql_type(self, field: Any) -> str:
        database = self.database(field.model)
        if database is not None:
            context = database.get_sql_context()
            return context.sql(field.ddl_datatype(context)).query()[0]
        generic = {"AUTO": "INTEGER", "BIGAUTO": "BIGINT"}.get(field.field_type, field.field_type)
        modifiers = field.get_modifiers()
        return f"{generic}({', '.join(map(str, modifiers))})" if modifiers else generic

    def primary_key(self, model: type) -> list[str]:
        meta = model._meta
        if isinstance(meta.primary_key, CompositeKey):
            return [meta.fields[name].column_name for name in meta.primary_key.field_names]
        return [meta.primary_key.column_name] if meta.primary_key else []

    def extract(self) -> SchemaDocument:
        self.diagnostics = []
        selected = {id(m) for m in self.models}
        tables, models, relationships = [], [], []
        for model in sorted(self.models, key=self.model_id):
            tables.append(self.extract_table(model))
            if is_generated_through(model):
                continue
            mid = self.model_id(model)
            models.append(Model(
                id=mid, class_name=f"{model.__module__}.{model.__qualname__}",
                table_ids=[self.table_id(model)], primary_key=self.primary_key(model),
                source=class_source(model, self.project_root),
            ))
            relationships.extend(self.relationships(model, selected))
        return SchemaDocument(
            tables=tables, models=models,
            relationships=sorted(relationships, key=lambda r: r.id),
            diagnostics=sorted(self.diagnostics, key=lambda d: (d.kind, d.subject, d.message)),
        )

    def extract_table(self, model: type) -> DocumentTable:
        meta = model._meta
        tid = self.table_id(model)
        primary_key = self.primary_key(model)
        columns, constraints, foreign_keys = [], [], []
        for field in meta.sorted_fields:
            default = server_default = None
            if field.default is not None:
                default = (Default(kind="callable", value=callable_name(field.default))
                           if callable(field.default) else Default(kind="scalar", value=str(field.default)))
            for node in field.constraints or []:
                text = self.render(model, node)
                if text.upper().startswith("DEFAULT "):
                    server_default = Default(kind="sql", value=text[len("DEFAULT "):].strip())
                else:
                    constraints.extend(self.table_constraint(model, text, column=field.column_name))
            columns.append(Column(
                name=field.column_name, type=self.sql_type(field), nullable=field.null,
                primary_key=field.column_name in primary_key, autoincrement=isinstance(field, AutoField),
                default=default, server_default=server_default,
            ))
            if isinstance(field, ForeignKeyField):
                foreign_keys.append(self.foreign_key(model, field))
        for node in meta.constraints or []:
            constraints.extend(self.table_constraint(model, self.render(model, node)))
        indexes = [index for item in meta.fields_to_index()
                   if (index := self.index(model, item)) is not None]
        return DocumentTable(
            id=tid, namespace=NAMESPACE, name=meta.table_name, schema_name=meta.schema,
            columns=columns, primary_key=primary_key,
            indexes=sorted(indexes, key=lambda i: i.model_dump_json()),
            foreign_keys=sorted(foreign_keys, key=lambda f: f.id),
            constraints=sorted(constraints, key=lambda c: c.model_dump_json()),
        )

    def table_constraint(self, model: type, text: str, *, column: str | None = None) -> list[Constraint]:
        found = TABLE_CONSTRAINT.match(text)
        if found is None:
            self.diagnostics.append(Diagnostic(
                kind="unsupported_constraint", subject=self.table_id(model),
                message=f"Raw SQL constraint is not represented: {text}",
            ))
            return []
        if found["kind"].upper() == "CHECK":
            return [Constraint(kind="check", name=found["name"], columns=[column] if column else [],
                               expression=found["body"].strip())]
        names = [name.strip().strip('"`') for name in found["body"].split(",")]
        return [Constraint(kind="unique", name=found["name"], columns=names)]

    def foreign_key(self, model: type, field: Any) -> ForeignKey:
        tid = self.table_id(model)
        target = self.table_id(field.rel_model)
        target_column = field.rel_field.column_name
        deferrable = initially = None
        if field.deferrable:
            setting = field.deferrable.upper()
            deferrable = "NOT DEFERRABLE" not in setting
            found = re.search(r"INITIALLY\s+(\w+)", setting)
            initially = found.group(1) if found else None
        return ForeignKey(
            id=identifier("foreign_key", tid, field.constraint_name, identifier(field.column_name),
                          target, identifier(target_column)),
            name=field.constraint_name, columns=[field.column_name], target_table=target,
            target_columns=[target_column], on_delete=field.on_delete, on_update=field.on_update,
            deferrable=deferrable, initially=initially,
        )

    def index(self, model: type, item: Any) -> Index | None:
        table = model._meta.table_name
        if isinstance(item, SQL):
            self.diagnostics.append(Diagnostic(
                kind="unsupported_index", subject=self.table_id(model),
                message=f"Raw SQL index statement is not represented: {item.sql}",
            ))
            return None
        columns, expressions = [], []
        for node in item._expressions:
            inner = node.node if isinstance(node, Ordering) else node
            if isinstance(inner, Field):
                columns.append(inner.column_name)
                suffix = f" {node.direction}" if isinstance(node, Ordering) else ""
                expressions.append(f"{table}.{inner.column_name}{suffix}")
            else:
                expressions.append(self.render(model, node))
        dialect = self.dialect(model)
        return Index(
            name=item._name, columns=columns, expressions=expressions, unique=bool(item._unique),
            predicates={dialect: self.render(model, item._where)} if item._where is not None else {},
            methods={dialect: item._using} if item._using else {},
        )

    def relationships(self, model: type, selected: set[int]) -> list[Relationship]:
        meta = model._meta
        mid = self.model_id(model)
        own = self.table_id(model)
        result = []

        def relation(name: str, target: type, direction: str, uselist: bool, back: str | None,
                     pairs: list[tuple[type, str, type, str]], secondary: type | None = None) -> None:
            joins = [f"{a._meta.table_name}.{ac} = {b._meta.table_name}.{bc}" for a, ac, b, bc in pairs]
            result.append(Relationship(
                id=identifier("relationship", mid, name), name=name, model=mid,
                target_model=self.model_id(target), source_table=own,
                target_table=self.table_id(target), direction=direction, uselist=uselist,
                viewonly=False, back_populates=back,
                secondary=self.table_id(secondary) if secondary is not None else None,
                primary_join=joins[0], secondary_join=joins[1] if len(joins) > 1 else None,
                join_pairs=[JoinPair(local=Endpoint(table=self.table_id(a), column=ac),
                                     remote=Endpoint(table=self.table_id(b), column=bc))
                            for a, ac, b, bc in pairs],
            ))

        def visible(backref: str | None) -> str | None:
            return None if backref in (None, "+", "!") else backref

        for field in meta.sorted_fields:
            if isinstance(field, ForeignKeyField) and id(field.rel_model) in selected:
                back = None if is_generated_through(field.rel_model) else visible(field.backref)
                relation(field.name, field.rel_model, "MANYTOONE", False, back,
                         [(model, field.column_name, field.rel_model, field.rel_field.column_name)])
        for field, source in meta.backrefs.items():
            if visible(field.backref) and id(source) in selected and not is_generated_through(source):
                relation(field.backref, source, "ONETOMANY", not field.unique, field.name,
                         [(model, field.rel_field.column_name, source, field.column_name)])
        for name, field in sorted(meta.manytomany.items()):
            through = field.through_model or field.get_through_model()
            if id(field.rel_model) not in selected or id(through) not in selected:
                continue
            source_fk = through._meta.model_refs[field.model][0]
            target_fk = through._meta.model_refs[field.rel_model][0]
            back = next((n for n, f in field.rel_model._meta.manytomany.items()
                         if (f.through_model or f.get_through_model()) is through and f is not field), None)
            relation(name, field.rel_model, "MANYTOMANY", True, back,
                     [(model, source_fk.rel_field.column_name, through, source_fk.column_name),
                      (field.rel_model, target_fk.rel_field.column_name, through, target_fk.column_name)],
                     secondary=through)
        return result


def document_from_modules(
    loaded: dict[str, ModuleType], *, project_root: Path, targets: list[str],
) -> SchemaDocument:
    """Extract Peewee models visible in the imported modules, with their join tables."""
    found: dict[int, type] = {}
    for _, module in sorted(loaded.items()):
        for _, value in sorted(vars(module).items()):
            if is_model(value):
                found[id(value)] = value
    pending = list(found.values())
    while pending:
        meta = pending.pop()._meta
        linked = [f.rel_model for f in meta.sorted_fields if isinstance(f, ForeignKeyField)]
        linked += [f.through_model or f.get_through_model() for f in meta.manytomany.values()]
        linked += [f.rel_model for f in meta.manytomany.values()]
        for model in linked:
            if is_model(model) and id(model) not in found:
                found[id(model)] = model
                pending.append(model)
    referenced = {id(f.rel_model) for m in found.values() for f in m._meta.sorted_fields
                  if isinstance(f, ForeignKeyField)}
    models, diagnostics = [], []
    for model in found.values():
        meta = model._meta
        is_base = (any(other is not model and issubclass(other, model) for other in found.values())
                   and list(meta.fields) == ["id"] and isinstance(meta.primary_key, AutoField)
                   and id(model) not in referenced)
        if is_base:
            diagnostics.append(Diagnostic(
                kind="skipped_base_model", subject=f"{model.__module__}.{model.__qualname__}",
                message="Base class with only an implicit id and subclasses; not extracted as a table",
            ))
        else:
            models.append(model)
    if not models:
        raise ValueError("No Peewee models found in the selected modules")
    document = PeeweeAdapter(models, project_root).extract()
    return document.model_copy(update={"diagnostics": sorted(
        document.diagnostics + diagnostics, key=lambda d: (d.kind, d.subject, d.message))})
