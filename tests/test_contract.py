"""The Python contract layer against the shared laranex-schema 2.0 fixtures and conformance cases.

tests/fixtures/contract is vendored unchanged from laranex-schema-viewer-ui by scripts/sync_contract.py; its manifest
records the source commit and every file's SHA-256. The Python implementation validates them independently: every
valid document passes strict validation and serializes back to the same bytes, every invalid one fails with the
expected rule, and the graph, comparison and migration conformance cases give the expected results. Inference
conformance belongs to a later milestone; its cases are vendored but not run yet.
"""

import hashlib
import json
import unittest
from pathlib import Path

from laranex_schema_viewer import model as m
from laranex_schema_viewer.contract import (assert_document, build_graph, canonical_json, document_to_json,
                                            finalize, from_json, revision, to_json, validate_document)
from laranex_schema_viewer.contract.comparison import SeverityOverride, compare_scopes
from laranex_schema_viewer.contract.migration import migration_history, migration_state
from laranex_schema_viewer.contract.shape import json_schema
from laranex_schema_viewer.contract.validate import InvalidDocumentError, UnsupportedDocumentError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "fixtures" / "contract"
MANIFEST = json.loads((CONTRACT / "manifest.json").read_text())


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cases(directory: str) -> list[Path]:
    return sorted((CONTRACT / directory).glob("*.json"))


class VendoredCopy(unittest.TestCase):
    def test_files_match_the_manifest(self):
        found = sorted(str(p.relative_to(CONTRACT)) for p in CONTRACT.rglob("*.json") if p.name != "manifest.json")
        self.assertEqual(found, sorted(MANIFEST["files"]), "files were added or removed; run scripts/sync_contract.py")
        for name, digest in MANIFEST["files"].items():
            with self.subTest(name):
                self.assertEqual(hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest(), digest,
                                 f"{name} was edited; vendored fixtures change only through scripts/sync_contract.py")
        self.assertEqual((MANIFEST["source"], len(MANIFEST["commit"])), ("laranex-schema-viewer-ui", 40))

    def test_the_package_distributes_the_same_schema(self):
        package = ROOT / "src" / "laranex_schema_viewer" / "contract" / "schema.json"
        self.assertEqual(package.read_bytes(), (CONTRACT / "schema.json").read_bytes())

    def test_the_python_shapes_describe_the_shared_schema(self):
        self.assertEqual(json_schema(), load(CONTRACT / "schema.json"))


