"""Live database introspection: accuracy, read-only enforcement, role checks, and separation from application code.

SQLite tests always run. Server tests need disposable servers (tests/databases/compose.yaml) and admin URLs:

    docker compose -f tests/databases/compose.yaml up -d
    export LSV_TEST_POSTGRES_URL=postgresql+psycopg://postgres:lsv-admin@127.0.0.1:55432/lsv
    export LSV_TEST_MYSQL_URL=mysql+pymysql://root:lsv-admin@127.0.0.1:53306/lsv
    export LSV_TEST_MARIADB_URL=mysql+pymysql://root:lsv-admin@127.0.0.1:53307/lsv

Each test class creates its own database and roles with random passwords, and drops them afterwards.
"""

import hashlib
import importlib.util
import os
import secrets
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from laranex_schema_viewer.contract import validate_document
from laranex_schema_viewer.introspection import DatabaseRequest, IntrospectionError

ROOT = Path(__file__).resolve().parents[1]
DATABASES = ROOT / "tests" / "databases"
HAS_SQLALCHEMY = importlib.util.find_spec("sqlalchemy") is not None

if HAS_SQLALCHEMY:
    from laranex_schema_viewer.introspection.readonly import STATEMENT, create_engine, read_only
    from laranex_schema_viewer.introspection.runner import introspect
    from laranex_schema_viewer.introspection.worker import loaded_from, scrub


def tables(doc):
    return {t["name"]: t for c in doc["connections"] for t in c["tables"]}


def by_name(items):
    return {i["name"]: i for i in items}


def statements(path):
    for statement in path.read_text().split(";\n"):
        body = "\n".join(line for line in statement.splitlines() if not line.startswith("--")).strip()
        if body:
            yield body


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class SQLiteIntrospection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="lsv-sqlite-")).resolve()
        cls.path = cls.dir / "notes.db"
        connection = sqlite3.connect(cls.path)
        connection.executescript((DATABASES / "sqlite.sql").read_text())
        connection.close()
        cls.before = hashlib.sha256(cls.path.read_bytes()).hexdigest()
        cls.doc = introspect(DatabaseRequest(url=f"sqlite:///{cls.path}"), project_roots=[str(ROOT)])
        cls.tables = tables(cls.doc)

    def test_is_a_strictly_valid_database_document(self):
        self.assertEqual(validate_document(self.doc, strict=True)[1], [])
        self.assertEqual(self.doc["source"], "database")
        connection = self.doc["connections"][0]
        self.assertEqual((connection["driver"], connection["coverage"]["declared"]), ("sqlite", None))
        features = connection["coverage"]["database"]["features"]
        self.assertEqual((features["comments"], features["checkConstraints"]), ("not_applicable", "partial"))

    def test_the_database_file_is_unchanged(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["notes.db"])

    def test_composite_keys_and_foreign_keys(self):
        self.assertEqual(self.tables["tags"]["primaryKey"], ["note_id", "name"])
        fk = self.tables["tag_links"]["foreignKeys"][0]
        self.assertEqual((fk["columns"], fk["foreignColumns"]), (["note_id", "tag"], ["note_id", "name"]))
        self.assertEqual((fk["deferrable"], fk["initially"]), (True, "DEFERRED"))
        self.assertEqual(self.tables["tags"]["foreignKeys"][0]["onDelete"], "CASCADE")
        self.assertEqual(len(self.doc["links"]), 2)

    def test_indexes(self):
        indexes = by_name(self.tables["notes"]["indexes"])
        self.assertEqual(indexes["notes_active_title"]["predicate"], "archived = 0")
        self.assertEqual(indexes["notes_lower_title"]["parts"][0]["expression"], "lower(title)")
        nocase = indexes["notes_title_nocase"]["parts"][0]
        self.assertEqual((nocase["collation"], nocase["order"]), ("NOCASE", "desc"))
        self.assertIsNone(indexes["notes_active_title"]["parts"][0]["collation"])
        self.assertEqual(indexes["sqlite_autoindex_notes_1"]["backs"]["kind"], "unique_constraint")
        primary = by_name(self.tables["tags"]["indexes"])["sqlite_autoindex_tags_1"]
        self.assertEqual((primary["primary"], primary["columns"]), (True, ["note_id", "name"]))

    def test_columns_and_constraints(self):
        notes = self.tables["notes"]
        archived = by_name(notes["columns"])["archived"]
        self.assertEqual((archived["default"], archived["nullable"]), ({"kind": "literal", "value": "0"}, False))
        self.assertTrue(by_name(notes["columns"])["id"]["autoIncrement"])
        check = by_name(notes["constraints"])["notes_archived_flag"]
        self.assertEqual((check["columns"], check["expression"]), (["archived"], "archived IN (0, 1)"))

    def test_missing_files_are_not_created(self):
        missing = self.dir / "missing.db"
        with self.assertRaises(IntrospectionError):
            introspect(DatabaseRequest(url=f"sqlite:///{missing}"))
        self.assertFalse(missing.exists())
        with self.assertRaisesRegex(IntrospectionError, "existing database file"):
            introspect(DatabaseRequest(url="sqlite://"))

    def test_the_connection_cannot_write(self):
        engine = create_engine(f"sqlite:///{self.path}", 5)
        try:
            with read_only(engine, 5) as connection:
                with self.assertRaisesRegex(Exception, "not a catalog read"):
                    connection.exec_driver_sql("DELETE FROM notes")
                # Past the statement allowlist, SQLite itself refuses: the file was opened read-only.
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly|read-only"):
                    connection.connection.driver_connection.execute("CREATE TABLE pwned (x)")
        finally:
            engine.dispose()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)


