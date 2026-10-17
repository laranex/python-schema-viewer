"""Reconciliation of declared models with a live database: the same drift on every supported database.

tests/fixtures/drift_models.py and tests/databases/drift-*.sql differ on purpose. SQLite always runs; the servers use
the admin URLs described in tests/test_introspection.py.
"""

import importlib.util
import os
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from laranex_schema_viewer.contract import document_to_json, validate_document

ROOT = Path(__file__).resolve().parents[1]
DATABASES = ROOT / "tests" / "databases"
HAS_SQLALCHEMY = importlib.util.find_spec("sqlalchemy") is not None

if HAS_SQLALCHEMY:
    from laranex_schema_viewer.build import build_document
    from laranex_schema_viewer.introspection import DatabaseRequest
    from laranex_schema_viewer.introspection.runner import introspect
    from laranex_schema_viewer.reconcile import combine, normalize_type

EXPECTED = {
    ("column_drift", "users.email", "likely"),
    ("column_drift", "users.status", "likely"),
    ("database_only", "users.legacy_flag", "certain"),
    ("declared_only", "users.nickname", "certain"),
    ("declared_only", None, "certain"),  # the users.team_id foreign key, which is unnamed
    ("foreign_key_drift", "line_notes_line_fk", "certain"),
    ("index_drift", "ix_users_email", "certain"),
    ("model_without_table", "Ghost", "certain"),
    ("table_without_model", "audit_log", "certain"),
}


def declared():
    return build_document(["tests.fixtures.drift_models"], project_root=ROOT)


def combined(declared_document, database_document, **kwargs):
    """The combined document as the laranex-schema 2.0 JSON a build writes."""
    return document_to_json(combine(declared_document, database_document, **kwargs))


def label(diagnostic):
    value = diagnostic["subject"]["label"]
    return value.rsplit(".", 1)[-1] if diagnostic["kind"] == "model_without_table" else value


class Reconciliation:
    """Shared assertions; subclasses set cls.doc to the combined document."""

    def tables(self):
        return {t["name"]: t for c in self.doc["connections"] for t in c["tables"]}

    def test_is_a_strictly_valid_combined_document(self):
        self.assertEqual(validate_document(self.doc, strict=True)[1], [])
        self.assertEqual(self.doc["source"], "combined")
        coverage = self.doc["connections"][0]["coverage"]
        self.assertIsNotNone(coverage["database"])
        self.assertIsNotNone(coverage["declared"])

    def test_reports_exactly_the_planted_differences(self):
        found = {(d["kind"], label(d), d["certainty"]) for d in self.doc["diagnostics"]
                 if d["category"] in ("drift", "reconciliation")}
        self.assertEqual(found, EXPECTED)

    def test_provenance(self):
        tables = self.tables()
        self.assertEqual(tables["users"]["provenance"], ["database", "declared"])
        self.assertEqual((tables["ghosts"]["provenance"], tables["audit_log"]["provenance"]),
                         (["declared"], ["database"]))
        columns = {c["name"]: c for c in tables["users"]["columns"]}
        self.assertEqual((columns["nickname"]["provenance"], columns["legacy_flag"]["provenance"]),
                         (["declared"], ["database"]))
        self.assertEqual(columns["email"]["drift"][0]["field"], "type")
        self.assertEqual(columns["status"]["drift"][0]["field"], "default")
        memberships = tables["memberships"]
        self.assertEqual(memberships["primaryKey"], ["user_id", "team_id"])
        self.assertTrue(all(fk["provenance"] == ["database", "declared"] for fk in memberships["foreignKeys"]))

    def test_keeps_declared_identities_and_relationships(self):
        tables = self.tables()
        self.assertEqual(tables["users"]["id"], '["default",null,"users"]')
        self.assertEqual(len(self.doc["relationships"]), len(self.declared["relationships"]))
        self.assertEqual(self.doc["models"], self.declared["models"])
        note = tables["line_notes"]["foreignKeys"][0]
        self.assertEqual((note["columns"], note["onDelete"], note["drift"][0]),
                         (["order_id", "line_no"], "NO ACTION",
                          {"field": "onDelete", "database": "NO ACTION", "declared": "CASCADE"}))
        states = {(link["from"]["table"].split(",")[-1], link["state"]) for link in self.doc["links"]}
        self.assertIn(('"line_notes"]', "both"), states)

    def test_matching_tables_report_nothing(self):
        subjects = {d["subject"]["id"] for d in self.doc["diagnostics"]}
        for table in ("teams", "order_lines", "memberships"):
            tid = self.tables()[table]["id"]
            self.assertFalse([s for s in subjects if s and s.startswith(tid)], table)


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class SQLiteReconciliation(Reconciliation, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(tempfile.mkdtemp(prefix="lsv-drift-")) / "drift.db"
        connection = sqlite3.connect(path)
        connection.executescript((DATABASES / "drift-sqlite.sql").read_text())
        connection.close()
        cls.declared_document = declared()
        cls.declared = document_to_json(cls.declared_document)
        cls.doc = combined(cls.declared_document, introspect(DatabaseRequest(url=f"sqlite:///{path}")))

    def test_requires_a_declared_connection_of_that_name(self):
        database = introspect(DatabaseRequest(url=f"sqlite:///{self.path()}"))
        with self.assertRaisesRegex(ValueError, "no connection named 'replica'"):
            combine(self.declared_document, database, connection="replica")

    def path(self):
        path = Path(tempfile.mkdtemp(prefix="lsv-drift-")) / "other.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY);")
        return path


