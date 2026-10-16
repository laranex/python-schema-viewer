"""Django model metadata through the public ``Model._meta`` API.

Django needs a settings module and ``django.setup()`` before models can be imported.
Database connections are blocked while settings load and metadata is read: column
types and SQL text come from the configured backend's compiler, never from a query.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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

NAMESPACE = "default"


@contextmanager
def connections_blocked() -> Iterator[None]:
    from django.db.backends.base.base import BaseDatabaseWrapper

    original = BaseDatabaseWrapper.ensure_connection

    def deny(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Schema extraction must not connect to a database")

    BaseDatabaseWrapper.ensure_connection = deny
    try:
        yield
    finally:
        BaseDatabaseWrapper.ensure_connection = original


def setup(settings_module: str | None) -> None:
    import django
    from django.conf import settings

    if settings_module:
        current = os.environ.get("DJANGO_SETTINGS_MODULE")
        if settings.configured and current != settings_module:
            raise ValueError(f"Django is already configured with {current or 'settings.configure()'}")
        os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    elif not settings.configured and not os.environ.get("DJANGO_SETTINGS_MODULE"):
        raise ValueError("Django needs a settings module: pass --settings or set DJANGO_SETTINGS_MODULE")
    django.setup()


def literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def callable_name(value: Any) -> str:
    return f"{getattr(value, '__module__', '')}.{getattr(value, '__qualname__', type(value).__name__)}"


class DjangoAdapter:
    def __init__(self, models: list[type], project_root: Path | None = None) -> None:
        from django.db import connections, router

        self.models = models
        self.project_root = project_root
        self.connections = connections
        self.router = router
        self.diagnostics: list[Diagnostic] = []

    def connection(self, model: type) -> Any:
        return self.connections[self.router.db_for_write(model) or "default"]

    def table_id(self, model: type) -> str:
        return identifier(NAMESPACE, None, model._meta.db_table)

    def model_id(self, model: type) -> str:
        return identifier("model", model.__module__, model.__qualname__, *self.model_tables(model))

    def model_tables(self, model: type) -> list[str]:
        concrete = model._meta.concrete_model
        return sorted({self.table_id(m) for m in [concrete, *concrete._meta.get_parent_list()]})

    def compile(self, model: type, expression: Any, *, condition: bool = False) -> str:
        """Render an expression as the backend's SQL, inlining parameters as literals."""
        from django.db.models.sql.query import Query

        try:
            connection = self.connection(model)
            query = Query(model, alias_cols=False)
            compiler = query.get_compiler(connection=connection)
            node = (query.build_where(expression) if condition
                    else expression.resolve_expression(query, allow_joins=False))
            sql, params = compiler.compile(node)
            return sql % tuple(literal(p) for p in params) if params else sql
        except Exception as error:
            self.diagnostics.append(Diagnostic(
                kind="uncompiled_expression", subject=self.table_id(model),
                message=f"Kept Django's expression text because SQL compilation failed: {error}",
            ))
            return str(expression)

    def extract(self) -> SchemaDocument:
        self.diagnostics = []
        concrete = {}
        for model in self.models:
            opts = model._meta
            if opts.proxy:
                continue
            if opts.db_table in concrete:
                self.diagnostics.append(Diagnostic(
                    kind="shared_table", subject=self.table_id(model),
                    message=f"{opts.label} and {concrete[opts.db_table]._meta.label} declare the same table",
                ))
                continue
            concrete[opts.db_table] = model
            if not opts.managed:
                self.diagnostics.append(Diagnostic(
                    kind="unmanaged_model", subject=self.table_id(model),
                    message=f"{opts.label} has managed=False; Django does not create or migrate this table",
                ))
        selected = {id(m) for m in self.models}
        tables = [self.extract_table(m) for _, m in sorted(concrete.items())]
        models, relationships = [], []
        for model in sorted(self.models, key=self.model_id):
            opts = model._meta
            if opts.auto_created:
                continue
            mid = self.model_id(model)
            models.append(Model(
                id=mid, class_name=f"{model.__module__}.{model.__qualname__}",
                table_ids=self.model_tables(model), primary_key=self.primary_key(model),
                source=class_source(model, self.project_root),
            ))
            for field in opts.private_fields:
                if field.is_relation:
                    self.diagnostics.append(Diagnostic(
                        kind="unsupported_relation", subject=f"{mid}.{field.name}",
                        message=f"{type(field).__name__} has no fixed target table and is not represented",
                    ))
            if not opts.proxy:
                relationships.extend(self.relationships(model, selected))
        return SchemaDocument(
            tables=tables, models=models,
            relationships=sorted(relationships, key=lambda r: r.id),
            diagnostics=sorted(self.diagnostics, key=lambda d: (d.kind, d.subject, d.message)),
        )

    def primary_key(self, model: type) -> list[str]:
        pk = model._meta.pk
        return [f.column for f in pk.fields] if hasattr(pk, "fields") else [pk.column]

    def default(self, field: Any) -> Default | None:
        if not field.has_default():
            return None
        if callable(field.default):
            return Default(kind="callable", value=callable_name(field.default))
        return Default(kind="scalar", value=str(field.default))

    def server_default(self, model: type, field: Any) -> Default | None:
        from django.db.models import NOT_PROVIDED

        value = getattr(field, "db_default", NOT_PROVIDED)
        if value is NOT_PROVIDED:
            return None
        return Default(kind="sql", value=self.compile(model, value))

    def extract_table(self, model: type) -> DocumentTable:
        from django.db.models import CheckConstraint, UniqueConstraint
        from django.db.models.fields import AutoFieldMixin

        opts = model._meta
        tid = self.table_id(model)
        connection = self.connection(model)
        fields = [f for f in opts.local_fields if f.column is not None]
        primary_key = self.primary_key(model)

        def column_name(name: str) -> str:
            return opts.get_field(name).column

        columns = [Column(
            name=f.column, type=f.db_type(connection) or f.get_internal_type(), nullable=f.null,
            primary_key=f.column in primary_key, autoincrement=isinstance(f, AutoFieldMixin),
            default=self.default(f), server_default=self.server_default(model, f),
            comment=getattr(f, "db_comment", None),
        ) for f in fields]

        foreign_keys = []
        for field in fields:
            if not field.is_relation or not getattr(field, "db_constraint", False):
                continue
            target = field.target_field
            target_table = self.table_id(target.model)
            on_delete = getattr(field.remote_field.on_delete, "operation", None)
            foreign_keys.append(ForeignKey(
                id=identifier("foreign_key", tid, None, identifier(field.column), target_table,
                              identifier(target.column)),
                name=None, columns=[field.column], target_table=target_table,
                target_columns=[target.column], on_delete=on_delete,
            ))

        indexes, constraints = [], []
        for field in fields:
            if field.unique and not field.primary_key:
                constraints.append(Constraint(kind="unique", name=None, columns=[field.column]))
            elif field.db_index and not field.primary_key:
                indexes.append(Index(name=None, columns=[field.column],
                                     expressions=[f"{opts.db_table}.{field.column}"], unique=False))
        for names in opts.unique_together:
            constraints.append(Constraint(kind="unique", name=None,
                                          columns=[column_name(n) for n in names]))
        for names in getattr(opts, "index_together", ()):
            columns_ = [column_name(n) for n in names]
            indexes.append(Index(name=None, columns=columns_, unique=False,
                                 expressions=[f"{opts.db_table}.{c}" for c in columns_]))
        for index in opts.indexes:
            indexes.append(self.index(model, index, unique=False, include=index.include,
                                      method=getattr(index, "suffix", "idx")))
        for constraint in opts.constraints:
            if isinstance(constraint, UniqueConstraint):
                if getattr(constraint, "deferrable", None) is not None:
                    self.diagnostics.append(Diagnostic(
                        kind="unsupported_constraint_option", subject=tid,
                        message=f"Deferrable setting of {constraint.name} is not represented",
                    ))
                if constraint.condition is None and not constraint.expressions and not constraint.include:
                    constraints.append(Constraint(kind="unique", name=constraint.name,
                                                  columns=[column_name(n) for n in constraint.fields]))
                else:
                    # Django creates conditional or expression uniqueness as a unique index.
                    indexes.append(self.index(model, constraint, unique=True, include=constraint.include))
            elif isinstance(constraint, CheckConstraint):
                check = getattr(constraint, "condition", None) or getattr(constraint, "check", None)
                constraints.append(Constraint(kind="check", name=constraint.name,
                                              expression=self.compile(model, check, condition=True)))
            else:
                self.diagnostics.append(Diagnostic(
                    kind="unsupported_constraint", subject=tid,
                    message=f"{type(constraint).__name__} {constraint.name} is not represented",
                ))
        return DocumentTable(
            id=tid, namespace=NAMESPACE, name=opts.db_table, schema_name=None,
            comment=getattr(opts, "db_table_comment", None) or None, columns=columns,
            primary_key=primary_key,
            indexes=sorted(indexes, key=lambda i: i.model_dump_json()),
            foreign_keys=sorted(foreign_keys, key=lambda f: f.id),
            constraints=sorted(constraints, key=lambda c: c.model_dump_json()),
        )

    def index(self, model: type, index: Any, *, unique: bool, include: Any,
              method: str = "idx") -> Index:
        opts = model._meta
        vendor = self.connection(model).vendor
        columns, expressions = [], []
        for name in index.fields:
            descending = name.startswith("-")
            column = opts.get_field(name.lstrip("-")).column
            columns.append(column)
            expressions.append(f"{opts.db_table}.{column}" + (" DESC" if descending else ""))
        expressions += [self.compile(model, e) for e in index.expressions]
        return Index(
            name=index.name, columns=columns, expressions=expressions, unique=unique,
            predicates=({vendor: self.compile(model, index.condition, condition=True)}
                        if index.condition is not None else {}),
            included_columns={vendor: [opts.get_field(n).column for n in include]} if include else {},
            methods={vendor: method} if method != "idx" else {},
        )

    def relationships(self, model: type, selected: set[int]) -> list[Relationship]:
        opts = model._meta
        mid = self.model_id(model)
        result = []

        def relation(name: str, target: type, direction: str, uselist: bool,
                     back: str | None, pairs: list[tuple[type, str, type, str]],
                     secondary: type | None = None) -> None:
            joins = [JoinPair(local=Endpoint(table=self.table_id(a), column=ac),
                              remote=Endpoint(table=self.table_id(b), column=bc))
                     for a, ac, b, bc in pairs]
            text = [f"{a._meta.db_table}.{ac} = {b._meta.db_table}.{bc}" for a, ac, b, bc in pairs]
            result.append(Relationship(
                id=identifier("relationship", mid, name), name=name, model=mid,
                target_model=self.model_id(target), source_table=self.table_id(model),
                target_table=self.table_id(target), direction=direction, uselist=uselist,
                viewonly=False, back_populates=back,
                secondary=self.table_id(secondary) if secondary is not None else None,
                primary_join=text[0], secondary_join=text[1] if len(text) > 1 else None,
                join_pairs=joins,
            ))

        for field in opts.local_fields:
            if field.is_relation and field.column is not None and id(field.related_model) in selected:
                target = field.target_field
                rel = field.remote_field
                relation(field.name, field.related_model, "MANYTOONE", False,
                         None if rel.hidden else rel.get_accessor_name(),
                         [(model, field.column, target.model, target.column)])
        for field in opts.local_many_to_many:
            through = field.remote_field.through
            if id(field.related_model) not in selected or id(through) not in selected:
                continue
            source = through._meta.get_field(field.m2m_field_name())
            target = through._meta.get_field(field.m2m_reverse_field_name())
            rel = field.remote_field
            relation(field.name, field.related_model, "MANYTOMANY", True,
                     None if rel.hidden else rel.get_accessor_name(),
                     [(source.target_field.model, source.target_field.column, through, source.column),
                      (target.target_field.model, target.target_field.column, through, target.column)],
                     secondary=through)
        for rel in opts.related_objects:
            # Inherited reverse accessors belong to the parent model's table.
            if (rel.hidden or id(rel.related_model) not in selected
                    or rel.model._meta.concrete_model is not opts.concrete_model):
                continue
            field = rel.field
            if rel.many_to_many:
                through = field.remote_field.through
                if id(through) not in selected:
                    continue
                source = through._meta.get_field(field.m2m_reverse_field_name())
                target = through._meta.get_field(field.m2m_field_name())
                relation(rel.get_accessor_name(), rel.related_model, "MANYTOMANY", True, field.name,
                         [(source.target_field.model, source.target_field.column, through, source.column),
                          (target.target_field.model, target.target_field.column, through, target.column)],
                         secondary=through)
            else:
                target = field.target_field
                relation(rel.get_accessor_name(), rel.related_model, "ONETOMANY", not rel.one_to_one,
                         field.name, [(target.model, target.column, field.model, field.column)])
        return result


