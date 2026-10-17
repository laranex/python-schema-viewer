"""Several database connections: one introspection worker per connection, per-connection reconciliation, and
connection-qualified identities. tests/fixtures/multiconnection_models.py declares a users table on both connections
and a relationship between them without a foreign key. SQLite always runs; the mixed-driver test needs the PostgreSQL
and MySQL admin URLs described in tests/test_introspection.py."""

import importlib.util
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from laranex_schema_viewer.contract import canonical_json, document_to_json, validate_document

ROOT = Path(__file__).resolve().parents[1]
HAS_SQLALCHEMY = importlib.util.find_spec("sqlalchemy") is not None
MODULE = "tests.fixtures.multiconnection_models"

if HAS_SQLALCHEMY:
    from laranex_schema_viewer.adapters.sqlalchemy import SQLAlchemyAdapter
    from laranex_schema_viewer.build import build_document
    from laranex_schema_viewer.introspection import DatabaseRequest
    from laranex_schema_viewer.introspection.runner import introspect_all
    from laranex_schema_viewer.reconcile import combine as combine_documents
    from tests.fixtures import multiconnection_models as models

DEFAULT_USERS = '["default",null,"users"]'
ANALYTICS_USERS = '["analytics",null,"users"]'
# Planted on the analytics connection only.
EXPECTED = {
    ("column_drift", ANALYTICS_USERS, "users.segment"),
    ("database_only", ANALYTICS_USERS, "users.legacy"),
}


def declared():
    """Both metadata objects under the connection names, as a project that names its namespaces would have them."""
    return SQLAlchemyAdapter({"default": models.Base.metadata, "analytics": models.AnalyticsBase.metadata},
                             [models.Team, models.User, models.AnalyticsUser, models.Event]).extract()


def combine(declared_document, database_document, **kwargs):
    """The combined document as the laranex-schema 2.0 JSON a build writes."""
    return document_to_json(combine_documents(declared_document, database_document, **kwargs))


def sqlite_databases(directory):
    paths = {"default": Path(directory) / "app.db", "analytics": Path(directory) / "analytics.db"}
    for name, sql in (("default", models.DEFAULT_SQL), ("analytics", models.ANALYTICS_SQL)):
        connection = sqlite3.connect(paths[name])
        connection.executescript(sql)
        connection.close()
    return {name: f"sqlite:///{path}" for name, path in paths.items()}


def reconciliation(doc):
    return {(d["kind"], d["subject"]["id"], d["subject"]["label"]) for d in doc["diagnostics"]
            if d["category"] in ("drift", "reconciliation")}


