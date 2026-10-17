"""The schema comparison every producer implements (docs/contract.md, "Comparisons"), a port of
laranex-schema-viewer-ui's comparison.ts (``compareScopes``) on the schema model, checked against
contract/conformance/comparison. Producers compute comparisons; viewers only display the stored result.

The port keeps the TypeScript's semantics where Python differs: ``\\w`` and ``\\b`` are ASCII, ``\\s`` and ``trim`` use
JavaScript's whitespace, and the orders the reference takes from JavaScript's ``<`` compare UTF-16 code units.
Difference field names (``onDelete``, ``foreignTable``) are spelled as the contract enumerates them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from .. import model as m
from .graph import in_scope
from .ids import by_list, comparison_id, difference_id
from .shape import CHANGES, OBJECT_TYPES

__all__ = ["COMPARISON_FEATURES", "FIELDS", "SeverityOverride", "compare_scopes", "coverage_gaps", "default_severity",
           "normalize_sql", "normalize_type", "scope_covered", "table_label"]

# The features a comparison reads; a side whose coverage does not have them all complete makes it incomplete.
COMPARISON_FEATURES = (
    "tables", "columns", "columnTypes", "defaults", "comments", "primaryKeys", "foreignKeys", "referentialActions",
    "indexes", "indexExpressions", "indexPredicates", "uniqueConstraints", "checkConstraints",
)

# The fields a changed difference of each object type can list.
FIELDS = {
    "table": ("comment",),
    "column": ("collation", "comment", "default", "enum", "generated", "nullable", "type"),
    "primaryKey": ("columns",),
    "index": ("included", "method", "name", "parts", "predicate", "unique"),
    "foreignKey": ("columns", "deferrable", "foreignColumns", "foreignTable", "initially", "name", "onDelete",
                   "onUpdate"),
    "constraint": ("columns", "expression", "name"),
}
SEVERITIES = ("error", "warning", "info")

# Fields whose comparison rests on normalized text: their differences are "likely".
LIKELY_FIELDS = {"table": (), "column": ("default", "generated"), "primaryKey": (), "index": ("predicate",),
                 "foreignKey": (), "constraint": ("expression",)}
RANK = {"info": 0, "warning": 1, "error": 2}

TYPE_SYNONYMS = {
    "int": "integer", "int4": "integer", "integer": "integer", "mediumint": "integer", "serial": "integer",
    "bigint": "bigint", "int8": "bigint", "bigserial": "bigint", "smallint": "smallint", "int2": "smallint",
    "smallserial": "smallint", "tinyint": "tinyint", "bool": "boolean", "boolean": "boolean",
    "character varying": "varchar", "varchar": "varchar", "nvarchar": "varchar", "character": "char", "char": "char",
    "bpchar": "char", "nchar": "char", "text": "text", "clob": "text", "datetime": "timestamp",
    "timestamp": "timestamp", "timestamp without time zone": "timestamp", "timestamptz": "timestamptz",
    "timestamp with time zone": "timestamptz", "time without time zone": "time", "time with time zone": "timetz",
    "timetz": "timetz", "float": "double", "float8": "double", "double": "double", "double precision": "double",
    "real": "real", "float4": "real", "numeric": "numeric", "decimal": "numeric", "blob": "blob", "bytea": "blob",
    "varbinary": "blob", "largebinary": "blob",
}
INTEGERS = {"integer", "bigint", "smallint", "tinyint"}

# JavaScript's \s and String.prototype.trim: WhiteSpace and LineTerminator.
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"
_S = f"[{re.escape(_JS_SPACE)}]"
_SPACES = re.compile(f"{_S}+")
_SIGNEDNESS = re.compile(r"\b(unsigned|zerofill|signed)\b", re.ASCII)
_TYPE = re.compile(f"([a-z_][\\w ]*?){_S}*(\\((.*)\\))?{_S}*(\\[\\])?{_S}*(with(out)? time zone)?", re.ASCII)
_CAST = re.compile(r'::[\w .\[\]"]+', re.ASCII)
_LOOSE = re.compile(f"[{re.escape(_JS_SPACE)}()`\"]")


def _trim(text: str) -> str:
    return text.strip(_JS_SPACE)


def _js(text: str) -> bytes:
    """A sort key that orders strings as JavaScript's < does, by UTF-16 code units."""
    return text.encode("utf-16-be", "surrogatepass")


