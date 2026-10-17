"""The normalized schema model every producer fills: ORM adapters, database introspection and reconciliation.

Frozen dataclasses with Python attribute names. They know nothing about JSON: ``contract.serialize`` is the only
code that maps them to and from laranex-schema 2.0 documents, and ``contract.graph`` builds their links. Values that
the contract enumerates (driver, provenance, kinds, feature names, subject types) are stored as the contract spells
them. Sequences are tuples, so a document cannot change after it is built; derive changed copies with ``replace``.

Never imports an ORM, a database driver or the JSON layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "Backs", "Column", "Comparison", "Connection", "ConnectionCoverage", "Constraint", "Coverage", "CoverageEvidence",
    "DefaultValue", "DerivedLink", "Diagnostic", "Difference", "DifferenceRef", "Document", "Drift", "Endpoint",
    "EnumType", "FieldDifference", "ForeignKey", "Generated", "Generator", "Index", "IndexOptions", "IndexPart",
    "InferenceEvidence", "InferredAssociation", "Join", "Keys", "Link", "MigrationCoverage", "MigrationHistory",
    "MigrationRevision", "MigrationTrack", "Model", "OrmDefault", "Owner", "Pivot", "Polymorphic", "Relationship",
    "Scope", "Source", "Subject", "Summary", "Table", "replace",
]

Extensions = Mapping[str, Mapping[str, Any]]

FEATURES = (
    "tables", "columns", "columnTypes", "defaults", "comments", "primaryKeys", "foreignKeys", "referentialActions",
    "indexes", "indexExpressions", "indexPredicates", "includedColumns", "uniqueConstraints", "checkConstraints",
    "models", "relationships",
)
DATABASE = ("database",)
DECLARED = ("declared",)
BOTH = ("database", "declared")


def _frozen(cls: type) -> type:
    return dataclass(frozen=True, kw_only=True)(cls)


@_frozen
class Drift:
    """A field (Python attribute name) both sources report with different values; the entity holds the database's."""

    field: str
    database: Any
    declared: Any


@_frozen
class Coverage:
    complete: bool
    # The schemas read, or None when the source has no notion of schemas.
    schemas: tuple[str, ...] | None
    # Feature name (as the contract spells it) -> complete | partial | unavailable | not_applicable.
    features: Mapping[str, str]


@_frozen
class ConnectionCoverage:
    database: Coverage | None = None
    declared: Coverage | None = None


@_frozen
class DefaultValue:
    kind: str  # literal | expression | sequence | identity | raw
    value: str


@_frozen
class OrmDefault:
    kind: str  # scalar | callable | sequence | expression
    value: str


@_frozen
class EnumType:
    name: str | None
    values: tuple[str, ...]


@_frozen
class Generated:
    kind: str | None  # stored | virtual | None
    expression: str


@_frozen
class Column:
    name: str
    type: str
    nullable: bool
    provenance: tuple[str, ...]
    type_name: str | None = None
    auto_increment: bool | None = None
    timezone: bool | None = None
    collation: str | None = None
    enum: EnumType | None = None
    default: DefaultValue | None = None
    orm_default: OrmDefault | None = None
    generated: Generated | None = None
    comment: str | None = None
    note: str | None = None
    drift: tuple[Drift, ...] = ()
    extensions: Extensions | None = None


@_frozen
class IndexPart:
    column: str | None = None
    expression: str | None = None
    order: str | None = None  # asc | desc
    nulls: str | None = None  # first | last
    length: int | None = None
    collation: str | None = None
    opclass: str | None = None


@_frozen
class Backs:
    kind: str  # primary_key | unique_constraint
    constraint: str | None = None


@_frozen
class IndexOptions:
    method: str | None = None
    predicate: str | None = None
    included: tuple[str, ...] | None = None
    options: Mapping[str, Any] | None = None


