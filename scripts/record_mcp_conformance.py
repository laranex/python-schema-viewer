"""Record the MCP query responses for every shared valid contract fixture, for other implementations to verify.

    PYTHONPATH=src python scripts/record_mcp_conformance.py          # rewrite tests/fixtures/mcp-conformance
    PYTHONPATH=src python scripts/record_mcp_conformance.py --check  # fail if a recording is stale

Each file lists tool calls (the MCP tool name and its arguments) with the exact result, or the error message a tool
error carries: every tool, every table section, every table and model, pagination with the pinned revision, and
invalid arguments, and schema_compare over the stored comparisons. byte-limit.json and comparison-byte-limit.json embed
synthetic documents whose pages are cut by the 64 KiB response limit. The response format is specified in docs/mcp.md; tests/test_mcp.py checks the recordings against query.py.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/fixtures/contract"
TARGET = ROOT / "tests/fixtures/mcp-conformance"
COMPARE_FILTERS = ("comparison", "baseline", "target", "object_type", "change", "severity", "certainty")
SECTIONS = ("columns", "indexes", "foreign_keys", "constraints", "models", "relationships", "links",
            "derived_links", "diagnostics")


def call(query: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One tool call on the query service, as the MCP tools make it."""
    pagination = {k: arguments[k] for k in ("limit", "offset", "revision") if k in arguments}
    methods = {
        "schema_overview": lambda: query.overview(**pagination),
        "schema_search": lambda: query.search(arguments["query_text"], **pagination),
        "schema_get_table": lambda: query.get_table(arguments["table"], arguments.get("section", "columns"),
                                                    **pagination),
        "schema_get_model": lambda: query.get_model(arguments["model"], **pagination),
        "schema_get_relationships": lambda: query.relationships(arguments.get("table"),
                                                                arguments.get("kind", "orm"), **pagination),
        "schema_compare": lambda: query.compare(
            arguments.get("section", "comparisons"), *(arguments.get(k) for k in COMPARE_FILTERS), **pagination),
    }
    try:
        return {"tool": tool, "arguments": arguments, "result": methods[tool]()}
    except ValueError as error:
        return {"tool": tool, "arguments": arguments, "error": str(error)}


def calls(query: Any) -> list[dict[str, Any]]:
    out = [call(query, "schema_overview", {"limit": 100}), call(query, "schema_overview", {})]
    first = call(query, "schema_overview", {"limit": 1})
    out.append(first)
    if first["result"]["nextOffset"] is not None:
        out.append(call(query, "schema_overview", {"limit": 1, "offset": first["result"]["nextOffset"],
                                                   "revision": query.revision}))
    for text in ("id", "USER", "_at", "zzz-no-match"):
        out.append(call(query, "schema_search", {"query_text": text, "limit": 100}))
    for table in query.tables:
        for section in SECTIONS:
            out.append(call(query, "schema_get_table", {"table": table, "section": section, "limit": 100}))
        for kind in ("orm", "foreign_key"):
            out.append(call(query, "schema_get_relationships", {"table": table, "kind": kind, "limit": 100}))
    if query.tables:
        table = next(iter(query.tables))
        out.append(call(query, "schema_get_table", {"table": table}))
        out.append(call(query, "schema_get_table", {"table": table, "section": "columns", "limit": 1,
                                                    "offset": 1, "revision": query.revision}))
    for model in query.models:
        out.append(call(query, "schema_get_model", {"model": model, "limit": 100}))
    for kind in ("orm", "foreign_key"):
        out.append(call(query, "schema_get_relationships", {"kind": kind, "limit": 100}))
    out.append(call(query, "schema_get_relationships", {}))
    some_table = next(iter(query.tables), "missing")
    for tool, arguments in (
            ("schema_overview", {"limit": 0}), ("schema_overview", {"limit": 101}),
            ("schema_overview", {"offset": -1}), ("schema_overview", {"revision": "0" * 64}),
            ("schema_search", {"query_text": ""}), ("schema_search", {"query_text": "x" * 201}),
            ("schema_get_table", {"table": "../../.env"}),
            ("schema_get_table", {"table": some_table, "section": "rows"}),
            ("schema_get_model", {"model": "missing"}),
            ("schema_get_relationships", {"table": "missing", "kind": "orm"}),
            ("schema_get_relationships", {"kind": "sql"})):
        out.append(call(query, tool, arguments))
    return out + compare_calls(query)


