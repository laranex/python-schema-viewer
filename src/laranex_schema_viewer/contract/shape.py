"""The laranex-schema 2.x document shape, a port of laranex-schema-viewer-ui's shape.ts and document.ts.

Each shape both checks a JSON value (with the same messages as the TypeScript validator) and describes itself as JSON
Schema, so ``json_schema()`` reproduces the shared ``schema.json`` from the same definitions the checks use.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..model import FEATURES

SUBJECT_TYPES = ("document", "connection", "table", "column", "index", "foreignKey", "constraint", "model",
                 "relationship", "link", "comparison", "migration", "inferredAssociation")
OBJECT_TYPES = ("table", "column", "primaryKey", "index", "foreignKey", "constraint")
CHANGES = ("missing", "extra", "changed")
MIGRATION_STATUSES = ("up_to_date", "behind", "diverged", "uninitialized", "unknown")
VERSION_PATTERN = "^2\\.(0|[1-9][0-9]*)$"
SCHEMA_ID = "https://laranex.github.io/schema-viewer-ui/laranex-schema/2.0.json"


@dataclass
class Context:
    errors: list[str]
    strict: bool


@dataclass(frozen=True)
class Shape:
    schema: dict[str, Any]
    check: Callable[[Any, str, Context], None]


def stringify(value: Any) -> str:
    """JSON.stringify for the values messages quote."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def kind_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object" if isinstance(value, dict) else type(value).__name__


def string(min_length: int | None = None, pattern: str | None = None) -> Shape:
    # JavaScript's $ never matches before a trailing newline; Python's does.
    compiled = re.compile(pattern[:-1] + r"\Z" if pattern and pattern.endswith("$") else pattern) if pattern else None
    schema: dict[str, Any] = {"type": "string"}
    if min_length is not None:
        schema["minLength"] = min_length
    if pattern is not None:
        schema["pattern"] = pattern

    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, str):
            context.errors.append(f"{path}: expected a string, got {kind_of(value)}")
        elif min_length is not None and len(value) < min_length:
            context.errors.append(f"{path}: expected at least {min_length} characters")
        elif compiled is not None and not compiled.search(value):
            context.errors.append(f"{path}: {stringify(value)} does not match {pattern}")

    return Shape(schema, check)


def boolean() -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, bool):
            context.errors.append(f"{path}: expected a boolean, got {kind_of(value)}")

    return Shape({"type": "boolean"}, check)


def integer(minimum: int | None = None) -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        whole = not isinstance(value, bool) and (
            isinstance(value, int) or (isinstance(value, float) and value.is_integer()))
        if not whole:
            context.errors.append(f"{path}: expected an integer, got {kind_of(value)}")
        elif minimum is not None and value < minimum:
            context.errors.append(f"{path}: expected at least {minimum}")

    return Shape({"type": "integer", **({"minimum": minimum} if minimum is not None else {})}, check)


def literal(expected: str) -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if value != expected or not isinstance(value, str):
            context.errors.append(f"{path}: expected {stringify(expected)}, got {stringify(value)}")

    return Shape({"const": expected}, check)


def one_of(values: tuple[str, ...] | list[str]) -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, str) or value not in values:
            context.errors.append(f"{path}: expected one of {', '.join(values)}, got {stringify(value)}")

    return Shape({"enum": list(values)}, check)


def nullable(shape: Shape) -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if value is not None:
            shape.check(value, path, context)

    return Shape({"anyOf": [shape.schema, {"type": "null"}]}, check)


def array(item: Shape, min_items: int | None = None, unique_items: bool = False) -> Shape:
    schema: dict[str, Any] = {"type": "array", "items": item.schema}
    if min_items is not None:
        schema["minItems"] = min_items
    if unique_items:
        schema["uniqueItems"] = True

    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, list):
            context.errors.append(f"{path}: expected an array, got {kind_of(value)}")
            return
        if min_items is not None and len(value) < min_items:
            context.errors.append(f"{path}: expected at least {min_items} items")
        if unique_items and len({stringify(v) for v in value}) != len(value):
            context.errors.append(f"{path}: expected unique items")
        for i, v in enumerate(value):
            item.check(v, f"{path}[{i}]", context)

    return Shape(schema, check)


def record(item: Shape, keys: tuple[str, ...] | None = None) -> Shape:
    schema: dict[str, Any] = {"type": "object"}
    if keys is not None:
        schema["propertyNames"] = {"enum": list(keys)}
    schema["additionalProperties"] = item.schema

    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, dict):
            context.errors.append(f"{path}: expected an object, got {kind_of(value)}")
            return
        for key, v in value.items():
            if keys is not None and key not in keys:
                if context.strict:
                    context.errors.append(f"{path}: unknown key {key}")
                continue
            item.check(v, f"{path}.{key}", context)

    return Shape(schema, check)


