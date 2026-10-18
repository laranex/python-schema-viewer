"""The Python contract layer against the shared laranex-schema 2.0 fixtures and conformance cases.

tests/fixtures/contract is installed unchanged from the laranex-schema-viewer-ui npm tarball that ui.lock.json pins by
scripts/fetch_ui.py, which records every file's SHA-256 in ui.stamp.json. The Python implementation validates them independently: every
valid document passes strict validation and serializes back to the same bytes, every invalid one fails with the
expected rule, the graph, comparison, request, coverage-gap, override, comparison-diagnostic, store and migration
conformance cases give byte-identical results, and the gate conformance cases give byte-identical reports through
gate_document. Inference conformance belongs to a later
milestone; its cases are fetched but not run yet.
"""

import hashlib
import json
import unittest
from pathlib import Path

from laranex_schema_viewer import model as m
from laranex_schema_viewer.contract import (assert_document, build_graph, canonical_json, document_to_json,
                                            finalize, from_json, revision, to_json, validate_document)
from laranex_schema_viewer.contract.comparison import (DIFFERENCE_FIELDS, SeverityOverride, compare_scopes,
                                                       comparison_diagnostics, coverage_gap_text, coverage_gaps,
                                                       expand_comparison_requests, scope_covered, store_comparisons,
                                                       validate_severity_overrides)
from laranex_schema_viewer.contract.gate import UNPARSED, gate_document, gate_json_schema, validate_gate_report
from laranex_schema_viewer.contract.migration import migration_history, migration_state
from laranex_schema_viewer.contract.shape import json_schema
from laranex_schema_viewer.contract.validate import InvalidDocumentError, UnsupportedDocumentError

from hatch_build import check_assets

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "fixtures" / "contract"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cases(directory: str) -> list[Path]:
    return sorted((CONTRACT / directory).glob("*.json"))


def compared_document(data) -> m.Document:
    """The part of a document a comparison reads ({source, connections}) as a model document."""
    return m.Document(source=data["source"], generator=m.Generator(name="conformance"),
                      connections=tuple(from_json(m.Connection, c) for c in data["connections"]))


class FetchedAssets(unittest.TestCase):
    def test_files_match_the_stamp_and_the_lock(self):
        # Every installed file has the SHA-256 ui.stamp.json records, and the stamp names the locked package and
        # version. A local build installed with --allow-unverified is accepted here, but never by sdist/wheel builds.
        stamp = check_assets(ROOT, allow_unverified=True)
        lock = json.loads((ROOT / "ui.lock.json").read_text())
        self.assertEqual((stamp["name"], stamp["version"]), (lock["name"], lock["version"]))
        self.assertTrue(any(name.startswith("tests/fixtures/contract/conformance/") for name in stamp["files"]))

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

    def test_requests(self):
        paths = cases("conformance/requests")
        self.assertEqual(len(paths), 13)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                pairs, errors = expand_comparison_requests(compared_document(case["input"]["document"]),
                                                           case["input"]["requests"])
                result = {"pairs": [{"baseline": to_json(p.baseline), "target": to_json(p.target),
                                     "request": p.request} for p in pairs],
                          "errors": [{"kind": e.kind, "request": e.request, "message": e.message} for e in errors]}
                self.assertEqual(canonical_json(result), canonical_json(case["expected"]))

    def test_coverage_gaps(self):
        paths = cases("conformance/coverage-gaps")
        self.assertEqual(len(paths), 11)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                document = compared_document(case["input"]["document"])
                scope = from_json(m.Scope, case["input"]["scope"])
                connection = next(c for c in document.connections if c.id == scope.connection)
                coverage = connection.coverage.declared if document.source == "declared" else connection.coverage.database
                result = {"gaps": coverage_gaps(document, scope),
                          "covered": scope_covered(coverage, connection, scope.schema)}
                self.assertEqual(canonical_json(result), canonical_json(case["expected"]))

    def test_overrides(self):
        paths = cases("conformance/overrides")
        self.assertEqual(len(paths), 9)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                self.assertEqual(canonical_json({"errors": validate_severity_overrides(case["input"]["overrides"])}),
                                 canonical_json(case["expected"]))

    def test_comparison_diagnostics(self):
        paths = cases("conformance/comparison-diagnostics")
        self.assertEqual(len(paths), 6)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                diagnostics = comparison_diagnostics(compared_document(case["input"]["document"]),
                                                     from_json(m.Comparison, case["input"]["comparison"]))
                self.assertEqual(canonical_json({"diagnostics": to_json(diagnostics)}),
                                 canonical_json(case["expected"]))

    def test_store(self):
        paths = cases("conformance/store")
        self.assertEqual(len(paths), 4)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                document = assert_document(case["input"]["document"], strict=True)
                expected = assert_document(case["expected"]["document"], strict=True)
                comparisons = [from_json(m.Comparison, c) for c in case["input"]["comparisons"]]
                self.assertEqual(canonical_json(document_to_json(store_comparisons(document, comparisons))),
                                 canonical_json(case["expected"]["document"]))
                self.assertEqual(canonical_json(document_to_json(store_comparisons(expected, comparisons))),
                                 canonical_json(case["expected"]["document"]))

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


