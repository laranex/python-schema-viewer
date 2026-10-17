"""The serialization boundary: the schema model (``model.py``) to and from laranex-schema 2.0 JSON, and canonical bytes.

This is the only module that knows the document's field names. Attribute names map to camelCase keys, except the
endpoints of keys, links and derived links (``source``/``target`` are ``from``/``to``). Drift field names follow the
same mapping. Optional fields the contract leaves out (``drift`` when empty, ``extensions``, ``dialectOptions`` and
index options when unset) are absent, never null. Diagnostic ids are computed from their content.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import typing
from collections.abc import Mapping
from functools import cache
from typing import Any, TypeVar

from .. import model as m

FORMAT = "laranex-schema"
VERSION = "2.0"
MAJOR = 2

RENAMES: dict[type, dict[str, str]] = {
    m.Keys: {"source": "from", "target": "to"},
    m.Link: {"source": "from", "target": "to"},
    m.DerivedLink: {"source": "from", "target": "to"},
}
OMIT_EMPTY = {"drift"}
OMIT_NONE = {"extensions", "dialect_options"}

T = TypeVar("T")


def camel(name: str) -> str:
    return re.sub(r"_([a-z0-9])", lambda found: found.group(1).upper(), name)


def snake(name: str) -> str:
    return re.sub(r"[A-Z]", lambda found: "_" + found.group(0).lower(), name)


def _key(cls: type, name: str) -> str:
    return RENAMES.get(cls, {}).get(name) or camel(name)


def to_json(value: Any) -> Any:
    """JSON data for a model object (or a tuple, list or mapping of them)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        out: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            item = getattr(value, f.name)
            if (f.name in OMIT_EMPTY and not item) or (item is None and (f.name in OMIT_NONE or cls is m.IndexOptions)):
                continue
            out[_key(cls, f.name)] = to_json(item)
        if cls is m.Drift:
            out["field"] = camel(value.field)
        elif cls is m.Diagnostic:
            out["id"] = value.id
        elif cls is m.Document:
            out.update(format=FORMAT, version=VERSION)
        return out
    if isinstance(value, (tuple, list)):
        return [to_json(v) for v in value]
    if isinstance(value, Mapping):
        return {k: to_json(v) for k, v in value.items()}
    return value


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls, vars(m))


def _convert(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if dataclasses.is_dataclass(annotation):
        return from_json(annotation, value)
    if origin is tuple:
        return tuple(_convert(args[0], v) for v in value)
    if origin in (Mapping, dict) or annotation is Mapping:
        inner = args[1] if len(args) == 2 else Any
        return {k: _convert(inner, v) for k, v in value.items()}
    if args and type(None) in args:  # X | None
        return _convert(next(a for a in args if a is not type(None)), value)
    return value


def from_json(cls: type[T], data: Mapping[str, Any]) -> T:
    """A model object from validated JSON data. Properties the model does not know are ignored (lenient readers)."""
    hints = _hints(cls)
    values = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        key = _key(cls, f.name)
        if key in data:
            values[f.name] = _convert(hints[f.name], data[key])
    if cls is m.Drift:
        values["field"] = snake(values["field"])
    return cls(**values)


def document_to_json(document: m.Document) -> dict[str, Any]:
    return to_json(document)


def document_from_json(data: Mapping[str, Any]) -> m.Document:
    return from_json(m.Document, data)


def canonical_json(data: Any) -> str:
    """Keys sorted by code point, two-space indentation, nothing escaped that need not be, a trailing newline."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def revision(data: Any) -> str:
    """The SHA-256 of a document's canonical bytes."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def document_bytes(document: m.Document) -> bytes:
    return canonical_json(document_to_json(document)).encode("utf-8")