@unittest.skipUnless(HAS_SQLALCHEMY, "SQLAlchemy is not installed")
class Separation(unittest.TestCase):
    def test_the_worker_never_imports_project_code(self):
        project = Path(tempfile.mkdtemp(prefix="lsv-project-")).resolve()
        marker = project / "imported"
        hostile = f"open({str(marker)!r}, 'w').write('imported')\n"
        # Shadow the modules the worker needs, and offer the usual hooks, from the project and PYTHONPATH.
        for name in ("sqlalchemy.py", "sitecustomize.py", "usercustomize.py", "laranex_schema_viewer.py"):
            (project / name).write_text(hostile)
        database = project / "app.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY);")
        cwd = os.getcwd()
        os.chdir(project)
        try:
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(project), "PYTHONSTARTUP": str(project / "x.py")}):
                doc = introspect(DatabaseRequest(url=f"sqlite:///{database}"), project_roots=[str(project)])
        finally:
            os.chdir(cwd)
        self.assertEqual(list(tables(doc)), ["t"])
        self.assertFalse(marker.exists())

    def test_modules_loaded_from_the_project_are_detected(self):
        self.assertIn(__name__, loaded_from([str(ROOT / "tests")]))
        self.assertNotIn("sqlalchemy", loaded_from([str(ROOT / "tests")]))

    def test_urls_and_passwords_are_scrubbed(self):
        url = "postgresql+psycopg://reader:s3cr3t-pw@db.internal/app"
        message = scrub(f"failed for {url}: password s3cr3t-pw rejected", url)
        self.assertNotIn("s3cr3t-pw", message)
        self.assertIn("<database url>", message)

    def test_only_catalog_statements_pass(self):
        for allowed in ("SELECT 1", "  select c.relname from pg_class c", "SHOW transaction_read_only",
                        "SET SESSION max_execution_time = 30000", "PRAGMA index_list(\"notes\")",
                        "PRAGMA query_only = 1", "WITH x AS (SELECT 1) SELECT * FROM x"):
            self.assertTrue(STATEMENT.match(allowed), allowed)
        for refused in ("INSERT INTO t VALUES (1)", "CREATE TABLE t (x int)", "DROP TABLE t", "UPDATE t SET x = 1",
                        "DELETE FROM t", "PRAGMA user_version = 5", "PRAGMA query_only = 0", "VACUUM",
                        "ALTER TABLE t ADD y int", "GRANT ALL ON t TO u", "COPY t FROM '/tmp/x'", "CALL p()",
                        "SET GLOBAL read_only = 0", "TRUNCATE t", "PRAGMA journal_mode = WAL"):
            self.assertFalse(STATEMENT.match(refused), refused)

    def test_unsupported_backends_and_drivers_fail_clearly(self):
        with self.assertRaisesRegex(IntrospectionError, "not supported"):
            introspect(DatabaseRequest(url="oracle://u:p@h/db"))
        with self.assertRaisesRegex(IntrospectionError, "not a valid"):
            introspect(DatabaseRequest(url="not a url"))


