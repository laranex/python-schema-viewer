"""Saved documents (reading, strict writing) and the bounded query service the MCP tools use."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laranex_schema_viewer import model as m
from laranex_schema_viewer.contract import InvalidDocumentError, document_to_json
from laranex_schema_viewer.query import MAX_RESPONSE_BYTES, SnapshotQuery, size
from laranex_schema_viewer.snapshot import read_document, write_document
from tests.test_extraction import example_document


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.json"
        self.doc = example_document()
        write_document(self.doc, self.path)
        self.query = SnapshotQuery(self.path)

    def test_roundtrip_and_atomic_write(self):
        snapshot = read_document(self.path)
        self.assertEqual(snapshot.document, self.doc)
        self.assertEqual(snapshot.data, document_to_json(self.doc))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(self.query.overview()["counts"]["tables"], 6)

    def test_invalid_json_versions_and_duplicate_entities(self):
        data = document_to_json(self.doc)
        self.path.write_text("not json")
        with self.assertRaisesRegex(ValueError, "is not JSON"):
            read_document(self.path)
        self.path.write_text(json.dumps({**data, "version": "1.0"}))
        with self.assertRaisesRegex(ValueError, 'version "1.0"; this reader supports 2.x; rebuild it'):
            read_document(self.path)
        self.path.write_text(json.dumps({**data, "version": "2.3", "later": True}))
        self.assertEqual(read_document(self.path).document, self.doc)  # Readers accept later 2.x minors.
        data["connections"][0]["tables"].append(data["connections"][0]["tables"][0])
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(InvalidDocumentError, "Duplicate table id"):
            read_document(self.path)

    def test_writes_validate_strictly_and_leave_the_old_file(self):
        before = self.path.read_bytes()
        broken = m.replace(self.doc, models=(m.replace(self.doc.models[0], tables=("missing",)),))
        with self.assertRaisesRegex(InvalidDocumentError, "Unknown model table"):
            write_document(broken, self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_oversized_snapshot_rejected_before_parsing(self):
        with patch("laranex_schema_viewer.snapshot.MAX_SNAPSHOT_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "limit"):
                read_document(self.path)

    def test_table_sections_and_exact_ids(self):
        table = next(t for t in self.doc.tables() if t.name == "users")
        result = self.query.get_table(table.id, "indexes")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["counts"]["columns"], 5)
        self.assertEqual(result["items"], document_to_json(self.doc)["connections"][0]["tables"][5]["indexes"])
        self.assertNotIn("columns", result["table"])
        with self.assertRaises(ValueError):
            self.query.get_table("users")
        with self.assertRaises(ValueError):
            self.query.get_table(table.id, "bad")

    def test_pagination_and_pinned_revision(self):
        first = self.query.overview(limit=2)
        second = self.query.overview(limit=2, offset=first["nextOffset"], revision=first["revision"])
        self.assertNotEqual(first["items"], second["items"])
        write_document(m.Document(source="declared", generator=m.Generator(name="x")), self.path)
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
        foreign_keys = self.query.relationships(kind="foreign_key")
        self.assertEqual(foreign_keys["total"], 4)
        self.assertEqual(set(foreign_keys["items"][0]), {"table", "foreignKey"})
        model = self.doc.models[0]
        self.assertEqual(self.query.get_model(model.id)["model"]["className"], model.class_name)
        with self.assertRaises(ValueError):
            self.query.relationships(kind="sql")

    def test_response_byte_limit(self):
        result = self.query.page([dict(value="x" * 3000) for _ in range(100)], limit=100)
        self.assertLessEqual(size(result), MAX_RESPONSE_BYTES)
        self.assertLess(result["nextOffset"], 100)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.query.page(["x" * MAX_RESPONSE_BYTES])


if __name__ == "__main__":
    unittest.main()
