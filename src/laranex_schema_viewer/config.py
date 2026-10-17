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
    format: str | None = None
    database_url_env: str | None = None
    database_schemas: list[str] = field(default_factory=list)
    database_connection: str | None = None


SOURCES = ("declared", "database", "combined")
FORMATS = ("v1", "laranex-schema")
DATABASE_KEYS = {"source", "format", "database_url_env", "database_schemas", "database_connection"}
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
    known = {"modules", "targets", "recursive", "output", "orm", "settings", *ISOLATION_KEYS, *DATABASE_KEYS}
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
    for key in ("orm", "settings", "isolation", "container_image", "container_runtime", "source", "format",
                "database_url_env", "database_connection"):
        if not isinstance(section.get(key, ""), str):
            raise ValueError(f"[tool.{SECTION}] {key} must be a string")
    if section.get("isolation", "process") not in ("process", "container"):
        raise ValueError(f"[tool.{SECTION}] isolation must be process or container")
    if section.get("source", "declared") not in SOURCES:
        raise ValueError(f"[tool.{SECTION}] source must be declared, database or combined")
    if section.get("format", "v1") not in FORMATS:
        raise ValueError(f"[tool.{SECTION}] format must be v1 or laranex-schema")
    timeout = section.get("timeout")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ValueError(f"[tool.{SECTION}] timeout must be a positive number of seconds")
    env = section.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ValueError(f"[tool.{SECTION}.env] values must be strings")
    return ProjectConfig(
        modules=section.get("modules", []), targets=section.get("targets", []),
        recursive=section.get("recursive", False),
        output=project_root / output if output is not None else None, orm=section.get("orm"),
        settings=section.get("settings"), isolation=section.get("isolation"),
        timeout=float(timeout) if timeout is not None else None,
        env_passthrough=section.get("env_passthrough", []), env=env, allow_writes=section.get("allow_writes", []),
        container_image=section.get("container_image"), container_runtime=section.get("container_runtime"),
        source=section.get("source"), format=section.get("format"), database_url_env=section.get("database_url_env"),
        database_schemas=section.get("database_schemas", []), database_connection=section.get("database_connection"),
    )


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