class ServerCase(unittest.TestCase):
    """A disposable database and roles on a server whose admin URL is in the environment."""

    variable = ""
    dialect = ""

    @classmethod
    def setUpClass(cls):
        admin = os.environ.get(cls.variable)
        if not admin or not HAS_SQLALCHEMY:
            raise unittest.SkipTest(f"set {cls.variable} to run these tests (see this module's docstring)")
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy.engine import make_url

        cls.admin_url = make_url(admin)
        cls.suffix = secrets.token_hex(4)
        cls.database = f"lsv_it_{cls.suffix}"
        cls.passwords = {role: secrets.token_hex(12) for role in ("reader", "writer")}
        cls.roles = {role: f"lsv_{role}_{cls.suffix}" for role in ("reader", "writer")}
        cls.admin = sa_create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        try:
            cls.create()
        except Exception:
            cls.drop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.drop()
        cls.admin.dispose()

    @classmethod
    def url(cls, role=None):
        url = cls.admin_url.set(database=cls.database)
        if role:
            url = url.set(username=cls.roles[role], password=cls.passwords[role])
        return url.render_as_string(hide_password=False)

    def inspect(self, role="reader", **kw):
        return introspect(DatabaseRequest(url=self.url(role), **kw), project_roots=[str(ROOT)])

    def test_admin_roles_are_refused_without_consent(self):
        with self.assertRaisesRegex(IntrospectionError, "can change the schema or data") as raised:
            self.inspect(role=None)
        self.assertNotIn(self.admin_url.password, str(raised.exception))

    def test_write_privileges_are_refused_then_reported_when_accepted(self):
        with self.assertRaisesRegex(IntrospectionError, "can change the schema or data") as raised:
            self.inspect("writer", **self.scope())
        self.assertNotIn(self.passwords["writer"], str(raised.exception))
        doc = self.inspect("writer", allow_write_privileges=True, **self.scope())
        self.assertIn("write_privileges", [d["kind"] for d in doc["diagnostics"]])

    def test_the_read_only_role_needs_no_consent(self):
        doc = self.inspect(**self.scope())
        self.assertEqual(validate_document(doc, strict=True)[1], [])
        self.assertNotIn("write_privileges", [d["kind"] for d in doc["diagnostics"]])

    def test_sessions_are_read_only_even_for_an_admin(self):
        engine = create_engine(self.url(), 5)
        try:
            with read_only(engine, 5) as connection:
                cursor = connection.connection.driver_connection.cursor()
                with self.assertRaisesRegex(Exception, "(?i)read.only"):
                    # Straight to the driver, past the statement allowlist: the database refuses.
                    cursor.execute("CREATE TABLE pwned (x int)")
        finally:
            engine.dispose()
        self.assertNotIn("pwned", tables(self.inspect(**self.scope())))

    def test_wrong_passwords_fail_without_leaking(self):
        url = self.admin_url.set(database=self.database, username=self.roles["reader"], password="wrong-pw-123")
        with self.assertRaises(IntrospectionError) as raised:
            introspect(DatabaseRequest(url=url.render_as_string(hide_password=False)))
        self.assertNotIn("wrong-pw-123", str(raised.exception))

    def scope(self):
        return {}