def compare_calls(query: Any) -> list[dict[str, Any]]:
    """schema_compare: the comparisons, each one's differences with every filter, pagination, and invalid arguments."""
    out = [call(query, "schema_compare", {}), call(query, "schema_compare", {"section": "differences", "limit": 100})]
    for comparison in query.document.comparisons:
        baseline, target = query.scope_label(comparison.baseline), query.scope_label(comparison.target)
        out.append(call(query, "schema_compare", {"baseline": baseline, "target": target}))
        out.append(call(query, "schema_compare", {"section": "differences", "comparison": comparison.id,
                                                  "limit": 100}))
        first = call(query, "schema_compare", {"section": "differences", "comparison": comparison.id, "limit": 2})
        out.append(first)
        if first["result"]["nextOffset"] is not None:
            out.append(call(query, "schema_compare", {"section": "differences", "comparison": comparison.id,
                                                      "limit": 2, "offset": first["result"]["nextOffset"],
                                                      "revision": query.revision}))
        for name, values in (("object_type", ("table", "column", "primaryKey", "index", "foreignKey", "constraint")),
                             ("change", ("missing", "extra", "changed")), ("severity", ("error", "warning", "info")),
                             ("certainty", ("certain", "likely"))):
            for value in values:
                out.append(call(query, "schema_compare", {"section": "differences", "comparison": comparison.id,
                                                          name: value, "limit": 100}))
        out.append(call(query, "schema_compare", {"section": "differences", "target": target, "severity": "error",
                                                  "certainty": "certain", "limit": 100}))
    connection = query.document.connections[0].name if query.document.connections else "missing"
    for arguments in ({"section": "tables"}, {"comparison": "missing"}, {"baseline": "missing"},
                      {"target": f"{connection}:"}, {"section": "differences", "object_type": "view"},
                      {"section": "differences", "change": "gone"}, {"section": "differences", "severity": "fatal"},
                      {"section": "differences", "certainty": "maybe"}, {"severity": "error"},
                      {"baseline": connection, "limit": 0}, {"offset": -1}, {"revision": "0" * 64}):
        out.append(call(query, "schema_compare", arguments))
    return out


def byte_limit_document() -> dict[str, Any]:
    """One table whose 40 column comments (2 KB each, with non-ASCII text) span several 64 KiB pages."""
    columns = [{"autoIncrement": None, "collation": None, "comment": f"{n:02d} " + "Zürich ✓ " * 220, "default": None,
                "enum": None, "generated": None, "name": f"c{n:02d}", "note": None, "nullable": True,
                "ormDefault": None, "provenance": ["declared"], "timezone": None, "type": "TEXT", "typeName": None}
               for n in range(40)]
    columns.append({**columns[0], "name": "huge", "comment": "x" * 70000})
    coverage = {"complete": True, "schemas": None, "features": {"tables": "complete", "columns": "complete"}}
    return {
        "format": "laranex-schema", "version": "2.0", "source": "declared",
        "generator": {"name": "python-schema-viewer", "orm": None}, "note": None,
        "connections": [{"id": "connection:default", "name": "default", "driver": None, "defaultSchema": None,
                         "coverage": {"database": None, "declared": coverage},
                         "tables": [{"id": "t:wide", "name": "wide", "schema": None, "comment": None, "note": None,
                                     "owner": {"kind": "application", "package": None}, "primaryKey": [],
                                     "columns": columns, "indexes": [], "foreignKeys": [], "constraints": [],
                                     "provenance": ["declared"]}]}],
        "models": [], "relationships": [], "links": [], "derivedLinks": [], "diagnostics": [], "comparisons": [],
        "migrations": [], "inferredAssociations": [],
    }