@dataclass(frozen=True)
class SeverityOverride:
    """A user's severity for ``(object_type, change)``, or for one changed ``field`` (contract spellings)."""

    object_type: str
    change: str
    severity: str
    field: str | None = None


def default_severity(object_type: str, change: str, field: str | None = None) -> str:
    if object_type == "table":
        return "info" if change == "changed" else "error"
    if object_type == "column":
        return "error" if change != "changed" else "info" if field == "comment" else "warning"
    if object_type == "primaryKey":
        return "error"
    return "warning"


def _severity(object_type: str, change: str, field: str | None, overrides: Sequence[SeverityOverride]) -> str:
    def match(f: str | None) -> str | None:
        return next((o.severity for o in overrides
                     if o.object_type == object_type and o.change == change and o.field == f), None)

    return ((match(field) if field is not None else None) or match(None)
            or default_severity(object_type, change, field))


def normalize_type(text: str | None, timezone: bool | None = None) -> str | None:
    """Base name and arguments of a column type, or None when it cannot be parsed: synonyms collapse (character
    varying is varchar, int4 integer), display widths and signedness drop."""
    if not text:
        return None
    value = _SPACES.sub(" ", _trim(text).lower())
    value = _trim(_SIGNEDNESS.sub("", value))
    match = _TYPE.fullmatch(value)
    if not match:
        return None
    base = _trim(f"{match[1]} {match[5]}" if match[5] else match[1])
    base = TYPE_SYNONYMS.get(base, base)
    if base == "timestamp" and timezone:
        base = "timestamptz"
    args = _SPACES.sub("", match[3] or "")
    if base == "tinyint" and args == "1":
        base, args = "boolean", ""
    if base in INTEGERS or (base == "double" and args == "53"):
        args = ""
    return f"{base}{'[]' if match[4] else ''}({args})"


def normalize_sql(text: str | None, schema: str | None = None, driver: str | None = None) -> str:
    """SQL text compared loosely: lower case, without casts, whitespace, parentheses or identifier quotes, and without
    the side's own schema qualifier, so tenant_a.seq and tenant_b.seq compare equal."""
    value = (text or "").lower()
    if schema is not None:
        s = re.escape(schema.lower())
        value = re.sub(f'(?:"{s}"|`{s}`|\\b{s})\\.', "", value, flags=re.ASCII)
    if driver == "mariadb":
        value = value.replace("octet_length(", "length(")
    value = _CAST.sub("", value)
    return _LOOSE.sub("", value)


def _trivial_type(text: str) -> str:
    return _SPACES.sub(" ", _trim(text).lower())


def table_label(table: m.Table) -> str:
    return f"{table.schema}.{table.name}" if table.schema else table.name


@dataclass(frozen=True)
class _Side:
    scope: m.Scope
    connection: m.Connection
    combined: bool
    tables: dict[str, m.Table]
    names: dict[str, str]
    coverage: m.Coverage | None

    def physical(self, entity: Any) -> bool:
        """In a combined document, only what the database reported."""
        return not self.combined or "database" in entity.provenance

    def name_of(self, table_id: str) -> str:
        return self.names.get(table_id, table_id)

    def sql(self, text: str | None) -> str:
        return normalize_sql(text, self.scope.schema, self.connection.driver)


def _side(document: m.Document, scope: m.Scope) -> _Side:
    connection = next((c for c in document.connections if c.id == scope.connection), None)
    if connection is None:
        raise ValueError(f"Unknown connection {scope.connection}")
    combined = document.source == "combined"
    tables: dict[str, m.Table] = {}
    for t in connection.tables:
        if in_scope(t, connection, scope.schema) and (not combined or "database" in t.provenance):
            if t.name in tables:
                where = json.dumps({"connection": scope.connection, "schema": scope.schema}, separators=(",", ":"),
                                   ensure_ascii=False)
                raise ValueError(f"Table name {t.name} occurs twice in scope {where}")
            tables[t.name] = t
    coverage = connection.coverage.declared if document.source == "declared" else connection.coverage.database
    return _Side(scope=scope, connection=connection, combined=combined, tables=tables,
                 names={t.id: t.name for t in connection.tables}, coverage=coverage)


def _gaps(coverage: m.Coverage | None, connection: m.Connection, schema: str | None) -> list[str]:
    if coverage is None:
        return ["coverage unknown"]
    gaps = [f"{f} {coverage.features.get(f, 'unknown')}" for f in COMPARISON_FEATURES
            if coverage.features.get(f) not in ("complete", "not_applicable")]
    read = schema if schema is not None else connection.default_schema
    if read is not None and coverage.schemas is not None and read not in coverage.schemas:
        gaps.append(f"schema {read} not read")
    return gaps


