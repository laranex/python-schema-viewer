"""ORM-neutral snapshot contract; identifiers are opaque to consumers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Default(Value):
    kind: Literal["scalar", "sql", "callable", "sequence", "generated"]
    value: str


class Column(Value):
    name: str
    type: str
    # Generic SQLAlchemy type strings omit timezone=True/False on date/time types.
    timezone: bool | None = None
    nullable: bool
    primary_key: bool = False
    autoincrement: bool | Literal["auto", "ignore_fk"] = "auto"
    default: Default | None = None
    server_default: Default | None = None
    comment: str | None = None


class Index(Value):
    name: str | None
    columns: list[str]
    expressions: list[str]
    unique: bool
    predicates: dict[str, str] = Field(default_factory=dict)
    included_columns: dict[str, list[str]] = Field(default_factory=dict)
    methods: dict[str, str] = Field(default_factory=dict)


class ForeignKey(Value):
    id: str
    name: str | None
    columns: list[str]
    target_table: str
    target_columns: list[str]
    on_delete: str | None = None
    on_update: str | None = None
    deferrable: bool | None = None
    initially: str | None = None


class Constraint(Value):
    kind: Literal["unique", "check"]
    name: str | None
    columns: list[str] = Field(default_factory=list)
    expression: str | None = None


class Table(Value):
    id: str
    namespace: str
    name: str
    schema_name: str | None = None
    comment: str | None = None
    columns: list[Column]
    primary_key: list[str]
    indexes: list[Index]
    foreign_keys: list[ForeignKey]
    constraints: list[Constraint] = Field(default_factory=list)


class Source(Value):
    file: str
    line: int


class Model(Value):
    id: str
    class_name: str
    table_ids: list[str]
    primary_key: list[str]
    source: Source | None = None


class Endpoint(Value):
    table: str
    column: str


class JoinPair(Value):
    local: Endpoint
    remote: Endpoint


class Relationship(Value):
    id: str
    name: str
    model: str
    target_model: str
    source_table: str
    target_table: str
    direction: Literal["ONETOMANY", "MANYTOONE", "MANYTOMANY"]
    uselist: bool
    viewonly: bool
    back_populates: str | None = None
    secondary: str | None = None
    primary_join: str
    secondary_join: str | None = None
    join_pairs: list[JoinPair]


class Diagnostic(Value):
    kind: str
    subject: str
    message: str


class SchemaDocument(Value):
    format: Literal["laranex-schema-viewer"] = "laranex-schema-viewer"
    version: Literal[1] = 1
    scope: Literal["declared_metadata"] = "declared_metadata"
    tables: list[Table] = Field(default_factory=list)
    models: list[Model] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> SchemaDocument:
        table_ids = {t.id for t in self.tables}
        table_by_id = {t.id: t for t in self.tables}
        model_ids = {m.id for m in self.models}
        collections = [self.tables, self.models, self.relationships]
        for items in collections:
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate entity identifiers")
        fk_ids = [fk.id for t in self.tables for fk in t.foreign_keys]
        if len(fk_ids) != len(set(fk_ids)):
            raise ValueError("Duplicate foreign-key identifiers")
        for table in self.tables:
            names = [c.name for c in table.columns]
            if len(names) != len(set(names)) or not set(table.primary_key) <= set(names):
                raise ValueError(f"Invalid columns or primary key: {table.id}")
            for fk in table.foreign_keys:
                if not fk.columns or len(fk.columns) != len(fk.target_columns):
                    raise ValueError(f"Invalid foreign-key column pairs: {fk.id}")
                if not set(fk.columns) <= set(names):
                    raise ValueError(f"Unknown foreign-key source column: {fk.id}")
                if fk.target_table in table_by_id and not set(fk.target_columns) <= {
                    c.name for c in table_by_id[fk.target_table].columns
                }:
                    # Missing referenced columns are allowed only when extraction
                    # explicitly recorded an unresolved-reference diagnostic.
                    if not any(d.kind == "unresolved_foreign_key" and d.subject == table.id
                               for d in self.diagnostics):
                        raise ValueError(f"Unknown foreign-key target column: {fk.id}")
        for model in self.models:
            if not set(model.table_ids) <= table_ids:
                raise ValueError(f"Unknown model table: {model.id}")
        for relation in self.relationships:
            if {relation.model, relation.target_model} - model_ids:
                raise ValueError(f"Unknown relationship model: {relation.id}")
            if {relation.source_table, relation.target_table} - table_ids:
                raise ValueError(f"Unknown relationship table: {relation.id}")
            if relation.secondary is not None and relation.secondary not in table_ids:
                raise ValueError(f"Unknown relationship secondary table: {relation.id}")
            for pair in relation.join_pairs:
                for endpoint in (pair.local, pair.remote):
                    if endpoint.table not in table_ids or endpoint.column not in {
                        c.name for c in table_by_id[endpoint.table].columns
                    }:
                        raise ValueError(f"Unknown relationship join endpoint: {relation.id}")
        return self

