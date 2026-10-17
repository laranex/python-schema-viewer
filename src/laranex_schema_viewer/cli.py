"""Lazy command imports keep application loading out of serve and mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import (DEFAULT_SNAPSHOT, add_config, config_section, detect_django_settings,
                     detect_model_module, load_config, resolve_snapshot)


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
    build.add_argument("--target", action="append", default=[],
                       help="SQLAlchemy module:metadata/base/registry, or Tortoise module:TORTOISE_ORM")
    build.add_argument("--recursive", action="store_true", help="Import every submodule of selected packages")
    build.add_argument("--orm", help="auto, sqlalchemy, django, tortoise, or peewee; default: pyproject.toml config, else auto")
    build.add_argument("--settings", help="Django settings module; default: config, else manage.py")
    build.add_argument("--output", type=Path, help="Default: pyproject.toml config, else schema-viewer.json")
    build.add_argument("--isolation", choices=("process", "container"),
                       help="Where application code runs; default: pyproject.toml config, else process")
    build.add_argument("--timeout", type=float, help="Seconds before extraction is stopped; default: config, else 120")
    build.add_argument("--container-image", help="Image for --isolation container")
    serve = commands.add_parser("serve", help="Serve the viewer from a saved snapshot only")
    serve.add_argument("--snapshot", type=Path, help="Default: pyproject.toml output, else schema-viewer.json")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    mcp = commands.add_parser("mcp", help="Run read-only snapshot MCP tools over stdio")
    mcp.add_argument("--snapshot", type=Path, help="Default: pyproject.toml output, else schema-viewer.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            def ask(question: str, default: str) -> str:
                if args.yes:
                    return default
                answer = input(f"{question} [{default}]: " if default else f"{question}: ")
                return answer.strip() or default

            settings = detect_django_settings(args.project_root)
            if settings:
                settings = ask("Django settings module", settings)
                question, detected = "Limit to Django apps or modules, comma-separated (optional)", ""
            else:
                try:
                    detected = detect_model_module(args.project_root)
                except ValueError:
                    detected = ""
                question = "Model modules, comma-separated"
            modules = [name.strip() for name in ask(question, detected).split(",") if name.strip()]
            recursive = (not settings and
                         ask("Import submodules recursively? (y/n)", "y").lower().startswith("y"))
            output = ask("Snapshot output path", DEFAULT_SNAPSHOT.as_posix())
            section = config_section(modules, recursive, output, orm="django" if settings else None,
                                     settings=settings)
            path = add_config(args.project_root, section)
            print(f"Added [tool.laranex-schema-viewer] to {path}. Next: laranex-schema-viewer build")
        elif args.command == "build":
            from .document import SchemaDocument
            from .isolation import ExtractRequest, IsolationConfig
            from .isolation.runner import extract
            from .snapshot import write_snapshot

            config = load_config(args.project_root)
            orm = args.orm or config.orm or "auto"
            settings = args.settings or config.settings
            modules, targets, recursive = args.module, args.target, args.recursive
            if not modules and not targets:
                modules, targets = config.modules, config.targets
                recursive = recursive or config.recursive
            if orm in ("auto", "django") and not (settings or modules or targets):
                settings = detect_django_settings(args.project_root)
                if settings:
                    print(f"Detected Django settings {settings}")
            django = orm == "django" or (orm == "auto" and settings)
            if not django and not modules and not targets:
                modules, recursive = [detect_model_module(args.project_root)], True
                print(f"Detected model module {modules[0]}; importing it recursively")
            output = args.output or config.output or DEFAULT_SNAPSHOT
            isolation = IsolationConfig(
                level=args.isolation or config.isolation or "process",
                timeout=args.timeout or config.timeout or IsolationConfig.timeout,
                env_passthrough=tuple(config.env_passthrough), env=tuple(sorted(config.env.items())),
                allow_writes=tuple(config.allow_writes),
                container_image=args.container_image or config.container_image,
                container_runtime=config.container_runtime,
            )
            request = ExtractRequest(project_root=str(args.project_root), modules=tuple(modules),
                                     targets=tuple(targets), recursive=recursive, orm=orm, settings=settings)
            result = extract(request, isolation)
            print(f"Extracted with {isolation.level} isolation; {result.withheld} environment variables withheld")
            for blocked in result.blocked:
                print(f"Warning: blocked {blocked['event']} during extraction ({blocked['detail']})", file=sys.stderr)
            document = SchemaDocument.model_validate(result.document)
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

