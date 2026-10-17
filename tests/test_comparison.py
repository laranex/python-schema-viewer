"""Comparisons a project asks for: configuration, scope expansion, overrides, limits, storage and the compare command.

The algorithm itself is checked against the shared conformance cases in test_contract.py. These tests use the shared
valid fixtures and SQLite; tests/test_comparison_databases.py runs PostgreSQL, MySQL and MariaDB.
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
from dataclasses import replace
from pathlib import Path

from laranex_schema_viewer import model as m
from laranex_schema_viewer.compare import compute, describe, scopes, store
from laranex_schema_viewer.config import (DEFAULT_COMPARISON_LIMIT, ComparisonConfig, OverrideConfig, ScopeConfig,
                                          comparison_option, load_config)
from laranex_schema_viewer.contract import (assert_document, canonical_json, document_to_json, validate_document)
from laranex_schema_viewer.contract.comparison import normalize_sql, normalize_type

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "tests/fixtures/contract/fixtures/valid"
HAS_SQLALCHEMY = importlib.util.find_spec("sqlalchemy") is not None


def large_document(tables: int = 400) -> m.Document:
    """Two PostgreSQL connections with many wide tables: the replica lacks every tenth table, has a wider type in
    every table and an extra column in every other one, so the comparison finds several differences per table."""
    def table(connection: str, n: int, drift: bool) -> m.Table:
        columns = [m.Column(name=f"c{k:02d}", type="integer", nullable=True, provenance=m.DATABASE) for k in range(12)]
        if drift:
            columns[3] = m.replace(columns[3], type="bigint")
            if n % 2:
                columns.append(m.Column(name="extra_column_with_a_long_name", type="text", nullable=True,
                                        provenance=m.DATABASE))
        return m.Table(id=f"{connection}:t{n:04d}", name=f"table_{n:04d}", schema="public", provenance=m.DATABASE,
                       columns=tuple(columns))

    features = {f: "complete" for f in m.FEATURES}
    connections = tuple(m.Connection(
        id=f"connection:{name}", name=name, driver="postgresql", default_schema="public",
        coverage=m.ConnectionCoverage(database=m.coverage(features, ("public",))),
        tables=tuple(table(name, n, name == "replica") for n in range(tables) if name == "primary" or n % 10))
        for name in ("primary", "replica"))
    return m.Document(source="database", generator=m.Generator(name="python-schema-viewer"), connections=connections)


def fixture(name: str) -> m.Document:
    return assert_document(json.loads((VALID / name).read_text()), strict=True)


def spec(text: str, **options) -> ComparisonConfig:
    return replace(comparison_option(text), **options)


class Configuration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def load(self, text: str):
        (self.root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\n" + text)
        return load_config(self.root)

    def test_comparisons_are_read(self):
        config = self.load(
            "[[tool.laranex-schema-viewer.comparisons]]\n"
            "baseline = { connection = 'app', schema = 'tenant_template' }\n"
            "target = { connection = 'app', schema = '*' }\n"
            "limit = 20\n"
            "overrides = [{ object_type = 'index', change = 'missing', severity = 'error' },\n"
            "  { object_type = 'foreignKey', change = 'changed', field = 'onDelete', severity = 'info' }]\n"
            "[[tool.laranex-schema-viewer.comparisons]]\n"
            "baseline = { connection = 'primary' }\n"
            "target = { connection = 'replica' }\n")
        self.assertEqual(config.comparisons, (
            ComparisonConfig(baseline=ScopeConfig("app", "tenant_template"), target=ScopeConfig("app", "*"), limit=20,
                             overrides=(OverrideConfig("index", "missing", "error"),
                                        OverrideConfig("foreignKey", "changed", "info", "onDelete"))),
            ComparisonConfig(baseline=ScopeConfig("primary"), target=ScopeConfig("replica"),
                             limit=DEFAULT_COMPARISON_LIMIT),
        ))

    def test_invalid_comparisons_are_rejected(self):
        entry = "[[tool.laranex-schema-viewer.comparisons]]\n"
        scopes = "baseline = { connection = 'a' }\ntarget = { connection = 'b' }\n"
        for text, message in (
                ("comparisons = 'a=b'\n", "must be an array of tables"),
                (entry + scopes + "url = 'postgresql://u:p@h/db'\n", "Unknown .* keys: url"),
                (entry + "baseline = 'a'\ntarget = { connection = 'b' }\n", "baseline must be a table"),
                (entry + "baseline = { connection = 'a', url = 'x' }\ntarget = { connection = 'b' }\n",
                 "Unknown .* baseline keys: url"),
                (entry + "baseline = { connection = 'postgresql://u:p@h/db' }\ntarget = { connection = 'b' }\n",
                 "baseline needs connection"),
                (entry + "baseline = { connection = 'a', schema = '*' }\ntarget = { connection = 'b' }\n",
                 "schema cannot be"),
                (entry + "baseline = { connection = 'a', schema = '' }\ntarget = { connection = 'b' }\n",
                 "schema must be a schema name"),
                (entry + "baseline = { connection = 'a', schema = 'u:p@h/db' }\ntarget = { connection = 'b' }\n",
                 "never a URL"),
                (entry + "baseline = { connection = 'a' }\ntarget = { connection = 'a' }\n", "with itself"),
                (entry + scopes + "limit = -1\n", "nonnegative integer"),
                (entry + scopes + "limit = true\n", "nonnegative integer"),
                (entry + scopes + "overrides = [{ object_type = 'view', change = 'missing', severity = 'error' }]\n",
                 "object_type must be one of"),
                (entry + scopes + "overrides = [{ object_type = 'table', change = 'gone', severity = 'error' }]\n",
                 "change must be one of"),
                (entry + scopes + "overrides = [{ object_type = 'table', change = 'missing', severity = 'fatal' }]\n",
                 "severity must be one of"),
                (entry + scopes + "overrides = [{ object_type = 'foreignKey', change = 'changed', "
                                  "field = 'on_delete', severity = 'info' }]\n", "field applies"),
                (entry + scopes + "overrides = [{ object_type = 'column', change = 'missing', field = 'type', "
                                  "severity = 'info' }]\n", "field applies"),
                (entry + scopes + "overrides = [{ object_type = 'table', change = 'missing', severity = 'info', "
                                  "level = 1 }]\n", "override keys: level")):
            with self.subTest(text):
                with self.assertRaisesRegex(ValueError, message):
                    self.load(text)

    def test_command_line_scopes(self):
        self.assertEqual(comparison_option("app:tenant_a=app:*"),
                         ComparisonConfig(baseline=ScopeConfig("app", "tenant_a"), target=ScopeConfig("app", "*")))
        self.assertEqual(comparison_option("primary=replica:db:x"),
                         ComparisonConfig(baseline=ScopeConfig("primary"), target=ScopeConfig("replica", "db:x")))
        for value in ("app", "app=app", "app:*=app:b", "=b", "a:=b", "postgresql://u:secret@h/db=b", "a b=c",
                      "a=user:secret@host/db"):
            with self.subTest(value):
                with self.assertRaises(ValueError) as caught:
                    comparison_option(value)
                self.assertNotIn("secret", str(caught.exception))


class Expansion(unittest.TestCase):
    def setUp(self):
        self.document = fixture("comparison-tenant-schemas.json")  # app: default public, read tenant_a and tenant_b

    def pairs(self, text: str):
        return [(b.schema, t.schema) for b, t in scopes(self.document, spec(text))]

    def test_a_wildcard_target_expands_to_the_inspected_schemas_except_the_baseline(self):
        self.assertEqual(self.pairs("app:tenant_a=app:*"), [("tenant_a", "tenant_b")])
        self.assertEqual(self.pairs("app=app:*"), [(None, "tenant_a"), (None, "tenant_b")])
        self.assertEqual(self.pairs("app:tenant_b=app:tenant_a"), [("tenant_b", "tenant_a")])

    def test_unknown_connections_and_schemaless_targets_fail(self):
        with self.assertRaisesRegex(ValueError, "names connection other, which the document does not have"):
            scopes(self.document, spec("other=app:*"))
        sqlite = fixture("database-sqlite-indexes.json")
        name = sqlite.connections[0].name
        with self.assertRaisesRegex(ValueError, "read named schemas"):
            scopes(sqlite, spec(f"{name}={name}:*"))

    def test_explicit_pairs_take_precedence_over_expansion_and_duplicates_fail(self):
        overridden = spec("app:tenant_a=app:tenant_b", overrides=(OverrideConfig("table", "missing", "info"),))
        comparisons, _ = compute(self.document, [spec("app:tenant_a=app:*"), overridden])
        self.assertEqual(len(comparisons), 1)
        missing = [d for d in comparisons[0].differences if d.object_type == "table" and d.change == "missing"]
        self.assertTrue(missing and all(d.severity == "info" for d in missing))
        with self.assertRaisesRegex(ValueError, "configured more than once"):
            compute(self.document, [spec("app:tenant_a=app:tenant_b"), spec("app:tenant_a=app:tenant_b")])

    def test_tenant_schema_names_are_never_drift(self):
        comparisons, _ = compute(self.document, [spec("app:tenant_a=app:*"), spec("app:tenant_b=app:*")])
        self.assertEqual(len(comparisons), 2)
        for comparison in comparisons:
            for difference in comparison.differences:
                self.assertFalse({"tenant_a", "tenant_b"} & set(difference.key), difference)
        document = store(self.document, *compute(self.document, [spec("app:tenant_a=app:*")]))
        self.assertEqual(validate_document(document_to_json(document), strict=True)[1], [])


class Results(unittest.TestCase):
    def setUp(self):
        self.document = fixture("comparison-tenant-schemas.json")

    def test_limits_omit_differences_and_explain_the_incomplete_comparison(self):
        comparisons, diagnostics = compute(self.document, [spec("app:tenant_a=app:tenant_b", limit=3)])
        comparison = comparisons[0]
        total = comparison.summary.missing + comparison.summary.extra + comparison.summary.changed
        self.assertEqual((len(comparison.differences), comparison.omitted, comparison.complete), (3, total - 3, False))
        [diagnostic] = diagnostics
        self.assertEqual((diagnostic.kind, diagnostic.category, diagnostic.subject.type, diagnostic.subject.id),
                         ("comparison_incomplete", "inspection", "comparison", comparison.id))
        self.assertEqual(diagnostic.message, f"Comparison app:tenant_a=app:tenant_b is incomplete: {total - 3} of "
                                             f"{total} differences are left out by the limit of 3")
        unlimited, none = compute(self.document, [spec("app:tenant_a=app:tenant_b", limit=1000)])
        self.assertEqual((unlimited[0].complete, unlimited[0].omitted, none), (True, 0, ()))
        self.assertEqual(unlimited[0].differences[:3], comparison.differences)

    def test_incomplete_coverage_is_explained(self):
        document = fixture("comparison-incomplete.json")
        [stored] = document.comparisons
        comparisons, [diagnostic] = compute(document, [ComparisonConfig(
            baseline=ScopeConfig(*self.scope(document, stored.baseline)),
            target=ScopeConfig(*self.scope(document, stored.target)))])
        self.assertFalse(comparisons[0].complete)
        self.assertIn("not completely covered", diagnostic.message)

    @staticmethod
    def scope(document, scope):
        return next(c.name for c in document.connections if c.id == scope.connection), scope.schema

    def test_overrides_and_the_most_severe_field(self):
        overrides = (OverrideConfig("column", "changed", "error", "nullable"), OverrideConfig("column", "changed", "info"),
                     OverrideConfig("table", "extra", "warning"))
        [comparison], _ = compute(self.document, [spec("app:tenant_a=app:tenant_b", overrides=overrides)])
        by_key = {(d.object_type, d.change, d.key): d for d in comparison.differences}
        self.assertEqual(by_key["column", "changed", ("orders", "placed_at")].severity, "error")
        self.assertEqual(by_key["column", "changed", ("orders", "total")].severity, "info")
        self.assertEqual(by_key["table", "extra", ("coupons",)].severity, "warning")
        self.assertEqual(by_key["table", "missing", ("invoices",)].severity, "error")

    def test_results_are_deterministic(self):
        specs = [spec("app:tenant_b=app:*"), spec("app:tenant_a=app:*")]
        first = store(self.document, *compute(self.document, specs))
        second = store(self.document, *compute(self.document, list(reversed(specs))))
        self.assertEqual(canonical_json(document_to_json(first)), canonical_json(document_to_json(second)))
        self.assertEqual([c.id for c in first.comparisons], sorted(c.id for c in first.comparisons))

    def test_storing_replaces_the_same_scopes_and_keeps_others(self):
        limited = store(self.document, *compute(self.document, [spec("app:tenant_a=app:tenant_b", limit=1)]))
        self.assertEqual(sum(d.kind == "comparison_incomplete" for d in limited.diagnostics), 1)
        other = store(limited, *compute(limited, [spec("app:tenant_b=app:tenant_a")]))
        self.assertEqual(len(other.comparisons), 2)
        again = store(other, *compute(other, [spec("app:tenant_a=app:tenant_b")]))
        self.assertEqual(len(again.comparisons), 2)
        self.assertFalse(any(d.kind == "comparison_incomplete" for d in again.diagnostics))
        self.assertEqual(validate_document(document_to_json(again), strict=True)[1], [])

    def test_summary_lines(self):
        comparisons, _ = compute(self.document, [spec("app:tenant_a=app:tenant_b", limit=2)])
        lines = describe(self.document, comparisons).splitlines()
        self.assertTrue(lines[0].startswith("app:tenant_a -> app:tenant_b (incomplete): 8 missing, 8 extra, 9 changed"))
        self.assertEqual(len(lines), 3)

    def test_normalization_matches_the_reference(self):
        # Expected values computed with laranex-schema-viewer-ui's normalizeType and normalizeSql.
        self.assertEqual(normalize_type("INT(11) unsigned"), "integer()")
        self.assertEqual(normalize_type("tinyint(1)"), "boolean()")
        self.assertEqual(normalize_type("timestamp(6)", True), "timestamptz(6)")
        self.assertEqual(normalize_type("character varying ( 20 ) []"), "varchar[](20)")
        self.assertIsNone(normalize_type("enum('a','b') x"))
        self.assertEqual(normalize_sql("nextval('\"Tenant_A\".seq'::regclass)", "Tenant_A"), "nextval'seq'")
        self.assertEqual(normalize_sql("OCTET_LENGTH(`tenant_a`.a)", "tenant_a", "mariadb"), "lengtha")
        self.assertEqual(normalize_sql("mytenant_a.x + tenant_a.y", "tenant_a"), "mytenant_a.x+y")


class LargeSchemas(unittest.TestCase):
    def test_the_default_limit_bounds_a_large_comparison(self):
        document = large_document()
        [comparison], [diagnostic] = compute(document, [comparison_option("primary=replica")])
        total = comparison.summary.missing + comparison.summary.extra + comparison.summary.changed
        self.assertEqual((comparison.summary.missing, comparison.summary.extra, comparison.summary.changed),
                         (40, 200, 360))
        self.assertEqual((len(comparison.differences), comparison.omitted), (DEFAULT_COMPARISON_LIMIT,
                                                                            total - DEFAULT_COMPARISON_LIMIT))
        self.assertIn(f"left out by the limit of {DEFAULT_COMPARISON_LIMIT}", diagnostic.message)
        data = document_to_json(store(document, [comparison], [diagnostic]))
        self.assertEqual(validate_document(data, strict=True)[1], [])


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class CombinedDocuments(unittest.TestCase):
    """Only what the database reported is compared: a table only the ORM declares never shows up."""

    def test_declared_only_entities_are_left_out(self):
        from laranex_schema_viewer.introspection import DatabaseRequest
        from laranex_schema_viewer.introspection.runner import introspect_all
        from laranex_schema_viewer.reconcile import combine
        from tests.fixtures import multiconnection_models as models
        from tests.test_multiconnection import declared

        with tempfile.TemporaryDirectory() as tmp:
            paths = {"default": Path(tmp) / "default.db", "analytics": Path(tmp) / "analytics.db"}
            # The analytics database lacks the events table its models declare.
            for name, sql in (("default", models.DEFAULT_SQL), ("analytics", models.ANALYTICS_SQL.split(";")[0])):
                with closing(sqlite3.connect(paths[name])) as connection:
                    connection.executescript(sql)
            database = introspect_all([DatabaseRequest(url=f"sqlite:///{path}", connection=name)
                                       for name, path in paths.items()])
        document = combine(declared(), database)
        analytics = next(c for c in document.connections if c.name == "analytics")
        events = next(t for t in analytics.tables if t.name == "events")
        self.assertEqual(events.provenance, ("declared",))
        [comparison], _ = compute(document, [comparison_option("analytics=default")])
        self.assertFalse(any(d.key[0] == "events" for d in comparison.differences))
        self.assertEqual({(d.object_type, d.change, d.key) for d in comparison.differences}, {
            ("table", "extra", ("teams",)),
            ("column", "missing", ("users", "legacy")), ("column", "missing", ("users", "segment")),
            ("column", "missing", ("users", "user_id")), ("column", "extra", ("users", "email")),
            ("column", "extra", ("users", "team_id")),
            ("foreignKey", "extra", ("users", "references", "team_id->teams(id)")),
        })
        data = document_to_json(store(document, [comparison], []))
        self.assertEqual(validate_document(data, strict=True)[1], [])


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.snapshot = self.root / "schema.json"
        document = document_to_json(m.replace(fixture("comparison-tenant-schemas.json"), comparisons=()))
        self.snapshot.write_text(canonical_json(document))

    def run_cli(self, *args, env=None):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"), **(env or {}))
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=60)

    def test_compare_prints_and_writes(self):
        before = self.snapshot.read_bytes()
        result = self.run_cli("compare", "--snapshot", str(self.snapshot), "app:tenant_a=app:*")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("app:tenant_a -> app:tenant_b (complete): 8 missing, 8 extra, 9 changed", result.stdout)
        self.assertEqual(self.snapshot.read_bytes(), before)

        result = self.run_cli("compare", "--snapshot", str(self.snapshot), "--json", "--limit", "2",
                              "app:tenant_a=app:tenant_b", "app:tenant_b=app:tenant_a")
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = json.loads(result.stdout)
        self.assertEqual([(len(c["differences"]), c["omitted"], c["complete"]) for c in printed],
                         [(2, 23, False), (2, 23, False)])
        self.assertEqual(self.snapshot.read_bytes(), before)

        result = self.run_cli("compare", "--snapshot", str(self.snapshot), "--write", "--json",
                              "app:tenant_a=app:tenant_b")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Stored 1 comparisons", result.stderr)
        written = json.loads(self.snapshot.read_text())
        self.assertEqual(self.snapshot.read_text(), canonical_json(written))
        self.assertEqual(validate_document(written, strict=True)[1], [])
        self.assertEqual(written["comparisons"], json.loads(result.stdout))
        self.assertEqual({k: v for k, v in written.items() if k != "comparisons"},
                         {k: v for k, v in json.loads(before).items() if k != "comparisons"})

    def test_tables_named_like_a_compared_schema_are_stored(self):
        # Table billing in schema billing (with a column archive), compared with schema archive: the keys hold those
        # names as object names, which the contract allows; a key naming another table is still rejected.
        named = fixture("comparison-schema-named-tables.json")
        self.snapshot.write_text(canonical_json(document_to_json(m.replace(named, comparisons=()))))
        result = self.run_cli("compare", "--snapshot", str(self.snapshot), "--write", "ledger:billing=ledger:archive")
        self.assertEqual(result.returncode, 0, result.stderr)
        written = json.loads(self.snapshot.read_text())
        self.assertEqual(validate_document(written, strict=True)[1], [])
        self.assertEqual({(d["objectType"], tuple(d["key"]), d["change"]) for d in written["comparisons"][0]["differences"]},
                         {("table", ("archive",), "extra"), ("column", ("billing", "archive"), "missing"),
                          ("index", ("billing", "name", "archive"), "missing")})
        column = next(d for d in written["comparisons"][0]["differences"] if d["objectType"] == "column")
        for key, message in ((["archive", "archive"], "A difference key starts with its table's name"),
                             (["billing", "id_x"], "A column difference key names a column of its table")):
            with self.subTest(key):
                broken = json.loads(json.dumps(written))
                [d for d in broken["comparisons"][0]["differences"] if d["id"] == column["id"]][0]["key"] = key
                errors = validate_document(broken, strict=True)[1]
                self.assertTrue(any(message in e for e in errors), errors)

    def test_compare_uses_the_configuration(self):
        (self.root / "pyproject.toml").write_text(
            "[tool.laranex-schema-viewer]\noutput = 'schema.json'\n"
            "[[tool.laranex-schema-viewer.comparisons]]\n"
            "baseline = { connection = 'app', schema = 'tenant_b' }\ntarget = { connection = 'app', schema = '*' }\n"
            "limit = 1\n")
        result = self.run_cli("compare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("app:tenant_b -> app:tenant_a (incomplete)", result.stdout)

    def test_compare_failures(self):
        for args, message in (
                (("compare", "--snapshot", str(self.snapshot)), "Name comparisons as BASELINE=TARGET"),
                (("compare", "--snapshot", str(self.snapshot), "--limit", "-1", "app=app:*"), "nonnegative"),
                (("compare", "--snapshot", str(self.snapshot), "nope=app"), "names connection nope"),
                (("compare", "--snapshot", str(self.snapshot), "postgresql://u:secret@db/x=app"),
                 "A comparison is BASELINE=TARGET")):
            with self.subTest(args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn("secret", result.stdout + result.stderr)
        newer = json.loads(self.snapshot.read_text())
        newer["version"] = "2.1"
        self.snapshot.write_text(json.dumps(newer))
        result = self.run_cli("compare", "--snapshot", str(self.snapshot), "--write", "app:tenant_a=app:tenant_b")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--write rewrites only 2.0 documents", result.stderr)
        self.assertEqual(json.loads(self.snapshot.read_text())["version"], "2.1")

    def test_compare_never_connects_or_imports_application_code(self):
        # A sitecustomize-free probe: the audit hook refuses sockets, subprocesses and database drivers, and the
        # application package in the working directory would fail loudly if imported.
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("raise SystemExit('application code was imported')\n")
        probe = (
            "import sys\n"
            "def hook(event, args):\n"
            "    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system',\n"
            "                 'os.exec', 'os.posix_spawn', 'sqlite3.connect'):\n"
            "        raise RuntimeError('blocked ' + event)\n"
            "sys.addaudithook(hook)\n"
            "from laranex_schema_viewer.cli import main\n"
            f"code = main(['compare', '--snapshot', {str(self.snapshot)!r}, '--write', 'app:tenant_a=app:*'])\n"
            "loaded = sorted(n for n in sys.modules if n.split('.')[0] in ('sqlalchemy', 'psycopg', 'pymysql',\n"
            "    'django', 'tortoise', 'peewee', 'sqlmodel', 'mcp', 'app') or n.startswith(\n"
            "    ('laranex_schema_viewer.build', 'laranex_schema_viewer.isolation', 'laranex_schema_viewer.adapters',\n"
            "     'laranex_schema_viewer.introspection', 'laranex_schema_viewer.reconcile')))\n"
            "print(code, loaded)\n")
        result = subprocess.run([sys.executable, "-c", probe], cwd=self.root, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src")),
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "0 []")


class Builds(unittest.TestCase):
    """build --compare over two SQLite connections, each inspected by its own worker."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name, ddl in (("primary", "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(320) NOT NULL);"
                                      "CREATE INDEX users_email ON users (email);"),
                          ("replica", "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);"
                                      "CREATE TABLE extra (id INTEGER PRIMARY KEY);")):
            with closing(sqlite3.connect(self.root / f"{name}.db")) as connection:
                connection.executescript(ddl)

    def run_cli(self, *args):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"),
                           PRIMARY_URL=f"sqlite:///{self.root / 'primary.db'}",
                           REPLICA_URL=f"sqlite:///{self.root / 'replica.db'}")
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=120)

    def test_build_compares_inspected_connections(self):
        result = self.run_cli("build", "--source", "database", "--database", "primary=PRIMARY_URL",
                              "--database", "replica=REPLICA_URL", "--compare", "primary=replica",
                              "--output", "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Compared 1 scope pairs", result.stdout)
        self.assertNotIn(str(self.root / "primary.db"), result.stdout + result.stderr)
        document = json.loads((self.root / "out.json").read_text())
        self.assertEqual(validate_document(document, strict=True)[1], [])
        [comparison] = document["comparisons"]
        found = {(d["objectType"], d["change"], tuple(d["key"])): d for d in comparison["differences"]}
        self.assertEqual(set(found), {("table", "extra", ("extra",)), ("column", "changed", ("users", "email")),
                                      ("index", "missing", ("users", "name", "users_email"))})
        self.assertEqual([f["field"] for f in found["column", "changed", ("users", "email")]["fields"]],
                         ["nullable", "type"])
        # SQLite's check constraints are partial, so the comparison is incomplete and says why.
        self.assertFalse(comparison["complete"])
        [diagnostic] = [d for d in document["diagnostics"] if d["kind"] == "comparison_incomplete"]
        self.assertIn("checkConstraints partial", diagnostic["message"])

    def test_configured_comparisons_need_an_inspection(self):
        (self.root / "app.py").write_text("")
        (self.root / "pyproject.toml").write_text(
            "[tool.laranex-schema-viewer]\n"
            "[[tool.laranex-schema-viewer.comparisons]]\n"
            "baseline = { connection = 'primary' }\ntarget = { connection = 'replica' }\n")
        result = self.run_cli("build", "--source", "database", "--database", "primary=PRIMARY_URL",
                              "--database", "replica=REPLICA_URL", "--compare-limit", "1", "--output", "out.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("applies to comparisons named on the command line", result.stderr)
        result = self.run_cli("build", "--source", "database", "--database", "primary=PRIMARY_URL",
                              "--database", "replica=REPLICA_URL", "--output", "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads((self.root / "out.json").read_text())["comparisons"]), 1)
        result = self.run_cli("build", "--source", "declared", "--module", "app", "--compare", "primary=replica")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--compare compares inspected databases", result.stderr)


if __name__ == "__main__":
    unittest.main()
