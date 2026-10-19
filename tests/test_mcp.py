"""The MCP query service on laranex-schema 2.0 documents, against the recorded conformance responses.

tests/fixtures/mcp-conformance holds, for every shared valid contract fixture, the response (or tool error) of every
tool, table section, table, model and stored comparison, with pagination and invalid arguments; byte-limit.json and
comparison-byte-limit.json cover pages cut by the 64 KiB limit. scripts/record_mcp_conformance.py writes them; docs/mcp.md specifies the format they follow.
"""

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer.compare import compute
from laranex_schema_viewer.config import comparison_option
from laranex_schema_viewer.contract.comparison import store_comparisons
from laranex_schema_viewer.query import MAX_RESPONSE_BYTES, SnapshotQuery, size
from laranex_schema_viewer.snapshot import write_document
from tests.test_comparison import large_document

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/contract/fixtures/valid"
RECORDINGS = ROOT / "tests/fixtures/mcp-conformance"

sys.path.insert(0, str(ROOT / "scripts"))
import record_mcp_conformance as recorder  # noqa: E402

sys.path.remove(str(ROOT / "scripts"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Conformance(unittest.TestCase):
    def test_every_valid_fixture_has_current_recordings(self):
        expected = recorder.recordings()
        self.assertEqual(sorted(p.name for p in RECORDINGS.glob("*.json")), sorted(expected))
        self.assertEqual(len(expected), 16)
        for name, text in expected.items():
            with self.subTest(name):
                self.assertEqual((RECORDINGS / name).read_text(encoding="utf-8"), text,
                                 "stale; run scripts/record_mcp_conformance.py after an intended response change")

    def test_responses_stay_within_the_byte_limit(self):
        for path in RECORDINGS.glob("*.json"):
            for call in load(path)["calls"]:
                if "result" in call:
                    self.assertLessEqual(size(call["result"]), MAX_RESPONSE_BYTES)
        steps = load(RECORDINGS / "byte-limit.json")["calls"]
        self.assertEqual([len(s["result"]["items"]) for s in steps[:-1]], [22, 18])
        self.assertEqual(steps[-1]["error"], "One result exceeds the response byte limit")
        steps = load(RECORDINGS / "comparison-byte-limit.json")["calls"]
        self.assertEqual([(len(s["result"]["items"]), s["result"]["total"]) for s in steps], [(21, 40), (19, 40)])


class Responses(unittest.TestCase):
    def test_items_are_the_documents_own_objects(self):
        for path in sorted(FIXTURES.glob("*.json")):
            document = load(path)
            query = SnapshotQuery(path)
            with self.subTest(path.name):
                for connection in document["connections"]:
                    for table in connection["tables"]:
                        for section, key in (("columns", "columns"), ("indexes", "indexes"),
                                             ("foreign_keys", "foreignKeys"), ("constraints", "constraints")):
                            result = query.get_table(table["id"], section, limit=100)
                            self.assertEqual(result["items"], table[key])
                        header = query.get_table(table["id"])["table"]
                        self.assertEqual(header, {k: v for k, v in table.items()
                                                  if k not in ("columns", "indexes", "foreignKeys", "constraints")})
                for model in document["models"]:
                    result = query.get_model(model["id"], limit=100)
                    self.assertEqual(result["model"], model)
                    self.assertEqual(result["items"], [r for r in document["relationships"] if r["model"] == model["id"]])
                self.assertEqual(query.relationships(limit=100)["items"], document["relationships"][:100])

    def test_overview(self):
        query = SnapshotQuery(FIXTURES / "combined-drift.json")
        overview = query.overview()
        self.assertEqual((overview["version"], overview["source"]), ("2.0", "combined"))
        self.assertEqual(overview["connections"][0]["coverage"]["database"]["features"]["checkConstraints"],
                         "unavailable")
        self.assertEqual((overview["counts"]["drift"], overview["diagnosticCounts"]["category"]["drift"]), (2, 2))
        self.assertEqual(set(overview["items"][0]), {"id", "connection", "name", "schema", "provenance", "counts"})
        self.assertEqual(list(overview)[-6:], ["revision", "total", "offset", "limit", "items", "nextOffset"])

    def test_comparisons_and_their_differences(self):
        document = load(FIXTURES / "comparison-tenant-schemas.json")
        query = SnapshotQuery(FIXTURES / "comparison-tenant-schemas.json")
        [stored] = document["comparisons"]
        [summary] = query.compare()["items"]
        self.assertEqual({k: v for k, v in summary.items() if k not in ("labels", "counts")},
                         {k: v for k, v in stored.items() if k != "differences"})
        self.assertEqual(summary["labels"], {"baseline": "app:tenant_a", "target": "app:tenant_b"})
        differences = query.compare("differences", stored["id"], limit=100)
        self.assertEqual([i["difference"] for i in differences["items"]], stored["differences"])
        self.assertEqual((differences["comparisons"], differences["omitted"]), (1, 0))
        likely = query.compare("differences", target="app:tenant_b", certainty="likely", limit=100)["items"]
        self.assertEqual([i["difference"] for i in likely],
                         [d for d in stored["differences"] if d["certainty"] == "likely"])
        self.assertEqual(query.compare(baseline="app:tenant_b")["total"], 0)
        self.assertEqual(list(differences)[-6:], ["revision", "total", "offset", "limit", "items", "nextOffset"])

    def test_large_comparisons_page_within_the_byte_limit(self):
        document = large_document()
        document = store_comparisons(document, compute(document, [comparison_option("primary=replica")]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.json"
            write_document(document, path)
            query = SnapshotQuery(path)
        [comparison] = document.comparisons
        seen, offset = [], 0
        while offset is not None:
            page = query.compare("differences", comparison.id, limit=100, offset=offset, revision=query.revision)
            self.assertLessEqual(size(page), MAX_RESPONSE_BYTES)
            self.assertEqual((page["total"], page["omitted"]), (500, comparison.omitted))
            seen += [i["difference"]["id"] for i in page["items"]]
            offset = page["nextOffset"]
        self.assertEqual(seen, [d.id for d in comparison.differences])
        self.assertEqual(query.compare("differences", object_type="table", change="missing", limit=100)["total"], 40)

    def test_foreign_keys_and_relationships_stay_apart(self):
        query = SnapshotQuery(FIXTURES / "combined-eloquent.json")
        orm = query.relationships(kind="orm", limit=100)["items"]
        physical = query.relationships(kind="foreign_key", limit=100)["items"]
        self.assertTrue(orm and physical)
        self.assertTrue(all("foreignTable" not in r for r in orm))
        self.assertTrue(all(set(f) == {"table", "foreignKey"} for f in physical))


@unittest.skipUnless(importlib.util.find_spec("mcp"), "the mcp extra is not installed")
class Stdio(unittest.TestCase):
    def test_mcp_serves_a_combined_document(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        path = FIXTURES / "combined-drift.json"

        async def check():
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "laranex_schema_viewer", "mcp", "--snapshot", str(path)],
                env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            async with Client(params, read_timeout_seconds=10) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual(sorted(t.name for t in tools),
                                 ["schema_compare", "schema_get_model", "schema_get_relationships",
                                  "schema_get_table", "schema_overview", "schema_search"])
                self.assertTrue(all(t.annotations.read_only_hint for t in tools))
                result = await client.call_tool("schema_overview", {"limit": 5})
                self.assertFalse(result.is_error, result)
                self.assertEqual(json.loads(result.content[0].text), SnapshotQuery(path).overview(limit=5))
                table = json.loads(result.content[0].text)["items"][0]["id"]
                result = await client.call_tool("schema_get_table", {"table": table, "section": "diagnostics"})
                self.assertFalse(result.is_error, result)
                result = await client.call_tool("schema_get_table", {"table": table, "section": "rows"})
                self.assertTrue(result.is_error)
                result = await client.call_tool("schema_compare", {"section": "differences"})
                self.assertFalse(result.is_error, result)
                self.assertEqual(json.loads(result.content[0].text), SnapshotQuery(path).compare("differences"))
                result = await client.call_tool("schema_compare", {"section": "differences", "severity": "fatal"})
                self.assertTrue(result.is_error)

        asyncio.run(asyncio.wait_for(check(), timeout=30))


# Strings an SDK could mistake for JSON; IDs and revisions are opaque, so each must reach the query service unchanged.
JSON_LOOKING = ("null", "true", "false", "0", "1.5", "[]", "[1]", '{"a":1}', '["default",null,"missing"]', '""', "",
                " null")
OPTIONAL_STRINGS = {"schema_overview": ("revision",), "schema_search": ("revision",),
                    "schema_get_table": ("revision",), "schema_get_model": ("revision",),
                    "schema_get_relationships": ("table", "revision"),
                    "schema_compare": ("comparison", "baseline", "target", "revision")}
REQUIRED = {"schema_search": {"query_text": "user"}, "schema_get_table": {"table": None}, "schema_get_model": {"model": None}}


@unittest.skipUnless(importlib.util.find_spec("mcp") and importlib.util.find_spec("sqlalchemy"),
                     "the mcp extra or SQLAlchemy is not installed")
class StdioArguments(unittest.TestCase):
    """Arguments over a genuine SDK client and stdio reach the query service exactly as sent: JSON-array table and
    model IDs (SQLAlchemy's) and JSON-looking strings stay strings, never lists or null."""

    def documents(self, tmp):
        from laranex_schema_viewer.adapters.sqlalchemy import SQLAlchemyAdapter
        from tests.fixtures import sqlalchemy_models as models
        from tests.test_multiconnection import declared

        single = SQLAlchemyAdapter({"default": models.Base.metadata}, [models.User, models.Team, models.Audit],
                                   ROOT).extract()
        multi = declared()
        multi = store_comparisons(multi, compute(multi, [comparison_option("default=analytics")]))
        paths = {"combined-multi-connection": FIXTURES / "combined-multi-connection.json"}
        for name, document in (("single", single), ("multi", multi)):
            paths[name] = Path(tmp) / f"{name}.json"
            write_document(document, paths[name])
        return paths

    def cases(self, query):
        table, model = next(iter(query.tables)), next(iter(query.models))
        yield "schema_get_relationships", {}
        for kind in ("orm", "foreign_key"):
            for id in query.tables:
                yield "schema_get_relationships", {"table": id, "kind": kind, "limit": 100}
            yield "schema_get_relationships", {"table": None, "kind": kind}
            yield "schema_get_relationships", {"table": '["default",null,"missing"]', "kind": kind}
        first = recorder.call(query, "schema_get_relationships", {"table": table, "limit": 1})["result"]
        if first["nextOffset"] is not None:
            yield "schema_get_relationships", {"table": table, "limit": 1, "offset": first["nextOffset"],
                                               "revision": query.revision}
        for id in query.tables:
            yield "schema_get_table", {"table": id, "section": "relationships"}
        for id in query.models:
            yield "schema_get_model", {"model": id}
        yield "schema_get_model", {"model": '["model","missing"]'}
        for comparison in query.comparisons:
            yield "schema_compare", {"section": "differences", "comparison": comparison, "limit": 100}
        yield "schema_compare", {"baseline": "default", "target": "analytics"}
        for tool, names in OPTIONAL_STRINGS.items():
            required = {k: v or (table if k == "table" else model) for k, v in REQUIRED.get(tool, {}).items()}
            for name in names:
                yield tool, {**required, name: None}
                for value in JSON_LOOKING:
                    yield tool, {**required, name: value}

    async def check_document(self, path):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        query = SnapshotQuery(path)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "laranex_schema_viewer", "mcp", "--snapshot", str(path)],
            env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        async with Client(params, read_timeout_seconds=30) as client:
            for tool in (await client.list_tools()).tools:
                self.assertIsNone(tool.output_schema)
                for name in OPTIONAL_STRINGS[tool.name]:
                    self.assertEqual(tool.input_schema["properties"][name],
                                     {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                                      "title": name.title()})
            for tool, arguments in self.cases(query):
                with self.subTest(path.name, tool=tool, arguments=arguments):
                    expected = recorder.call(query, tool, {k: v for k, v in arguments.items() if v is not None})
                    result = await client.call_tool(tool, arguments)
                    [content] = result.content
                    self.assertIsNone(result.structured_content)
                    if "result" in expected:
                        self.assertFalse(result.is_error, content.text)
                        self.assertEqual(json.loads(content.text), expected["result"])
                    else:
                        self.assertTrue(result.is_error)
                        self.assertEqual(content.text, f"Error executing tool {tool}: {expected['error']}")
            filtered = await client.call_tool("schema_get_relationships",
                                              {"table": next(iter(query.tables)), "kind": "foreign_key"})
            self.assertEqual(json.loads(filtered.content[0].text)["table"], next(iter(query.tables)))
            for tool, arguments in (("schema_get_relationships", {"table": ["default", None, "users"]}),
                                    ("schema_get_relationships", {"table": 1}),
                                    ("schema_get_relationships", {"table": True}),
                                    ("schema_get_relationships", {"kind": "null"}),
                                    ("schema_overview", {"revision": {"a": 1}}),
                                    ("schema_overview", {"limit": "5"}),
                                    ("schema_compare", {"comparison": 1}),
                                    ("schema_compare", {"section": "differences", "severity": "null"}),
                                    ("schema_compare", {"section": "differences", "object_type": "[]"})):
                with self.subTest(path.name, tool=tool, arguments=arguments):
                    result = await client.call_tool(tool, arguments)
                    self.assertTrue(result.is_error)
                    self.assertIn(f"validation error for {tool}Arguments", result.content[0].text)

    def test_ids_and_json_looking_strings_reach_the_query_service_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in self.documents(tmp).values():
                asyncio.run(asyncio.wait_for(self.check_document(path), timeout=120))


if __name__ == "__main__":
    unittest.main()