def any_json() -> Shape:
    return Shape({}, lambda value, path, context: None)


def json_object() -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, dict):
            context.errors.append(f"{path}: expected an object, got {kind_of(value)}")

    return Shape({"type": "object"}, check)


def ref(name: str, shape: Callable[[], Shape]) -> Shape:
    return Shape({"$ref": f"#/$defs/{name}"}, lambda value, path, context: shape().check(value, path, context))


def obj(fields: dict[str, Shape]) -> Shape:
    """An object with these properties; a key ending in ? is optional. Unknown properties fail strict checks."""
    entries = [(key.rstrip("?"), key.endswith("?"), shape) for key, shape in fields.items()]
    properties = {name: shape.schema for name, _, shape in entries}
    required = [name for name, optional, _ in entries if not optional]

    def check(value: Any, path: str, context: Context) -> None:
        if not isinstance(value, dict):
            context.errors.append(f"{path}: expected an object, got {kind_of(value)}")
            return
        for name, optional, shape in entries:
            if name not in value:
                if not optional:
                    context.errors.append(f"{path}: missing {name}")
                continue
            shape.check(value[name], f"{path}.{name}", context)
        if context.strict:
            for key in value:
                if key not in properties:
                    context.errors.append(f"{path}: unknown property {key}")

    return Shape({"type": "object", "properties": properties, "required": required, "additionalProperties": False},
                 check)


def _id() -> Shape:
    return string(min_length=1)


def _ids() -> Shape:
    return array(_id())


def _names() -> Shape:
    return array(string(min_length=1))


def _text() -> Shape:
    return nullable(string())


extensions = record(json_object())
provenance = array(one_of(["database", "declared"]), min_items=1, unique_items=True)
drift = obj({"field": string(min_length=1), "database": any_json(), "declared": any_json()})
physical = {
    "provenance": ref("Provenance", lambda: provenance),
    "drift?": array(ref("Drift", lambda: drift)),
    "extensions?": ref("Extensions", lambda: extensions),
}
ext = {"extensions?": ref("Extensions", lambda: extensions)}