@_frozen
class Index:
    id: str
    name: str | None
    parts: tuple[IndexPart, ...]
    unique: bool
    provenance: tuple[str, ...]
    expression: bool = False
    backs: Backs | None = None
    method: str | None = None
    partial: bool = False
    predicate: str | None = None
    included: tuple[str, ...] = ()
    dialect_options: Mapping[str, IndexOptions] | None = None
    drift: tuple[Drift, ...] = ()
    extensions: Extensions | None = None

    @property
    def columns(self) -> tuple[str, ...]:
        """The column key parts, in order."""
        return tuple(p.column for p in self.parts if p.column is not None)


@_frozen
class ForeignKey:
    id: str
    name: str | None
    columns: tuple[str, ...]
    foreign_table: str
    foreign_columns: tuple[str, ...]
    provenance: tuple[str, ...]
    on_update: str | None = None
    on_delete: str | None = None
    deferrable: bool | None = None
    initially: str | None = None
    drift: tuple[Drift, ...] = ()
    extensions: Extensions | None = None


@_frozen
class Constraint:
    id: str
    kind: str  # unique | check
    name: str | None
    provenance: tuple[str, ...]
    columns: tuple[str, ...] = ()
    expression: str | None = None
    drift: tuple[Drift, ...] = ()
    extensions: Extensions | None = None


@_frozen
class Owner:
    kind: str = "application"  # application | framework | package
    package: str | None = None