def scope_covered(coverage: m.Coverage | None, connection: m.Connection, schema: str | None) -> bool:
    """Whether a side's coverage supports a comparison (scopeCovered): every compared feature complete or not
    applicable, and the scope's schema read."""
    return not _gaps(coverage, connection, schema)


def coverage_gaps(document: m.Document, scope: m.Scope) -> list[str]:
    """Why a side cannot be compared completely: the features a comparison reads that its coverage does not have
    complete or not applicable, and its schema when the coverage did not read it. Empty when the side is covered."""
    s = _side(document, scope)
    return _gaps(s.coverage, s.connection, s.scope.schema)


@dataclass
class _Found:
    object_type: str
    key: list[str]
    change: str
    baseline: m.DifferenceRef | None
    target: m.DifferenceRef | None
    fields: list[m.FieldDifference]
    likely: bool


E = TypeVar("E")


def _pair(a: Sequence[E], b: Sequence[E], passes: Sequence[Callable[[E], str | None]]
          ) -> tuple[list[tuple[E, E, int]], list[E], list[E]]:
    """Pairs entities in passes, each by an identity both sides share; equal identities pair in id order. Returns the
    pairs with the pass that matched them, and the unmatched on each side."""
    pairs: list[tuple[E, E, int]] = []
    left = sorted(a, key=lambda x: _js(x.id))  # type: ignore[attr-defined]
    right = sorted(b, key=lambda x: _js(x.id))  # type: ignore[attr-defined]
    for number, identity in enumerate(passes):
        rest = []
        for x in left:
            k = identity(x)
            i = -1 if k is None else next((n for n, y in enumerate(right) if identity(y) == k), -1)
            if i < 0:
                rest.append(x)
            else:
                pairs.append((x, right[i], number))
                right = right[:i] + right[i + 1:]
        left = rest
    return pairs, left, right


def compare_scopes(document: m.Document, baseline: m.Scope, target: m.Scope, *,
                   overrides: Sequence[SeverityOverride] = (), limit: int | None = None) -> m.Comparison:
    """Compares the tables of two scopes of one document. Raises ValueError for an unknown connection, or when a
    scope's table names repeat."""
    A = _side(document, baseline)
    B = _side(document, target)
    same_driver = A.connection.driver is not None and A.connection.driver == B.connection.driver
    found: list[_Found] = []

    def add(object_type: str, key: list[str], a: m.DifferenceRef | None, b: m.DifferenceRef | None,
            fields: list[m.FieldDifference] | None = None, likely: bool = False) -> None:
        change = "changed" if a and b else "missing" if a else "extra"
        if change != "changed":
            found.append(_Found(object_type, key, change, a, b, [], False))
        elif fields:
            found.append(_Found(object_type, key, change, a, b, sorted(fields, key=lambda x: _js(x.field)), likely))

    def ref(t: m.Table | None, id: str | None = None, suffix: str = "") -> m.DifferenceRef | None:
        return m.DifferenceRef(table=t.id, id=id, label=f"{table_label(t)}{suffix}") if t else None

    def diff(field: str, baseline: Any, target: Any) -> m.FieldDifference:
        return m.FieldDifference(field=field, baseline=baseline, target=target)

    names = list(dict.fromkeys([*A.tables, *B.tables]))
    for name in names:
        ta, tb = A.tables.get(name), B.tables.get(name)
        if not ta or not tb:
            add("table", [name], ref(ta), ref(tb))
            continue
        add("table", [name], ref(ta), ref(tb),
            [] if ta.comment == tb.comment else [diff("comment", ta.comment, tb.comment)])

        # Columns, by name.
        ca = {c.name: c for c in ta.columns if A.physical(c)}
        cb = {c.name: c for c in tb.columns if B.physical(c)}
        for column in dict.fromkeys([*ca, *cb]):
            x, y = ca.get(column), cb.get(column)
            fields, likely = _column_fields(x, y, A, B, same_driver) if x and y else ([], False)
            add("column", [name, column], ref(ta, None, f".{column}") if x else None,
                ref(tb, None, f".{column}") if y else None, fields, likely)

        # The primary key, by its ordered columns.
        pa, pb = ta.primary_key, tb.primary_key
        if pa or pb:
            add("primaryKey", [name], ref(ta, None, " primary key") if pa else None,
                ref(tb, None, " primary key") if pb else None, [] if pa == pb else [diff("columns", pa, pb)])

        _indexes(name, ta, tb, A, B, add, ref)
        _foreign_keys(name, ta, tb, A, B, add, ref)
        _constraints(name, ta, tb, A, B, add, ref)

    # Identities that still coincide (unnamed duplicates) get "#2", "#3", … in the order of their entity ids.
    groups: dict[tuple[str, tuple[str, ...]], list[_Found]] = {}
    for f in found:
        groups.setdefault((f.object_type, tuple(f.key)), []).append(f)
    for group in groups.values():
        if len(group) > 1:
            def ref_id(f: _Found) -> str:
                side = f.baseline or f.target
                return (side.id if side else None) or ""

            group.sort(key=lambda f: (_js(ref_id(f)), CHANGES.index(f.change)))
            for n, f in enumerate(group[1:], start=2):
                f.key.append(f"#{n}")

    cid = comparison_id(baseline.connection, baseline.schema, target.connection, target.schema)
    ordered = sorted(found, key=_OrderKey)
    differences = []
    for f in ordered:
        if f.change == "changed":
            severity = "info"
            for x in f.fields:
                s = _severity(f.object_type, f.change, x.field, overrides)
                if RANK[s] > RANK[severity]:
                    severity = s
        else:
            severity = _severity(f.object_type, f.change, None, overrides)
        differences.append(m.Difference(
            id=difference_id(cid, f.object_type, f.key), change=f.change, object_type=f.object_type,
            key=tuple(f.key), baseline=f.baseline, target=f.target, fields=tuple(f.fields), severity=severity,
            certainty="likely" if f.likely else "certain"))
    summary = m.Summary(**{c: sum(1 for d in differences if d.change == c) for c in CHANGES})
    kept = differences if limit is None else differences[:limit]
    omitted = len(differences) - len(kept)
    complete = omitted == 0 and not coverage_gaps(document, baseline) and not coverage_gaps(document, target)
    return m.Comparison(id=cid, baseline=baseline, target=target, complete=complete, omitted=omitted,
                        summary=summary, differences=tuple(kept))