coverage = obj({
    "complete": boolean(),
    "schemas": nullable(_names()),
    "features": record(one_of(["complete", "partial", "unavailable", "not_applicable"]), FEATURES),
})
default_value = obj({"kind": one_of(["literal", "expression", "sequence", "identity", "raw"]), "value": string()})
orm_default = obj({"kind": one_of(["scalar", "callable", "sequence", "expression"]), "value": string()})
column = obj({
    "name": string(min_length=1),
    "type": string(),
    "typeName": _text(),
    "nullable": boolean(),
    "autoIncrement": nullable(boolean()),
    "timezone": nullable(boolean()),
    "collation": _text(),
    "enum": nullable(obj({"name": _text(), "values": array(string())})),
    "default": nullable(ref("DefaultValue", lambda: default_value)),
    "ormDefault": nullable(ref("OrmDefault", lambda: orm_default)),
    "generated": nullable(obj({"kind": nullable(one_of(["stored", "virtual"])), "expression": string()})),
    "comment": _text(),
    "note": _text(),
    **physical,
})
index_options = obj({"method?": _text(), "predicate?": _text(), "included?": _names(), "options?": json_object()})
index_part = obj({
    "column": nullable(string(min_length=1)),
    "expression": nullable(string(min_length=1)),
    "order": nullable(one_of(["asc", "desc"])),
    "nulls": nullable(one_of(["first", "last"])),
    "length": nullable(integer(minimum=1)),
    "collation": _text(),
    "opclass": _text(),
})
index = obj({
    "id": _id(),
    "name": _text(),
    "parts": array(ref("IndexPart", lambda: index_part)),
    "expression": boolean(),
    "unique": boolean(),
    "backs": nullable(obj({"kind": one_of(["primary_key", "unique_constraint"]), "constraint": nullable(_id())})),
    "method": _text(),
    "partial": boolean(),
    "predicate": _text(),
    "included": _names(),
    "dialectOptions?": record(ref("IndexOptions", lambda: index_options)),
    **physical,
})
referential_action = one_of(["CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"])
foreign_key = obj({
    "id": _id(),
    "name": _text(),
    "columns": array(string(min_length=1), min_items=1),
    "foreignTable": _id(),
    "foreignColumns": array(string(min_length=1), min_items=1),
    "onUpdate": nullable(referential_action),
    "onDelete": nullable(referential_action),
    "deferrable": nullable(boolean()),
    "initially": nullable(one_of(["DEFERRED", "IMMEDIATE"])),
    **physical,
})
constraint = obj({
    "id": _id(),
    "kind": one_of(["unique", "check"]),
    "name": _text(),
    "columns": _names(),
    "expression": _text(),
    **physical,
})
table = obj({
    "id": _id(),
    "name": string(min_length=1),
    "schema": _text(),
    "comment": _text(),
    "note": _text(),
    "owner": obj({"kind": one_of(["application", "framework", "package"]), "package": _text()}),
    "primaryKey": _names(),
    "columns": array(ref("Column", lambda: column)),
    "indexes": array(ref("Index", lambda: index)),
    "foreignKeys": array(ref("ForeignKey", lambda: foreign_key)),
    "constraints": array(ref("Constraint", lambda: constraint)),
    **physical,
})
connection = obj({
    "id": _id(),
    "name": string(min_length=1),
    "driver": nullable(string(pattern="^[a-z][a-z0-9_]*$")),
    "defaultSchema": _text(),
    "coverage": obj({"database": nullable(ref("Coverage", lambda: coverage)),
                     "declared": nullable(ref("Coverage", lambda: coverage))}),
    "tables": array(ref("Table", lambda: table)),
    **ext,
})
source = obj({"file": string(min_length=1), "line": nullable(integer(minimum=1))})
model = obj({
    "id": _id(),
    "className": string(min_length=1),
    "tables": _ids(),
    "primaryKey": _names(),
    "source": nullable(ref("Source", lambda: source)),
    **ext,
})
endpoint = obj({"table": _id(), "columns": array(string(min_length=1), min_items=1)})
pivot = obj({
    "table": _id(),
    "parentColumns": _names(),
    "parentPivotColumns": _names(),
    "relatedColumns": _names(),
    "relatedPivotColumns": _names(),
    "columns": _names(),
})
relationship = obj({
    "id": _id(),
    "name": string(min_length=1),
    "model": _id(),
    "table": _id(),
    "relatedModel": nullable(_id()),
    "relatedTable": nullable(_id()),
    "type": string(min_length=1),
    "cardinality": one_of(["one-to-one", "one-to-many", "many-to-one", "many-to-many"]),
    "collection": boolean(),
    "readOnly": boolean(),
    "keys": nullable(obj({"from": ref("Endpoint", lambda: endpoint), "to": ref("Endpoint", lambda: endpoint)})),
    "pivot": nullable(ref("Pivot", lambda: pivot)),
    "polymorphic": nullable(obj({"typeColumn": string(min_length=1), "idColumn": string(min_length=1),
                                 "typeValue": _text()})),
    "through": nullable(_ids()),
    "join": nullable(obj({"primary": _text(), "secondary": _text()})),
    "constraints": array(string()),
    "inverse": nullable(_id()),
    "note": _text(),
    "source": nullable(ref("Source", lambda: source)),
    **ext,
})
link = obj({
    "id": _id(),
    "from": ref("Endpoint", lambda: endpoint),
    "to": ref("Endpoint", lambda: endpoint),
    "foreignKey": nullable(_id()),
    "relationships": _ids(),
    "state": one_of(["both", "relationship", "foreign_key"]),
    "polymorphic": boolean(),
    "note": _text(),
})
derived_link = obj({"id": _id(), "from": _id(), "to": _id(), "via": array(_id(), min_items=1),
                    "relationship": _id()})
