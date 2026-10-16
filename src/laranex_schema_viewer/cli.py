"""Lazy command imports keep application loading out of serve and mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="laranex-schema-viewer")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Import ORM definitions and save a JSON snapshot")
    build.add_argument("--project-root", type=Path, default=Path.cwd())
    build.add_argument("--module", action="append", default=[])
    build.add_argument("--target", action="append", default=[], help="module:metadata/base/registry")
    build.add_argument("--recursive", action="store_true", help="Import every submodule of selected packages")
    build.add_argument("--output", type=Path, default=Path("schema-viewer.json"))
    serve = commands.add_parser("serve", help="Serve the viewer from a saved snapshot only")
    serve.add_argument("--snapshot", type=Path, default=Path("schema-viewer.json"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    mcp = commands.add_parser("mcp", help="Run read-only snapshot MCP tools over stdio")
    mcp.add_argument("--snapshot", type=Path, default=Path("schema-viewer.json"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            from .build import build_document
            from .snapshot import write_snapshot

            document = build_document(args.module, project_root=args.project_root,
                                      recursive=args.recursive, targets=args.target)
            write_snapshot(document, args.output)
            print(f"Saved {len(document.tables)} tables, {len(document.models)} models, "
                  f"{len(document.relationships)} ORM relationships, "
                  f"{len(document.diagnostics)} diagnostics to {args.output}")
        elif args.command == "serve":
            from .server import create_server

            with create_server(args.snapshot, args.host, args.port) as server:
                print(f"Laranex Schema Viewer: http://{args.host}:{server.server_port}", flush=True)
                server.serve_forever()
        else:
            from .mcp import create_server

            create_server(args.snapshot).run(transport="stdio")
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0