class ServerReconciliation(Reconciliation):
    variable = ""

    @classmethod
    def setUpClass(cls):
        admin = os.environ.get(cls.variable)
        if not admin or not HAS_SQLALCHEMY:
            raise unittest.SkipTest(f"set {cls.variable} to run these tests (see tests/test_introspection.py)")
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        cls.admin_url = make_url(admin)
        cls.database = f"lsv_drift_{secrets.token_hex(4)}"
        cls.role, cls.password = f"lsv_reader_{secrets.token_hex(4)}", secrets.token_hex(12)
        cls.admin = create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        try:
            cls.create()
            url = cls.admin_url.set(database=cls.database, username=cls.role, password=cls.password)
            cls.declared_document = declared()
            cls.declared = document_to_json(cls.declared_document)
            cls.doc = combined(cls.declared_document,
                               introspect(DatabaseRequest(url=url.render_as_string(hide_password=False))))
        except Exception:
            cls.drop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.drop()
        cls.admin.dispose()


class PostgreSQLReconciliation(ServerReconciliation, unittest.TestCase):
    variable = "LSV_TEST_POSTGRES_URL"

    @classmethod
    def create(cls):
        from sqlalchemy import create_engine

        with cls.admin.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{cls.database}"')
            c.exec_driver_sql(f'CREATE ROLE "{cls.role}" LOGIN PASSWORD \'{cls.password}\'')
        engine = create_engine(cls.admin_url.set(database=cls.database))
        with engine.begin() as c:
            c.exec_driver_sql((DATABASES / "drift-postgresql.sql").read_text())
            c.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{cls.role}"')
        engine.dispose()

    @classmethod
    def drop(cls):
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            c.exec_driver_sql(f'DROP ROLE IF EXISTS "{cls.role}"')


class MySQLFamilyReconciliation(ServerReconciliation):
    @classmethod
    def create(cls):
        from sqlalchemy import create_engine

        with cls.admin.connect() as c:
            c.exec_driver_sql(f"CREATE DATABASE `{cls.database}`")
            c.exec_driver_sql(f"CREATE USER '{cls.role}'@'%%' IDENTIFIED BY '{cls.password}'")
            c.exec_driver_sql(f"GRANT SELECT ON `{cls.database}`.* TO '{cls.role}'@'%%'")
        engine = create_engine(cls.admin_url.set(database=cls.database))
        with engine.begin() as c:
            for statement in (DATABASES / "drift-mysql.sql").read_text().split(";\n"):
                body = "\n".join(line for line in statement.splitlines() if not line.startswith("--")).strip()
                if body:
                    c.exec_driver_sql(body.replace("%", "%%"))
        engine.dispose()

    @classmethod
    def drop(cls):
        with cls.admin.connect() as c:
            c.exec_driver_sql(f"DROP DATABASE IF EXISTS `{cls.database}`")
            c.exec_driver_sql(f"DROP USER IF EXISTS '{cls.role}'@'%%'")


class MySQLReconciliation(MySQLFamilyReconciliation, unittest.TestCase):
    variable = "LSV_TEST_MYSQL_URL"


class MariaDBReconciliation(MySQLFamilyReconciliation, unittest.TestCase):
    variable = "LSV_TEST_MARIADB_URL"


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class TypeNormalization(unittest.TestCase):
    def test_equivalent_spellings(self):
        for a, b in (("VARCHAR(255)", "character varying(255)"), ("INTEGER", "int(11)"), ("INTEGER", "int4"),
                     ("BIGINT", "bigint(20) unsigned"), ("DATETIME", "timestamp without time zone"),
                     ("NUMERIC(10, 2)", "decimal(10,2)"), ("BOOLEAN", "tinyint(1)"), ("TEXT[]", "text[]"),
                     ("FLOAT", "double precision")):
            self.assertEqual(normalize_type(a), normalize_type(b), (a, b))

    def test_real_differences(self):
        for a, b in (("VARCHAR(255)", "varchar(320)"), ("INTEGER", "bigint"), ("JSON", "jsonb"),
                     ("TEXT", "varchar(10)")):
            self.assertNotEqual(normalize_type(a), normalize_type(b), (a, b))
        self.assertEqual(normalize_type("DATETIME", timezone=True), normalize_type("timestamp with time zone"))
        self.assertNotEqual(normalize_type("DATETIME"), normalize_type("timestamp with time zone"))


del Reconciliation, ServerReconciliation, MySQLFamilyReconciliation

if __name__ == "__main__":
    unittest.main()