diagnostic = obj({
    "id": _id(),
    "kind": string(min_length=1),
    "category": one_of(["extraction", "inspection", "reconciliation", "drift", "lint"]),
    "severity": one_of(["error", "warning", "info"]),
    "certainty": one_of(["certain", "likely"]),
    "subject": obj({"type": one_of(SUBJECT_TYPES), "id": nullable(_id()), "columns": nullable(_names()),
                    "label": _text()}),
    "message": string(),
    **ext,
})
severity = one_of(["error", "warning", "info"])
certainty = one_of(["certain", "likely"])
scope = obj({"connection": _id(), "schema": nullable(string(min_length=1))})
difference_ref = obj({"table": _id(), "id": nullable(_id()), "label": string(min_length=1)})
difference = obj({
    "id": _id(),
    "change": one_of(CHANGES),
    "objectType": one_of(OBJECT_TYPES),
    "key": array(string(min_length=1), min_items=1),
    "baseline": nullable(ref("DifferenceRef", lambda: difference_ref)),
    "target": nullable(ref("DifferenceRef", lambda: difference_ref)),
    "fields": array(obj({"field": string(min_length=1), "baseline": any_json(), "target": any_json()})),
    "severity": severity,
    "certainty": certainty,
    **ext,
})
comparison = obj({
    "id": _id(),
    "baseline": ref("Scope", lambda: scope),
    "target": ref("Scope", lambda: scope),
    "complete": boolean(),
    "omitted": integer(minimum=0),
    "summary": obj({"missing": integer(minimum=0), "extra": integer(minimum=0), "changed": integer(minimum=0)}),
    "differences": array(ref("Difference", lambda: difference)),
    **ext,
})
migration_history = obj({
    "revisions": array(obj({"id": _id(), "parents": _ids(), "branchLabels": _names()})),
    "heads": _ids(),
    "missing": _ids(),
})
migration_coverage = obj({
    "table": nullable(_id()),
    "name": string(min_length=1),
    "declared": boolean(),
    "autogenerate": one_of(["included", "excluded", "unknown"]),
    "migrationManaged": one_of(["yes", "no", "unknown"]),
    "certainty": certainty,
    "evidence": array(obj({"kind": one_of(["create_table", "drop_table", "include_filter", "version_table",
                                           "model"]), "detail": string()})),
})
migration_track = obj({
    "id": _id(),
    "provider": string(pattern="^[a-z][a-z0-9_]*$"),
    "connection": _id(),
    "schema": nullable(string(min_length=1)),
    "versionTable": nullable(_id()),
    "history": nullable(ref("MigrationHistory", lambda: migration_history)),
    "current": nullable(_ids()),
    "status": one_of(MIGRATION_STATUSES),
    "pending": nullable(_ids()),
    "unknownRevisions": _ids(),
    "coverage": array(ref("MigrationCoverage", lambda: migration_coverage)),
    **ext,
})
inferred_association = obj({
    "id": _id(),
    "kind": one_of(["foreign_key", "association_table"]),
    "model": _id(),
    "relatedModel": _id(),
    "table": _id(),
    "relatedTable": _id(),
    "via": nullable(_id()),
    "cardinality": one_of(["many-to-one", "one-to-one", "many-to-many"]),
    "foreignKeys": array(_id(), min_items=1),
    "links": _ids(),
    "confidence": one_of(["high", "medium", "low"]),
    "evidence": array(obj({"kind": one_of(["foreign_key", "primary_key", "unique_constraint", "unique_index",
                                           "model_mapping"]), "id": _id(), "confirmed": boolean()}), min_items=1),
    "ambiguities": array(string(pattern="^[a-z][a-z0-9_]*$")),
    **ext,
})
document = obj({
    "format": literal("laranex-schema"),
    "version": string(pattern=VERSION_PATTERN),
    "source": one_of(["declared", "database", "combined"]),
    "generator": obj({"name": string(min_length=1), "orm": _text()}),
    "note": _text(),
    "connections": array(ref("Connection", lambda: connection)),
    "models": array(ref("Model", lambda: model)),
    "relationships": array(ref("Relationship", lambda: relationship)),
    "links": array(ref("Link", lambda: link)),
    "derivedLinks": array(ref("DerivedLink", lambda: derived_link)),
    "diagnostics": array(ref("Diagnostic", lambda: diagnostic)),
    "comparisons": array(ref("Comparison", lambda: comparison)),
    "migrations": array(ref("MigrationTrack", lambda: migration_track)),
    "inferredAssociations": array(ref("InferredAssociation", lambda: inferred_association)),
    **ext,
})

DEFINITIONS = {
    "Column": column, "Comparison": comparison, "Connection": connection, "Constraint": constraint,
    "Coverage": coverage, "DefaultValue": default_value, "DerivedLink": derived_link, "Diagnostic": diagnostic,
    "Difference": difference, "DifferenceRef": difference_ref, "Drift": drift, "Endpoint": endpoint,
    "Extensions": extensions, "ForeignKey": foreign_key, "Index": index, "IndexOptions": index_options,
    "IndexPart": index_part, "InferredAssociation": inferred_association, "Link": link,
    "MigrationCoverage": migration_coverage, "MigrationHistory": migration_history, "MigrationTrack": migration_track,
    "Model": model, "OrmDefault": orm_default, "Pivot": pivot, "Provenance": provenance, "Relationship": relationship,
    "Scope": scope, "Source": source, "Table": table,
}


def json_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of laranex-schema 2.x, as the shared schema.json publishes it."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "laranex-schema 2.x document",
        **document.schema,
        "$defs": {name: shape.schema for name, shape in DEFINITIONS.items()},
    }
