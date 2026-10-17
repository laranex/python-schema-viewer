"""Opaque ids the contract defines by rule: a prefix and the first 16 hex digits of the SHA-256 of compact JSON parts,
exactly as laranex-schema-viewer-ui's ids.ts and graph.ts compute them."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any


def hash_id(prefix: str, parts: list[Any]) -> str:
    """Compact JSON as JavaScript's JSON.stringify writes it: no whitespace, slashes and non-ASCII unescaped."""
    text = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def link_id(from_table: str, to_table: str, pairs: Sequence[Sequence[str]]) -> str:
    return hash_id("l", ["link", from_table, to_table, [list(p) for p in pairs]])


def derived_link_id(relationship: str) -> str:
    return hash_id("d", ["derived", relationship])


def diagnostic_id(kind: str, subject_type: str, subject_id: str | None, columns: Sequence[str] | None,
                  message: str) -> str:
    return hash_id("n", ["diagnostic", kind, subject_type, subject_id,
                         None if columns is None else list(columns), message])


def comparison_id(baseline_connection: str, baseline_schema: str | None, target_connection: str,
                  target_schema: str | None) -> str:
    return hash_id("c", ["comparison", baseline_connection, baseline_schema, target_connection, target_schema])


def difference_id(comparison: str, object_type: str, key: Sequence[str]) -> str:
    return hash_id("x", ["difference", comparison, object_type, list(key)])


def migration_track_id(provider: str, connection: str, schema: str | None) -> str:
    return hash_id("m", ["migration", provider, connection, schema])


def inferred_association_id(kind: str, foreign_keys: Iterable[str], via: str | None) -> str:
    return hash_id("i", ["inferred", kind, sorted(foreign_keys), via])


def sorted_strings(values: Iterable[str]) -> list[str]:
    """Unique strings in Unicode code point order (Python's str order)."""
    return sorted(set(values))


def by_list(a: Sequence[str], b: Sequence[str]) -> int:
    """Element-wise code point order of string lists; a prefix sorts first."""
    for x, y in zip(a, b):
        if x != y:
            return -1 if x < y else 1
    return len(a) - len(b)
