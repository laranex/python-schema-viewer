"""Project defaults from pyproject.toml and filesystem-only model package detection."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

SECTION = "laranex-schema-viewer"
DEFAULT_SNAPSHOT = Path("schema-viewer.json")
MAX_DETECTION_DEPTH = 4
MODULE_NAME = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")
DJANGO_SETTINGS = re.compile(r"""DJANGO_SETTINGS_MODULE["']\s*,\s*["']([A-Za-z_][\w.]*)["']""")
SKIPPED_DIRECTORIES = {"build", "dist", "docs", "node_modules", "site-packages", "test", "tests",
                       "venv", "__pycache__"}
CONNECTION_NAME = re.compile(r"[A-Za-z0-9_][\w.-]*")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Differences listed per comparison; the rest are counted as omitted. Keeps a document with many tenant comparisons
# well inside the 32 MiB snapshot limit.
DEFAULT_COMPARISON_LIMIT = 500
WILDCARD = "*"
# check: the lowest severity that fails, and whether likely findings fail too.
CHECK_SEVERITIES = ("error", "warning", "info")
CHECK_CERTAINTIES = ("certain", "likely")
DEFAULT_CHECK_SEVERITY = "warning"
DEFAULT_CHECK_CERTAINTY = "certain"
COMPARISON_FORMAT = ("A comparison is BASELINE=TARGET, each scope a connection name, optionally with :SCHEMA "
                     "(the target's schema may be *)")


@dataclass(frozen=True)
class DatabaseConfig:
    """One named database connection: the variable holding its URL, never the URL itself."""

    name: str
    url_env: str
    schemas: tuple[str, ...] = ()
    # The declared namespace this connection reconciles with; None means the connection's own name.
    namespace: str | None = None


@dataclass(frozen=True)
class ScopeConfig:
    """A comparison side: a connection by name and a schema, None for the unqualified scope, or "*" (targets only)
    for every schema the connection's inspection read."""

    connection: str
    schema: str | None = None

    def __str__(self) -> str:
        return self.connection if self.schema is None else f"{self.connection}:{self.schema}"


@dataclass(frozen=True)
class OverrideConfig:
    """A severity for (object_type, change), or for one field of a changed difference; contract spellings."""

    object_type: str
    change: str
    severity: str
    field: str | None = None


@dataclass(frozen=True)
class ComparisonConfig:
    baseline: ScopeConfig
    target: ScopeConfig
    limit: int = DEFAULT_COMPARISON_LIMIT
    overrides: tuple[OverrideConfig, ...] = ()


@dataclass(frozen=True)
class CheckConfig:
    """[tool.laranex-schema-viewer.check]: the thresholds a finding must meet to fail ``check``; None for the
    default."""

    severity: str | None = None
    certainty: str | None = None


@dataclass(frozen=True)
class ProjectConfig:
    modules: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    recursive: bool = False
    output: Path | None = None
    orm: str | None = None
    settings: str | None = None
    isolation: str | None = None
    timeout: float | None = None
    env_passthrough: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    allow_writes: list[str] = field(default_factory=list)
    container_image: str | None = None
    container_runtime: str | None = None
    source: str | None = None
    database_url_env: str | None = None
    database_schemas: list[str] = field(default_factory=list)
    database_connection: str | None = None
    databases: dict[str, DatabaseConfig] = field(default_factory=dict)
    comparisons: tuple[ComparisonConfig, ...] = ()
    check: CheckConfig = CheckConfig()


SOURCES = ("declared", "database", "combined")
DATABASE_KEYS = {"source", "database_url_env", "database_schemas", "database_connection", "databases"}
SINGLE_DATABASE_KEYS = ("database_url_env", "database_schemas", "database_connection")
ISOLATION_KEYS = {"isolation", "timeout", "env_passthrough", "env", "allow_writes", "container_image",
                  "container_runtime"}


