"""The extraction worker: imports application code in its own process (or container) and returns declared metadata.

Run as ``python -B -m laranex_schema_viewer.isolation.worker --result PATH --allow-write DIR ...`` with the request on
stdin. It never receives database credentials (the parent filters its environment) and never opens a connection on
purpose. The result goes to a file, not stdout, which application code may write to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="laranex_schema_viewer.isolation.worker")
    parser.add_argument("--result", required=True, help="Where to write the result JSON")
    parser.add_argument("--allow-write", action="append", default=[], help="Directories the guards let code write to")
    args = parser.parse_args()
    request = json.loads(sys.stdin.read())

    from .guards import install

    guards = install(args.allow_write)
    try:
        from ..build import build_document

        document = build_document(
            request.get("modules", []), project_root=Path(request["project_root"]),
            recursive=request.get("recursive", False), targets=request.get("targets", []),
            orm=request.get("orm", "auto"), settings=request.get("settings"),
        )
        result = {"ok": True, "document": document.model_dump(mode="json"), "blocked": guards.blocked}
    except BaseException as error:  # noqa: BLE001 - every failure is reported to the parent
        result = {"ok": False, "error": f"{type(error).__name__}: {error}", "blocked": guards.blocked}
    data = json.dumps(result, ensure_ascii=False).encode("utf-8")
    fd = os.open(args.result, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    sys.stdout.flush()
    sys.stderr.flush()
    # Leave without running the application's atexit hooks or waiting for its threads.
    os._exit(0)


if __name__ == "__main__":
    main()
