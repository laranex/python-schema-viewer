"""Runs the introspection worker in its own process and validates the document it returns."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from .. import model as m
from ..isolation.env import ALLOWED_PREFIXES
from ..isolation.runner import MAX_RESULT_BYTES, _kill_group, _log_tail
from . import DatabaseRequest, IntrospectionError

# What a database driver may need; never PYTHONPATH or application settings.
ENVIRONMENT = {"PATH", "LANG", "LANGUAGE", "TZ", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SSL_CERT_FILE",
               "SSL_CERT_DIR", "PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY", "PGSSLCRL", "PGGSSENCMODE"}
BOOTSTRAP = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
             "from laranex_schema_viewer.introspection.worker import main; main()")


def introspect(request: DatabaseRequest, *, project_roots: list[str] | tuple[str, ...] = ()) -> m.Document:
    """The database as a database document, validated strictly, or IntrospectionError."""
    from ..contract import assert_document

    scratch = Path(tempfile.mkdtemp(prefix="lsv-introspect-")).resolve()
    try:
        home, out, log = scratch / "home", scratch / "out", scratch / "output.log"
        home.mkdir()
        out.mkdir()
        env = {k: v for k, v in os.environ.items() if k in ENVIRONMENT or k.startswith(ALLOWED_PREFIXES)}
        env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home), TEMP=str(home), TMP=str(home))
        package_root = str(Path(__file__).resolve().parents[2])
        command = [sys.executable, "-I", "-B", "-c", BOOTSTRAP, package_root, "--result", str(out / "result.json")]
        payload = {**asdict(request), "project_roots": [str(Path(r).resolve()) for r in project_roots]}
        posix = os.name == "posix"
        with open(log, "wb") as output:
            process = subprocess.Popen(
                command, cwd=scratch, env=env, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=posix,
                creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
            )
            try:
                process.stdin.write(json.dumps(payload).encode())  # type: ignore[union-attr]
                process.stdin.close()  # type: ignore[union-attr]
                process.wait(timeout=request.timeout)
            except subprocess.TimeoutExpired:
                raise IntrospectionError(f"Database introspection did not finish within {request.timeout:g} "
                                         "seconds and was stopped") from None
            finally:
                _kill_group(process)
                process.wait()
        path = out / "result.json"
        if not path.is_file():
            tail = _log_tail(log).strip()
            raise IntrospectionError("The introspection worker exited without a result" +
                                     (f":\n{_scrub(tail, request.url)}" if tail else ""))
        if path.stat().st_size > MAX_RESULT_BYTES:
            raise IntrospectionError("The introspection worker's result exceeds 32 MiB")
        result = json.loads(path.read_bytes())
        if not result.get("ok"):
            raise IntrospectionError(_scrub(str(result.get("error")), request.url))
        try:
            document = assert_document(result["document"], strict=True)
        except ValueError as error:
            raise IntrospectionError(f"The introspection worker returned an invalid document: {error}") from None
        if document.source != "database":
            raise IntrospectionError("The introspection worker returned a document that is not database metadata")
        return document
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def introspect_all(requests: list[DatabaseRequest] | tuple[DatabaseRequest, ...], *,
                   project_roots: list[str] | tuple[str, ...] = ()) -> m.Document:
    """Several connections as one database document. Each connection is read by its own worker, which receives only
    that connection's URL; one failure fails the whole inspection."""
    names = [r.connection for r in requests]
    if not names or len(set(names)) != len(names):
        raise IntrospectionError("Inspect at least one connection, each under a distinct name")
    documents = [introspect(r, project_roots=project_roots) for r in requests]
    return merge(documents)


def merge(documents: list[m.Document]) -> m.Document:
    """Database documents with distinct connection names as one document."""
    from ..contract import finalize

    return finalize(m.replace(documents[0], connections=tuple(c for d in documents for c in d.connections),
                              diagnostics=tuple(x for d in documents for x in d.diagnostics)))


def _scrub(message: str, url: str) -> str:
    from .worker import scrub

    return scrub(message, url)