class PostgreSQLIntrospection(ServerCase):
    variable = "LSV_TEST_POSTGRES_URL"

    @classmethod
    def create(cls):
        from sqlalchemy import create_engine as sa_create_engine

        with cls.admin.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{cls.database}"')
            for role in ("reader", "writer"):
                c.exec_driver_sql(f'CREATE ROLE "{cls.roles[role]}" LOGIN PASSWORD \'{cls.passwords[role]}\'')
        engine = sa_create_engine(cls.admin_url.set(database=cls.database))
        with engine.begin() as c:
            c.exec_driver_sql((DATABASES / "postgresql.sql").read_text())
            for role in ("reader", "writer"):
                c.exec_driver_sql(f'GRANT USAGE ON SCHEMA public, billing TO "{cls.roles[role]}"')
            c.exec_driver_sql(f'GRANT INSERT ON billing.invoices TO "{cls.roles["writer"]}"')
        engine.dispose()

    @classmethod
    def drop(cls):
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            for role in cls.roles.values():
                c.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')

    def scope(self):
        return {"schemas": ("public", "billing")}

    @classmethod
    def document(cls):
        if not hasattr(cls, "_doc"):
            cls._doc = introspect(DatabaseRequest(url=cls.url("reader"), schemas=("public", "billing")))
        return cls._doc

    def test_schemas_are_namespaced(self):
        found = tables(self.document())
        self.assertEqual(found["accounts"]["id"], '["default",null,"accounts"]')
        self.assertEqual(found["invoices"]["id"], '["default","billing","invoices"]')
        self.assertEqual((found["accounts"]["schema"], found["invoices"]["schema"]), ("public", "billing"))
        coverage = self.document()["connections"][0]["coverage"]["database"]
        self.assertEqual((coverage["schemas"], coverage["complete"]), (["public", "billing"], True))
        self.assertEqual(sorted(tables(self.inspect())), ["accounts"])
        with self.assertRaisesRegex(IntrospectionError, "does not exist"):
            self.inspect(schemas=("missing",))

    def test_composite_and_cross_schema_foreign_keys(self):
        found = tables(self.document())
        self.assertEqual(found["invoice_lines"]["primaryKey"], ["invoice_id", "line_no"])
        line = found["line_notes"]["foreignKeys"][0]
        self.assertEqual((line["columns"], line["foreignTable"], line["foreignColumns"]),
                         (["invoice_id", "line_no"], '["default","billing","invoice_lines"]', ["invoice_id", "line_no"]))
        self.assertEqual((line["onUpdate"], line["deferrable"], line["initially"]), ("CASCADE", True, "DEFERRED"))
        invoice = found["invoices"]["foreignKeys"][0]
        self.assertEqual((invoice["foreignTable"], invoice["onDelete"]), ('["default",null,"accounts"]', "CASCADE"))

    def test_index_features(self):
        indexes = by_name(tables(self.document())["accounts"]["indexes"])
        self.assertEqual(indexes["accounts_pkey"]["backs"], {"kind": "primary_key", "constraint": None})
        self.assertEqual(indexes["accounts_email_key"]["backs"]["kind"], "unique_constraint")
        created = indexes["accounts_created_at_idx"]
        self.assertEqual((created["parts"][0]["order"], created["parts"][0]["nulls"], created["included"]),
                         ("desc", "last", ["status"]))
        self.assertEqual(indexes["accounts_live_email_idx"]["predicate"], "(deleted_at IS NULL)")
        self.assertEqual(indexes["accounts_lower_email_idx"]["parts"][0]["expression"], "lower(email)")
        mixed = indexes["accounts_status_created_idx"]["parts"]
        self.assertEqual((mixed[0]["column"], mixed[1]["column"]), ("status", None))
        pattern = indexes["accounts_email_pattern_idx"]["parts"][0]
        self.assertEqual((pattern["collation"], pattern["opclass"]), ("C", "text_pattern_ops"))
        self.assertEqual((indexes["accounts_data_idx"]["method"], indexes["accounts_data_idx"]["parts"][0]["opclass"]),
                         ("gin", "jsonb_path_ops"))
        self.assertEqual(indexes["accounts_external_ref_key"]["dialectOptions"],
                         {"postgresql": {"options": {"nullsNotDistinct": True}}})

    def test_columns(self):
        columns = by_name(tables(self.document())["accounts"]["columns"])
        self.assertEqual(columns["id"]["default"]["kind"], "identity")
        self.assertEqual(columns["status"]["enum"], {"name": "account_status", "values": ["trial", "active", "closed"]})
        self.assertEqual(columns["search"]["generated"]["kind"], "stored")
        self.assertEqual((columns["created_at"]["type"], columns["created_at"]["typeName"]),
                         ("timestamp with time zone", "timestamptz"))
        self.assertEqual(columns["email"]["comment"], "Login address")
        self.assertEqual(tables(self.document())["accounts"]["comment"], "Customer accounts")
        serial = by_name(tables(self.document())["invoices"]["columns"])["id"]
        self.assertEqual((serial["default"]["kind"], serial["autoIncrement"]), ("sequence", True))


