"""The introspection worker: reads database catalogs and never imports application code.

Started by runner.py as ``python -I -B -c <bootstrap> --result PATH`` from an empty directory, so neither the project
nor PYTHONPATH is on sys.path. The request, including the database URL, arrives on stdin. Before it answers, the worker
checks that no module was loaded from the project directories it was given, and fails if one was.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def loaded_from(roots: list[str]) -> list[str]:
    """Modules imported from the project, other than this package and environments that happen to live inside it."""
    projects = [Path(r).resolve() for r in roots]
    allowed = {Path(p).resolve() for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}
    allowed.add(Path(__file__).resolve().parents[1])
    found = []
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not file:
            continue
        path = Path(file).resolve()
        if any(p == path or p in path.parents for p in allowed):
            continue
        if any(p in path.parents for p in projects):
            found.append(name)
    return sorted(found)


def scrub(message: str, url: str) -> str:
    """Remove the URL and its password from a message before it leaves the worker."""
    from sqlalchemy.engine import make_url

    message = message.replace(url, "<database url>")
    try:
        password = make_url(url).password
    except Exception:  # noqa: BLE001
        password = None
    # Very short passwords would mask fragments of ordinary words; they are not worth hiding anyway.
    if password and len(str(password)) >= 4:
        message = message.replace(str(password), "***")
    return message


def run(request: dict[str, Any]) -> dict[str, Any]:
    """The database as laranex-schema 2.0 JSON (source: database)."""
    from .. import model as m
    from ..adapters import connection_id
    from ..contract import document_to_json
    from . import IntrospectionError
    from .catalog import inspect_database
    from .readonly import create_engine, privilege_problems, read_only

    engine = create_engine(request["url"], request["statement_timeout"])
    try:
        with read_only(engine, request["statement_timeout"]) as connection:
            dialect = connection.dialect.name
            schemas = list(request.get("schemas") or [])
            if dialect == "postgresql" and not schemas:
                from sqlalchemy import text

                schemas = [connection.execute(text("SELECT current_schema()")).scalar()]
            problems = privilege_problems(connection, schemas)
            diagnostics: list[m.Diagnostic] = []
            if problems and not request.get("allow_write_privileges"):
                raise IntrospectionError(
                    "The database role can change the schema or data: " + "; ".join(problems) + ". Inspect with a "
                    "read-only role (see docs/database.md), or pass --allow-write-privileges to inspect anyway inside "
                    "read-only transactions.")
            if problems:
                diagnostics.append(m.Diagnostic(
                    kind="write_privileges", category="inspection", severity="warning", certainty="certain",
                    subject=m.Subject(type="connection", id=connection_id(request["connection"]),
                                      label=request["connection"]),
                    message="Inspected with a role that can change the schema or data (" + "; ".join(problems) +
                            "); the session was read-only."))
            document = inspect_database(connection, name=request["connection"], schemas=schemas,
                                        diagnostics=diagnostics)
    finally:
        engine.dispose()
    return document_to_json(document)


def main() -> None:
    parser = argparse.ArgumentParser(prog="laranex_schema_viewer.introspection.worker")
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request = json.loads(sys.stdin.read())
    try:
        document = run(request)
        imported = loaded_from(request.get("project_roots", []))
        if imported:
            result = {"ok": False, "error": "The introspection worker loaded modules from the project "
                                            f"({', '.join(imported[:5])}); nothing was returned"}
        else:
            result = {"ok": True, "document": document}
    except BaseException as error:  # noqa: BLE001 - every failure is reported to the parent, scrubbed
        result = {"ok": False, "error": scrub(f"{type(error).__name__}: {error}", request.get("url", ""))}
    data = json.dumps(result, ensure_ascii=False).encode("utf-8")
    fd = os.open(args.result, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os._exit(0)


if __name__ == "__main__":
    main()
