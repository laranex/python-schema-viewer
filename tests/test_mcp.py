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

from laranex_schema_viewer.compare import compute, store
from laranex_schema_viewer.config import comparison_option
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
        self.assertEqual(len(expected), 15)
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
        document = store(document, *compute(document, [comparison_option("primary=replica")]))
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


if __name__ == "__main__":
    unittest.main()
