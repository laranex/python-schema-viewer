"""The served shared viewer in a real browser, for declared, database, combined and multi-connection documents, and the
comparison, migration and inferred-association views (a built comparison, and the shared fixtures for the others).

Opt in with LSV_BROWSER=1; needs Node.js 22+ and Chrome or Chromium (CHROME_BIN, else found on PATH or in the usual
macOS location). scripts/browser-smoke.mjs drives the browser over the DevTools Protocol and fails on uncaught
exceptions, console errors, CSP violations and requests that leave the local server.
"""

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from laranex_schema_viewer.server import create_server

ROOT = Path(__file__).resolve().parents[1]
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def find_chrome():
    if os.environ.get("CHROME_BIN"):
        return os.environ["CHROME_BIN"]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return MAC_CHROME if Path(MAC_CHROME).exists() else None


CHROME = find_chrome()
NODE = shutil.which("node")


@unittest.skipUnless(os.environ.get("LSV_BROWSER") == "1", "set LSV_BROWSER=1 to run the browser tests")
@unittest.skipUnless(NODE and CHROME, "Node.js and Chrome or Chromium are required")
@unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "SQLAlchemy is not installed")
class ServedViewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        cls.database = cls.root / "drift.db"
        connection = sqlite3.connect(cls.database)
        connection.executescript((ROOT / "tests/databases/drift-sqlite.sql").read_text())
        connection.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, name, *args, database=False, env=None):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))
        environment.pop("LARANEX_SCHEMA_DATABASE_URL", None)
        if database:
            environment["LARANEX_SCHEMA_DATABASE_URL"] = f"sqlite:///{self.database}"
        environment.update(env or {})
        output = self.root / name
        result = subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "build", *args, "--output", str(output)],
                                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output

    def check(self, snapshot, **expect):
        server = create_server(snapshot, port=0)
        server.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            environment = dict(os.environ, CHROME_BIN=CHROME, EXPECT=json.dumps(expect),
                               VIEWER_URL=f"http://127.0.0.1:{server.server_address[1]}/")
            result = subprocess.run([NODE, str(ROOT / "scripts/browser-smoke.mjs")], env=environment,
                                    capture_output=True, text=True, timeout=240)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def assert_declared(self, snapshot):
        report = self.check(snapshot, source="declared", tables=6, connections=1, coverage=["declared"],
                            diagnostics=0, table={"name": "users", "indexes": ["ix_users_active", "ix_users_email_lower"],
                                                  "relationships": ["manager", "reports", "teams"]})
        self.assertGreater(report["edges"], 0)

    def test_declared_document(self):
        snapshot = self.build("declared.json", "--module", "tests.fixtures.sqlalchemy_models")
        self.assertEqual(json.loads(snapshot.read_text())["version"], "2.0")
        self.assert_declared(snapshot)

    def test_database_document(self):
        snapshot = self.build("database.json", "--source", "database", database=True)
        report = self.check(snapshot, source="database", tables=6, connections=1, coverage=["database"], diagnostics=0,
                            table={"name": "users", "indexes": ["ix_users_email"]})
        self.assertGreater(report["edges"], 0)
        self.assertEqual(report["table"]["relationships"], [])

    def test_combined_document(self):
        snapshot = self.build("combined.json", "--source", "combined", "--module", "tests.fixtures.drift_models",
                              database=True)
        report = self.check(snapshot, source="combined", tables=7, connections=1, coverage=["database", "declared"],
                            diagnostics=9, table={
                                "name": "users", "indexes": ["ix_users_email"], "relationships": ["team", "users"],
                                "drift": ["email"],
                                "provenance": {"nickname": "declared only", "legacy_flag": "database only"}})
        self.assertGreater(report["edges"], 0)
        self.assertEqual(sorted(report["certainty"]), ["fact", "heuristic"])

    def test_comparison_document(self):
        # Two SQLite connections compared at build time: SQLite's partial check-constraint coverage makes the stored
        # comparison incomplete, which the Compare tab must say.
        urls = {}
        for name, ddl in (("primary", "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(320) NOT NULL);"
                                      "CREATE INDEX users_email ON users (email);"),
                          ("replica", "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);"
                                      "CREATE TABLE extra (id INTEGER PRIMARY KEY);")):
            connection = sqlite3.connect(self.root / f"{name}.db")
            connection.executescript(ddl)
            connection.close()
            urls[f"{name.upper()}_URL"] = f"sqlite:///{self.root / name}.db"
        snapshot = self.build("comparison.json", "--source", "database", "--database", "primary=PRIMARY_URL",
                              "--database", "replica=REPLICA_URL", "--compare", "primary=replica",
                              "--compare", "replica=primary", env=urls)
        report = self.check(snapshot, source="database", tables=3, connections=2, compare=True)
        self.assertEqual(report["compare"]["comparisons"], 2)
        self.assertEqual(report["compare"]["summary"], ["1", "1", "1", "3"])
        self.assertFalse(report["compare"]["complete"])

    def test_shared_intelligence_fixtures(self):
        valid = ROOT / "tests/fixtures/contract/fixtures/valid"
        for name, expect in (("comparison-tenant-schemas.json", {"compare": True}),
                             ("comparison-schema-named-tables.json", {"compare": True}),
                             ("migrations-alembic.json", {"migrations": 10}),
                             ("inferred-associations.json", {"inferred": True})):
            with self.subTest(name):
                source = json.loads((valid / name).read_text())["source"]
                report = self.check(valid / name, source=source, **expect)
                self.assertTrue(set(expect) <= set(report), report)

    def test_multiconnection_document(self):
        # Two SQLite connections from tests/fixtures/multiconnection_models.py: users on both, a relationship between
        # them without a foreign key, and drift on analytics only. The models are copied into a project of their own,
        # whose configuration maps each connection to its declared base.
        project = self.root / "multiconnection"
        project.mkdir()
        fixture = ROOT / "tests/fixtures/multiconnection_models.py"
        shutil.copy(fixture, project / "models.py")
        from tests.fixtures import multiconnection_models as models

        urls = {}
        for name, variable, sql in (("app", "APP_DATABASE_URL", models.DEFAULT_SQL),
                                    ("analytics", "ANALYTICS_DATABASE_URL", models.ANALYTICS_SQL)):
            connection = sqlite3.connect(project / f"{name}.db")
            connection.executescript(sql)
            connection.close()
            urls[variable] = f"sqlite:///{project / name}.db"
        (project / "pyproject.toml").write_text(
            "[tool.laranex-schema-viewer]\ntargets = ['models:Base', 'models:AnalyticsBase']\n"
            "[tool.laranex-schema-viewer.databases.default]\nurl_env = 'APP_DATABASE_URL'\nnamespace = 'models:Base'\n"
            "[tool.laranex-schema-viewer.databases.analytics]\nurl_env = 'ANALYTICS_DATABASE_URL'\n"
            "namespace = 'models:AnalyticsBase'\n")
        snapshot = self.build("multiconnection.json", "--project-root", str(project), "--source", "combined", env=urls)
        report = self.check(snapshot, source="combined", tables=4, connections=2, coverage=["database", "declared"],
                            diagnostics=2, multiConnection={
                                "regions": {"analytics": "sqlite", "default": "sqlite"}, "duplicate": "users",
                                "drift": {"analytics": ["segment"]}})
        multi = report["multiConnection"]
        self.assertEqual(multi["reference"], ["On connection analytics · sqlite", "users"])
        self.assertEqual([label for label, _ in multi["search"]], ["analytics · users", "default · users"])
        self.assertEqual(multi["diagnostics"], 2)


if __name__ == "__main__":
    unittest.main()
