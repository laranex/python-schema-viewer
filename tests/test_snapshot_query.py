import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from laranex_schema_viewer.document import SchemaDocument
from laranex_schema_viewer.query import MAX_RESPONSE_BYTES, SnapshotQuery
from laranex_schema_viewer.snapshot import read_snapshot, write_snapshot
from tests.test_extraction import example_document


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.json"
        self.doc = example_document()
        write_snapshot(self.doc, self.path)
        self.query = SnapshotQuery(self.path)

    def test_roundtrip_and_atomic_write(self):
        self.assertEqual(read_snapshot(self.path), self.doc)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(self.query.overview()["counts"]["tables"], 6)

    def test_invalid_json_version_and_duplicate_entities(self):
        self.path.write_text("not json")
        with self.assertRaises(ValidationError):
            read_snapshot(self.path)
        data = self.doc.model_dump()
        data["version"] = 999
        with self.assertRaises(ValidationError):
            SchemaDocument.model_validate(data)
        data = self.doc.model_dump()
        data["tables"].append(data["tables"][0])
        with self.assertRaisesRegex(ValidationError, "Duplicate"):
            SchemaDocument.model_validate(data)

    def test_unknown_model_and_table_references_rejected(self):
        data = self.doc.model_dump()
        data["relationships"][0]["target_model"] = "missing"
        with self.assertRaisesRegex(ValidationError, "Unknown relationship model"):
            SchemaDocument.model_validate(data)

    def test_shipped_json_schema_matches_contract(self):
        from importlib.resources import files

        shipped = json.loads(files("laranex_schema_viewer").joinpath("schema.json").read_text())
        self.assertEqual(shipped, SchemaDocument.model_json_schema())

    def test_older_version_one_columns_without_timezone_remain_readable(self):
        data = self.doc.model_dump()
        for table in data["tables"]:
            for column in table["columns"]:
                column.pop("timezone")
        document = SchemaDocument.model_validate(data)
        self.assertTrue(all(c.timezone is None for t in document.tables for c in t.columns))

    def test_oversized_snapshot_rejected_before_parsing(self):
        from unittest.mock import patch

        with patch("laranex_schema_viewer.snapshot.MAX_SNAPSHOT_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "limit"):
                read_snapshot(self.path)

    def test_table_sections_and_exact_ids(self):
        table = next(t for t in self.doc.tables if t.name == "users")
        result = self.query.get_table(table.id, "indexes")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["counts"]["columns"], 5)
        with self.assertRaises(ValueError):
            self.query.get_table("users")
        with self.assertRaises(ValueError):
            self.query.get_table(table.id, "bad")

    def test_pagination_and_pinned_revision(self):
        first = self.query.overview(limit=2)
        second = self.query.overview(limit=2, offset=first["next_offset"], revision=first["revision"])
        self.assertNotEqual(first["items"], second["items"])
        write_snapshot(SchemaDocument(), self.path)
        self.assertEqual(self.query.overview()["counts"]["tables"], 6)
        self.assertEqual(SnapshotQuery(self.path).overview()["counts"]["tables"], 0)
        with self.assertRaisesRegex(ValueError, "revision"):
            self.query.overview(revision="bad")

    def test_invalid_limits_and_search(self):
        for args in [dict(limit=0), dict(limit=101), dict(limit=True), dict(offset=-1)]:
            with self.assertRaises(ValueError):
                self.query.overview(**args)
        with self.assertRaises(ValueError):
            self.query.search("")
        matches = self.query.search("USER")
        self.assertIn("table", {i["kind"] for i in matches["items"]})
        self.assertIn("model", {i["kind"] for i in matches["items"]})

    def test_foreign_keys_and_orm_are_distinct(self):
        self.assertEqual(self.query.relationships(kind="orm")["total"], 5)
        self.assertEqual(self.query.relationships(kind="foreign_key")["total"], 4)
        model = self.doc.models[0]
        self.assertEqual(self.query.get_model(model.id)["model"]["class_name"], model.class_name)
        with self.assertRaises(ValueError):
            self.query.relationships(kind="sql")

    def test_response_byte_limit(self):
        result = self.query.page([dict(value="x" * 3000) for _ in range(100)], limit=100)
        self.assertLess(len(json.dumps(result).encode()), MAX_RESPONSE_BYTES)
        self.assertLess(result["next_offset"], 100)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.query.page(["x" * MAX_RESPONSE_BYTES])

