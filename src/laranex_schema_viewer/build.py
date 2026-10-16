"""Explicit application imports. Never imported by viewer or MCP paths."""

from __future__ import annotations

import importlib
import inspect as python_inspect
import pkgutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .document import SchemaDocument


@contextmanager
def import_context(project_root: Path) -> Iterator[None]:
    previous_path = sys.path[:]
    previous_bytecode = sys.dont_write_bytecode
    sys.path.insert(0, str(project_root))
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.path[:] = previous_path
        sys.dont_write_bytecode = previous_bytecode


def build_document(
    modules: list[str], *, project_root: Path, recursive: bool = False,
    targets: list[str] | None = None,
) -> SchemaDocument:
    """Load named modules and optional module:metadata/base/registry targets.

    Run this in a fresh process: Python caches imports and ORMs keep registries.
    Imports are trusted application code, not a sandbox.
    """
    from sqlalchemy import MetaData, inspect
    from sqlalchemy.orm import Mapper, registry

    from .adapters.sqlalchemy import SQLAlchemyAdapter

    if not modules and not targets:
        raise ValueError("Supply at least one --module or --target")
    metadata: dict[str, MetaData] = {}
    classes: set[type] = set()

    def add_metadata(value: MetaData, name: str) -> None:
        if not any(m is value for m in metadata.values()):
            metadata[name] = value

    with import_context(project_root.resolve()):
        loaded = {}
        for name in modules:
            loaded[name] = importlib.import_module(name)
            if recursive and hasattr(loaded[name], "__path__"):
                def fail(package: str) -> None:
                    raise ImportError(f"Cannot discover package {package}")
                for item in sorted(pkgutil.walk_packages(
                    loaded[name].__path__, name + ".", onerror=fail,
                ), key=lambda item: item.name):
                    loaded[item.name] = importlib.import_module(item.name)
        for name, module in sorted(loaded.items()):
            for attribute, value in sorted(vars(module).items()):
                if isinstance(value, MetaData):
                    add_metadata(value, f"{name}:{attribute}")
                elif python_inspect.isclass(value):
                    mapper = inspect(value, raiseerr=False)
                    if isinstance(mapper, Mapper):
                        classes.add(value)
                        for table in mapper.tables:
                            add_metadata(table.metadata, f"{name}:{attribute}")
        for target in targets or []:
            module_name, separator, attribute = target.partition(":")
            if not separator or not attribute:
                raise ValueError("Targets use module:attribute syntax")
            value = getattr(importlib.import_module(module_name), attribute)
            if isinstance(value, registry):
                add_metadata(value.metadata, target)
                classes.update(m.class_ for m in value.mappers)
            elif isinstance(value, MetaData):
                add_metadata(value, target)
            elif python_inspect.isclass(value) and isinstance(
                getattr(value, "metadata", None), MetaData,
            ):
                add_metadata(value.metadata, target)
                if hasattr(value, "registry"):
                    classes.update(m.class_ for m in value.registry.mappers)
                mapper = inspect(value, raiseerr=False)
                if isinstance(mapper, Mapper):
                    classes.add(value)
            else:
                raise ValueError(f"Unsupported metadata/base/registry target: {target}")
        if not metadata:
            raise ValueError("No SQLAlchemy metadata or mapped classes found")
        # Stable names independent of which module first imports a shared registry.
        named = {"default" if len(metadata) == 1 else name: value
                 for name, value in metadata.items()}
        return SQLAlchemyAdapter(named, list(classes), project_root.resolve()).extract()

