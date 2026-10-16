"""Explicit application imports. Never imported by viewer or MCP paths."""

from __future__ import annotations

import importlib
import pkgutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from .document import SchemaDocument

# ORM name -> top-level package that application imports must already have loaded.
ADAPTERS = {"sqlalchemy": "sqlalchemy", "tortoise": "tortoise"}
# Django loads models from settings during django.setup(), so it is selected, not detected.
ORMS = ("auto", *ADAPTERS, "django")


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


def adapter(orm: str) -> ModuleType:
    return importlib.import_module(f".adapters.{orm}", __package__)


def import_modules(modules: list[str], recursive: bool) -> dict[str, ModuleType]:
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
    return loaded


def detect_orm(loaded: dict[str, ModuleType]) -> str:
    """Choose the ORM whose models the imported modules declare, without importing new ORMs."""
    found = [orm for orm, package in ADAPTERS.items()
             if package in sys.modules and adapter(orm).defines_models(loaded)]
    if len(found) > 1:
        raise ValueError(f"Modules declare models for several ORMs ({', '.join(found)}); choose one with --orm")
    if not found:
        raise ValueError("No SQLAlchemy metadata or ORM models found in the selected modules")
    return found[0]


def build_document(
    modules: list[str], *, project_root: Path, recursive: bool = False,
    targets: list[str] | None = None, orm: str = "auto", settings: str | None = None,
) -> SchemaDocument:
    """Load named modules and extract them with the selected or detected ORM adapter.

    Run this in a fresh process: Python caches imports and ORMs keep registries.
    Imports are trusted application code, not a sandbox. A Django settings module
    selects Django; ``modules`` then optionally limit which apps are extracted.
    """
    if orm not in ORMS:
        raise ValueError(f"Unsupported ORM {orm!r}; choose one of {', '.join(ORMS)}")
    if orm == "django" or (orm == "auto" and settings):
        if targets:
            raise ValueError("--target applies only to SQLAlchemy, SQLModel, and Tortoise")
        root = project_root.resolve()
        with import_context(root):
            return adapter("django").document_from_settings(
                settings=settings, modules=modules, project_root=root)
    if settings:
        raise ValueError("--settings applies only to Django")
    if not modules and not targets:
        raise ValueError("Supply at least one --module or --target")
    if targets and orm not in ("auto", "sqlalchemy", "tortoise"):
        raise ValueError("--target applies only to SQLAlchemy, SQLModel, and Tortoise")
    root = project_root.resolve()
    with import_context(root):
        loaded = import_modules(modules, recursive)
        if orm == "auto":
            orm = "sqlalchemy" if targets else detect_orm(loaded)
        return adapter(orm).document_from_modules(loaded, project_root=root, targets=targets or [])
