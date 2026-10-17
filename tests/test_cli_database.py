"""build --source database|combined, convert, and how serve treats laranex-schema documents. Uses SQLite only."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
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
        sqlite3.connect(self.database).executescript(
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

    def test_declared_documents_and_conversion(self):
        result = self.run_cli("build", "--module", "app.models", "--format", "laranex-schema", "--output", "d.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read("d.json")["source"], "declared")
        result = self.run_cli("build", "--module", "app.models", "--output", "v1.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "v1.json").read_text())["format"], "laranex-schema-viewer")
        result = self.run_cli("convert", "v1.json", "--output", "converted.json", "--orm", "sqlalchemy")
        self.assertEqual(result.returncode, 0, result.stderr)
        converted = self.read("converted.json")
        self.assertEqual(converted["generator"]["orm"], "sqlalchemy")
        self.assertEqual(converted["connections"], {**self.read("d.json"), "generator": converted["generator"]}[
            "connections"])

    def test_failures_are_clear_and_change_nothing(self):
        result = self.run_cli("build", "--source", "combined", "--module", "app.models", "--output", "o.json",
                              url=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Set LARANEX_SCHEMA_DATABASE_URL", result.stderr)
        result = self.run_cli("build", "--source", "database", "--format", "v1", "--output", "o.json")
        self.assertIn("cannot hold database metadata", result.stderr)
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

    def test_serve_explains_laranex_schema_documents(self):
        self.assertEqual(self.run_cli("build", "--source", "database", "--output", "db.json").returncode, 0)
        result = self.run_cli("serve", "--snapshot", "db.json", "--port", "0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is a laranex-schema document", result.stderr)


if __name__ == "__main__":
    unittest.main()
