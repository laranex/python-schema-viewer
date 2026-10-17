"""The drift gate (`check`): findings, errors, thresholds, exit codes, the JSON report and its determinism.

The report format is checked against the shared fixtures in test_contract.py; here the valid fixtures are also
reproduced from the documents they were made from. The command reads only the saved document: an audit-hook test
proves it opens no socket, subprocess or database and imports no application code. CIEndToEnd builds a disposable
PostgreSQL multi-tenant database with comparisons and gates it, as a CI job would (needs LSV_TEST_POSTGRES_URL).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer import model as m
from laranex_schema_viewer.check import check, errors as gate_errors, findings as gate_findings
from laranex_schema_viewer.config import CheckConfig, load_config
from laranex_schema_viewer.contract import assert_document, canonical_json
from laranex_schema_viewer.contract.gate import gate_report, qualifies, validate_gate_report

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "fixtures" / "contract"
VALID = CONTRACT / "fixtures" / "valid"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fixture(name: str) -> m.Document:
    return assert_document(load(VALID / f"{name}.json"), strict=True)


def diagnostic(severity: str, certainty: str, kind: str = "column_drift", category: str = "drift") -> m.Diagnostic:
    return m.Diagnostic(kind=kind, category=category, severity=severity, certainty=certainty,
                        subject=m.Subject(type="document", label="doc"), message=f"{severity} {certainty}")


def run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))
    return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args], cwd=cwd, env=environment,
                          capture_output=True, text=True, timeout=120)


class Conformance(unittest.TestCase):
    def test_valid_reports_are_reproduced(self):
        # Each fixture's findings and errors come from the shared valid documents, selected as gate.ts selects them;
        # the Python finding and error rules (check.py) and report assembly must give the same bytes.
        def document(name):
            return assert_document(load(CONTRACT / "fixtures" / "valid" / f"{name}.json"), strict=True)

        tenants, migrations, incomplete = (document(n) for n in ("comparison-tenant-schemas", "migrations-alembic",
                                                                  "comparison-incomplete"))
        differences = [f for f in gate_findings(tenants) if f["source"] == "comparison"]
        behind = next(t for t in migrations.migrations if t.status == "behind")
        [migration] = [f for f in gate_findings(migrations) if f["id"] == behind.id]
        [incomplete_error] = gate_errors(incomplete, "2.0")
        expected = {
            "passed.json": gate_report({"severity": "warning", "certainty": "certain"},
                                       [f for f in differences if f["severity"] == "info"]),
            "drift.json": gate_report({"severity": "warning", "certainty": "certain"}, [*differences, migration]),
            "incomplete.json": gate_report({"severity": "error", "certainty": "likely"},
                                           [f for f in gate_findings(incomplete) if f["source"] == "comparison"],
                                           [incomplete_error]),
        }
        for name, report in expected.items():
            with self.subTest(name):
                self.assertEqual(canonical_json(report),
                                 (CONTRACT / "fixtures" / "gate" / "valid" / name).read_text(encoding="utf-8"))
        # The whole document's check adds its diagnostics as findings; errors and exit code are the fixture's.
        whole = check(incomplete, "2.0", {"severity": "error", "certainty": "likely"})
        fixture = load(CONTRACT / "fixtures" / "gate" / "valid" / "incomplete.json")
        self.assertEqual((whole["errors"], whole["exitCode"]), (fixture["errors"], 2))
        self.assertEqual([f for f in whole["findings"] if f["source"] == "comparison"], fixture["findings"])




class Thresholds(unittest.TestCase):
    def test_severity_and_certainty_matrix(self):
        found = [diagnostic(s, c) for s in ("error", "warning", "info") for c in ("certain", "likely")]
        document = m.Document(source="declared", generator=m.Generator(name="test"), diagnostics=tuple(found))
        expected = {("error", "certain"): 1, ("error", "likely"): 2, ("warning", "certain"): 2,
                    ("warning", "likely"): 4, ("info", "certain"): 3, ("info", "likely"): 6}
        for (severity, certainty), qualifying in expected.items():
            with self.subTest(severity=severity, certainty=certainty):
                report = check(document, "2.0", {"severity": severity, "certainty": certainty})
                self.assertEqual(report["counts"], {"findings": 6, "qualifying": qualifying, "error": 2,
                                                    "warning": 2, "info": 2})
                self.assertEqual((report["exitCode"], report["passed"]), (1, False))
                self.assertEqual(validate_gate_report(report, strict=True)[1], [])
        info = m.Document(source="declared", generator=m.Generator(name="test"),
                          diagnostics=(diagnostic("info", "certain"), diagnostic("warning", "likely")))
        report = check(info, "2.0", {"severity": "warning", "certainty": "certain"})
        self.assertEqual((report["exitCode"], report["passed"], report["counts"]["qualifying"]), (0, True, 0))
        self.assertTrue(qualifies({"severity": "error", "certainty": "likely"},
                                  {"severity": "info", "certainty": "likely"}))

    def test_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(load_config(root).check, CheckConfig())
            (root / "pyproject.toml").write_text(
                "[tool.laranex-schema-viewer.check]\nseverity = 'error'\ncertainty = 'likely'\n")
            self.assertEqual(load_config(root).check, CheckConfig(severity="error", certainty="likely"))
            for text, message in (("severity = 'fatal'", "severity must be one of error, warning, info"),
                                  ("certainty = 1", "certainty must be one of certain, likely"),
                                  ("fail_on = 'x'", "Unknown [tool.laranex-schema-viewer.check] keys: fail_on")):
                with self.subTest(text):
                    (root / "pyproject.toml").write_text(f"[tool.laranex-schema-viewer.check]\n{text}\n")
                    with self.assertRaisesRegex(ValueError, message.replace("[", r"\[").replace("]", r"\]")):
                        load_config(root)
            (root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\ncheck = 'error'\n")
            with self.assertRaisesRegex(ValueError, "must be a table"):
                load_config(root)


class Errors(unittest.TestCase):
    def test_errors_win_over_findings_and_never_pass(self):
        document = fixture("comparison-incomplete")
        for severity, certainty in (("error", "certain"), ("info", "likely")):
            report = check(document, "2.0", {"severity": severity, "certainty": certainty})
            self.assertEqual((report["exitCode"], report["passed"]), (2, False))
        # Nothing listed, nothing qualifying: an incomplete comparison still fails with 2.
        [comparison] = document.comparisons
        empty = m.replace(document, diagnostics=(), comparisons=(m.replace(
            comparison, differences=(), omitted=25,
            summary=m.Summary(missing=25, extra=0, changed=0)),))
        report = check(empty, "2.0", {"severity": "error", "certainty": "certain"})
        self.assertEqual((report["counts"]["findings"], report["exitCode"]), (0, 2))
        self.assertEqual([e["kind"] for e in report["errors"]], ["comparison_incomplete"])

    def test_migration_inspection_and_version_errors(self):
        document = fixture("migrations-alembic")
        kinds = [e["kind"] for e in gate_errors(document, "2.0")]
        self.assertEqual(kinds, ["migration_unknown"] * 3 + ["inspection_failed"])
        statuses = {f["kind"] for f in gate_findings(document) if f["source"] == "migration"}
        self.assertEqual(statuses, {"migration_behind", "migration_diverged", "migration_uninitialized"})
        clean = fixture("declared-sqlalchemy")
        self.assertEqual(gate_errors(clean, "2.0"), [])
        self.assertEqual([e["kind"] for e in gate_errors(clean, "2.3")], ["validation_incomplete"])

    def test_reports_are_deterministic(self):
        document = fixture("comparison-tenant-schemas")
        thresholds = {"severity": "warning", "certainty": "certain"}
        first = canonical_json(check(document, "2.0", thresholds))
        reordered = m.replace(document, diagnostics=tuple(reversed(document.diagnostics)))
        self.assertEqual(canonical_json(check(reordered, "2.0", thresholds)), first)
        self.assertEqual(canonical_json(gate_report(thresholds, reversed(gate_findings(document)))), first)


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.snapshot = self.root / "schema.json"
        self.snapshot.write_text((VALID / "comparison-tenant-schemas.json").read_text())

    def cli(self, *args):
        return run_cli(self.root, "check", *args)

    def assert_report(self, text, exit_code):
        report = json.loads(text)
        self.assertEqual(validate_gate_report(report, strict=True)[1], [])
        self.assertEqual(report["exitCode"], exit_code)
        self.assertEqual(text, canonical_json(report))
        return report

    def test_exit_codes_and_output(self):
        before = self.snapshot.read_bytes()
        result = self.cli("--snapshot", "schema.json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(result.stdout.startswith("Schema check FAILED (exit code 1): 23 of 26 findings"),
                        result.stdout)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        result = self.cli("--snapshot", "schema.json", "--json", "--severity", "error")
        report = self.assert_report(result.stdout, 1)
        # 12 error differences and tenant_b's migration track, behind its head.
        self.assertEqual(report["counts"]["qualifying"], 13)
        self.assertIn(("migration", "migration_behind"), {(f["source"], f["kind"]) for f in report["findings"]})
        (self.root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\noutput = 'schema.json'\n"
                                                  "[tool.laranex-schema-viewer.check]\nseverity = 'info'\n")
        result = self.cli("--json")
        self.assertEqual(self.assert_report(result.stdout, 1)["thresholds"], {"severity": "info",
                                                                              "certainty": "certain"})
        result = self.cli("--json", "--severity", "error", "--certainty", "likely")
        self.assertEqual(self.assert_report(result.stdout, 1)["thresholds"], {"severity": "error",
                                                                              "certainty": "likely"})
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_a_clean_document_passes(self):
        clean = load(VALID / "comparison-tenant-schemas.json")
        clean["comparisons"], clean["migrations"] = [], []
        self.snapshot.write_text(canonical_json(clean))
        result = self.cli("--snapshot", "schema.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Schema check PASSED (exit code 0)", result.stdout)

    def test_output_file_and_determinism(self):
        runs = [self.cli("--snapshot", "schema.json", "--output", f"reports/gate-{n}.json") for n in range(2)]
        self.assertEqual({r.returncode for r in runs}, {1})
        self.assertEqual(runs[0].stdout, runs[1].stdout)
        written = [(self.root / f"reports/gate-{n}.json").read_text() for n in range(2)]
        self.assertEqual(written[0], written[1])
        self.assert_report(written[0], 1)
        printed = self.cli("--snapshot", "schema.json", "--json")
        self.assertEqual(printed.stdout, written[0])
        self.assertEqual(sorted(p.name for p in (self.root / "reports").iterdir()), sorted(["gate-0.json", "gate-1.json"]))

    def test_ad_hoc_comparisons_are_not_written(self):
        data = load(VALID / "comparison-tenant-schemas.json")
        data["comparisons"], data["migrations"] = [], []
        self.snapshot.write_text(canonical_json(data))
        before = self.snapshot.read_bytes()
        result = self.cli("--snapshot", "schema.json", "--json", "app:tenant_a=app:tenant_b")
        report = self.assert_report(result.stdout, 1)
        self.assertEqual(report["counts"]["findings"], 25)
        result = self.cli("--snapshot", "schema.json", "--json", "--limit", "2", "app:tenant_a=app:tenant_b")
        report = self.assert_report(result.stdout, 2)
        self.assertEqual([e["kind"] for e in report["errors"]], ["comparison_incomplete"])
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_invalid_documents_exit_2_with_a_report(self):
        newer = load(VALID / "comparison-tenant-schemas.json")
        newer["version"] = "2.1"
        unknown = load(VALID / "comparison-tenant-schemas.json")
        unknown["unexpected"] = True
        broken = load(VALID / "comparison-tenant-schemas.json")
        broken["comparisons"][0]["differences"][0]["key"] = ["no_such_table"]
        cases = {"missing.json": None, "text.json": "not json", "v1.json": json.dumps({"format": "laranex-schema-viewer"}),
                 "v3.json": json.dumps({**newer, "version": "3.0"}), "unknown.json": json.dumps(unknown),
                 "broken.json": json.dumps(broken)}
        for name, text in cases.items():
            with self.subTest(name):
                if text is not None:
                    (self.root / name).write_text(text)
                result = self.cli("--snapshot", name, "--json")
                self.assertEqual(result.returncode, 2, result.stderr)
                report = self.assert_report(result.stdout, 2)
                [error] = report["errors"]
                self.assertEqual(error["kind"], "invalid_document")
                self.assertTrue(error["message"].startswith(name) or name in error["message"], error)
                self.assertNotIn(str(self.root), result.stdout)
        (self.root / "newer.json").write_text(json.dumps(newer))
        result = self.cli("--snapshot", "newer.json", "--json")
        self.assertIn("validation_incomplete", [e["kind"] for e in self.assert_report(result.stdout, 2)["errors"]])

    def test_invalid_arguments_and_configuration_exit_2(self):
        for args, message in (
                (("--severity", "fatal"), "invalid choice"),
                (("--certainty", "maybe"), "invalid choice"),
                (("--unknown",), "unrecognized arguments"),
                (("--limit", "2"), "--limit applies to comparisons named on the command line"),
                (("--limit", "-1", "app=app:*"), "nonnegative"),
                (("nope=app",), "names connection nope"),
                (("app:tenant_a",), "A comparison is BASELINE=TARGET"),
                (("--output", "."), "Error:")):
            with self.subTest(args):
                result = self.cli("--snapshot", "schema.json", *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
        for text in ("[tool.laranex-schema-viewer.check]\nseverity = 'fatal'\n",
                     "[tool.laranex-schema-viewer.check]\nunknown = 1\n", "[tool.laranex-schema-viewer\n"):
            with self.subTest(text):
                (self.root / "pyproject.toml").write_text(text)
                result = self.cli("--snapshot", "schema.json")
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("Error:", result.stderr)

    def test_check_never_connects_imports_or_writes(self):
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("raise SystemExit('application code was imported')\n")
        before = self.snapshot.read_bytes()
        probe = (
            "import sys\n"
            "def hook(event, args):\n"
            "    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system',\n"
            "                 'os.exec', 'os.posix_spawn', 'sqlite3.connect'):\n"
            "        raise RuntimeError('blocked ' + event)\n"
            "    if event == 'open' and isinstance(args[0], str) and args[0].endswith('schema.json') \\\n"
            "            and args[1] not in ('r', 'rb', None):\n"
            "        raise RuntimeError('snapshot opened for writing')\n"
            "sys.addaudithook(hook)\n"
            "from laranex_schema_viewer.cli import main\n"
            f"code = main(['check', '--snapshot', {str(self.snapshot)!r}, 'app:tenant_a=app:*'])\n"
            "loaded = sorted(n for n in sys.modules if n.split('.')[0] in ('sqlalchemy', 'psycopg', 'pymysql',\n"
            "    'django', 'tortoise', 'peewee', 'sqlmodel', 'mcp', 'app') or n.startswith(\n"
            "    ('laranex_schema_viewer.build', 'laranex_schema_viewer.isolation', 'laranex_schema_viewer.adapters',\n"
            "     'laranex_schema_viewer.introspection', 'laranex_schema_viewer.reconcile')))\n"
            "print(code, loaded)\n")
        result = subprocess.run([sys.executable, "-c", probe], cwd=self.root, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src")),
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "1 []")
        self.assertEqual(self.snapshot.read_bytes(), before)
