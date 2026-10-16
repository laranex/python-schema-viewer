"""Validated, deterministic snapshots. This module never imports an ORM."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .document import SchemaDocument

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def snapshot_bytes(document: SchemaDocument) -> bytes:
    return (json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n").encode("utf-8")


def revision(document: SchemaDocument) -> str:
    return hashlib.sha256(snapshot_bytes(document)).hexdigest()


def read_snapshot(path: str | Path) -> SchemaDocument:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds 32 MiB limit")
    return SchemaDocument.model_validate_json(data)


def write_snapshot(document: SchemaDocument, path: str | Path) -> None:
    # Validate even programmatically constructed documents before persisting.
    document = SchemaDocument.model_validate_json(document.model_dump_json())
    data = snapshot_bytes(document)
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

