"""Canonical laranex-schema bytes, revisions and hashed ids, identical to the TypeScript implementation."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    """Keys sorted by code point, two-space indentation, nothing escaped that need not be, a trailing newline."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def revision(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def hash_id(prefix: str, parts: list[Any]) -> str:
    """A short stable id from compact JSON, as TypeScript's JSON.stringify writes it."""
    text = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