class MultiConnection:
    """Shared assertions; subclasses set cls.doc to the combined document of both connections."""

    def connection(self, name):
        return next(c for c in self.doc["connections"] if c["name"] == name)

    def test_one_valid_document_with_both_connections(self):
        self.assertEqual(validate_document(self.doc, strict=True)[1], [])
        self.assertEqual(self.doc["source"], "combined")
        self.assertEqual([c["name"] for c in self.doc["connections"]], ["analytics", "default"])
        for connection in self.doc["connections"]:
            self.assertIsNotNone(connection["coverage"]["database"])
            self.assertEqual(connection["coverage"]["declared"]["complete"], True)

    def test_duplicate_table_names_never_collide(self):
        ids = {c["name"]: {t["name"]: t["id"] for t in c["tables"]} for c in self.doc["connections"]}
        self.assertEqual((ids["default"]["users"], ids["analytics"]["users"]), (DEFAULT_USERS, ANALYTICS_USERS))
        self.assertEqual(self.connection("analytics")["tables"][1]["provenance"], ["database", "declared"])
        fk_links = {(link["from"]["table"], link["to"]["table"]) for link in self.doc["links"] if link["foreignKey"]}
        self.assertEqual(fk_links, {(DEFAULT_USERS, '["default",null,"teams"]'),
                                    ('["analytics",null,"events"]', ANALYTICS_USERS)})

    def test_drift_lands_on_its_connection_and_names_it(self):
        self.assertEqual(reconciliation(self.doc), EXPECTED)
        for diagnostic in self.doc["diagnostics"]:
            if diagnostic["category"] in ("drift", "reconciliation"):
                self.assertTrue(diagnostic["message"].startswith("Connection analytics: "), diagnostic["message"])

    def test_cross_connection_relationship_needs_no_foreign_key(self):
        relationship = next(r for r in self.doc["relationships"] if r["name"] == "analytics_profile")
        self.assertEqual(relationship["keys"], {"from": {"table": ANALYTICS_USERS, "columns": ["user_id"]},
                                                "to": {"table": DEFAULT_USERS, "columns": ["id"]}})
        link = next(x for x in self.doc["links"] if relationship["id"] in x["relationships"])
        self.assertEqual((link["state"], link["foreignKey"]), ("relationship", None))
        users = next(t for t in self.connection("analytics")["tables"] if t["name"] == "users")
        self.assertEqual(users["foreignKeys"], [])
        subjects = {d["subject"]["id"] for d in self.doc["diagnostics"]}
        self.assertNotIn(relationship["id"], subjects)

    def test_models_keep_valid_table_mappings(self):
        tables = {t["id"] for c in self.doc["connections"] for t in c["tables"]}
        self.assertEqual(len(self.doc["models"]), 4)
        for model in self.doc["models"]:
            self.assertTrue(set(model["tables"]) <= tables, model["id"])


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class SQLiteMultiConnection(MultiConnection, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.urls = sqlite_databases(cls.tmp.name)
        cls.declared = declared()
        cls.database = introspect_all([DatabaseRequest(url=url, connection=name) for name, url in cls.urls.items()])
        cls.doc = combine(cls.declared, cls.database)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_database_document_has_one_connection_per_name(self):
        self.assertEqual(validate_document(document_to_json(self.database), strict=True)[1], [])
        self.assertEqual([(c.name, c.driver) for c in self.database.connections],
                         [("analytics", "sqlite"), ("default", "sqlite")])
        with self.assertRaisesRegex(Exception, "distinct name"):
            introspect_all([DatabaseRequest(url=self.urls["default"]), DatabaseRequest(url=self.urls["analytics"])])

    def test_declared_connection_without_a_database_stays_declared(self):
        database = introspect_all([DatabaseRequest(url=self.urls["default"], connection="default")])
        doc = combine(self.declared, database)
        self.assertEqual(validate_document(doc, strict=True)[1], [])
        analytics = next(c for c in doc["connections"] if c["name"] == "analytics")
        self.assertEqual(analytics["coverage"]["database"]["complete"], False)
        self.assertEqual({t["provenance"][0] for t in analytics["tables"]}, {"declared"})
        self.assertEqual(reconciliation(doc), set())
        self.assertIn(("inspection_unavailable", "connection:analytics"),
                      {(d["kind"], d["subject"]["id"]) for d in doc["diagnostics"]})

    def test_database_connection_without_declarations_stays_database_only(self):
        database = introspect_all([DatabaseRequest(url=url, connection=name) for name, url in
                                   (("default", self.urls["default"]), ("reporting", self.urls["analytics"]))])
        doc = combine(self.declared, database)
        self.assertEqual(validate_document(doc, strict=True)[1], [])
        reporting = next(c for c in doc["connections"] if c["name"] == "reporting")
        self.assertEqual((reporting["coverage"]["declared"]["complete"], reporting["tables"][1]["provenance"]),
                         (False, ["database"]))
        # Only a connection diagnostic: no table_without_model for every table of an undeclared connection.
        self.assertEqual({(d["kind"], d["subject"]["id"]) for d in doc["diagnostics"]
                          if d["category"] == "reconciliation"}, {("database_only", "connection:reporting")})

    def test_namespaces_map_discovered_metadata_names(self):
        # Discovered by importing the module, each namespace is named after the first attribute (in sorted order)
        # holding that metadata: here mapped classes, not the bases.
        names = {"default": f"{MODULE}:Team", "analytics": f"{MODULE}:AnalyticsUser"}
        found = build_document([MODULE], project_root=ROOT)
        self.assertEqual(sorted(c.name for c in found.connections), sorted(names.values()))
        with self.assertRaisesRegex(ValueError, f"matches a declared namespace \\(found: {MODULE}:AnalyticsUser, "):
            combine(found, self.database)
        doc = combine(found, self.database, namespaces=names)
        self.assertEqual(validate_document(doc, strict=True)[1], [])
        self.assertEqual([c["name"] for c in doc["connections"]], ["analytics", "default"])
        self.assertEqual({(kind, label) for kind, _, label in reconciliation(doc)},
                         {(kind, label) for kind, _, label in EXPECTED})
        users = {t["id"] for c in doc["connections"] for t in c["tables"] if t["name"] == "users"}
        self.assertEqual(len(users), 2)  # Declared ids stay as extracted; they are opaque.

    def test_namespace_mapping_errors(self):
        with self.assertRaisesRegex(ValueError, "maps to the declared namespace 'missing'"):
            combine(self.declared, self.database, namespaces={"analytics": "missing"})
        with self.assertRaisesRegex(ValueError, "give the connection another name"):
            combine(self.declared, self.database, namespaces={"analytics": "default", "default": "analytics"})
        replicas = introspect_all([DatabaseRequest(url=self.urls["default"], connection=name) for name in "xy"])
        with self.assertRaisesRegex(ValueError, "x and y both map to the declared namespace 'default'"):
            combine(self.declared, replicas, namespaces={"x": "default", "y": "default"})
        with self.assertRaisesRegex(ValueError, "one connection"):
            combine(self.declared, self.database, connection="default")

    def test_one_database_belongs_to_the_only_declared_namespace(self):
        single = SQLAlchemyAdapter({f"{MODULE}:AnalyticsBase": models.AnalyticsBase.metadata},
                                   [models.AnalyticsUser, models.Event]).extract()
        database = introspect_all([DatabaseRequest(url=self.urls["analytics"], connection="analytics")])
        doc = combine(single, database)
        self.assertEqual([c["name"] for c in doc["connections"]], ["analytics"])
        self.assertEqual({label for _, _, label in reconciliation(doc)}, {"users.segment", "users.legacy"})

    def test_queries_tell_duplicate_tables_apart(self):
        from laranex_schema_viewer.query import SnapshotQuery
        from laranex_schema_viewer.contract import document_from_json
        from laranex_schema_viewer.snapshot import write_document

        path = Path(self.tmp.name) / "combined.json"
        write_document(document_from_json(self.doc), path)
        query = SnapshotQuery(path)
        found = [i for i in query.search("users")["items"] if i["kind"] == "table"]
        self.assertEqual(sorted(i["id"] for i in found), [ANALYTICS_USERS, DEFAULT_USERS])
        overview = query.overview(limit=100)
        self.assertEqual({(t["connection"], t["name"]) for t in overview["items"] if t["name"] == "users"},
                         {("connection:default", "users"), ("connection:analytics", "users")})
        self.assertEqual([c["driver"] for c in overview["connections"]], ["sqlite", "sqlite"])
        self.assertEqual(query.get_table(ANALYTICS_USERS)["connection"]["name"], "analytics")
        orm = query.relationships(ANALYTICS_USERS)["items"]
        self.assertEqual([r["name"] for r in orm], ["analytics_profile"])
        self.assertEqual([fk["table"] for fk in query.relationships(ANALYTICS_USERS, "foreign_key")["items"]],
                         ['["analytics",null,"events"]'])


class RecordingPopen:
    """Wraps subprocess.Popen to record what each introspection worker is given."""

    def __init__(self):
        self.calls = []
        self.real = subprocess.Popen

    def __call__(self, *args, **kwargs):
        process = self.real(*args, **kwargs)
        call = {"args": args[0], "env": kwargs.get("env"), "stdin": b""}
        self.calls.append(call)
        stdin = process.stdin

        class Recorder:
            def write(self, data):
                call["stdin"] += data
                return stdin.write(data)

            def close(self):
                stdin.close()

        process.stdin = Recorder()
        return process


class MixedDrivers(MultiConnection, unittest.TestCase):
    """default on PostgreSQL, analytics on MySQL, each inspected with its own read-only role."""

    @classmethod
    def setUpClass(cls):
        postgres, mysql = os.environ.get("LSV_TEST_POSTGRES_URL"), os.environ.get("LSV_TEST_MYSQL_URL")
        if not (postgres and mysql and HAS_SQLALCHEMY):
            raise unittest.SkipTest("set LSV_TEST_POSTGRES_URL and LSV_TEST_MYSQL_URL to run these tests")
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        cls.name = f"lsv_multi_{secrets.token_hex(4)}"
        cls.role, cls.password = f"lsv_reader_{secrets.token_hex(4)}", secrets.token_hex(12)
        cls.pg_admin = create_engine(make_url(postgres), isolation_level="AUTOCOMMIT")
        cls.my_admin = create_engine(make_url(mysql), isolation_level="AUTOCOMMIT")
        try:
            with cls.pg_admin.connect() as c:
                c.exec_driver_sql(f'CREATE DATABASE "{cls.name}"')
                c.exec_driver_sql(f"CREATE ROLE \"{cls.role}\" LOGIN PASSWORD '{cls.password}'")
            engine = create_engine(make_url(postgres).set(database=cls.name))
            with engine.begin() as c:
                c.exec_driver_sql(models.DEFAULT_SQL)
                c.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{cls.role}"')
            engine.dispose()
            with cls.my_admin.connect() as c:
                c.exec_driver_sql(f"CREATE DATABASE `{cls.name}`")
                c.exec_driver_sql(f"CREATE USER '{cls.role}'@'%%' IDENTIFIED BY '{cls.password}'")
                c.exec_driver_sql(f"GRANT SELECT ON `{cls.name}`.* TO '{cls.role}'@'%%'")
            engine = create_engine(make_url(mysql).set(database=cls.name))
            with engine.begin() as c:
                c.exec_driver_sql("CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL, "
                                  "segment VARCHAR(40) NOT NULL, legacy INTEGER)")
                c.exec_driver_sql("CREATE TABLE events (id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL, "
                                  "name VARCHAR(100) NOT NULL, FOREIGN KEY (user_id) REFERENCES users (id))")
            engine.dispose()
            cls.urls = {
                "default": make_url(postgres).set(database=cls.name, username=cls.role, password=cls.password)
                .render_as_string(hide_password=False),
                "analytics": make_url(mysql).set(database=cls.name, username=cls.role, password=cls.password)
                .render_as_string(hide_password=False),
            }
            cls.popen = RecordingPopen()
            with mock.patch("laranex_schema_viewer.introspection.runner.subprocess.Popen", cls.popen):
                database = introspect_all([DatabaseRequest(url=url, connection=name)
                                           for name, url in cls.urls.items()])
            cls.drivers = {c.name: c.driver for c in database.connections}
            cls.doc = combine(declared(), database)
        except Exception:
            cls.drop()
            raise

    @classmethod
    def drop(cls):
        with cls.pg_admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{cls.name}" WITH (FORCE)')
            c.exec_driver_sql(f'DROP ROLE IF EXISTS "{cls.role}"')
        with cls.my_admin.connect() as c:
            c.exec_driver_sql(f"DROP DATABASE IF EXISTS `{cls.name}`")
            c.exec_driver_sql(f"DROP USER IF EXISTS '{cls.role}'@'%%'")
        cls.pg_admin.dispose()
        cls.my_admin.dispose()

    @classmethod
    def tearDownClass(cls):
        cls.drop()

    def test_each_connection_keeps_its_driver(self):
        self.assertEqual(self.drivers, {"default": "postgresql", "analytics": "mysql"})

    def test_each_worker_received_only_its_own_url_on_stdin(self):
        self.assertEqual(len(self.popen.calls), 2)
        for call, (name, url) in zip(self.popen.calls, self.urls.items()):
            other = next(u for n, u in self.urls.items() if n != name)
            payload = json.loads(call["stdin"])
            self.assertEqual((payload["connection"], payload["url"]), (name, url))
            self.assertNotIn(other, call["stdin"].decode())
            visible = json.dumps([call["args"], call["env"]])
            self.assertNotIn(self.password, visible)


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class MultiConnectionBuilds(unittest.TestCase):
    """The CLI end to end, in a subprocess, with two SQLite connections."""

    MODEL = ("import os\n"
             "assert not {'APP_DATABASE_URL', 'ANALYTICS_DATABASE_URL'} & set(os.environ), 'the worker saw a URL'\n"
             f"from {MODULE} import *  # noqa: F403\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("")
        (self.root / "app" / "models.py").write_text(self.MODEL)
        (self.root / "tests" / "fixtures").mkdir(parents=True)
        for package in ("tests", "tests/fixtures"):
            (self.root / package / "__init__.py").write_text("")
        source = ROOT / "tests" / "fixtures" / "multiconnection_models.py"
        (self.root / "tests" / "fixtures" / "multiconnection_models.py").write_text(source.read_text())
        self.urls = sqlite_databases(self.root)

    def config(self, body, selection="modules = ['app.models']\n"):
        (self.root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\n" + selection + body)

    def run_cli(self, *args, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.endswith("DATABASE_URL")}
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"),
                           APP_DATABASE_URL=self.urls["default"], ANALYTICS_DATABASE_URL=self.urls["analytics"],
                           **(env or {}))
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "build", *args], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=180)

    def read(self, name):
        text = (self.root / name).read_text()
        document = json.loads(text)
        self.assertEqual(text, canonical_json(document))
        self.assertEqual(validate_document(document, strict=True)[1], [])
        return document

    def test_combined_from_configured_connections(self):
        self.config("[tool.laranex-schema-viewer.databases.default]\nurl_env = 'APP_DATABASE_URL'\n"
                    "namespace = 'app.models:Base'\n"
                    "[tool.laranex-schema-viewer.databases.analytics]\nurl_env = 'ANALYTICS_DATABASE_URL'\n"
                    "namespace = 'app.models:AnalyticsBase'\n",
                    # Targets name the namespaces after themselves, which keeps the mapping stable.
                    selection="targets = ['app.models:Base', 'app.models:AnalyticsBase']\n")
        result = self.run_cli("--source", "combined", "--output", "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Inspected connection analytics read-only (sqlite)", result.stdout)
        self.assertIn("Inspected connection default read-only (sqlite)", result.stdout)
        self.assertNotIn(str(self.root / "app.db"), result.stdout + result.stderr)
        document = self.read("out.json")
        self.assertEqual([c["name"] for c in document["connections"]], ["analytics", "default"])
        self.assertEqual({(d["kind"], d["subject"]["label"]) for d in document["diagnostics"]},
                         {("column_drift", "users.segment"), ("database_only", "users.legacy")})

    def test_database_options_select_connections(self):
        result = self.run_cli("--source", "database", "--output", "db.json", "--database", "default=APP_DATABASE_URL",
                              "--database", "analytics=ANALYTICS_DATABASE_URL")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Extracted", result.stdout)
        document = self.read("db.json")
        self.assertEqual([c["name"] for c in document["connections"]], ["analytics", "default"])
        self.assertEqual(len({t["id"] for c in document["connections"] for t in c["tables"]}), 4)

    def test_single_connection_options_are_unchanged(self):
        (self.root / "app" / "models.py").write_text(
            self.MODEL.replace("import *  # noqa: F403", "import Base, Team, User  # noqa: F401")
            .replace(f"from {MODULE}", "from tests.fixtures.single"))
        (self.root / "tests" / "fixtures" / "single.py").write_text(
            "from sqlalchemy import ForeignKey, String\n"
            "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
            "class Base(DeclarativeBase):\n    pass\n"
            "class Team(Base):\n    __tablename__ = 'teams'\n    id: Mapped[int] = mapped_column(primary_key=True)\n"
            "class User(Base):\n    __tablename__ = 'users'\n    id: Mapped[int] = mapped_column(primary_key=True)\n"
            "    email: Mapped[str] = mapped_column(String(255))\n"
            "    team_id: Mapped[int | None] = mapped_column(ForeignKey('teams.id'))\n")
        legacy = self.run_cli("--source", "combined", "--output", "legacy.json", "--database-url-env",
                              "APP_DATABASE_URL")
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertIn("Inspected the database read-only (sqlite)", legacy.stdout)
        named = self.run_cli("--source", "combined", "--output", "named.json", "--database", "default=APP_DATABASE_URL")
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assertEqual(self.read("legacy.json"), self.read("named.json"))
        self.assertEqual(self.read("legacy.json")["diagnostics"], [])

    def test_failures_are_clear(self):
        cases = [
            (["--database", "default=APP_DATABASE_URL", "--schema", "public"], "cannot be combined with --schema"),
            (["--database", "default=sqlite:///x.db?mode=ro"], "--database takes NAME=ENV_VAR"),
            (["--database", "default=APP_DATABASE_URL", "--database", "default=ANALYTICS_DATABASE_URL"],
             "more than once"),
            (["--database", "default=MISSING_URL"], "Set MISSING_URL to a read-only database URL for connection default"),
        ]
        self.config("")
        for args, message in cases:
            result = self.run_cli("--source", "database", "--output", "o.json", *args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(message, result.stderr)
            self.assertNotIn("x.db", result.stderr)
        self.config("database_url_env = 'APP_DATABASE_URL'\n")
        result = self.run_cli("--source", "database", "--database", "default=APP_DATABASE_URL")
        self.assertIn("cannot be combined with database_url_env in pyproject.toml", result.stderr)
        self.config("[tool.laranex-schema-viewer.databases.default]\nurl_env = 'APP_DATABASE_URL'\n")
        result = self.run_cli("--source", "database", "--database-url-env", "APP_DATABASE_URL")
        self.assertIn("cannot be combined with [tool.laranex-schema-viewer.databases]", result.stderr)
        self.config("env_passthrough = ['ANALYTICS_DATABASE_URL']\n"
                    "[tool.laranex-schema-viewer.databases.analytics]\nurl_env = 'ANALYTICS_DATABASE_URL'\n")
        result = self.run_cli("--source", "combined")
        self.assertIn("ANALYTICS_DATABASE_URL holds a database URL and must not be passed", result.stderr)
        self.config("[tool.laranex-schema-viewer.env]\nAPP_DATABASE_URL = 'x'\n")
        result = self.run_cli("--source", "combined", "--database", "default=APP_DATABASE_URL")
        self.assertIn("APP_DATABASE_URL holds a database URL", result.stderr)
        self.assertFalse((self.root / "o.json").exists())


del MultiConnection

if __name__ == "__main__":
    unittest.main()
