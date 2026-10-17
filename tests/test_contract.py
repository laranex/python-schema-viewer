"""The Python contract layer against the shared laranex-schema fixtures (tests/fixtures/contract)."""

import json
import unittest
from pathlib import Path

from laranex_schema_viewer.contract import (assert_document, build_graph, canonical_json, revision,
                                            validate_document)
from laranex_schema_viewer.contract.validate import InvalidDocumentError, UnsupportedDocumentError

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ValidFixtures(unittest.TestCase):
    def test_every_valid_fixture_passes_strict_validation(self):
        paths = sorted((FIXTURES / "fixtures" / "valid").glob("*.json"))
        self.assertEqual(len(paths), 6)
        for path in paths:
            with self.subTest(path.name):
                document, errors = validate_document(load(path), strict=True)
                self.assertEqual(errors, [])

    def test_round_trip_is_byte_identical(self):
        for path in sorted((FIXTURES / "fixtures" / "valid").glob("*.json")):
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                document = assert_document(json.loads(text), strict=True)
                self.assertEqual(canonical_json(document.to_json()), text)

    def test_stored_links_match_the_graph_builder(self):
        for path in sorted((FIXTURES / "fixtures" / "valid").glob("*.json")):
            with self.subTest(path.name):
                data = load(path)
                self.assertEqual(build_graph(data), {"links": data["links"], "derivedLinks": data["derivedLinks"]})

    def test_revision_ignores_key_order(self):
        data = load(FIXTURES / "fixtures" / "valid" / "database-sqlite-indexes.json")
        reordered = json.loads(json.dumps(data, sort_keys=False), object_pairs_hook=lambda p: dict(reversed(p)))
        self.assertEqual(revision(reordered), revision(data))


class InvalidFixtures(unittest.TestCase):
    def test_every_invalid_fixture_is_rejected(self):
        paths = sorted((FIXTURES / "fixtures" / "invalid").glob("*.json"))
        self.assertEqual(len(paths), 20)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                document, errors = validate_document(case["document"], strict=case["strict"])
                self.assertIsNone(document)
                self.assertTrue(errors)

    def test_rule_errors_use_the_shared_messages(self):
        # Shape errors come from Pydantic and are worded differently; rule errors must match TypeScript's.
        shape = {"missing-field", "unknown-property-strict"}
        for path in sorted((FIXTURES / "fixtures" / "invalid").glob("*.json")):
            if path.stem in shape:
                continue
            case = load(path)
            with self.subTest(path.name):
                _, errors = validate_document(case["document"], strict=case["strict"])
                for expected in case["errors"]:
                    self.assertTrue(any(expected in e for e in errors), f"{expected!r} not in {errors}")

    def test_strict_mode_alone_rejects_unknown_properties(self):
        case = load(FIXTURES / "fixtures" / "invalid" / "unknown-property-strict.json")
        self.assertEqual(validate_document(case["document"])[1], [])
        self.assertIn("$.connections[0].tables[0]: unknown property engine",
                      validate_document(case["document"], strict=True)[1])

    def test_typed_errors(self):
        with self.assertRaises(UnsupportedDocumentError):
            assert_document({"format": "laranex-schema-viewer", "version": 1})
        with self.assertRaises(InvalidDocumentError):
            assert_document({"format": "laranex-schema", "version": "1.0"})


class GraphConformance(unittest.TestCase):
    def test_every_graph_case(self):
        paths = sorted((FIXTURES / "conformance" / "graph").glob("*.json"))
        self.assertEqual(len(paths), 12)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                self.assertEqual(build_graph(case["input"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