def load_config(project_root: Path) -> ProjectConfig:
    """Read [tool.laranex-schema-viewer]; output is relative to the project root."""
    path = project_root / "pyproject.toml"
    if not path.is_file():
        return ProjectConfig()
    with path.open("rb") as file:
        section = tomllib.load(file).get("tool", {}).get(SECTION, {})
    if "database_url" in section or "url" in section:
        raise ValueError(f"[tool.{SECTION}] must not contain a database URL; name the environment variable that holds "
                         "it with database_url_env")
    if "format" in section:
        raise ValueError(f"[tool.{SECTION}] format was removed: build always writes laranex-schema 2.0; delete the "
                         "format key")
    known = {"modules", "targets", "recursive", "output", "orm", "settings", "comparisons", "check", *ISOLATION_KEYS,
             *DATABASE_KEYS}
    unknown = set(section) - known
    if unknown:
        raise ValueError(f"Unknown [tool.{SECTION}] keys in {path}: {', '.join(sorted(unknown))}")
    for key in ("modules", "targets", "env_passthrough", "allow_writes", "database_schemas"):
        value = section.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"[tool.{SECTION}] {key} must be a list of strings")
    if not isinstance(section.get("recursive", False), bool):
        raise ValueError(f"[tool.{SECTION}] recursive must be true or false")
    output = section.get("output")
    if output is not None and not isinstance(output, str):
        raise ValueError(f"[tool.{SECTION}] output must be a string path")
    for key in ("orm", "settings", "isolation", "container_image", "container_runtime", "source",
                "database_url_env", "database_connection"):
        if not isinstance(section.get(key, ""), str):
            raise ValueError(f"[tool.{SECTION}] {key} must be a string")
    if section.get("isolation", "process") not in ("process", "container"):
        raise ValueError(f"[tool.{SECTION}] isolation must be process or container")
    if section.get("source", "declared") not in SOURCES:
        raise ValueError(f"[tool.{SECTION}] source must be declared, database or combined")
    timeout = section.get("timeout")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ValueError(f"[tool.{SECTION}] timeout must be a positive number of seconds")
    env = section.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ValueError(f"[tool.{SECTION}.env] values must be strings")
    databases = database_section(section.get("databases", {}))
    if databases and any(key in section for key in SINGLE_DATABASE_KEYS):
        raise ValueError(f"[tool.{SECTION}] sets both [tool.{SECTION}.databases] and "
                         f"{', '.join(k for k in SINGLE_DATABASE_KEYS if k in section)}; configure every connection "
                         f"under [tool.{SECTION}.databases.NAME]")
    return ProjectConfig(
        modules=section.get("modules", []), targets=section.get("targets", []),
        recursive=section.get("recursive", False),
        output=project_root / output if output is not None else None, orm=section.get("orm"),
        settings=section.get("settings"), isolation=section.get("isolation"),
        timeout=float(timeout) if timeout is not None else None,
        env_passthrough=section.get("env_passthrough", []), env=env, allow_writes=section.get("allow_writes", []),
        container_image=section.get("container_image"), container_runtime=section.get("container_runtime"),
        source=section.get("source"), database_url_env=section.get("database_url_env"),
        database_schemas=section.get("database_schemas", []), database_connection=section.get("database_connection"),
        databases=databases, comparisons=comparison_section(section.get("comparisons", [])),
        check=check_section(section.get("check", {})),
    )


def check_section(value: object) -> CheckConfig:
    """[tool.laranex-schema-viewer.check]: severity and certainty thresholds."""
    where = f"[tool.{SECTION}.check]"
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a table")
    unknown = set(value) - {"severity", "certainty"}
    if unknown:
        raise ValueError(f"Unknown {where} keys: {', '.join(sorted(unknown))}")
    for key, allowed in (("severity", CHECK_SEVERITIES), ("certainty", CHECK_CERTAINTIES)):
        if key in value and value[key] not in allowed:
            raise ValueError(f"{where} {key} must be one of {', '.join(allowed)}")
    return CheckConfig(severity=value.get("severity"), certainty=value.get("certainty"))


def database_section(value: object) -> dict[str, DatabaseConfig]:
    """[tool.laranex-schema-viewer.databases.NAME] tables: url_env (required), schemas, namespace."""
    where = f"[tool.{SECTION}.databases]"
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        raise ValueError(f"{where} must contain one table per connection, such as [tool.{SECTION}.databases.default]")
    databases = {}
    for name, entry in value.items():
        table = f"[tool.{SECTION}.databases.{name}]"
        if not CONNECTION_NAME.fullmatch(name):
            raise ValueError(f"{table}: connection names use letters, digits, '_', '.' and '-'")
        if "url" in entry or "database_url" in entry:
            raise ValueError(f"{table} must not contain a database URL; name the environment variable that holds it "
                             "with url_env")
        unknown = set(entry) - {"url_env", "schemas", "namespace"}
        if unknown:
            raise ValueError(f"Unknown {table} keys: {', '.join(sorted(unknown))}")
        url_env = entry.get("url_env")
        if not isinstance(url_env, str) or not ENV_NAME.fullmatch(url_env):
            raise ValueError(f"{table} needs url_env, the name of the environment variable holding its URL")
        schemas = entry.get("schemas", [])
        if not isinstance(schemas, list) or not all(isinstance(item, str) and item for item in schemas):
            raise ValueError(f"{table} schemas must be a list of strings")
        namespace = entry.get("namespace")
        if namespace is not None and (not isinstance(namespace, str) or not namespace):
            raise ValueError(f"{table} namespace must be a non-empty string")
        databases[name] = DatabaseConfig(name=name, url_env=url_env, schemas=tuple(schemas), namespace=namespace)
    return databases