class ValidFixtures(unittest.TestCase):
    def test_every_valid_fixture_passes_strict_validation(self):
        paths = cases("fixtures/valid")
        self.assertEqual(len(paths), 14)
        for path in paths:
            with self.subTest(path.name):
                self.assertEqual(validate_document(load(path), strict=True)[1], [])

    def test_the_model_serializes_back_to_identical_bytes(self):
        for path in cases("fixtures/valid"):
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(canonical_json(document_to_json(assert_document(json.loads(text), strict=True))),
                                 text)

    def test_stored_links_match_the_graph_builder(self):
        for path in cases("fixtures/valid"):
            with self.subTest(path.name):
                doc = assert_document(load(path), strict=True)
                self.assertEqual(build_graph(doc.connections, doc.relationships), (doc.links, doc.derived_links))

    def test_the_docs_demo_is_a_canonical_document(self):
        path = ROOT / ".gh-page" / "demo" / "schema.json"
        text = path.read_text(encoding="utf-8")
        doc = assert_document(json.loads(text), strict=True)
        self.assertEqual(canonical_json(document_to_json(finalize(doc))), text)

    def test_revision_ignores_key_order(self):
        data = load(CONTRACT / "fixtures" / "valid" / "database-sqlite-indexes.json")
        reordered = json.loads(json.dumps(data), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
        self.assertEqual(revision(reordered), revision(data))


class InvalidFixtures(unittest.TestCase):
    def test_every_invalid_fixture_fails_with_the_expected_rule(self):
        paths = cases("fixtures/invalid")
        self.assertEqual(len(paths), 53)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                document, errors = validate_document(case["document"], strict=case["strict"])
                self.assertIsNone(document)
                for expected in case["errors"]:
                    self.assertTrue(any(expected in e for e in errors), f"{expected!r} not in {errors}")

    def test_unknown_properties_fail_only_strict_validation(self):
        case = load(CONTRACT / "fixtures" / "invalid" / "unknown-property-strict.json")
        self.assertEqual(validate_document(case["document"])[1], [])

    def test_versions(self):
        data = load(CONTRACT / "fixtures" / "valid" / "declared-sqlalchemy.json")
        later = {**data, "version": "2.7"}
        self.assertEqual(validate_document(later)[1], [])
        self.assertIn("$.version: strict validation is for version 2.0, got \"2.7\"",
                      validate_document(later, strict=True)[1])
        with self.assertRaisesRegex(UnsupportedDocumentError, 'version "1.0"; this reader supports 2.x'):
            assert_document({**data, "version": "1.0"})
        with self.assertRaisesRegex(UnsupportedDocumentError, "Not a laranex-schema document"):
            assert_document({"format": "laranex-schema-viewer", "version": 1})
        with self.assertRaises(InvalidDocumentError):
            assert_document({"format": "laranex-schema", "version": "2.0"})


class Conformance(unittest.TestCase):
    def test_graph(self):
        paths = cases("conformance/graph")
        self.assertEqual(len(paths), 15)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                connections = [from_json(m.Connection, c) for c in case["input"]["connections"]]
                relationships = [from_json(m.Relationship, r) for r in case["input"]["relationships"]]
                links, derived = build_graph(connections, relationships)
                self.assertEqual({"links": to_json(links), "derivedLinks": to_json(derived)}, case["expected"])

    def test_comparison(self):
        paths = cases("conformance/comparison")
        self.assertEqual(len(paths), 14)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                given = case["input"]
                document = m.Document(source=given["document"]["source"], generator=m.Generator(name="conformance"),
                                      connections=tuple(from_json(m.Connection, c)
                                                        for c in given["document"]["connections"]))
                overrides = [SeverityOverride(object_type=o["objectType"], change=o["change"],
                                              severity=o["severity"], field=o.get("field"))
                             for o in given["overrides"]]
                comparison = compare_scopes(document, from_json(m.Scope, given["baseline"]),
                                            from_json(m.Scope, given["target"]), overrides=overrides,
                                            limit=given["limit"])
                self.assertEqual(canonical_json(to_json(comparison)), canonical_json(case["expected"]))

    def test_stored_comparisons_are_reproduced(self):
        # The options the shared fixtures were computed with (laranex-schema-viewer-ui tests/fixtures/intelligence.ts).
        overrides = {"comparison-multi-connection.json": [SeverityOverride(object_type="column", change="changed",
                                                                           field="type", severity="error")]}
        for path in cases("fixtures/valid"):
            data = load(path)
            document = assert_document(data, strict=True)
            for stored in document.comparisons:
                with self.subTest(path.name, comparison=stored.id):
                    limit = len(stored.differences) if stored.omitted else None
                    comparison = compare_scopes(document, stored.baseline, stored.target, limit=limit,
                                                overrides=overrides.get(path.name, ()))
                    self.assertEqual(to_json(comparison), to_json(stored))

    def test_migrations(self):
        paths = cases("conformance/migrations")
        self.assertEqual(len(paths), 14)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                revisions = case["input"]["revisions"]
                history = None if revisions is None else migration_history(
                    from_json(m.MigrationRevision, r) for r in revisions)
                current = case["input"]["current"]
                state = migration_state(history, None if current is None else tuple(current))
                self.assertEqual({"history": to_json(history), "status": state.status,
                                  "pending": to_json(state.pending),
                                  "unknownRevisions": list(state.unknown_revisions)}, case["expected"])


class Serialization(unittest.TestCase):
    def test_canonical_form(self):
        self.assertEqual(canonical_json({"b": 1, "a": {"d": [3, {"z": 1, "y": 2}], "c": "é/"}}),
                         '{\n  "a": {\n    "c": "é/",\n    "d": [\n      3,\n      {\n        "y": 2,\n        "z": 1\n'
                         '      }\n    ]\n  },\n  "b": 1\n}\n')

    def test_diagnostic_ids_follow_the_rule(self):
        diagnostic = m.Diagnostic(kind="declared_only", category="reconciliation", severity="warning",
                                  certainty="certain", subject=m.Subject(type="column", id="t", columns=("a",)),
                                  message="x")
        expected = "n" + hashlib.sha256(
            '["diagnostic","declared_only","column","t",["a"],"x"]'.encode()).hexdigest()[:16]
        self.assertEqual((diagnostic.id, to_json(diagnostic)["id"]), (expected, expected))

    def test_attribute_names_are_mapped_by_the_serializer_only(self):
        drift = m.Drift(field="primary_key", database=["a"], declared=["b"])
        self.assertEqual(to_json(drift), {"field": "primaryKey", "database": ["a"], "declared": ["b"]})
        self.assertEqual(from_json(m.Drift, to_json(drift)), drift)
        keys = m.Keys(source=m.Endpoint(table="a", columns=("x",)), target=m.Endpoint(table="b", columns=("y",)))
        self.assertEqual(to_json(keys), {"from": {"table": "a", "columns": ["x"]}, "to": {"table": "b", "columns": ["y"]}})


if __name__ == "__main__":
    unittest.main()