class _OrderKey:
    """Object type in contract order, then key element-wise by code point (a prefix first), then change."""

    def __init__(self, f: _Found) -> None:
        self.f = f

    def __lt__(self, other: _OrderKey) -> bool:
        a, b = self.f, other.f
        d = (OBJECT_TYPES.index(a.object_type) - OBJECT_TYPES.index(b.object_type)
             or by_list(a.key, b.key) or CHANGES.index(a.change) - CHANGES.index(b.change))
        return d < 0


def _column_fields(a: m.Column, b: m.Column, A: _Side, B: _Side, same_driver: bool
                   ) -> tuple[list[m.FieldDifference], bool]:
    fields = []
    likely = False
    if same_driver:
        type_differs = _trivial_type(a.type) != _trivial_type(b.type)
    else:
        type_differs = ((normalize_type(a.type, a.timezone) or _trivial_type(a.type))
                        != (normalize_type(b.type, b.timezone) or _trivial_type(b.type)))
    if type_differs:
        fields.append(m.FieldDifference(field="type", baseline=a.type, target=b.type))
        likely = likely or not same_driver
    if a.nullable != b.nullable:
        fields.append(m.FieldDifference(field="nullable", baseline=a.nullable, target=b.nullable))

    # Identity, sequence and auto-increment defaults are one kind of value, whatever the sequence or dialect.
    def default(c: m.Column, s: _Side) -> str | None:
        if (c.default is not None and c.default.kind in ("identity", "sequence")) or (
                c.default is None and c.auto_increment is True):
            return "auto"
        return None if c.default is None else f"={s.sql(c.default.value)}"

    if default(a, A) != default(b, B):
        fields.append(m.FieldDifference(field="default", baseline=a.default, target=b.default))
    if a.collation is not None and b.collation is not None and a.collation.lower() != b.collation.lower():
        fields.append(m.FieldDifference(field="collation", baseline=a.collation, target=b.collation))
    ea = a.enum.values if a.enum is not None else None
    eb = b.enum.values if b.enum is not None else None
    if ea != eb:
        fields.append(m.FieldDifference(field="enum", baseline=ea, target=eb))

    def generated(c: m.Column, s: _Side) -> str | None:
        if c.generated is None:
            return None
        kind = "null" if c.generated.kind is None else c.generated.kind
        return f"{kind}:{s.sql(c.generated.expression)}"

    if generated(a, A) != generated(b, B):
        fields.append(m.FieldDifference(field="generated", baseline=a.generated, target=b.generated))
    if a.comment != b.comment:
        fields.append(m.FieldDifference(field="comment", baseline=a.comment, target=b.comment))
    likely = likely or any(f.field in LIKELY_FIELDS["column"] for f in fields)
    return fields, likely