class ProducerHelpers(unittest.TestCase):
    """The checks laranex-schema-viewer-ui's "producer helpers" tests make of the same functions."""

    def setUp(self):
        self.tenants = assert_document(load(CONTRACT / "fixtures" / "valid" / "comparison-tenant-schemas.json"),
                                       strict=True)

    def test_compare_scopes_rejects_invalid_overrides(self):
        stored = self.tenants.comparisons[0]
        with self.assertRaisesRegex(ValueError, r"^Invalid severity overrides: overrides\[0\]\.field applies only to "
                                                "changed differences, not missing$"):
            compare_scopes(self.tenants, stored.baseline, stored.target,
                           overrides=[SeverityOverride(object_type="column", change="missing", field="type",
                                                       severity="error")])

    def test_difference_fields_list_every_field_compare_scopes_reports(self):
        for c in self.tenants.comparisons:
            for d in c.differences:
                for f in d.fields:
                    self.assertIn(f.field, DIFFERENCE_FIELDS[d.object_type])

    def test_complete_follows_from_the_coverage_gaps_and_the_omitted_differences(self):
        doc = assert_document(load(CONTRACT / "fixtures" / "valid" / "comparison-incomplete.json"), strict=True)
        for c in doc.comparisons:
            gaps = [*coverage_gaps(doc, c.baseline), *coverage_gaps(doc, c.target)]
            self.assertEqual(c.complete, c.omitted == 0 and not gaps)
            self.assertEqual([coverage_gap_text(g) for g in gaps],
                             ["checkConstraints unavailable", "checkConstraints unavailable"])

    def test_expansion_never_raises(self):
        empty = m.Document(source="database", generator=m.Generator(name="conformance"))
        _, errors = expand_comparison_requests(empty, [{"baseline": {"connection": "a", "schema": None},
                                                        "target": {"connection": "a", "schema": "*"}}])
        self.assertEqual([e.kind for e in errors], ["unknown_connection"])

    def test_every_valid_fixture_stores_the_template_diagnostics(self):
        for path in cases("fixtures/valid"):
            with self.subTest(path.name):
                doc = assert_document(load(path), strict=True)
                stored = [d for d in doc.diagnostics if d.kind == "comparison_incomplete"]
                self.assertEqual(stored, [d for c in doc.comparisons for d in comparison_diagnostics(doc, c)])
                self.assertEqual(store_comparisons(doc, doc.comparisons), doc)

    def test_storing_two_comparisons_with_one_id_raises(self):
        stored = self.tenants.comparisons[0]
        with self.assertRaisesRegex(ValueError, f"^Comparison {stored.id} is listed more than once$"):
            store_comparisons(self.tenants, [stored, stored])


