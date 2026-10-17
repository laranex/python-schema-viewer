"""Lazy command imports keep application loading out of serve and mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import (DEFAULT_SNAPSHOT, SOURCES, DatabaseConfig, ProjectConfig, add_config, config_section,
                     database_option, detect_django_settings, detect_model_module, load_config, resolve_snapshot)

DATABASE_URL_ENV = "LARANEX_SCHEMA_DATABASE_URL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="laranex-schema-viewer")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Add build defaults to pyproject.toml interactively")
    init.add_argument("--project-root", type=Path, default=Path.cwd())
    init.add_argument("--yes", action="store_true", help="Accept detected defaults without prompting")
    build = commands.add_parser("build", help="Save the schema as a laranex-schema 2.0 document")
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
    build.add_argument("--database-url-env", metavar="NAME",
                       help=f"Environment variable holding the database URL; default: config, else {DATABASE_URL_ENV}")
    build.add_argument("--schema", action="append", default=[], help="Database schema to inspect; repeatable")
    build.add_argument("--database-connection", metavar="NAME",
                       help="Declared connection the database belongs to; default: config, else the only one")
    build.add_argument("--database", action="append", default=[], metavar="NAME=ENV_VAR",
                       help="Inspect connection NAME, whose URL is in ENV_VAR; repeatable. Replaces the single "
                            "database options and [tool.laranex-schema-viewer.databases]")
    build.add_argument("--allow-write-privileges", action="store_true",
                       help="Inspect with a role that can change the database (sessions stay read-only)")
    serve = commands.add_parser("serve", help="Serve the viewer for a saved laranex-schema 2.0 document")
    serve.add_argument("--snapshot", type=Path, help="Default: pyproject.toml output, else schema-viewer.json")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    mcp = commands.add_parser("mcp", help="Run read-only MCP tools over a saved document, on stdio")
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

    from .isolation import ExtractRequest, IsolationConfig
    from .isolation.runner import extract
    from .snapshot import write_document

    config = load_config(args.project_root)
    source = args.source or config.source or "declared"
    output = args.output or config.output or DEFAULT_SNAPSHOT
    timeout = args.timeout or config.timeout or IsolationConfig.timeout
    databases = database_connections(args, config)
    url_env = args.database_url_env or config.database_url_env or DATABASE_URL_ENV
    for variable in [d.url_env for d in databases] or [url_env]:
        if variable in config.env_passthrough or variable in config.env:
            raise ValueError(f"{variable} holds a database URL and must not be passed to the extraction worker")
    url, urls = None, {}
    if source != "declared":
        for database in databases:
            urls[database.name] = os.environ.get(database.url_env)
            if not urls[database.name]:
                raise ValueError(f"Set {database.url_env} to a read-only database URL for connection {database.name} "
                                 "(see docs/database.md); URLs are never read from arguments or configuration")
        if not databases:
            url = os.environ.get(url_env)
            if not url:
                raise ValueError(f"Set {url_env} to a read-only database URL (see docs/database.md); URLs are never "
                                 "read from arguments or configuration")

    declared = None
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
        declared = result.document

    document = declared
    if source != "declared" and databases:
        from .introspection import DatabaseRequest
        from .introspection.runner import introspect_all

        database = introspect_all([DatabaseRequest(
            url=urls[d.name], schemas=d.schemas, connection=d.name,
            allow_write_privileges=args.allow_write_privileges, timeout=timeout,
        ) for d in databases], project_roots=[str(Path(args.project_root).resolve())])
        for connection in database.connections:
            print(f"Inspected connection {connection.name} read-only ({connection.driver})")
        if declared is None:
            document = database
        else:
            from .reconcile import combine

            document = combine(declared, database, namespaces={d.name: d.namespace for d in databases if d.namespace})
    elif source != "declared":
        from .introspection import DatabaseRequest
        from .introspection.runner import introspect

        names = [c.name for c in declared.connections] if declared else []
        connection = args.database_connection or config.database_connection or (
            names[0] if len(names) == 1 else "default")
        database = introspect(DatabaseRequest(
            url=url, schemas=tuple(args.schema or config.database_schemas), connection=connection,
            allow_write_privileges=args.allow_write_privileges, timeout=timeout,
        ), project_roots=[str(Path(args.project_root).resolve())])
        print(f"Inspected the database read-only ({database.connections[0].driver})")
        if declared is None:
            document = database
        else:
            from .reconcile import combine

            document = combine(declared, database, connection=connection)
    write_document(document, output)
    counts: dict[str, int] = {}
    for diagnostic in document.diagnostics:
        counts[diagnostic.category] = counts.get(diagnostic.category, 0) + 1
    summary = ", ".join(f"{n} {category}" for category, n in sorted(counts.items())) or "no"
    print(f"Saved laranex-schema 2.0 ({document.source}): {len(document.tables())} tables, "
          f"{len(document.models)} models, {len(document.relationships)} ORM relationships, {summary} diagnostics, "
          f"to {output}")


def database_connections(args: argparse.Namespace, config: ProjectConfig) -> list[DatabaseConfig]:
    """The named connections to inspect, or [] for the single-database options. Never both."""
    single = [option for option, value in (("--database-url-env", args.database_url_env), ("--schema", args.schema),
                                           ("--database-connection", args.database_connection)) if value]
    single_config = [key for key, value in (("database_url_env", config.database_url_env),
                                            ("database_schemas", config.database_schemas),
                                            ("database_connection", config.database_connection)) if value]
    if args.database and single:
        raise ValueError(f"--database cannot be combined with {', '.join(single)}; give each connection as "
                         "NAME=ENV_VAR and its schemas in [tool.laranex-schema-viewer.databases.NAME]")
    if args.database and single_config:
        raise ValueError(f"--database cannot be combined with {', '.join(single_config)} in pyproject.toml; "
                         "configure the connections under [tool.laranex-schema-viewer.databases.NAME]")
    if config.databases and single:
        raise ValueError(f"{', '.join(single)} cannot be combined with [tool.laranex-schema-viewer.databases]; "
                         "select connections with --database NAME=ENV_VAR")
    if not args.database:
        return [config.databases[name] for name in sorted(config.databases)]
    selected: dict[str, DatabaseConfig] = {}
    for value in args.database:
        name, url_env = database_option(value)
        if name in selected:
            raise ValueError(f"--database names connection {name} more than once")
        configured = config.databases.get(name)
        selected[name] = DatabaseConfig(name=name, url_env=url_env, schemas=configured.schemas if configured else (),
                                        namespace=configured.namespace if configured else None)
    return [selected[name] for name in sorted(selected)]
