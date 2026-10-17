"""Isolated execution for the build.

Two workers with different powers:

- the extraction worker imports application code and returns declared ORM metadata. It runs with a filtered
  environment, so database credentials configured for introspection never reach it;
- the introspection worker (introspection.py) reads the database's catalogs with read-only credentials and never
  imports application code.

Isolation levels for the extraction worker:

- ``process``: a separate Python process with an allowlisted environment, its own HOME and TMPDIR, a timeout, and
  cleanup of its whole process group. Application code can still read files, open sockets and write files the user
  can write, apart from what the best-effort guards catch.
- ``container``: Docker or Podman with no network, a read-only project mount and root filesystem, only a tmpfs and
  a disposable output directory writable, all capabilities dropped, and no new privileges.

A level that cannot be established fails the build. Nothing falls back to a weaker level.
See docs/security.md for what is guaranteed and what is best-effort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LEVELS = ("process", "container")
Level = Literal["process", "container"]


class IsolationError(RuntimeError):
    """A requested protection could not be established, or the isolated worker failed."""


@dataclass(frozen=True)
class IsolationConfig:
    level: Level = "process"
    timeout: float = 120.0
    # Environment variables the extraction worker may see, beyond the built-in allowlist.
    env_passthrough: tuple[str, ...] = ()
    # Fixed values for the worker, such as a placeholder SECRET_KEY that settings modules require.
    env: tuple[tuple[str, str], ...] = ()
    # Directories (relative to the project root) application code may write to during extraction, such as logs/.
    allow_writes: tuple[str, ...] = ()
    container_image: str | None = None
    container_runtime: str | None = None
    container_memory: str = "1g"


@dataclass(frozen=True)
class ExtractRequest:
    project_root: str
    modules: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    recursive: bool = False
    orm: str = "auto"
    settings: str | None = None


@dataclass
class ExtractResult:
    document: dict
    blocked: list[dict[str, str]] = field(default_factory=list)
    # How many of the parent's environment variables the worker did not get; names are never reported.
    withheld: int = 0
