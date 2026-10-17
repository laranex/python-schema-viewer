"""Validated, deterministic snapshots. This module never imports an ORM."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

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
    if b'"laranex-schema"' in data:
        try:
            unified = json.loads(data).get("format") == "laranex-schema"
        except (ValueError, AttributeError):
            unified = False
        if unified:
            raise ValueError(f"{path} is already a laranex-schema document, not a version 1 snapshot")
    return SchemaDocument.model_validate_json(data)


class Loaded:
    """A saved document as serve and mcp use it: a laranex-schema document, or a version 1 snapshot that is converted
    explicitly with unified.from_v1 only when the unified form is asked for (MCP answers v1 snapshots from v1)."""

    def __init__(self, format: str, *, unified: dict[str, Any] | None = None, v1: SchemaDocument | None = None):
        self.format, self.v1, self._unified = format, v1, unified

    @property
    def unified(self) -> dict[str, Any]:
        if self._unified is None:
            from .unified import from_v1

            self._unified = from_v1(self.v1)  # type: ignore[arg-type]
        return self._unified


def load_document(path: str | Path) -> Loaded:
    """A version 1 snapshot or a laranex-schema 1.x document, validated (laranex-schema leniently, as a reader)."""
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds 32 MiB limit")
    try:
        parsed = json.loads(data)
    except ValueError as error:
        raise ValueError(f"{path} is not JSON: {error}") from None
    if isinstance(parsed, dict) and parsed.get("format") == "laranex-schema":
        from .contract import assert_document

        return Loaded("laranex-schema", unified=assert_document(parsed).to_json())
    return Loaded("v1", v1=SchemaDocument.model_validate_json(data))


def write_snapshot(document: SchemaDocument, path: str | Path) -> None:
    # Validate even programmatically constructed documents before persisting.
    document = SchemaDocument.model_validate_json(document.model_dump_json())
    write_atomically(snapshot_bytes(document), path)


def write_unified(document: dict, path: str | Path) -> None:
    """A laranex-schema document, strictly validated, in canonical JSON."""
    from .contract import assert_document, canonical_json

    write_atomically(canonical_json(assert_document(document, strict=True).to_json()).encode("utf-8"), path)


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
