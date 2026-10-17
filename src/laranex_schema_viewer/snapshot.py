"""Saved laranex-schema 2.0 documents: bounded reading with validation, strict validation before atomic writes.

This module never imports an ORM.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import (FORMAT, MAJOR, assert_document, canonical_json, document_to_json, revision, supported)
from .model import Document

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
REBUILD = "rebuild it with `laranex-schema-viewer build`, which writes laranex-schema 2.0"


@dataclass(frozen=True)
class Snapshot:
    """A saved document as serve and mcp use it: the model, the validated JSON as read, and its revision."""

    document: Document
    data: dict[str, Any]
    revision: str


def read_document(path: str | Path) -> Snapshot:
    """A laranex-schema 2.x document, validated as readers do (any 2.x minor, unknown properties ignored)."""
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds 32 MiB limit")
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{path} is not JSON: {error}") from None
    problem = supported(data)
    if problem is not None:
        if isinstance(data, dict) and data.get("format") == "laranex-schema-viewer":
            raise ValueError(f"{path} is a version 1 snapshot, which this version no longer reads; {REBUILD}")
        if isinstance(data, dict) and data.get("format") == FORMAT:
            raise ValueError(f"{path}: {problem}; {REBUILD}")
        raise ValueError(f"{path} is not a {FORMAT} {MAJOR}.x document")
    return Snapshot(document=assert_document(data), data=data, revision=revision(data))


def write_document(document: Document, path: str | Path) -> None:
    """A document, strictly validated, as canonical JSON."""
    data = document_to_json(document)
    assert_document(data, strict=True)
    write_atomically(canonical_json(data).encode("utf-8"), path)


def write_atomically(data: bytes, path: str | Path) -> None:
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds 32 MiB limit")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".schema-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
