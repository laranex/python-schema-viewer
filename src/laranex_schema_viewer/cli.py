"""Lazy command imports keep application loading out of serve and mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import (DEFAULT_SNAPSHOT, FORMATS, SOURCES, add_config, config_section, detect_django_settings,
                     detect_model_module, load_config, resolve_snapshot)

DATABASE_URL_ENV = "LARANEX_SCHEMA_DATABASE_URL"


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
    build.add_argument("--source", choices=SOURCES,
                       help="declared (models), database (live catalogs), or combined; default: config, else declared")
    build.add_argument("--format", choices=FORMATS,
                       help="v1 (read by serve and mcp) or laranex-schema; default: v1 for declared sources")
    build.add_argument("--database-url-env", metavar="NAME",
                       help=f"Environment variable holding the database URL; default: config, else {DATABASE_URL_ENV}")
    build.add_argument("--schema", action="append", default=[], help="Database schema to inspect; repeatable")
    build.add_argument("--database-connection", metavar="NAME",
                       help="Declared connection the database belongs to; default: config, else the only one")
    build.add_argument("--allow-write-privileges", action="store_true",
                       help="Inspect with a role that can change the database (sessions stay read-only)")
    convert = commands.add_parser("convert", help="Convert a version 1 snapshot to a laranex-schema 1.0 document")
    convert.add_argument("snapshot", type=Path)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--orm", help="The ORM the snapshot was built from, recorded in the document")
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
            run_build(args)
        elif args.command == "convert":
            from .snapshot import read_snapshot, write_unified
            from .unified import from_v1

            write_unified(from_v1(read_snapshot(args.snapshot), orm=args.orm), args.output)
            print(f"Saved {args.snapshot} as laranex-schema 1.0 to {args.output}")
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



def run_build(args: argparse.Namespace) -> None:
    """Extract declared metadata in the isolated worker, inspect the database in its own worker, or both."""
    import os

    from .document import SchemaDocument
    from .isolation import ExtractRequest, IsolationConfig
    from .isolation.runner import extract
    from .snapshot import write_snapshot, write_unified

    config = load_config(args.project_root)
    source = args.source or config.source or "declared"
    output_format = args.format or config.format or ("v1" if source == "declared" else "laranex-schema")
    if source != "declared" and output_format == "v1":
        raise ValueError(f"--source {source} writes laranex-schema documents; version 1 snapshots cannot hold "
                         "database metadata")
    output = args.output or config.output or DEFAULT_SNAPSHOT
    timeout = args.timeout or config.timeout or IsolationConfig.timeout
    url_env = args.database_url_env or config.database_url_env or DATABASE_URL_ENV
    if url_env in config.env_passthrough or url_env in config.env:
        raise ValueError(f"{url_env} holds the database URL and must not be passed to the extraction worker")
    url = None
    if source != "declared":
        url = os.environ.get(url_env)
        if not url:
            raise ValueError(f"Set {url_env} to a read-only database URL (see docs/database.md); URLs are never read "
                             "from arguments or configuration")

    v1 = None
    orm = args.orm or config.orm or "auto"
    if source != "database":
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
        isolation = IsolationConfig(
            level=args.isolation or config.isolation or "process", timeout=timeout,
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
        v1 = SchemaDocument.model_validate(result.document)
        if output_format == "v1":
            write_snapshot(v1, output)
            print(f"Saved {len(v1.tables)} tables, {len(v1.models)} models, "
                  f"{len(v1.relationships)} ORM relationships, {len(v1.diagnostics)} diagnostics to {output}")
            return

    from .unified import from_v1

    declared = from_v1(v1, orm=None if orm == "auto" else orm) if v1 is not None else None
    document = declared
    if source != "declared":
        from .introspection import DatabaseRequest
        from .introspection.runner import introspect

        names = [c["name"] for c in declared["connections"]] if declared else []
        connection = args.database_connection or config.database_connection or (
            names[0] if len(names) == 1 else "default")
        database = introspect(DatabaseRequest(
            url=url, schemas=tuple(args.schema or config.database_schemas), connection=connection,
            allow_write_privileges=args.allow_write_privileges, timeout=timeout,
        ), project_roots=[str(Path(args.project_root).resolve())])
        print(f"Inspected the database read-only ({database['connections'][0]['driver']})")
        if declared is None:
            document = database
        else:
            from .reconcile import combine

            document = combine(declared, database, connection=connection)
    write_unified(document, output)
    counts = {}
    for diagnostic in document["diagnostics"]:
        counts[diagnostic["category"]] = counts.get(diagnostic["category"], 0) + 1
    summary = ", ".join(f"{n} {category}" for category, n in sorted(counts.items())) or "no"
    tables = sum(len(c["tables"]) for c in document["connections"])
    print(f"Saved laranex-schema 1.0 ({document['source']}): {tables} tables, {summary} diagnostics, to {output}. "
          "serve and mcp read version 1 snapshots; open this document in laranex-schema-viewer-ui.")