@_frozen
class Table:
    id: str
    name: str
    provenance: tuple[str, ...]
    schema: str | None = None
    comment: str | None = None
    note: str | None = None
    owner: Owner = field(default_factory=Owner)
    primary_key: tuple[str, ...] = ()
    columns: tuple[Column, ...] = ()
    indexes: tuple[Index, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    drift: tuple[Drift, ...] = ()
    extensions: Extensions | None = None


@_frozen
class Connection:
    id: str
    name: str
    coverage: ConnectionCoverage
    driver: str | None = None
    default_schema: str | None = None
    tables: tuple[Table, ...] = ()
    extensions: Extensions | None = None


@_frozen
class Source:
    file: str
    line: int | None = None


@_frozen
class Model:
    id: str
    class_name: str
    tables: tuple[str, ...]
    primary_key: tuple[str, ...]
    source: Source | None = None
    extensions: Extensions | None = None


@_frozen
class Endpoint:
    table: str
    columns: tuple[str, ...]


@_frozen
class Keys:
    """Oriented like a foreign key: ``source`` holds the referencing columns, ``target`` the referenced ones."""

    source: Endpoint
    target: Endpoint


@_frozen
class Pivot:
    table: str
    parent_columns: tuple[str, ...]
    parent_pivot_columns: tuple[str, ...]
    related_columns: tuple[str, ...]
    related_pivot_columns: tuple[str, ...]
    columns: tuple[str, ...] = ()


@_frozen
class Polymorphic:
    type_column: str
    id_column: str
    type_value: str | None = None


@_frozen
class Join:
    primary: str | None = None
    secondary: str | None = None


@_frozen
class Relationship:
    id: str
    name: str
    model: str
    table: str
    related_model: str | None
    related_table: str | None
    type: str
    cardinality: str  # one-to-one | one-to-many | many-to-one | many-to-many
    collection: bool
    read_only: bool = False
    keys: Keys | None = None
    pivot: Pivot | None = None
    polymorphic: Polymorphic | None = None
    through: tuple[str, ...] | None = None
    join: Join | None = None
    constraints: tuple[str, ...] = ()
    inverse: str | None = None
    note: str | None = None
    source: Source | None = None
    extensions: Extensions | None = None


@_frozen
class Link:
    id: str
    source: Endpoint
    target: Endpoint
    foreign_key: str | None
    relationships: tuple[str, ...]
    state: str  # both | relationship | foreign_key
    polymorphic: bool
    note: str | None


@_frozen
class DerivedLink:
    id: str
    source: str
    target: str
    via: tuple[str, ...]
    relationship: str


@_frozen
class Subject:
    type: str  # document | connection | table | column | index | foreignKey | constraint | model | relationship | ...
    id: str | None = None
    columns: tuple[str, ...] | None = None
    label: str | None = None


@_frozen
class Diagnostic:
    kind: str
    category: str  # extraction | inspection | reconciliation | drift | lint
    severity: str  # error | warning | info
    certainty: str  # certain | likely
    subject: Subject
    message: str
    extensions: Extensions | None = None

    @property
    def id(self) -> str:
        """Defined by the contract from the diagnostic's content, so two equal diagnostics share one id."""
        from .contract.ids import diagnostic_id

        return diagnostic_id(self.kind, self.subject.type, self.subject.id, self.subject.columns, self.message)


@_frozen
class Scope:
    connection: str
    schema: str | None = None


@_frozen
class DifferenceRef:
    table: str
    id: str | None
    label: str


@_frozen
class FieldDifference:
    field: str
    baseline: Any
    target: Any


@_frozen
class Difference:
    id: str
    change: str  # missing | extra | changed
    object_type: str
    key: tuple[str, ...]
    baseline: DifferenceRef | None
    target: DifferenceRef | None
    fields: tuple[FieldDifference, ...]
    severity: str
    certainty: str
    extensions: Extensions | None = None


@_frozen
class Summary:
    missing: int
    extra: int
    changed: int


@_frozen
class Comparison:
    id: str
    baseline: Scope
    target: Scope
    complete: bool
    omitted: int
    summary: Summary
    differences: tuple[Difference, ...]
    extensions: Extensions | None = None


@_frozen
class MigrationRevision:
    id: str
    parents: tuple[str, ...] = ()
    branch_labels: tuple[str, ...] = ()


@_frozen
class MigrationHistory:
    revisions: tuple[MigrationRevision, ...]
    heads: tuple[str, ...]
    missing: tuple[str, ...]


@_frozen
class CoverageEvidence:
    kind: str
    detail: str


@_frozen
class MigrationCoverage:
    table: str | None
    name: str
    declared: bool
    autogenerate: str  # included | excluded | unknown
    migration_managed: str  # yes | no | unknown
    certainty: str
    evidence: tuple[CoverageEvidence, ...] = ()


@_frozen
class MigrationTrack:
    id: str
    provider: str
    connection: str
    schema: str | None
    version_table: str | None
    history: MigrationHistory | None
    current: tuple[str, ...] | None
    status: str  # up_to_date | behind | diverged | uninitialized | unknown
    pending: tuple[str, ...] | None
    unknown_revisions: tuple[str, ...] = ()
    coverage: tuple[MigrationCoverage, ...] = ()
    extensions: Extensions | None = None


@_frozen
class InferenceEvidence:
    kind: str
    id: str
    confirmed: bool


@_frozen
class InferredAssociation:
    id: str
    kind: str  # foreign_key | association_table
    model: str
    related_model: str
    table: str
    related_table: str
    via: str | None
    cardinality: str
    foreign_keys: tuple[str, ...]
    links: tuple[str, ...]
    confidence: str  # high | medium | low: heuristic; never implies an ORM relationship attribute exists
    evidence: tuple[InferenceEvidence, ...]
    ambiguities: tuple[str, ...] = ()
    extensions: Extensions | None = None


@_frozen
class Generator:
    name: str
    orm: str | None = None


@_frozen
class Document:
    source: str  # declared | database | combined
    generator: Generator
    connections: tuple[Connection, ...] = ()
    models: tuple[Model, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    links: tuple[Link, ...] = ()
    derived_links: tuple[DerivedLink, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    comparisons: tuple[Comparison, ...] = ()
    migrations: tuple[MigrationTrack, ...] = ()
    inferred_associations: tuple[InferredAssociation, ...] = ()
    note: str | None = None
    extensions: Extensions | None = None

    def tables(self) -> list[Table]:
        return [t for c in self.connections for t in c.tables]


def coverage(features: Mapping[str, str], schemas: tuple[str, ...] | None = None) -> Coverage:
    """Coverage whose ``complete`` follows its features: every one complete or not applicable."""
    return Coverage(complete=all(v in ("complete", "not_applicable") for v in features.values()), schemas=schemas,
                    features=dict(features))


def unavailable_coverage() -> Coverage:
    """A source that was not read for a connection."""
    return Coverage(complete=False, schemas=None, features={f: "unavailable" for f in FEATURES})