class GateReports(unittest.TestCase):
    """laranex-schema-gate 1.0: gate_document against the shared gate conformance cases, the shared JSON Schema, and
    the valid and invalid reports. tests/test_check.py runs the same cases through the check command."""

    def test_gate_conformance(self):
        paths = cases("conformance/gate")
        self.assertEqual(len(paths), 23)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                given = case["input"]
                report = gate_document(given["document"], thresholds=given["thresholds"],
                                       requested=given["requested"])
                self.assertEqual(canonical_json(report), canonical_json(case["expected"]))
                self.assertEqual(validate_gate_report(report, strict=True)[1], [])

    def test_unparsed_input(self):
        report = gate_document(UNPARSED, requested=[{"baseline": {"connection": "b", "schema": None},
                                                     "target": {"connection": "a", "schema": "*"}}])
        self.assertEqual(report["errors"], [{"kind": "invalid_document",
                                             "message": "The input could not be parsed as JSON."}])
        self.assertEqual((report["exitCode"], report["comparisons"], len(report["requested"])), (2, [], 1))
        # JSON null is parsed input, not a document.
        self.assertEqual(gate_document(None)["errors"][0]["message"], "The input is not a laranex-schema document.")

    def test_the_python_shape_describes_the_shared_schema(self):
        self.assertEqual(gate_json_schema(), load(CONTRACT / "gate-report.schema.json"))
        package = ROOT / "src" / "laranex_schema_viewer" / "contract" / "gate-report.schema.json"
        self.assertEqual(package.read_bytes(), (CONTRACT / "gate-report.schema.json").read_bytes())

    def test_valid_reports(self):
        paths = cases("fixtures/gate/valid")
        self.assertEqual([p.name for p in paths], ["drift.json", "incomplete.json", "invalid.json", "migrations.json",
                                                   "passed.json", "requested.json"])
        for path in paths:
            with self.subTest(path.name):
                report, errors = validate_gate_report(load(path), strict=True)
                self.assertEqual(errors, [])
                self.assertEqual(canonical_json(report), path.read_text(encoding="utf-8"))

    def test_valid_reports_are_reproduced_from_their_documents(self):
        # The options laranex-schema-viewer-ui's tests/fixtures/gate.ts made them with.
        def document(name):
            return load(CONTRACT / "fixtures" / "valid" / f"{name}.json")

        wildcard = [{"baseline": {"connection": "app", "schema": "tenant_a"},
                     "target": {"connection": "app", "schema": "*"}}]
        made = {
            "passed.json": gate_document(document("inferred-associations"), thresholds={"severity": "error"}),
            "drift.json": gate_document(document("comparison-tenant-schemas")),
            "requested.json": gate_document(document("comparison-tenant-schemas"), requested=wildcard),
            "incomplete.json": gate_document(document("comparison-incomplete"),
                                             thresholds={"severity": "error", "certainty": "likely"}),
            "migrations.json": gate_document(document("migrations-alembic")),
            "invalid.json": gate_document({**document("database-sqlite-indexes"), "version": "3.0"}),
        }
        for name, report in made.items():
            with self.subTest(name):
                self.assertEqual(canonical_json(report),
                                 (CONTRACT / "fixtures" / "gate" / "valid" / name).read_text(encoding="utf-8"))

    def test_invalid_reports_fail_with_the_expected_rule(self):
        paths = cases("fixtures/gate/invalid")
        self.assertEqual(len(paths), 13)
        for path in paths:
            case = load(path)
            with self.subTest(path.name):
                report, errors = validate_gate_report(case["report"], strict=True)
                self.assertIsNone(report)
                for expected in case["errors"]:
                    self.assertTrue(any(expected in e for e in errors), f"{expected!r} not in {errors}")

    def test_versions_and_shape(self):
        report = load(CONTRACT / "fixtures" / "gate" / "valid" / "passed.json")
        self.assertEqual(validate_gate_report({**report, "version": "1.3"})[1], [])
        self.assertIn('$.version: strict validation is for version 1.0, got "1.3"',
                      validate_gate_report({**report, "version": "1.3"}, strict=True)[1])
        self.assertIn("$: unknown property extra", validate_gate_report({**report, "extra": 1}, strict=True)[1])
        self.assertEqual(validate_gate_report({**report, "extra": 1})[1], [])
        self.assertIn("$.exitCode: expected 0, 1 or 2, got true", validate_gate_report({**report, "exitCode": True})[1])


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
