"""Lazy command imports keep application loading out of serve and mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import (DEFAULT_SNAPSHOT, add_config, config_section, detect_model_module, load_config,
                     resolve_snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="laranex-schema-viewer")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Add build defaults to pyproject.toml interactively")
    init.add_argument("--project-root", type=Path, default=Path.cwd())
    init.add_argument("--yes", action="store_true", help="Accept detected defaults without prompting")
    build = commands.add_parser("build", help="Import ORM definitions and save a JSON snapshot")
    build.add_argument("--project-root", type=Path, default=Path.cwd())
    build.add_argument("--module", action="append", default=[],
                       help="Default: pyproject.toml config, else the detected models package")
    build.add_argument("--target", action="append", default=[], help="module:metadata/base/registry")
    build.add_argument("--recursive", action="store_true", help="Import every submodule of selected packages")
    build.add_argument("--orm", help="auto (default), sqlalchemy; default: pyproject.toml config, else auto")
    build.add_argument("--output", type=Path, help="Default: pyproject.toml config, else schema-viewer.json")
    serve = commands.add_parser("serve", help="Serve the viewer from a saved snapshot only")
    serve.add_argument("--snapshot", type=Path, help="Default: pyproject.toml output, else schema-viewer.json")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    mcp = commands.add_parser("mcp", help="Run read-only snapshot MCP tools over stdio")
    mcp.add_argument("--snapshot", type=Path, help="Default: pyproject.toml output, else schema-viewer.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            try:
                detected = detect_model_module(args.project_root)
            except ValueError:
                detected = ""

            def ask(question: str, default: str) -> str:
                if args.yes:
                    return default
                answer = input(f"{question} [{default}]: " if default else f"{question}: ")
                return answer.strip() or default

            modules = [name.strip() for name in ask("Model modules, comma-separated", detected)
                       .split(",") if name.strip()]
            recursive = ask("Import submodules recursively? (y/n)", "y").lower().startswith("y")
            output = ask("Snapshot output path", DEFAULT_SNAPSHOT.as_posix())
            path = add_config(args.project_root, config_section(modules, recursive, output))
            print(f"Added [tool.laranex-schema-viewer] to {path}. Next: laranex-schema-viewer build")
        elif args.command == "build":
            from .build import build_document
            from .snapshot import write_snapshot

            config = load_config(args.project_root)
            modules, targets, recursive = args.module, args.target, args.recursive
            if not modules and not targets:
                modules, targets = config.modules, config.targets
                recursive = recursive or config.recursive
            if not modules and not targets:
                modules, recursive = [detect_model_module(args.project_root)], True
                print(f"Detected model module {modules[0]}; importing it recursively")
            output = args.output or config.output or DEFAULT_SNAPSHOT
            document = build_document(modules, project_root=args.project_root,
                                      recursive=recursive, targets=targets,
                                      orm=args.orm or config.orm or "auto")
            write_snapshot(document, output)
            print(f"Saved {len(document.tables)} tables, {len(document.models)} models, "
                  f"{len(document.relationships)} ORM relationships, "
                  f"{len(document.diagnostics)} diagnostics to {output}")
        elif args.command == "serve":
            from .server import create_server

            snapshot = resolve_snapshot(args.snapshot, Path.cwd())
            with create_server(snapshot, args.host, args.port) as server:
                print(f"Laranex Schema Viewer: http://{args.host}:{server.server_port}", flush=True)
                server.serve_forever()
        else:
            from .mcp import create_server

            create_server(resolve_snapshot(args.snapshot, Path.cwd())).run(transport="stdio")
    except KeyboardInterrupt:
        return 0
    except EOFError:
        print("Error: input ended before the configuration was complete", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0

