"""laranex-schema 1.x documents as Pydantic models; field names follow the JSON (camelCase).

The normative description is docs/contract.md of laranex-schema-viewer-ui. This module never imports an ORM.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

FORMAT = "laranex-schema"
VERSION = "1.0"
MAJOR = 1

FEATURES = (
    "tables", "columns", "columnTypes", "defaults", "comments", "primaryKeys", "foreignKeys",
    "referentialActions", "indexes", "indexExpressions", "indexPredicates", "includedColumns",
    "uniqueConstraints", "checkConstraints", "models", "relationships",
)
Feature = Literal[
    "tables", "columns", "columnTypes", "defaults", "comments", "primaryKeys", "foreignKeys",
    "referentialActions", "indexes", "indexExpressions", "indexPredicates", "includedColumns",
    "uniqueConstraints", "checkConstraints", "models", "relationships",
]
FeatureCoverage = Literal["complete", "partial", "unavailable", "not_applicable"]
Provenance = list[Literal["database", "declared"]]
Extensions = dict[str, dict[str, Any]]
ReferentialAction = Literal["CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"]


class Value(BaseModel):
    # Unknown properties are ignored here (readers accept later 1.x minors); strict producer checks report them
    # separately, see validate.unknown_properties().
    model_config = ConfigDict(extra="ignore", strict=True, alias_generator=to_camel, populate_by_name=True)


class Drift(Value):
    field: str = Field(min_length=1)
    database: Any
    declared: Any


class Physical(Value):
    provenance: Provenance = Field(min_length=1)
    drift: list[Drift] | None = None
    extensions: Extensions | None = None


class Coverage(Value):
    complete: bool
    schemas: list[str] | None
    features: dict[Feature, FeatureCoverage]


class ConnectionCoverage(Value):
    database: Coverage | None
    declared: Coverage | None


class DefaultValue(Value):
    kind: Literal["literal", "expression", "sequence", "identity", "raw"]
    value: str


class OrmDefault(Value):
    kind: Literal["scalar", "callable", "sequence", "expression"]
    value: str


class EnumType(Value):
    name: str | None
    values: list[str]


class Generated(Value):
    kind: Literal["stored", "virtual"] | None
    expression: str


class Column(Physical):
    name: str = Field(min_length=1)
    type: str
    type_name: str | None
    nullable: bool
    auto_increment: bool | None
    timezone: bool | None
    collation: str | None
    enum: EnumType | None
    default: DefaultValue | None
    orm_default: OrmDefault | None
    generated: Generated | None
    comment: str | None
    note: str | None


class IndexOptions(Value):
    method: str | None = None
    predicate: str | None = None
    included: list[str] | None = None
    options: dict[str, Any] | None = None


class IndexPart(Value):
    column: str | None = Field(min_length=1)
    expression: str | None = Field(min_length=1)
    order: Literal["asc", "desc"] | None
    nulls: Literal["first", "last"] | None
    length: int | None = Field(ge=1)
    collation: str | None
    opclass: str | None


class Backs(Value):
    kind: Literal["primary_key", "unique_constraint"]
    constraint: str | None


class Index(Physical):
    id: str = Field(min_length=1)
    name: str | None
    parts: list[IndexPart]
    columns: list[str]
    expression: bool
    unique: bool
    primary: bool
    backs: Backs | None
    method: str | None
    partial: bool
    predicate: str | None
    included: list[str]
    dialect_options: dict[str, IndexOptions] | None = None


class ForeignKey(Physical):
    id: str = Field(min_length=1)
    name: str | None
    columns: list[str] = Field(min_length=1)
    foreign_table: str = Field(min_length=1)
    foreign_columns: list[str] = Field(min_length=1)
    on_update: ReferentialAction | None
    on_delete: ReferentialAction | None
    deferrable: bool | None
    initially: Literal["DEFERRED", "IMMEDIATE"] | None


class Constraint(Physical):
    id: str = Field(min_length=1)
    kind: Literal["unique", "check"]
    name: str | None
    columns: list[str]
    expression: str | None


class Owner(Value):
    kind: Literal["application", "framework", "package"]
    package: str | None


class Table(Physical):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    schema_name: str | None = Field(alias="schema")
    comment: str | None
    note: str | None
    owner: Owner
    primary_key: list[str]
    columns: list[Column]
    indexes: list[Index]
    foreign_keys: list[ForeignKey]
    constraints: list[Constraint]


class Connection(Value):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    driver: str | None
    default_schema: str | None
    coverage: ConnectionCoverage
    tables: list[Table]
    extensions: Extensions | None = None


class Source(Value):
    file: str = Field(min_length=1)
    line: int | None = Field(ge=1)


class Model(Value):
    id: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    tables: list[str]
    primary_key: list[str]
    source: Source | None
    extensions: Extensions | None = None


class Endpoint(Value):
    table: str = Field(min_length=1)
    columns: list[str] = Field(min_length=1)


class Keys(Value):
    from_: Endpoint = Field(alias="from")
    to: Endpoint


class Pivot(Value):
    table: str = Field(min_length=1)
    parent_columns: list[str]
    parent_pivot_columns: list[str]
    related_columns: list[str]
    related_pivot_columns: list[str]
    columns: list[str]


class Polymorphic(Value):
    type_column: str = Field(min_length=1)
    id_column: str = Field(min_length=1)
    type_value: str | None


class Join(Value):
    primary: str | None
    secondary: str | None


class Relationship(Value):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    table: str = Field(min_length=1)
    related_model: str | None
    related_table: str | None
    type: str = Field(min_length=1)
    cardinality: Literal["one-to-one", "one-to-many", "many-to-one", "many-to-many"]
    collection: bool
    read_only: bool
    keys: Keys | None
    pivot: Pivot | None
    polymorphic: Polymorphic | None
    through: list[str] | None
    join: Join | None
    constraints: list[str]
    inverse: str | None
    note: str | None
    source: Source | None
    extensions: Extensions | None = None


class Link(Value):
    id: str = Field(min_length=1)
    from_: Endpoint = Field(alias="from")
    to: Endpoint
    foreign_key: str | None
    relationships: list[str]
    state: Literal["both", "relationship", "foreign_key"]
    polymorphic: bool
    note: str | None


class DerivedLink(Value):
    id: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    via: list[str] = Field(min_length=1)
    relationship: str = Field(min_length=1)


class Subject(Value):
    type: Literal["document", "connection", "table", "column", "index", "foreignKey", "constraint",
                  "model", "relationship", "link"]
    id: str | None = Field(min_length=1)
    columns: list[str] | None
    label: str | None


class Diagnostic(Value):
    kind: str = Field(min_length=1)
    category: Literal["extraction", "inspection", "reconciliation", "drift", "lint"]
    severity: Literal["error", "warning", "info"]
    certainty: Literal["certain", "likely"]
    subject: Subject
    message: str
    extensions: Extensions | None = None


class Generator(Value):
    name: str = Field(min_length=1)
    orm: str | None


class Document(Value):
    format: Literal["laranex-schema"]
    version: str = Field(pattern=r"^1\.(0|[1-9][0-9]*)$")
    source: Literal["declared", "database", "combined"]
    generator: Generator
    note: str | None
    connections: list[Connection]
    models: list[Model]
    relationships: list[Relationship]
    links: list[Link]
    derived_links: list[DerivedLink]
    diagnostics: list[Diagnostic]
    extensions: Extensions | None = None

    def tables(self) -> list[Table]:
        return [t for c in self.connections for t in c.tables]

    def to_json(self) -> dict[str, Any]:
        """The document as JSON data, with every optional field the contract leaves out left out."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=False, exclude=_absent(self))


def _absent(model: BaseModel) -> Any:
    """Optional fields (drift, extensions, dialectOptions) that are None are absent in JSON, not null."""
    optional = {"drift", "extensions", "dialect_options"}
    excluded: dict[Any, Any] = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        if value is None and (name in optional or isinstance(model, IndexOptions)):
            excluded[name] = True
        elif isinstance(value, BaseModel):
            inner = _absent(value)
            if inner:
                excluded[name] = inner
        elif isinstance(value, list):
            inner = {i: _absent(v) for i, v in enumerate(value) if isinstance(v, BaseModel)}
            inner = {i: v for i, v in inner.items() if v}
            if inner:
                excluded[name] = inner
        elif isinstance(value, dict):
            inner = {k: _absent(v) for k, v in value.items() if isinstance(v, BaseModel)}
            inner = {k: v for k, v in inner.items() if v}
            if inner:
                excluded[name] = inner
    return excluded