def related_closure(models: list[type]) -> list[type]:
    """Add models that selected models depend on, so every reference resolves."""
    result: dict[int, type] = {id(m): m for m in models}
    pending = list(models)
    while pending:
        opts = pending.pop()._meta
        linked = [opts.concrete_model, *opts.get_parent_list()]
        linked += [f.related_model for f in opts.get_fields() if f.is_relation and f.concrete
                   and isinstance(f.related_model, type)]
        linked += [f.target_field.model for f in opts.local_fields if f.is_relation and f.column]
        linked += [f.related_model for f in opts.local_many_to_many]
        linked += [f.remote_field.through for f in opts.local_many_to_many]
        for model in linked:
            if id(model) not in result:
                result[id(model)] = model
                pending.append(model)
    return list(result.values())


def document_from_settings(
    *, settings: str | None, modules: list[str], project_root: Path,
) -> SchemaDocument:
    """Configure Django, then extract installed models, optionally limited to modules."""
    with connections_blocked():
        setup(settings)
        from django.apps import apps

        models = apps.get_models(include_auto_created=True)
        if modules:
            def matches(model: type) -> bool:
                names = (model.__module__, model._meta.app_config.name)
                return any(n == m or n.startswith(m + ".") for n in names for m in modules)
            chosen = [m for m in models if matches(m)]
            if not chosen:
                raise ValueError(f"No Django models found in {', '.join(modules)}")
            models = related_closure(chosen)
        if not models:
            raise ValueError("No Django models are installed")
        return DjangoAdapter(models, project_root).extract()
