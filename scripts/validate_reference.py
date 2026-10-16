"""Validate actual model definitions read-only; keep snapshots/reports outside the app."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import CheckConstraint, Column, UniqueConstraint
from sqlalchemy.engine import Engine

from laranex_schema_viewer.build import build_document
from laranex_schema_viewer.snapshot import read_snapshot, snapshot_bytes, write_snapshot


def manifest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def discover_declarations(root: Path) -> list[tuple[str, str]]:
    declarations = []
    for path in sorted((root / "src/infrastructure/models").rglob("*.py")):
        tree = ast.parse(path.read_text())
        module = path.relative_to(root).with_suffix("")
        if module.name == "__init__":
            module = module.parent
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                kw.arg == "table" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            ):
                declarations.append((".".join(module.parts), node.name))
    return declarations


def run_mapper_checks(root: Path) -> int:
    """Run this reference's reviewed zero-fixture tests without pytest conftest/DB setup."""
    definitions = runpy.run_path(str(root / "tests/test_infrastructure_models.py"))
    count = 0
    for name, cls in sorted(definitions.items()):
        if not name.startswith("Test") or not inspect.isclass(cls):
            continue
        for method in sorted(n for n in vars(cls) if n.startswith("test_")):
            test = getattr(cls(), method)
            if inspect.signature(test).parameters:
                raise ValueError(f"Reference check requires fixtures: {name}.{method}")
            test()
            count += 1
    return count


