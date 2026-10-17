"""The drift gate (`check`): the command over the shared gate (contract/gate.py, whose conformance test_contract.py
runs), the comparisons it requests, its output, exit codes and determinism.

CLIConformance runs every shared gate conformance case through the command and compares the printed report's bytes.
The command reads only the saved document: an audit-hook test proves it opens no socket, subprocess or database and
imports no application code. CIEndToEnd builds a disposable PostgreSQL multi-tenant database with comparisons and
gates it, as a CI job would (needs LSV_TEST_POSTGRES_URL).
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from laranex_schema_viewer import model as m
from laranex_schema_viewer.check import summary
from laranex_schema_viewer.config import CheckConfig, load_config
from laranex_schema_viewer.contract import canonical_json, document_to_json, finalize
from laranex_schema_viewer.contract.gate import gate_document, validate_gate_report

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "fixtures" / "contract"
VALID = CONTRACT / "fixtures" / "valid"
GATE_CASES = CONTRACT / "conformance" / "gate"
COMPLETE = {f: "complete" for f in m.FEATURES}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))
    return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args], cwd=cwd, env=environment,
                          capture_output=True, text=True, timeout=120)


def toml_scope(scope: dict) -> str:
    schema = "" if scope["schema"] is None else f", schema = {json.dumps(scope['schema'])}"
    return f"{{ connection = {json.dumps(scope['connection'])}{schema} }}"


def comparisons_toml(requested) -> str:
    return "".join(f"[[tool.laranex-schema-viewer.comparisons]]\nbaseline = {toml_scope(r['baseline'])}\n"
                   f"target = {toml_scope(r['target'])}\n" for r in requested)


def database_document(features=None, diagnostics=(), schemas=None) -> dict:
    """A one-connection database document with one table, finalized as the build writes it."""
    table = m.Table(id="t_users", name="users", schema=None, provenance=("database",), columns=(
        m.Column(name="id", type="integer", nullable=False, provenance=("database",)),))
    connection = m.Connection(id="c_app", name="app", driver="sqlite", tables=(table,), coverage=m.ConnectionCoverage(
        database=m.Coverage(complete=False, schemas=schemas, features={**COMPLETE, **(features or {})})))
    document = m.Document(source="database", generator=m.Generator(name="test"), connections=(connection,),
                          diagnostics=tuple(diagnostics))
    return document_to_json(finalize(document))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.snapshot = self.root / "schema.json"
        self.snapshot.write_text((VALID / "comparison-tenant-schemas.json").read_text())
        self.configure("")

    def cli(self, *args):
        return run_cli(self.root, "check", *args)

    def write(self, data, name="schema.json"):
        (self.root / name).write_text(canonical_json(data))

    def configure(self, text: str):
        (self.root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\noutput = 'schema.json'\n" + text)

    def assert_report(self, result, exit_code):
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(validate_gate_report(report, strict=True)[1], [])
        self.assertEqual(report["exitCode"], exit_code)
        self.assertEqual(result.stdout, canonical_json(report))
        return report


class CLIConformance(Fixture):
    def test_every_gate_case_through_the_command(self):
        # Each case's document is the snapshot, its thresholds the --severity and --certainty flags, and its requested
        # comparisons [[tool.laranex-schema-viewer.comparisons]] entries, which the command requests as configured.
        # Every case is expressible; unparseable input, which no case covers (gateDocument(undefined)), is checked
        # in Command.test_invalid_documents_exit_2_with_a_report.
        paths = sorted(GATE_CASES.glob("*.json"))
        self.assertEqual(len(paths), 23)
        for path in paths:
            case = load(path)
            given = case["input"]
            with self.subTest(path.name):
                self.write(given["document"])
                self.configure(comparisons_toml(given["requested"] or []))
                flags = [f"--{k}={v}" for k, v in sorted((given["thresholds"] or {}).items()) if v is not None]
                result = self.cli("--json", *flags)
                self.assertEqual(result.returncode, case["expected"]["exitCode"], result.stderr)
                self.assertEqual(result.stdout, canonical_json(case["expected"]))


class Requests(Fixture):
    """Which comparisons are checked: the ones named on the command line (computed in memory first), else the
    configured ones (as stored), else every stored one."""

    def setUp(self):
        super().setUp()
        data = load(VALID / "comparison-tenant-schemas.json")
        data["migrations"] = []
        self.write(data)

    def test_no_comparisons_configured_checks_every_stored_one(self):
        report = self.assert_report(self.cli("--json", "--severity", "error"), 1)
        self.assertEqual(report["requested"], [])
        self.assertEqual(len(report["comparisons"]), 1)
        data = load(self.snapshot)
        data["comparisons"] = []
        self.write(data)
        report = self.assert_report(self.cli("--json"), 0)
        self.assertEqual((report["comparisons"], report["requested"]), ([], []))

    def test_a_configured_comparison_missing_from_the_document_exits_2(self):
        self.configure(comparisons_toml([{"baseline": {"connection": "app", "schema": "tenant_b"},
                                          "target": {"connection": "app", "schema": "tenant_a"}}]))
        report = self.assert_report(self.cli("--json"), 2)
        self.assertEqual(report["errors"], [{"kind": "comparison_not_found", "message": "Requested comparison "
                                             "app:tenant_b → app:tenant_a is not in the document."}])
        self.assertEqual((report["comparisons"], report["findings"]), ([], []))

    def test_a_configured_wildcard_that_expands_to_nothing_exits_2(self):
        data = load(self.snapshot)
        data["comparisons"] = []
        data["connections"][0]["coverage"]["database"]["schemas"] = ["tenant_a"]
        self.write(data)
        self.configure(comparisons_toml([{"baseline": {"connection": "app", "schema": "tenant_a"},
                                          "target": {"connection": "app", "schema": "*"}}]))
        report = self.assert_report(self.cli("--json"), 2)
        self.assertEqual(report["errors"], [{"kind": "comparison_not_found",
                                             "message": "Requested comparison app:tenant_a → app:* matches no schema."}])
        # The same request named on the command line computes nothing, so it fails the same way.
        self.configure("")
        self.assertEqual(self.assert_report(self.cli("--json", "app:tenant_a=app:*"), 2)["errors"], report["errors"])

    def test_requested_comparisons_naming_unknown_connections_exit_2(self):
        report = self.assert_report(self.cli("--json", "app:tenant_a=app:tenant_b", "app:tenant_a=replica"), 2)
        self.assertEqual(report["errors"], [{"kind": "comparison_not_found", "message": "Requested comparison "
                                             "app:tenant_a → replica names connection replica, which the document "
                                             "does not have."}])
        self.assertEqual(len(report["comparisons"]), 1)

    def test_command_line_comparisons_are_computed_then_requested_and_never_written(self):
        data = load(self.snapshot)
        data["comparisons"] = []
        self.write(data)
        before = self.snapshot.read_bytes()
        # Configured requests are replaced by the ones named here.
        self.configure(comparisons_toml([{"baseline": {"connection": "app", "schema": "tenant_b"},
                                          "target": {"connection": "app", "schema": "tenant_a"}}]))
        report = self.assert_report(self.cli("--json", "app:tenant_a=app:*"), 1)
        self.assertEqual(report["requested"], [{"baseline": {"connection": "app", "schema": "tenant_a"},
                                                "target": {"connection": "app", "schema": "*"}}])
        self.assertEqual(report["counts"]["findings"], 25)
        self.assertEqual(len(report["comparisons"]), 1)
        report = self.assert_report(self.cli("--json", "--limit", "2", "app:tenant_a=app:tenant_b"), 2)
        self.assertEqual([e["message"] for e in report["errors"]], [
            "Comparison app:tenant_a → app:tenant_b is incomplete: 23 of 25 differences omitted."])
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_a_newer_minor_keeps_its_version_with_ad_hoc_comparisons(self):
        data = load(self.snapshot)
        data["version"] = "2.4"
        self.write(data)
        report = self.assert_report(self.cli("--json", "app:tenant_a=app:tenant_b"), 2)
        self.assertEqual([e["kind"] for e in report["errors"]], ["validation_incomplete"])
        self.assertEqual(report["counts"]["findings"], 25)


class Findings(Fixture):
    def test_extraction_warnings_never_fail_the_gate(self):
        unresolved = m.Diagnostic(kind="unresolved_foreign_key", category="extraction", severity="warning",
                                  certainty="certain", subject=m.Subject(type="table", id="t_users"),
                                  message="users.team_id references teams, which is not mapped")
        self.write(database_document(diagnostics=[unresolved]))
        report = self.assert_report(self.cli("--json"), 0)
        self.assertEqual(report["counts"]["findings"], 0)
        result = self.cli("--severity", "info", "--certainty", "likely")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Schema check PASSED (exit code 0)", result.stdout)

    def test_incomplete_required_inspections_exit_2(self):
        failed = m.Diagnostic(kind="catalog_query_failed", category="inspection", severity="error", certainty="certain",
                              subject=m.Subject(type="connection", id="c_app"), message="permission denied")
        cases = {
            "tables partial": (database_document({"tables": "partial"}), "coverage_incomplete",
                               "Connection app: the database inspection is incomplete: tables partial."),
            "columns unavailable": (database_document({"columns": "unavailable"}), "coverage_incomplete",
                                    "Connection app: the database inspection is incomplete: columns unavailable."),
            "a feature unavailable": (database_document({"comments": "unavailable"}), "coverage_incomplete",
                                      "Connection app: the database inspection is incomplete: comments unavailable."),
            "an inspection error": (database_document(diagnostics=[failed]), "inspection_failed",
                                    "catalog_query_failed on app: permission denied"),
        }
        for name, (data, kind, message) in cases.items():
            with self.subTest(name):
                self.write(data)
                report = self.assert_report(self.cli("--json"), 2)
                self.assertEqual(report["errors"], [{"kind": kind, "message": message}])

    def test_partial_dialect_features_pass(self):
        self.write(database_document({"checkConstraints": "partial", "indexExpressions": "partial",
                                      "models": "not_applicable", "relationships": "not_applicable"}))
        self.assert_report(self.cli("--json"), 0)

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "SQLAlchemy is not installed")
    def test_a_built_sqlite_document_without_drift_passes(self):
        database = self.root / "app.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(
                "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL CHECK (length(name) > 0));"
                "CREATE TABLE users (id INTEGER PRIMARY KEY, team_id INTEGER REFERENCES teams (id));")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"),
                           LARANEX_SCHEMA_DATABASE_URL=f"sqlite:///{database}")
        built = subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "build", "--source", "database",
                                "--output", "schema.json"], cwd=self.root, env=environment, capture_output=True,
                               text=True, timeout=120)
        self.assertEqual(built.returncode, 0, built.stderr)
        features = load(self.snapshot)["connections"][0]["coverage"]["database"]["features"]
        self.assertEqual(features["checkConstraints"], "partial")
        report = self.assert_report(self.cli("--json"), 0)
        self.assertEqual(report["errors"], [])


class Thresholds(unittest.TestCase):
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

    def test_reports_and_summaries_are_deterministic(self):
        data = load(VALID / "comparison-tenant-schemas.json")
        first = gate_document(data)
        reordered = {**data, "diagnostics": list(reversed(data["diagnostics"]))}
        self.assertEqual(canonical_json(gate_document(reordered)), canonical_json(first))
        self.assertEqual(summary(gate_document(reordered)), summary(first))


class Command(Fixture):
    def test_exit_codes_and_output(self):
        before = self.snapshot.read_bytes()
        result = self.cli("--snapshot", "schema.json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(result.stdout.startswith("Schema check FAILED (exit code 1): 23 of 26 findings"),
                        result.stdout)
        self.assertIn("  error   certain migration  migration_behind  app · tenant_b: alembic revisions not applied: "
                      "3c4d5e6f7a8b [", result.stdout)
        self.assertIn("app:tenant_a → app:tenant_b · tenant_a.orders.total: Differs in type [", result.stdout)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        report = self.assert_report(self.cli("--snapshot", "schema.json", "--json", "--severity", "error"), 1)
        # 12 error differences and tenant_b's migration track, behind its head.
        self.assertEqual(report["counts"]["qualifying"], 13)
        self.configure("[tool.laranex-schema-viewer.check]\nseverity = 'info'\n")
        self.assertEqual(self.assert_report(self.cli("--json"), 1)["thresholds"],
                         {"severity": "info", "certainty": "certain"})
        self.assertEqual(self.assert_report(self.cli("--json", "--severity", "error", "--certainty", "likely"),
                                            1)["thresholds"], {"severity": "error", "certainty": "likely"})
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_output_file_and_determinism(self):
        runs = [self.cli("--snapshot", "schema.json", "--output", f"reports/gate-{n}.json") for n in range(2)]
        self.assertEqual({r.returncode for r in runs}, {1})
        self.assertEqual(runs[0].stdout, runs[1].stdout)
        written = [(self.root / f"reports/gate-{n}.json").read_text() for n in range(2)]
        self.assertEqual(written[0], written[1])
        printed = self.cli("--snapshot", "schema.json", "--json")
        self.assert_report(printed, 1)
        self.assertEqual(printed.stdout, written[0])
        self.assertEqual(sorted(p.name for p in (self.root / "reports").iterdir()), ["gate-0.json", "gate-1.json"])

    def test_invalid_documents_exit_2_with_a_report(self):
        valid = load(VALID / "comparison-tenant-schemas.json")
        broken = load(VALID / "comparison-tenant-schemas.json")
        broken["comparisons"][0]["differences"][0]["key"] = ["no_such_table"]
        cases = {
            "missing.json": (None, "The input could not be parsed as JSON.", "Cannot read missing.json"),
            "text.json": ("not json", "The input could not be parsed as JSON.", "text.json is not JSON"),
            "nan.json": ('{"format": NaN}', "The input could not be parsed as JSON.", "NaN is not JSON"),
            "v1.json": (json.dumps({"format": "laranex-schema-viewer"}), "The input is not a laranex-schema document.",
                        None),
            "v3.json": (json.dumps({**valid, "version": "3.0"}),
                        "The document is laranex-schema 3.0; the gate reads 2.x.", None),
            "unknown.json": (json.dumps({**valid, "unexpected": True}),
                             "The document fails laranex-schema 2.0 validation.", "$: unknown property unexpected"),
            "broken.json": (json.dumps(broken), "The document fails laranex-schema 2.0 validation.", "broken.json: "),
        }
        for name, (text, message, note) in cases.items():
            with self.subTest(name):
                if text is not None:
                    (self.root / name).write_text(text)
                result = self.cli("--snapshot", name, "--json", "app:tenant_a=app:tenant_b")
                report = self.assert_report(result, 2)
                self.assertEqual(report["errors"], [{"kind": "invalid_document", "message": message}])
                self.assertEqual(len(report["requested"]), 1)
                if note:
                    self.assertIn(note, result.stderr)
                self.assertNotIn(str(self.root), result.stdout + result.stderr)

    def test_the_output_report_is_written_atomically(self):
        from tests.test_comparison import failing_writes, leftovers, run_main

        code, out, err = run_main(self.root, "check", "--snapshot", "schema.json", "--output", "report.json")
        self.assertEqual(code, 1, err)
        text = (self.root / "report.json").read_text()
        self.assertEqual((canonical_json(json.loads(text)), json.loads(text)["exitCode"]), (text, 1))
        self.assertEqual(leftovers(self.root), [])
        (self.root / "report.json").write_text("previous report")
        for name, patch in failing_writes("laranex_schema_viewer.snapshot").items():
            with self.subTest(name), patch:
                code, out, err = run_main(self.root, "check", "--snapshot", "schema.json", "--output", "report.json")
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith("Error: [Errno "), err)
                self.assertEqual((self.root / "report.json").read_text(), "previous report")
                self.assertEqual(leftovers(self.root), [])

    def test_invalid_arguments_and_configuration_exit_2(self):
        for args, message in (
                (("--severity", "fatal"), "invalid choice"),
                (("--certainty", "maybe"), "invalid choice"),
                (("--unknown",), "unrecognized arguments"),
                (("--limit", "2"), "--limit applies to comparisons named on the command line"),
                (("--limit", "-1", "app=app:*"), "nonnegative"),
                (("app:tenant_a",), "A comparison is BASELINE=TARGET"),
                (("app:tenant_a=app:tenant_b", "app:tenant_a=app:tenant_b"), "more than once"),
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


class CIEndToEnd(unittest.TestCase):
    """A CI job: build a disposable PostgreSQL multi-tenant database with --source database and the configured
    comparisons, then gate the saved document, which checks exactly those comparisons. tenant_b equals tenant_a;
    tenant_c has planted drift."""

    @classmethod
    def setUpClass(cls):
        from tests.test_comparison_databases import HAS_SQLALCHEMY, TENANTS, Disposable, template

        url = os.environ.get("LSV_TEST_POSTGRES_URL")
        if not (url and HAS_SQLALCHEMY):
            raise unittest.SkipTest("set LSV_TEST_POSTGRES_URL to run these tests")
        cls.database = type("GateDatabase", (Disposable,), {})
        try:
            cls.database.create({"pg": url})
            statements = []
            for tenant in TENANTS:
                statements += [f'CREATE SCHEMA "{tenant}"', *template(tenant, drift=tenant == "tenant_c")]
            statements += [f'GRANT USAGE ON SCHEMA "{s}" TO "{cls.database.role}"' for s in TENANTS]
            cls.database.execute("pg", statements)
        except Exception:
            cls.database.drop()
            raise
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.database.drop()

    def configure(self, target: str):
        (self.root / "pyproject.toml").write_text(
            "[tool.laranex-schema-viewer]\noutput = 'schema-viewer.json'\nsource = 'database'\n"
            "[tool.laranex-schema-viewer.databases.app]\nurl_env = 'APP_DATABASE_URL'\n"
            "schemas = ['tenant_a', 'tenant_b', 'tenant_c']\n"
            "[[tool.laranex-schema-viewer.comparisons]]\n"
            f"baseline = {{ connection = 'app', schema = 'tenant_a' }}\ntarget = {{ connection = 'app', schema = '{target}' }}\n")

    def build(self, target: str) -> subprocess.CompletedProcess:
        self.configure(target)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"),
                           APP_DATABASE_URL=self.database.urls["pg"])
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "build"], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=180)

    def test_build_then_check(self):
        result = self.build("tenant_b")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Checked 1 comparisons (requested: app:tenant_a → app:tenant_b)", result.stdout)

        result = self.build("*")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.root, "check", "--json", "--output", "gate.json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(validate_gate_report(report, strict=True)[1], [])
        self.assertEqual((self.root / "gate.json").read_text(), result.stdout)
        self.assertEqual(len(report["comparisons"]), 2)
        kinds = {(f["kind"], f["label"]) for f in report["findings"] if f["severity"] != "info"}
        self.assertIn(("table_missing", "app:tenant_a → app:tenant_c · tenant_a.audit_log"), kinds)
        self.assertIn(("column_changed", "app:tenant_a → app:tenant_c · tenant_a.orders.total"), kinds)
        self.assertNotIn(self.database.password, result.stdout + result.stderr)
        # Only certain errors: the missing and extra tables.
        result = run_cli(self.root, "check", "--severity", "error", "--json")
        self.assertEqual(json.loads(result.stdout)["counts"]["qualifying"], 2)
        # Configuring a comparison the build did not store fails the gate, as nothing checked it.
        self.configure("tenant_d")
        result = run_cli(self.root, "check", "--json")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["errors"], [{
            "kind": "comparison_not_found",
            "message": "Requested comparison app:tenant_a → app:tenant_d is not in the document."}])
        # Two runs of the gate give identical bytes, and the document is never rewritten.
        self.configure("*")
        before = (self.root / "schema-viewer.json").read_bytes()
        self.assertEqual(run_cli(self.root, "check").stdout, run_cli(self.root, "check").stdout)
        self.assertEqual((self.root / "schema-viewer.json").read_bytes(), before)