def comparison_section(value: object) -> tuple[ComparisonConfig, ...]:
    """[[tool.laranex-schema-viewer.comparisons]] entries: baseline and target ({connection, schema}), limit,
    overrides ([{object_type, change, field, severity}])."""
    from .contract.comparison import FIELDS, SEVERITIES
    from .contract.shape import CHANGES

    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ValueError(f"[tool.{SECTION}] comparisons must be an array of tables, "
                         f"such as [[tool.{SECTION}.comparisons]]")
    comparisons = []
    for n, entry in enumerate(value, start=1):
        where = f"[[tool.{SECTION}.comparisons]] entry {n}"
        unknown = set(entry) - {"baseline", "target", "limit", "overrides"}
        if unknown:
            raise ValueError(f"Unknown {where} keys: {', '.join(sorted(unknown))}")
        baseline = scope_config(entry.get("baseline"), f"{where} baseline", wildcard=False)
        target = scope_config(entry.get("target"), f"{where} target", wildcard=True)
        if baseline == target:
            raise ValueError(f"{where} compares {baseline} with itself")
        limit = entry.get("limit", DEFAULT_COMPARISON_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(f"{where} limit must be a nonnegative integer")
        overrides = entry.get("overrides", [])
        if not isinstance(overrides, list) or not all(isinstance(o, dict) for o in overrides):
            raise ValueError(f"{where} overrides must be an array of tables")
        parsed = []
        for o in overrides:
            unknown = set(o) - {"object_type", "change", "field", "severity"}
            if unknown:
                raise ValueError(f"Unknown {where} override keys: {', '.join(sorted(unknown))}")
            object_type, change, severity, name = (o.get("object_type"), o.get("change"), o.get("severity"),
                                                   o.get("field"))
            if object_type not in FIELDS:
                raise ValueError(f"{where} override object_type must be one of {', '.join(FIELDS)}")
            if change not in CHANGES:
                raise ValueError(f"{where} override change must be one of {', '.join(CHANGES)}")
            if severity not in SEVERITIES:
                raise ValueError(f"{where} override severity must be one of {', '.join(SEVERITIES)}")
            if name is not None and (change != "changed" or name not in FIELDS[object_type]):
                raise ValueError(f"{where} override field applies to changed differences; a {object_type} field is "
                                 f"one of {', '.join(FIELDS[object_type])}")
            parsed.append(OverrideConfig(object_type=object_type, change=change, severity=severity, field=name))
        comparisons.append(ComparisonConfig(baseline=baseline, target=target, limit=limit, overrides=tuple(parsed)))
    return tuple(comparisons)


def scope_config(value: object, where: str, *, wildcard: bool) -> ScopeConfig:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a table: {{ connection = \"NAME\", schema = \"SCHEMA\" }}")
    unknown = set(value) - {"connection", "schema"}
    if unknown:
        raise ValueError(f"Unknown {where} keys: {', '.join(sorted(unknown))}")
    connection, schema = value.get("connection"), value.get("schema")
    if not isinstance(connection, str) or not CONNECTION_NAME.fullmatch(connection):
        raise ValueError(f"{where} needs connection, a connection name")
    if schema is not None and (not isinstance(schema, str) or not schema or "@" in schema or "//" in schema):
        raise ValueError(f"{where} schema must be a schema name (never a URL)")
    if schema == WILDCARD and not wildcard:
        raise ValueError(f"{where} schema cannot be \"*\"; only a target expands to every inspected schema")
    return ScopeConfig(connection=connection, schema=schema)


def comparison_option(value: str) -> ComparisonConfig:
    """``BASELINE=TARGET``, each scope ``CONNECTION`` or ``CONNECTION:SCHEMA``; the target schema may be ``*``."""
    baseline, separator, target = value.partition("=")
    if not separator:
        raise ValueError(COMPARISON_FORMAT)
    spec = ComparisonConfig(baseline=scope_option(baseline, wildcard=False), target=scope_option(target, wildcard=True))
    if spec.baseline == spec.target:
        raise ValueError(f"A comparison compares two different scopes, not {spec.baseline} with itself")
    return spec


def scope_option(text: str, *, wildcard: bool) -> ScopeConfig:
    # Never echo the value: a URL passed by mistake would end up in the error output.
    connection, separator, schema = text.partition(":")
    if not CONNECTION_NAME.fullmatch(connection) or (separator and not schema):
        raise ValueError(COMPARISON_FORMAT)
    if "@" in schema or "//" in schema:
        raise ValueError(f"{COMPARISON_FORMAT}; database URLs are never accepted as arguments")
    if schema == WILDCARD and not wildcard:
        raise ValueError("Only a comparison target's schema can be \"*\"")
    return ScopeConfig(connection=connection, schema=schema if separator else None)


def database_option(value: str) -> tuple[str, str]:
    """``--database NAME=ENV_VAR`` as (name, variable)."""
    name, separator, url_env = value.partition("=")
    if not separator or not CONNECTION_NAME.fullmatch(name) or not ENV_NAME.fullmatch(url_env):
        # Never echo the value: a URL passed by mistake would end up in the error output.
        raise ValueError("--database takes NAME=ENV_VAR: the connection name and the environment variable holding its "
                         "URL; URLs are never accepted as arguments")
    return name, url_env


def detect_model_module(project_root: Path) -> str:
    """Find the single models package or models.py without importing anything."""
    candidates = []

    def visit(directory: Path, depth: int) -> None:
        for child in sorted(directory.iterdir()):
            if child.name == "models.py" and child.is_file():
                candidates.append(child.relative_to(project_root).with_suffix(""))
            elif child.is_dir() and not child.name.startswith(".") and child.name not in SKIPPED_DIRECTORIES:
                if child.name == "models" and (child / "__init__.py").is_file():
                    candidates.append(child.relative_to(project_root))
                elif depth < MAX_DETECTION_DEPTH:
                    visit(child, depth + 1)

    visit(project_root, 1)
    names = [".".join(path.parts) for path in candidates]
    if len(names) == 1:
        return names[0]
    hint = f"pass --module or set modules in [tool.{SECTION}] of pyproject.toml"
    if not names:
        raise ValueError(f"No models package or models.py found under {project_root}; {hint}")
    raise ValueError(f"Multiple model modules found ({', '.join(names)}); {hint}")


def detect_django_settings(project_root: Path) -> str | None:
    """Read DJANGO_SETTINGS_MODULE from manage.py without running it."""
    path = project_root / "manage.py"
    if not path.is_file():
        return None
    found = DJANGO_SETTINGS.search(path.read_text(errors="replace"))
    return found.group(1) if found else None


def resolve_snapshot(snapshot: Path | None, project_root: Path) -> Path:
    """Explicit path, else the configured build output, else schema-viewer.json."""
    return snapshot or load_config(project_root).output or DEFAULT_SNAPSHOT


def config_section(modules: list[str], recursive: bool, output: str, *,
                   orm: str | None = None, settings: str | None = None) -> str:
    """Render the section; Django reads models from settings, so its modules are optional."""
    invalid = [name for name in [*modules, *filter(None, [settings])] if not MODULE_NAME.fullmatch(name)]
    if invalid or not (modules or settings):
        raise ValueError(f"Invalid module names: {', '.join(invalid) or '(none)'}")
    lines = [f"[tool.{SECTION}]"]
    if orm:
        lines.append(f"orm = {json.dumps(orm)}")
    if settings:
        lines.append(f"settings = {json.dumps(settings)}")
    if modules:
        lines.append(f"modules = [{', '.join(json.dumps(name) for name in modules)}]")
        lines.append(f"recursive = {'true' if recursive else 'false'}")
    lines.append(f"output = {json.dumps(output)}")
    return "\n".join(lines) + "\n"


def add_config(project_root: Path, section: str) -> Path:
    """Append the section to pyproject.toml, preserving existing content."""
    path = project_root / "pyproject.toml"
    existing = path.read_text() if path.is_file() else ""
    if SECTION in tomllib.loads(existing).get("tool", {}):
        raise ValueError(f"{path} already contains [tool.{SECTION}]")
    path.write_text(existing.rstrip("\n") + "\n\n" + section if existing.strip() else section)
    load_config(project_root)
    return path