async def validate_mcp(snapshot: Path, expected: dict) -> dict:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    executable = Path(sys.executable).parent / "laranex-schema-viewer"
    params = StdioServerParameters(
        command=str(executable),
        args=["mcp", "--snapshot", str(snapshot)],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    configuration = {"mcpServers": {"laranex_schema": {
        "type": "stdio", "command": params.command, "args": params.args,
        "env": params.env,
    }}}
    snapshot.with_name("backend-mcp.json").write_text(json.dumps(configuration, indent=2) + "\n")
    document = read_snapshot(snapshot)
    calls = 0
    async with Client(params, read_timeout_seconds=10) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 5
        assert all(t.annotations.read_only_hint for t in tools.tools)
        pinned_revision = None

        async def pages(name, arguments):
            nonlocal calls, pinned_revision
            items, offset = [], 0
            first = None
            while True:
                args = dict(arguments, limit=17, offset=offset)
                if pinned_revision is not None:
                    args["revision"] = pinned_revision
                result = await client.call_tool(name, args)
                assert not result.is_error
                calls += 1
                value = json.loads(result.content[0].text)
                if pinned_revision is None:
                    pinned_revision = value["revision"]
                assert value["revision"] == pinned_revision
                first = first or value
                items.extend(value["items"])
                if value["next_offset"] is None:
                    assert len(items) == value["total"]
                    return items, first
                assert value["next_offset"] > offset
                offset = value["next_offset"]

        overview, summary = await pages("schema_overview", {})
        assert summary["counts"] == expected
        assert {t["id"] for t in overview} == {t.id for t in document.tables}
        for table in document.tables:
            sections = {
                "columns": table.columns, "indexes": table.indexes,
                "foreign_keys": table.foreign_keys, "constraints": table.constraints,
                "models": [m for m in document.models if table.id in m.table_ids],
                "relationships": [r for r in document.relationships
                                  if table.id in (r.source_table, r.target_table)],
            }
            for section, values in sections.items():
                items, _ = await pages("schema_get_table", {"table": table.id, "section": section})
                assert items == [value.model_dump(mode="json") for value in values]
        for model in document.models:
            items, value = await pages("schema_get_model", {"model": model.id})
            assert value["model"] == model.model_dump(mode="json")
            assert items == [r.model_dump(mode="json") for r in document.relationships
                             if r.model == model.id]
        for kind in ["foreign_key", "orm"]:
            items, _ = await pages("schema_get_relationships", {"kind": kind})
            assert len(items) == expected["foreign_keys" if kind == "foreign_key" else "relationships"]
        items, _ = await pages("schema_search", {"query_text": "users"})
        assert any(i["kind"] == "table" and i["name"] == "users" for i in items)
    return {"tools": 5, "successful_calls": calls, "transport": "stdio",
            "all_table_sections_and_models_compared": True, "paginated": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".validation/reference.json"))
    parser.add_argument("--report", type=Path, default=Path(".validation/reference-report.json"))
    args = parser.parse_args()
    root = args.project_root.resolve()
    snapshot = args.output.resolve()
    report_path = args.report.resolve()
    if snapshot.is_relative_to(root) or report_path.is_relative_to(root):
        parser.error("Outputs must remain outside the reference project")
    sys.dont_write_bytecode = True
    before = manifest(root)
    declarations = discover_declarations(root)
    # A DB connection attempt is a failure, even if imports catch the exception.
    attempts = []

    def deny_connection(*args, **kwargs):
        attempts.append("Engine.connect")
        raise AssertionError("Validation must not connect to a database")

    with patch.object(Engine, "connect", deny_connection):
        document = build_document(["src.infrastructure.models"], project_root=root, recursive=True)
        assert not attempts
        # Run a second independent build in a fresh process and compare bytes.
        write_snapshot(document, snapshot)
        second = snapshot.with_name("reference-second.json")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        result = subprocess.run([
            sys.executable, "-m", "laranex_schema_viewer", "build", "--project-root", str(root),
            "--module", "src.infrastructure.models", "--recursive", "--output", str(second),
        ], env=env, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert snapshot_bytes(document) == second.read_bytes(), "Fresh builds are nondeterministic"
        second.unlink()

        tables = {(t.schema_name, t.name): t for t in document.tables}
        loaded, failures = [], []
        expression_indexes = 0
        for module_name, name in declarations:
            try:
                model = getattr(importlib.import_module(module_name), name)
                actual = model.__table__
                table = tables[(actual.schema, actual.name)]
                assert [c.name for c in actual.columns] == [c.name for c in table.columns]
                assert [c.name for c in actual.primary_key] == table.primary_key
                for column, extracted in zip(actual.columns, table.columns):
                    assert str(column.type) == extracted.type
                    assert getattr(column.type, "timezone", None) == extracted.timezone
                    assert column.nullable == extracted.nullable
                    assert column.primary_key == extracted.primary_key
                assert len(actual.indexes) == len(table.indexes)
                indexes = {i.name: i for i in table.indexes}
                for index in actual.indexes:
                    expression_indexes += any(not isinstance(e, Column) for e in index.expressions)
                    extracted = indexes[index.name]
                    assert [str(e) for e in index.expressions] == extracted.expressions
                    assert [c.name for c in index.columns] == extracted.columns
                    assert bool(index.unique) == extracted.unique
                    for dialect, options in index.dialect_options.items():
                        if options.get("where") is not None:
                            assert str(options["where"]) == extracted.predicates[dialect]
                        if options.get("include"):
                            assert list(options["include"]) == extracted.included_columns[dialect]
                        if options.get("using"):
                            assert options["using"] == extracted.methods[dialect]
                actual_constraints = sorted(((
                    "check" if isinstance(c, CheckConstraint) else "unique", c.name,
                    tuple(column.name for column in c.columns),
                    str(c.sqltext) if isinstance(c, CheckConstraint) else None,
                ) for c in actual.constraints if isinstance(c, (CheckConstraint, UniqueConstraint))), key=repr)
                assert actual_constraints == sorted(((
                    c.kind, c.name, tuple(c.columns), c.expression,
                ) for c in table.constraints), key=repr)
                actual_fks = sorted(((
                    f.name, tuple(e.parent.name for e in f.elements),
                    f.elements[0].column.table.schema, f.elements[0].column.table.name,
                    tuple(e.column.name for e in f.elements), f.ondelete, f.onupdate,
                ) for f in actual.foreign_key_constraints), key=repr)
                table_map = {t.id: t for t in document.tables}
                extracted_fks = sorted(((
                    f.name, tuple(f.columns), table_map[f.target_table].schema_name,
                    table_map[f.target_table].name, tuple(f.target_columns), f.on_delete, f.on_update,
                ) for f in table.foreign_keys), key=repr)
                assert actual_fks == extracted_fks
                loaded.append(f"{module_name}.{name}")
            except Exception as error:
                failures.append({"model": f"{module_name}.{name}", "error": str(error)})
        mapper_checks = run_mapper_checks(root)
    from laranex_schema_viewer.query import SnapshotQuery

    counts = SnapshotQuery(snapshot).overview()["counts"]
    mcp = asyncio.run(asyncio.wait_for(validate_mcp(snapshot, counts), timeout=60))
    after = manifest(root)
    unchanged = before == after
    report = {
        "declarations_discovered": len(declarations), "declarations_validated": len(loaded),
        "counts": counts, "failures": failures, "reference_files_unchanged": unchanged,
        "reference_files_hashed": len(before), "deterministic_fresh_builds": True,
        "database_connection_attempts": len(attempts), "mcp": mcp,
        "qualified_tables": [f"{t.schema_name}.{t.name}" for t in document.tables if t.schema_name],
        "unqualified_tables": sum(t.schema_name is None for t in document.tables),
        "partial_indexes": sum(bool(i.predicates) for t in document.tables for i in t.indexes),
        "constraints": sum(len(t.constraints) for t in document.tables),
        "expression_indexes": expression_indexes,
        "timezone_aware_columns": sum(c.timezone is True for t in document.tables for c in t.columns),
        "existing_zero_fixture_mapper_tests_passed": mapper_checks,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    assert unchanged, "Reference project was modified"
    assert not failures and len(loaded) == len(declarations)
    assert len(read_snapshot(snapshot).models) == len(loaded)


if __name__ == "__main__":
    main()

