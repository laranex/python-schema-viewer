"""Adapters only run while building. Each ORM adapter module provides:

- ``defines_models(loaded)``: whether imported modules declare models for that ORM.
- ``document_from_modules(loaded, *, project_root, targets)``: extract a SchemaDocument.

Shared helpers here must not import any ORM.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Protocol

from ..document import SchemaDocument, Source


class SchemaAdapter(Protocol):
    def extract(self) -> SchemaDocument: ...


def identifier(*parts: str | None) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def callable_name(value: object) -> str:
    """Name a callable default without calling it; builtins such as datetime.now have no module."""
    name = getattr(value, "__qualname__", type(value).__name__)
    module = getattr(value, "__module__", None)
    return f"{module}.{name}" if module else name


def class_source(cls: type, project_root: Path | None) -> Source | None:
    """Source location relative to the project; never publish paths outside it."""
    try:
        filename = inspect.getsourcefile(cls)
        if filename is None or project_root is None:
            return None
        path = Path(filename).resolve()
        if not path.is_relative_to(project_root):
            return None
        return Source(file=path.relative_to(project_root).as_posix(),
                      line=inspect.getsourcelines(cls)[1])
    except (OSError, TypeError):
        return None