def _indexes(name: str, ta: m.Table, tb: m.Table, A: _Side, B: _Side, add: Callable[..., None],
             ref: Callable[..., m.DifferenceRef | None]) -> None:
    """By name, then by key parts and uniqueness. Indexes backing the primary key or a unique constraint in the
    document are compared as those."""
    def compared(t: m.Table, s: _Side) -> list[m.Index]:
        return [i for i in t.indexes if s.physical(i) and not (i.backs is not None and (
            i.backs.kind == "primary_key" or (i.backs.kind == "unique_constraint" and i.backs.constraint is not None)))]

    def parts(i: m.Index, s: _Side) -> str:
        text = ",".join((p.column if p.column is not None else f"expr:{s.sql(p.expression)}")
                        + (" desc" if p.order == "desc" else "") for p in i.parts)
        return text + (",?" if i.expression and not any(p.expression is not None for p in i.parts) else "")

    ia, ib = compared(ta, A), compared(tb, B)
    signature = {id(i): f"{'unique ' if i.unique else ''}({parts(i, s)})" for s, items in ((A, ia), (B, ib))
                 for i in items}
    pairs, missing, extra = _pair(ia, ib, [lambda i: None if i.name is None else f"name:{i.name}",
                                           lambda i: signature[id(i)]])

    def iref(t: m.Table, i: m.Index) -> m.DifferenceRef | None:
        return ref(t, i.id, f".{i.name if i.name is not None else signature[id(i)]}")

    for a, b, number in pairs:
        f = []
        if number == 1 and a.name is not None and b.name is not None and a.name != b.name:
            f.append(m.FieldDifference(field="name", baseline=a.name, target=b.name))
        if a.unique != b.unique:
            f.append(m.FieldDifference(field="unique", baseline=a.unique, target=b.unique))
        xa, xb = parts(a, A), parts(b, B)
        if xa != xb:
            f.append(m.FieldDifference(field="parts", baseline=xa, target=xb))
        if a.method is not None and b.method is not None and a.method.lower() != b.method.lower():
            f.append(m.FieldDifference(field="method", baseline=a.method, target=b.method))
        if a.partial != b.partial or A.sql(a.predicate) != B.sql(b.predicate):
            f.append(m.FieldDifference(field="predicate", baseline=a.predicate if a.partial else None,
                                       target=b.predicate if b.partial else None))
        if sorted(a.included) != sorted(b.included):
            f.append(m.FieldDifference(field="included", baseline=a.included, target=b.included))
        expression_parts = any(x.field == "parts" for x in f) and (a.expression or b.expression)
        key = [name, "name", a.name] if number == 0 else [name, "parts", signature[id(a)]]
        add("index", key, iref(ta, a), iref(tb, b), f,
            bool(expression_parts) or any(x.field in LIKELY_FIELDS["index"] for x in f))

    def unmatched(i: m.Index) -> list[str]:
        return [name, "name", i.name] if i.name is not None else [name, "parts", signature[id(i)]]

    for i in missing:
        add("index", unmatched(i), iref(ta, i), None)
    for i in extra:
        add("index", unmatched(i), None, iref(tb, i))