class MySQLFamily(ServerCase):
    functional = True

    @classmethod
    def create(cls):
        from sqlalchemy import create_engine as sa_create_engine

        with cls.admin.connect() as c:
            c.exec_driver_sql(f"CREATE DATABASE `{cls.database}`")
            for role in ("reader", "writer"):
                c.exec_driver_sql(f"CREATE USER '{cls.roles[role]}'@'%%' IDENTIFIED BY '{cls.passwords[role]}'")
            c.exec_driver_sql(f"GRANT SELECT ON `{cls.database}`.* TO '{cls.roles['reader']}'@'%%'")
            c.exec_driver_sql(f"GRANT SELECT, INSERT ON `{cls.database}`.* TO '{cls.roles['writer']}'@'%%'")
        engine = sa_create_engine(cls.admin_url.set(database=cls.database))
        with engine.begin() as c:
            files = ["mysql.sql", "mysql-functional.sql"] if cls.functional else ["mysql.sql"]
            for name in files:
                for statement in statements(DATABASES / name):
                    c.exec_driver_sql(statement.replace("%", "%%"))
        engine.dispose()

    @classmethod
    def drop(cls):
        with cls.admin.connect() as c:
            c.exec_driver_sql(f"DROP DATABASE IF EXISTS `{cls.database}`")
            for role in cls.roles.values():
                c.exec_driver_sql(f"DROP USER IF EXISTS '{role}'@'%%'")

    def test_keys_indexes_and_columns(self):
        doc = self.inspect()
        found = tables(doc)
        self.assertEqual(found["order_lines"]["primaryKey"], ["account_id", "order_number", "line_no"])
        fk = by_name(found["order_lines"]["foreignKeys"])["order_lines_order_fk"]
        self.assertEqual((fk["columns"], fk["foreignColumns"], fk["onUpdate"]),
                         (["account_id", "order_number"], ["account_id", "number"], "CASCADE"))
        indexes = by_name(found["products"]["indexes"])
        self.assertEqual(indexes["PRIMARY"]["backs"]["kind"], "primary_key")
        self.assertEqual(indexes["products_name_prefix"]["parts"][0]["length"], 10)
        self.assertEqual(indexes["products_status_created"]["parts"][1]["order"], "desc")
        self.assertEqual(indexes["products_description_fulltext"]["method"], "fulltext")
        self.assertEqual(indexes["products_location_spatial"]["parts"][0]["length"], None)
        columns = by_name(found["products"]["columns"])
        self.assertEqual(columns["status"]["enum"], {"name": None, "values": ["draft", "live"]})
        self.assertEqual(columns["name"]["collation"], "utf8mb4_bin")
        self.assertEqual(found["accounts"]["comment"], "Customer accounts")
        self.assertEqual(by_name(found["products"]["constraints"])["products_price_positive"]["columns"], ["price"])
        features = doc["connections"][0]["coverage"]["database"]["features"]
        self.assertEqual(features["indexExpressions"], "complete" if self.functional else "not_applicable")
        if self.functional:
            self.assertIn("'$.color'", indexes["products_color"]["parts"][0]["expression"])
            self.assertEqual(indexes["products_price"]["dialectOptions"], {"mysql": {"options": {"visible": False}}})


class MySQLIntrospection(MySQLFamily):
    variable = "LSV_TEST_MYSQL_URL"


class MariaDBIntrospection(MySQLFamily):
    variable = "LSV_TEST_MARIADB_URL"
    functional = False


del ServerCase, MySQLFamily

if __name__ == "__main__":
    unittest.main(argv=sys.argv)
