"""Best-effort guards inside the extraction worker. NOT a security boundary.

Application code runs in the same interpreter as these guards, so it can evade them: native extensions and ctypes
bypass audit events, and monkey-patched ORM methods can be restored. They exist to stop well-behaved code from
reaching the outside world by accident and to report what was attempted. The guarantees come from the worker's
process or container boundary (see docs/security.md).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Audit events that reach the network, start programs, or write and remove files.
NETWORK = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.bind",
           "socket.sendto", "socket.sendmsg"}
PROCESSES = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
             "os.startfile", "pty.spawn"}
FILE_CHANGES = {"os.remove", "os.rename", "os.replace", "os.mkdir", "os.rmdir", "os.symlink", "os.link", "os.chmod",
                "os.chown", "os.truncate", "os.utime", "shutil.rmtree", "shutil.copyfile", "shutil.copytree",
                "shutil.move"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


class GuardViolation(PermissionError):
    """Raised inside the worker when application code attempts a blocked operation."""


@dataclass
class Guards:
    allowed_write_dirs: list[Path]
    blocked: list[dict[str, str]] = field(default_factory=list)

    def writable(self, path: Any) -> bool:
        if isinstance(path, int):
            return True  # An already open descriptor; opening it was checked.
        try:
            resolved = Path(os.fsdecode(path)).resolve()
        except (TypeError, ValueError, OSError):
            return False
        if str(resolved) in (os.devnull, "/dev/null"):
            return True
        return any(resolved == d or d in resolved.parents for d in self.allowed_write_dirs)

    def deny(self, event: str, detail: str) -> None:
        self.blocked.append({"event": event, "detail": detail[:300]})
        raise GuardViolation(f"Schema extraction blocked {event} ({detail[:120]}); see docs/security.md")

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event in NETWORK:
            self.deny(event, repr(args[1:2] if event == "socket.connect" else args[:1]))
        elif event in PROCESSES:
            self.deny(event, repr(args[:1]))
        elif event == "sqlite3.connect":
            database = os.fsdecode(args[0]) if args and isinstance(args[0], (str, bytes, os.PathLike)) else ""
            if database not in (":memory:", "") and "mode=memory" not in database:
                self.deny(event, database)
        elif event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & WRITE_FLAGS)
            if writing and not self.writable(path):
                self.deny(event, os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else repr(path))
        elif event in FILE_CHANGES:
            targets = [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
            if not targets or not all(self.writable(t) for t in targets):
                self.deny(event, repr(targets[:2]))


def install(allowed_write_dirs: list[str]) -> Guards:
    """Install the audit hook and the ORM connection guards. Audit hooks cannot be removed once added."""
    guards = Guards([Path(d).resolve() for d in allowed_write_dirs])
    sys.addaudithook(guards.audit)
    _guard_orm_connections(guards)
    return guards


def _guard_orm_connections(guards: Guards) -> None:
    """Make the ORMs' own connect calls fail, so a guard trips before a driver loads native code.

    The ORMs are patched when the application imports them, never imported here: build.detect_orm tells ORMs apart
    by which ones the application loaded.
    """

    def refuse(name: str):
        def method(*_args: Any, **_kwargs: Any) -> Any:
            guards.deny("orm.connect", name)
        return method

    def sqlalchemy(module: Any) -> None:
        for name in ("connect", "raw_connection", "begin"):
            setattr(module.Engine, name, refuse(f"sqlalchemy Engine.{name}"))

    def peewee(module: Any) -> None:
        module.Database.connect = refuse("peewee Database.connect")

    def tortoise(module: Any) -> None:
        for name in ("init", "generate_schemas"):
            setattr(module.Tortoise, name, classmethod(refuse(f"tortoise Tortoise.{name}")))

    # Django's connections are blocked by adapters.django.connections_blocked() during extraction.
    sys.meta_path.insert(0, _PatchOnImport({"sqlalchemy.engine.base": sqlalchemy, "peewee": peewee, "tortoise": tortoise}))


class _PatchOnImport:
    """A meta path finder that patches chosen modules right after they are first executed."""

    def __init__(self, patches: dict[str, Any]) -> None:
        self.patches = patches

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        patch = self.patches.get(fullname)
        if patch is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None and hasattr(spec.loader, "exec_module"):
                original = spec.loader.exec_module

                def exec_module(module: Any, original: Any = original) -> None:
                    original(module)
                    patch(module)

                spec.loader.exec_module = exec_module  # type: ignore[method-assign]
                return spec
        return None