def _foreign_keys(name: str, ta: m.Table, tb: m.Table, A: _Side, B: _Side, add: Callable[..., None],
                  ref: Callable[..., m.DifferenceRef | None]) -> None:
    """By columns, referenced table name and referenced columns, then by name."""
    fa = [f for f in ta.foreign_keys if A.physical(f)]
    fb = [f for f in tb.foreign_keys if B.physical(f)]
    signature = {id(f): f"{','.join(f.columns)}->{s.name_of(f.foreign_table)}({','.join(f.foreign_columns)})"
                 for s, items in ((A, fa), (B, fb)) for f in items}
    pairs, missing, extra = _pair(fa, fb, [lambda f: signature[id(f)],
                                           lambda f: None if f.name is None else f"name:{f.name}"])

    def fref(t: m.Table, f: m.ForeignKey) -> m.DifferenceRef | None:
        return ref(t, f.id, f".{f.name if f.name is not None else signature[id(f)]}")

    # MySQL and MariaDB treat RESTRICT as NO ACTION, so either side being one makes them equal.
    restrict_is_no_action = any(x.connection.driver in ("mysql", "mariadb") for x in (A, B))

    def action(value: str | None) -> str:
        v = "NO ACTION" if value is None else value
        return "NO ACTION" if v == "RESTRICT" and restrict_is_no_action else v

    for a, b, number in pairs:
        f = []
        if number == 0 and a.name is not None and b.name is not None and a.name != b.name:
            f.append(m.FieldDifference(field="name", baseline=a.name, target=b.name))
        if number == 1:
            if a.columns != b.columns:
                f.append(m.FieldDifference(field="columns", baseline=a.columns, target=b.columns))
            if A.name_of(a.foreign_table) != B.name_of(b.foreign_table):
                f.append(m.FieldDifference(field="foreignTable", baseline=A.name_of(a.foreign_table),
                                           target=B.name_of(b.foreign_table)))
            if a.foreign_columns != b.foreign_columns:
                f.append(m.FieldDifference(field="foreignColumns", baseline=a.foreign_columns,
                                           target=b.foreign_columns))
        for field, attribute in (("onDelete", "on_delete"), ("onUpdate", "on_update")):
            if action(getattr(a, attribute)) != action(getattr(b, attribute)):
                f.append(m.FieldDifference(field=field, baseline=getattr(a, attribute), target=getattr(b, attribute)))
        for field in ("deferrable", "initially"):
            x, y = getattr(a, field), getattr(b, field)
            if x is not None and y is not None and x != y:
                f.append(m.FieldDifference(field=field, baseline=x, target=y))
        key = [name, "references", signature[id(a)]] if number == 0 else [name, "name", a.name]
        add("foreignKey", key, fref(ta, a), fref(tb, b), f)

    def unmatched(f: m.ForeignKey) -> list[str]:
        return [name, "name", f.name] if f.name is not None else [name, "references", signature[id(f)]]

    for f in missing:
        add("foreignKey", unmatched(f), fref(ta, f), None)
    for f in extra:
        add("foreignKey", unmatched(f), None, fref(tb, f))


def _constraints(name: str, ta: m.Table, tb: m.Table, A: _Side, B: _Side, add: Callable[..., None],
                 ref: Callable[..., m.DifferenceRef | None]) -> None:
    """By kind and name, then by kind and definition (columns, or the check expression)."""
    ka = [c for c in ta.constraints if A.physical(c)]
    kb = [c for c in tb.constraints if B.physical(c)]
    definition = {id(c): ",".join(c.columns) if c.kind == "unique" and c.columns else s.sql(c.expression)
                  for s, items in ((A, ka), (B, kb)) for c in items}
    pairs, missing, extra = _pair(ka, kb, [lambda c: None if c.name is None else f"{c.kind}:name:{c.name}",
                                           lambda c: f"{c.kind}:{definition[id(c)]}"])

    def kref(t: m.Table, c: m.Constraint) -> m.DifferenceRef | None:
        return ref(t, c.id, f".{c.name if c.name is not None else definition[id(c)]}")

    for a, b, number in pairs:
        f = []
        if number == 1 and a.name is not None and b.name is not None and a.name != b.name:
            f.append(m.FieldDifference(field="name", baseline=a.name, target=b.name))
        if number == 0 and a.kind == "unique" and a.columns != b.columns:
            f.append(m.FieldDifference(field="columns", baseline=a.columns, target=b.columns))
        if number == 0 and definition[id(a)] != definition[id(b)] and (a.kind == "check" or not a.columns):
            f.append(m.FieldDifference(field="expression", baseline=a.expression, target=b.expression))
        key = [name, a.kind, "name", a.name] if number == 0 else [name, a.kind, "definition", definition[id(a)]]
        add("constraint", key, kref(ta, a), kref(tb, b), f, any(x.field in LIKELY_FIELDS["constraint"] for x in f))

    def unmatched(c: m.Constraint) -> list[str]:
        return [name, c.kind, "name", c.name] if c.name is not None else [name, c.kind, "definition",
                                                                           definition[id(c)]]

    for c in missing:
        add("constraint", unmatched(c), kref(ta, c), None)
    for c in extra:
        add("constraint", unmatched(c), None, kref(tb, c))
