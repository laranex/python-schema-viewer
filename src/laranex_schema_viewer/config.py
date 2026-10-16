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
SKIPPED_DIRECTORIES = {"build", "dist", "docs", "node_modules", "site-packages", "test", "tests",
                       "venv", "__pycache__"}


@dataclass(frozen=True)
class ProjectConfig:
    modules: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    recursive: bool = False
    output: Path | None = None
    orm: str | None = None


def load_config(project_root: Path) -> ProjectConfig:
    """Read [tool.laranex-schema-viewer]; output is relative to the project root."""
    path = project_root / "pyproject.toml"
    if not path.is_file():
        return ProjectConfig()
    with path.open("rb") as file:
        section = tomllib.load(file).get("tool", {}).get(SECTION, {})
    unknown = set(section) - {"modules", "targets", "recursive", "output", "orm"}
    if unknown:
        raise ValueError(f"Unknown [tool.{SECTION}] keys in {path}: {', '.join(sorted(unknown))}")
    for key in ("modules", "targets"):
        value = section.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"[tool.{SECTION}] {key} must be a list of strings")
    if not isinstance(section.get("recursive", False), bool):
        raise ValueError(f"[tool.{SECTION}] recursive must be true or false")
    output = section.get("output")
    if output is not None and not isinstance(output, str):
        raise ValueError(f"[tool.{SECTION}] output must be a string path")
    if not isinstance(section.get("orm", ""), str):
        raise ValueError(f"[tool.{SECTION}] orm must be a string")
    return ProjectConfig(
        modules=section.get("modules", []), targets=section.get("targets", []),
        recursive=section.get("recursive", False),
        output=project_root / output if output is not None else None, orm=section.get("orm"),
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


def resolve_snapshot(snapshot: Path | None, project_root: Path) -> Path:
    """Explicit path, else the configured build output, else schema-viewer.json."""
    return snapshot or load_config(project_root).output or DEFAULT_SNAPSHOT


def config_section(modules: list[str], recursive: bool, output: str) -> str:
    invalid = [name for name in modules if not MODULE_NAME.fullmatch(name)]
    if not modules or invalid:
        raise ValueError(f"Invalid module names: {', '.join(invalid) or '(none)'}")
    names = ", ".join(json.dumps(name) for name in modules)
    return (f"[tool.{SECTION}]\nmodules = [{names}]\n"
            f"recursive = {'true' if recursive else 'false'}\noutput = {json.dumps(output)}\n")


def add_config(project_root: Path, section: str) -> Path:
    """Append the section to pyproject.toml, preserving existing content."""
    path = project_root / "pyproject.toml"
    existing = path.read_text() if path.is_file() else ""
    if SECTION in tomllib.loads(existing).get("tool", {}):
        raise ValueError(f"{path} already contains [tool.{SECTION}]")
    path.write_text(existing.rstrip("\n") + "\n\n" + section if existing.strip() else section)
    load_config(project_root)
    return path
