"""build --source declared|database|combined, and serve and mcp on what it writes. Uses SQLite only."""

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

from laranex_schema_viewer.contract import canonical_json, validate_document

ROOT = Path(__file__).resolve().parents[1]
MODEL = (
    "import os\n"
    "from sqlalchemy import Column, ForeignKey, Integer, String\n"
    "from sqlalchemy.orm import declarative_base\n"
    "assert 'LARANEX_SCHEMA_DATABASE_URL' not in os.environ, 'the extraction worker saw the database URL'\n"
    "Base = declarative_base()\n"
    "class Team(Base):\n"
    "    __tablename__ = 'teams'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "class User(Base):\n"
    "    __tablename__ = 'users'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    email = Column(String(255), nullable=False)\n"
    "    team_id = Column(ForeignKey('teams.id'))\n"
)


@unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "SQLAlchemy is not installed")
class DatabaseBuilds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("")
        (self.root / "app" / "models.py").write_text(MODEL)
        self.database = self.root / "app.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(
                "CREATE TABLE teams (id INTEGER PRIMARY KEY);"
                "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(320) NOT NULL,"
                " team_id INTEGER REFERENCES teams (id), legacy INTEGER);")

    def run_cli(self, *args, url=True, env=None):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"), **(env or {}))
        environment.pop("LARANEX_SCHEMA_DATABASE_URL", None)
        if url:
            environment["LARANEX_SCHEMA_DATABASE_URL"] = f"sqlite:///{self.database}"
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=120)

    def read(self, name):
        text = (self.root / name).read_text()
        document = json.loads(text)
        self.assertEqual(text, canonical_json(document))
        self.assertEqual(validate_document(document, strict=True)[1], [])
        return document

    def test_combined(self):
        result = self.run_cli("build", "--module", "app.models", "--source", "combined", "--output", "out.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Inspected the database read-only (sqlite)", result.stdout)
        self.assertNotIn(str(self.database), result.stdout + result.stderr)
        document = self.read("out.json")
        self.assertEqual(document["source"], "combined")
        kinds = {(d["kind"], d["subject"]["label"]) for d in document["diagnostics"]}
        self.assertEqual(kinds, {("column_drift", "users.email"), ("database_only", "users.legacy")})

    def test_database_only(self):
        result = self.run_cli("build", "--source", "database", "--output", "db.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Extracted", result.stdout)  # No application code is imported.
        document = self.read("db.json")
        self.assertEqual((document["source"], document["models"]), ("database", []))

    def test_declared_documents(self):
        result = self.run_cli("build", "--module", "app.models")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved laranex-schema 2.0 (declared): 2 tables, 2 models", result.stdout)
        document = self.read("schema-viewer.json")
        self.assertEqual((document["format"], document["version"], document["source"]),
                         ("laranex-schema", "2.0", "declared"))
        self.assertEqual(document["generator"], {"name": "python-schema-viewer", "orm": "sqlalchemy"})

    def test_removed_options_and_commands(self):
        for args in (["build", "--format", "v1"], ["convert", "v1.json", "--output", "o.json"]):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("invalid choice: 'convert'" if args[0] == "convert" else "unrecognized arguments: --format",
                          result.stderr)
        (self.root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\nformat = 'v1'\n")
        result = self.run_cli("build", "--module", "app.models")
        self.assertIn("format was removed: build always writes laranex-schema 2.0", result.stderr)

    def test_failures_are_clear_and_change_nothing(self):
        result = self.run_cli("build", "--source", "combined", "--module", "app.models", "--output", "o.json",
                              url=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Set LARANEX_SCHEMA_DATABASE_URL", result.stderr)
        result = self.run_cli("build", "--source", "database", "--output", "o.json", url=False,
                              env={"LARANEX_SCHEMA_DATABASE_URL": "sqlite:///missing.db"})
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / "o.json").exists())
        self.assertFalse((self.root / "missing.db").exists())

    def test_urls_never_come_from_configuration(self):
        (self.root / "pyproject.toml").write_text("[tool.laranex-schema-viewer]\ndatabase_url = 'sqlite:///x'\n")
        result = self.run_cli("build", "--source", "database")
        self.assertIn("must not contain a database URL", result.stderr)
        (self.root / "pyproject.toml").write_text(
            "[tool.laranex-schema-viewer]\nenv_passthrough = ['APP_DB']\nsource = 'combined'\n"
            "modules = ['app.models']\ndatabase_url_env = 'APP_DB'\n")
        result = self.run_cli("build", env={"APP_DB": f"sqlite:///{self.database}"})
        self.assertIn("must not be passed to the extraction worker", result.stderr)

    def test_serve_and_mcp_open_laranex_schema_documents(self):
        from laranex_schema_viewer.query import SnapshotQuery
        from laranex_schema_viewer.server import create_server

        self.assertEqual(self.run_cli("build", "--source", "database", "--output", "db.json").returncode, 0)
        create_server(self.root / "db.json", port=0).server_close()
        self.assertEqual(SnapshotQuery(self.root / "db.json").overview()["source"], "database")

if __name__ == "__main__":
    unittest.main()