def comparison_byte_limit_document() -> dict[str, Any]:
    """Two connections whose 40 column comments (2 KB each, with non-ASCII text) all differ, compared once: the
    changed-column differences span several 64 KiB pages."""
    sys.path.insert(0, str(ROOT / "src"))
    from laranex_schema_viewer import model as m
    from laranex_schema_viewer.contract import compare_scopes, document_to_json

    features = {f: "complete" for f in m.FEATURES}

    def connection(name: str) -> m.Connection:
        columns = tuple(m.Column(name=f"c{n:02d}", type="text", nullable=True, provenance=m.DATABASE,
                                 comment=f"{name} {n:02d} " + "Zürich ✓ " * 110) for n in range(40))
        return m.Connection(id=f"connection:{name}", name=name, driver="postgresql", default_schema=None,
                            coverage=m.ConnectionCoverage(database=m.coverage(features)),
                            tables=(m.Table(id=f"{name}:notes", name="notes", provenance=m.DATABASE,
                                            columns=columns),))

    document = m.Document(source="database", generator=m.Generator(name="python-schema-viewer"),
                          connections=(connection("primary"), connection("replica")))
    comparison = compare_scopes(document, m.Scope(connection="connection:primary"),
                                m.Scope(connection="connection:replica"))
    return document_to_json(m.replace(document, comparisons=(comparison,)))


def recordings() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    from laranex_schema_viewer.contract import canonical_json
    from laranex_schema_viewer.query import SnapshotQuery

    out = {}
    for path in sorted((CONTRACT / "fixtures/valid").glob("*.json")):
        query = SnapshotQuery(path)
        out[path.name] = {"document": f"tests/fixtures/contract/fixtures/valid/{path.name}",
                          "revision": query.revision, "calls": calls(query)}
    document = byte_limit_document()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wide.json"
        path.write_text(canonical_json(document))
        query = SnapshotQuery(path)
        first = call(query, "schema_get_table", {"table": "t:wide", "section": "columns", "limit": 100})
        second = call(query, "schema_get_table", {"table": "t:wide", "section": "columns", "limit": 100,
                                                  "offset": first["result"]["nextOffset"],
                                                  "revision": query.revision})
        steps = [first, second]
        while "result" in steps[-1] and steps[-1]["result"]["nextOffset"] is not None:
            steps.append(call(query, "schema_get_table", {"table": "t:wide", "section": "columns", "limit": 100,
                                                          "offset": steps[-1]["result"]["nextOffset"],
                                                          "revision": query.revision}))
        out["byte-limit.json"] = {"document": document, "revision": query.revision, "calls": steps}
    document = comparison_byte_limit_document()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "commented.json"
        path.write_text(canonical_json(document))
        query = SnapshotQuery(path)
        arguments = {"section": "differences", "limit": 100}
        steps = [call(query, "schema_compare", arguments)]
        while "result" in steps[-1] and steps[-1]["result"]["nextOffset"] is not None:
            steps.append(call(query, "schema_compare", {**arguments, "offset": steps[-1]["result"]["nextOffset"],
                                                        "revision": query.revision}))
        out["comparison-byte-limit.json"] = {"document": document, "revision": query.revision, "calls": steps}
    return {name: canonical_json(value) for name, value in out.items()}


def main() -> None:
    expected = recordings()
    if "--check" in sys.argv:
        stale = [n for n, text in expected.items() if not (TARGET / n).is_file() or (TARGET / n).read_text() != text]
        extra = sorted({p.name for p in TARGET.glob("*.json")} - set(expected))
        if stale or extra:
            raise SystemExit(f"Stale MCP conformance recordings: {', '.join(stale + extra)}")
        print(f"{len(expected)} MCP conformance recordings are current")
        return
    TARGET.mkdir(parents=True, exist_ok=True)
    for path in TARGET.glob("*.json"):
        path.unlink()
    for name, text in expected.items():
        (TARGET / name).write_text(text)
    print(f"Recorded MCP conformance for {len(expected)} documents in {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
