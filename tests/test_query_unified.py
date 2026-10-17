"""MCP queries over laranex-schema documents: declared, database and combined sources, same tools and fields."""

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer.query import MAX_RESPONSE_BYTES, SnapshotQuery

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/contract/fixtures/valid"
V1_COLUMN = {"name", "type", "timezone", "nullable", "primary_key", "autoincrement", "default", "server_default",
             "comment"}
V1_INDEX = {"name", "columns", "expressions", "unique", "predicates", "included_columns", "methods"}
V1_FOREIGN_KEY = {"id", "name", "columns", "target_table", "target_columns", "on_delete", "on_update", "deferrable",
                  "initially"}
V1_RELATIONSHIP = {"id", "name", "model", "target_model", "source_table", "target_table", "direction", "uselist",
                   "viewonly", "back_populates", "secondary", "primary_join", "secondary_join", "join_pairs"}


def every_call(query):
    yield query.overview(limit=100)
    yield query.search("id", limit=100)
    for table in query.tables:
        for section in ("columns", "indexes", "foreign_keys", "constraints", "models", "relationships"):
            yield query.get_table(table, section, limit=100)
    for model in query.models:
        yield query.get_model(model, limit=100)
    for kind in ("orm", "foreign_key"):
        yield query.relationships(None, kind, limit=100)


class UnifiedDocuments(unittest.TestCase):
    def query(self, name):
        return SnapshotQuery(FIXTURES / name)

    def test_every_source_answers_every_tool_within_bounds(self):
        for name in ("declared-sqlalchemy.json", "combined-eloquent.json", "combined-drift.json",
                     "database-postgresql-indexes.json", "database-mysql-indexes.json",
                     "database-sqlite-indexes.json"):
            with self.subTest(name=name):
                query = self.query(name)
                self.assertEqual(query.format, "laranex-schema")
                for response in every_call(query):
                    self.assertLessEqual(len(json.dumps(response).encode()), MAX_RESPONSE_BYTES)
                    self.assertEqual(response["revision"], query.revision)

    def test_overview_reports_source_and_coverage(self):
        overview = self.query("combined-drift.json").overview()
        self.assertEqual((overview["source"], overview["scope"]), ("combined", "combined_metadata"))
        coverage = overview["connections"][0]["coverage"]
        self.assertEqual(coverage["database"]["features"]["checkConstraints"], "unavailable")
        self.assertEqual(overview["counts"]["drift"], 2)
        self.assertIn("provenance", overview["items"][0])
        database = self.query("database-sqlite-indexes.json").overview()
        self.assertEqual((database["source"], database["counts"]["models"]), ("database", 0))

    def test_fields_keep_the_version_1_names_and_add_provenance_and_drift(self):
        query = self.query("combined-drift.json")
        users = next(t for t in query.tables.values() if t["name"] == "users")
        columns = query.get_table(users["id"], "columns", limit=100)
        self.assertLessEqual({"provenance", "drift"}, {*columns["table"], *columns["items"][1]})
        email = next(c for c in columns["items"] if c["name"] == "email")
        self.assertLessEqual(V1_COLUMN, set(email))
        self.assertEqual(email["drift"][0]["field"], "type")
        nickname = next(c for c in columns["items"] if c["name"] == "nickname")
        self.assertEqual(nickname["provenance"], ["declared"])
        index = query.get_table(users["id"], "indexes")["items"][0]
        self.assertLessEqual(V1_INDEX | {"parts", "backs", "primary", "provenance"}, set(index))
        fk = query.get_table(users["id"], "foreign_keys")["items"][0]
        self.assertLessEqual(V1_FOREIGN_KEY, set(fk))

    def test_index_details_survive(self):
        query = self.query("database-postgresql-indexes.json")
        accounts = next(t for t in query.tables.values() if t["name"] == "accounts")
        indexes = {i["name"]: i for i in query.get_table(accounts["id"], "indexes", limit=100)["items"]}
        self.assertEqual(indexes["accounts_live_email_idx"]["predicates"], {"postgresql": "(deleted_at IS NULL)"})
        self.assertEqual(indexes["accounts_created_at_idx"]["included_columns"], {"postgresql": ["status"]})
        self.assertEqual(indexes["accounts_tags_idx"]["methods"], {"postgresql": "gin"})
        self.assertEqual(indexes["accounts_lower_email_idx"]["expressions"], ["lower(email)"])
        self.assertEqual(indexes["accounts_email_pattern_idx"]["parts"][0]["opclass"], "text_pattern_ops")

    def test_relationships_keep_version_1_fields(self):
        query = self.query("combined-eloquent.json")
        relationships = query.relationships(None, "orm", limit=100)["items"]
        self.assertTrue(relationships)
        for relationship in relationships:
            self.assertLessEqual(V1_RELATIONSHIP | {"cardinality"}, set(relationship))
        model = next(iter(query.models))
        self.assertEqual(query.get_model(model)["model"]["id"], model)
        hits = query.search("belongsTo", limit=100)["items"]
        self.assertTrue(any(h["kind"] == "relationship" for h in hits))

    def test_revisions_pin_the_document(self):
        query = self.query("combined-drift.json")
        first = query.overview(limit=1)
        query.overview(limit=1, offset=1, revision=first["revision"])
        with self.assertRaisesRegex(ValueError, "revision differs"):
            query.overview(revision="0" * 64)

    def test_invalid_documents_fail_startup(self):
        broken = json.loads((FIXTURES / "combined-drift.json").read_text())
        broken["connections"][0]["tables"][0]["columns"][0]["nullable"] = "yes"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text(json.dumps(broken))
            with self.assertRaises(Exception):
                SnapshotQuery(path)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "the mcp extra is not installed")
class UnifiedStdio(unittest.TestCase):
    def test_mcp_serves_a_combined_document(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        async def check():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "laranex_schema_viewer", "mcp", "--snapshot", str(FIXTURES / "combined-drift.json")],
                env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            async with Client(params, read_timeout_seconds=10) as client:
                self.assertEqual(len((await client.list_tools()).tools), 5)
                result = await client.call_tool("schema_overview", {"limit": 5})
                self.assertFalse(result.is_error, result)
                value = json.loads(result.content[0].text)
                self.assertEqual(value["source"], "combined")
                result = await client.call_tool("schema_get_table", {"table": value["items"][0]["id"],
                                                                     "section": "indexes"})
                self.assertFalse(result.is_error, result)

        asyncio.run(asyncio.wait_for(check(), timeout=30))


if __name__ == "__main__":
    unittest.main()
