"""Comparisons of real catalogs: PostgreSQL tenant schemas, and PostgreSQL against MySQL and MariaDB.

Each class creates a disposable database with a unique name and a read-only role, inspects it through the
introspection workers (one per connection, the URL on stdin), compares what was read, and drops everything. Needs the
admin URLs described in tests/test_introspection.py; skipped otherwise. Schemas are synthetic.
"""

import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer.compare import compute
from laranex_schema_viewer.config import comparison_option
from laranex_schema_viewer.contract import document_to_json, validate_document
from laranex_schema_viewer.contract.comparison import comparison_diagnostics, store_comparisons

ROOT = Path(__file__).resolve().parents[1]
HAS_SQLALCHEMY = importlib.util.find_spec("sqlalchemy") is not None
TENANTS = ("tenant_a", "tenant_b", "tenant_c")


def template(schema: str | None = None, *, drift: bool = False, partial: bool = True) -> list[str]:
    """The application's PostgreSQL tables; drift plants every kind of difference a tenant can have."""
    q = f'"{schema}".' if schema else ""
    statements = [
        f"CREATE TABLE {q}users (id bigserial PRIMARY KEY, email varchar(255) NOT NULL, "
        f"name text{' NOT NULL' if drift else ''}, created_at timestamptz NOT NULL DEFAULT now()"
        f"{'' if drift else ', CONSTRAINT users_email_key UNIQUE (email)'})",
        f"CREATE INDEX users_lower_email_idx ON {q}users (lower(email))",
        f"CREATE TABLE {q}orders (id bigserial PRIMARY KEY, "
        f"user_id bigint NOT NULL REFERENCES {q}users (id) ON DELETE {'RESTRICT' if drift else 'CASCADE'}, "
        f"total numeric({'12' if drift else '10'},2) NOT NULL DEFAULT 0, "
        f"status varchar(20) NOT NULL DEFAULT '{'pending' if drift else 'new'}', "
        f"CONSTRAINT orders_total_check CHECK (total {'>' if drift else '>='} 0))",
        f"CREATE INDEX orders_user_id_idx ON {q}orders (user_id)",
        f"CREATE INDEX orders_status_idx ON {q}orders (status)" if drift else
        f"CREATE INDEX orders_open_idx ON {q}orders (user_id) WHERE status <> 'closed'" if partial else None,
        f"CREATE TABLE {q}coupons (code varchar(40) PRIMARY KEY)" if drift else
        f"CREATE TABLE {q}audit_log (id bigserial PRIMARY KEY, message text NOT NULL)",
    ]
    return [s for s in statements if s]


def mysql_template(functional: bool) -> list[str]:
    """The same application on MySQL or MariaDB, as its DDL would be written there."""
    return [
        "CREATE TABLE users (id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY, email varchar(255) NOT NULL, name text, "
        "created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, CONSTRAINT users_email_key UNIQUE (email))",
        *(["CREATE INDEX users_lower_email_idx ON users ((lower(email)))"] if functional else []),
        "CREATE TABLE orders (id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id bigint NOT NULL, "
        "total decimal(10,2) NOT NULL DEFAULT 0, status varchar(20) NOT NULL DEFAULT 'new', "
        "INDEX orders_user_id_idx (user_id), CONSTRAINT orders_total_check CHECK (total >= 0), "
        "CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)",
        "CREATE TABLE audit_log (id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY, message text NOT NULL)",
    ]


def found(comparison):
    return {(d.object_type, d.change, d.key): d for d in comparison.differences}


def spec(text: str):
    return comparison_option(text)


class Disposable:
    """A database and a read-only role with unique names on each admin URL, dropped when the class ends."""

    @classmethod
    def create(cls, admins):
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        cls.name = f"lsv_compare_{secrets.token_hex(4)}"
        cls.role, cls.password = f"lsv_compare_reader_{secrets.token_hex(4)}", secrets.token_hex(12)
        cls.admins = {key: (url, create_engine(make_url(url), isolation_level="AUTOCOMMIT"))
                      for key, url in admins.items()}
        cls.urls = {}
        for key, (url, admin) in cls.admins.items():
            with admin.connect() as c:
                if admin.dialect.name == "postgresql":
                    c.exec_driver_sql(f'CREATE DATABASE "{cls.name}"')
                    c.exec_driver_sql(f"CREATE ROLE \"{cls.role}\" LOGIN PASSWORD '{cls.password}'")
                else:
                    c.exec_driver_sql(f"CREATE DATABASE `{cls.name}`")
                    c.exec_driver_sql(f"CREATE USER '{cls.role}'@'%%' IDENTIFIED BY '{cls.password}'")
                    c.exec_driver_sql(f"GRANT SELECT ON `{cls.name}`.* TO '{cls.role}'@'%%'")
            cls.urls[key] = make_url(url).set(database=cls.name, username=cls.role, password=cls.password) \
                .render_as_string(hide_password=False)

    @classmethod
    def execute(cls, key, statements):
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        engine = create_engine(make_url(cls.admins[key][0]).set(database=cls.name))
        try:
            with engine.begin() as c:
                for statement in statements:
                    c.exec_driver_sql(statement)
        finally:
            engine.dispose()

    @classmethod
    def drop(cls):
        for _, admin in getattr(cls, "admins", {}).values():
            with admin.connect() as c:
                if admin.dialect.name == "postgresql":
                    c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{cls.name}" WITH (FORCE)')
                    c.exec_driver_sql(f'DROP ROLE IF EXISTS "{cls.role}"')
                else:
                    c.exec_driver_sql(f"DROP DATABASE IF EXISTS `{cls.name}`")
                    c.exec_driver_sql(f"DROP USER IF EXISTS '{cls.role}'@'%%'")
            admin.dispose()

    @classmethod
    def inspect(cls, requests):
        from laranex_schema_viewer.introspection import DatabaseRequest
        from laranex_schema_viewer.introspection.runner import introspect_all

        return introspect_all([DatabaseRequest(url=cls.urls[key], connection=name, schemas=tuple(schemas))
                               for name, key, schemas in requests])

    @classmethod
    def tearDownClass(cls):
        cls.drop()


class PostgreSQLTenants(Disposable, unittest.TestCase):
    """app on PostgreSQL: public (an unrelated users table), tenant_a, tenant_b identical to it, tenant_c drifted."""

    @classmethod
    def setUpClass(cls):
        url = os.environ.get("LSV_TEST_POSTGRES_URL")
        if not (url and HAS_SQLALCHEMY):
            raise unittest.SkipTest("set LSV_TEST_POSTGRES_URL to run these tests")
        try:
            cls.create({"pg": url})
            statements = ["CREATE TABLE users (id integer PRIMARY KEY, login text)"]
            for tenant in TENANTS:
                statements += [f'CREATE SCHEMA "{tenant}"', *template(tenant, drift=tenant == "tenant_c")]
            statements += [f'GRANT USAGE ON SCHEMA "{s}" TO "{cls.role}"' for s in ("public", *TENANTS)]
            cls.execute("pg", statements)
            cls.document = cls.inspect([("app", "pg", ("public", *TENANTS))])
        except Exception:
            cls.drop()
            raise

    def compare(self, *texts, **options):
        comparisons = compute(self.document, [spec(t) for t in texts])
        return comparisons, tuple(d for c in comparisons for d in comparison_diagnostics(self.document, c))

    def test_an_identical_tenant_has_no_differences(self):
        [comparison], diagnostics = self.compare("app:tenant_a=app:tenant_b")
        self.assertEqual((comparison.differences, comparison.complete, diagnostics), ((), True, ()))
        self.assertEqual((comparison.summary.missing, comparison.summary.extra, comparison.summary.changed), (0, 0, 0))

    def test_drift_is_detected_exactly(self):
        [comparison], _ = self.compare("app:tenant_a=app:tenant_c")
        differences = found(comparison)
        self.assertEqual({k: (d.severity, d.certainty, tuple(f.field for f in d.fields))
                          for k, d in differences.items()}, {
            ("table", "missing", ("audit_log",)): ("error", "certain", ()),
            ("table", "extra", ("coupons",)): ("error", "certain", ()),
            ("column", "changed", ("orders", "status")): ("warning", "likely", ("default",)),
            ("column", "changed", ("orders", "total")): ("warning", "certain", ("type",)),
            ("column", "changed", ("users", "name")): ("warning", "certain", ("nullable",)),
            ("index", "missing", ("orders", "name", "orders_open_idx")): ("warning", "certain", ()),
            ("index", "extra", ("orders", "name", "orders_status_idx")): ("warning", "certain", ()),
            ("foreignKey", "changed", ("orders", "references", "user_id->users(id)")):
                ("warning", "certain", ("onDelete",)),
            ("constraint", "changed", ("orders", "check", "name", "orders_total_check")):
                ("warning", "likely", ("expression",)),
            ("constraint", "missing", ("users", "unique", "name", "users_email_key")): ("warning", "certain", ()),
        })
        total = differences["column", "changed", ("orders", "total")].fields[0]
        self.assertEqual((total.baseline, total.target), ("numeric(10,2)", "numeric(12,2)"))
        self.assertEqual(differences["table", "missing", ("audit_log",)].baseline.label, "tenant_a.audit_log")
        self.assertTrue(comparison.complete)

    def test_reversed_scopes_swap_missing_and_extra(self):
        [forward], _ = self.compare("app:tenant_a=app:tenant_c")
        [backward], _ = self.compare("app:tenant_c=app:tenant_a")
        swap = {"missing": "extra", "extra": "missing", "changed": "changed"}
        self.assertEqual({(k[0], swap[k[1]]) for k in found(forward)}, {(k[0], k[1]) for k in found(backward)})

    def test_tenant_schema_names_are_never_drift(self):
        comparisons, _ = self.compare("app:tenant_a=app:*", "app:tenant_c=app:*")
        self.assertEqual({(c.baseline.schema, c.target.schema) for c in comparisons},
                         {("tenant_a", s) for s in ("public", "tenant_b", "tenant_c")}
                         | {("tenant_c", s) for s in ("public", "tenant_a", "tenant_b")})
        for comparison in comparisons:
            for difference in comparison.differences:
                self.assertFalse(set(TENANTS) & set(difference.key), difference)
        document = store_comparisons(self.document, self.compare("app:tenant_a=app:*")[0])
        self.assertEqual(validate_document(document_to_json(document), strict=True)[1], [])

    def test_the_unqualified_scope_is_the_default_schema(self):
        [comparison], _ = self.compare("app=app:tenant_a")
        differences = found(comparison)
        self.assertIn(("table", "extra", ("orders",)), differences)
        users = differences["column", "missing", ("users", "login")]
        self.assertEqual(users.baseline.label, "public.users.login")


class SchemaNamedTables(Disposable, unittest.TestCase):
    """Schemas billing and archive, each holding tables named like them: build stores their comparison."""

    @classmethod
    def setUpClass(cls):
        url = os.environ.get("LSV_TEST_POSTGRES_URL")
        if not (url and HAS_SQLALCHEMY):
            raise unittest.SkipTest("set LSV_TEST_POSTGRES_URL to run these tests")
        try:
            cls.create({"pg": url})
            cls.execute("pg", [
                'CREATE SCHEMA billing', 'CREATE SCHEMA archive',
                'CREATE TABLE billing.billing (id integer PRIMARY KEY, archive text)',
                'CREATE INDEX archive ON billing.billing (archive)',
                'CREATE TABLE archive.billing (id integer PRIMARY KEY)',
                'CREATE TABLE archive.archive (id integer PRIMARY KEY)',
                f'GRANT USAGE ON SCHEMA billing, archive TO "{cls.role}"'])
        except Exception:
            cls.drop()
            raise

    def test_build_stores_the_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "schema.json"
            (Path(tmp) / "pyproject.toml").write_text(
                "[tool.laranex-schema-viewer.databases.ledger]\n"
                "url_env = 'LEDGER_URL'\nschemas = ['public', 'billing', 'archive']\n")
            result = subprocess.run(
                [sys.executable, "-m", "laranex_schema_viewer", "build", "--source", "database",
                 "--compare", "ledger:billing=ledger:archive",
                 "--output", str(output)],
                cwd=tmp, capture_output=True, text=True, timeout=120,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"),
                         LEDGER_URL=self.urls["pg"]))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(self.password, result.stdout + result.stderr)
            document = json.loads(output.read_text())
        self.assertEqual(validate_document(document, strict=True)[1], [])
        [comparison] = document["comparisons"]
        self.assertEqual({(d["objectType"], tuple(d["key"]), d["change"]) for d in comparison["differences"]},
                         {("table", ("archive",), "extra"), ("column", ("billing", "archive"), "missing"),
                          ("index", ("billing", "name", "archive"), "missing")})


class MixedDrivers(Disposable, unittest.TestCase):
    """The same application on PostgreSQL, MySQL and MariaDB: one connection each, compared across drivers."""

    @classmethod
    def setUpClass(cls):
        urls = {key: os.environ.get(f"LSV_TEST_{key.upper()}_URL") for key in ("postgres", "mysql", "mariadb")}
        if not (all(urls.values()) and HAS_SQLALCHEMY):
            raise unittest.SkipTest("set LSV_TEST_POSTGRES_URL, LSV_TEST_MYSQL_URL and LSV_TEST_MARIADB_URL")
        try:
            cls.create(urls)
            cls.execute("postgres", [*template(), f'GRANT USAGE ON SCHEMA public TO "{cls.role}"'])
            cls.execute("mysql", mysql_template(functional=True))
            cls.execute("mariadb", mysql_template(functional=False))
            cls.document = cls.inspect([("primary", "postgres", ()), ("mysql", "mysql", ()),
                                        ("mariadb", "mariadb", ())])
        except Exception:
            cls.drop()
            raise

    def test_drivers(self):
        self.assertEqual({c.name: c.driver for c in self.document.connections},
                         {"primary": "postgresql", "mysql": "mysql", "mariadb": "mariadb"})

    def expected(self, functional: bool):
        """What differs between the PostgreSQL template and its MySQL or MariaDB version, and how."""
        out = {
            # MySQL's timestamp has no time zone: a real difference, compared after normalization. now() and
            # CURRENT_TIMESTAMP mean the same, but defaults compare as normalized SQL text: a likely false positive.
            ("column", "changed", ("users", "created_at")): ("likely", ("default", "type")),
            # The default 0 is read back as '0.00' (MySQL) or 0.00 (MariaDB): equal values, different text.
            ("column", "changed", ("orders", "total")): ("likely", ("default",)),
            # Partial indexes do not exist on MySQL or MariaDB.
            ("index", "missing", ("orders", "name", "orders_open_idx")): ("certain", ()),
            # MySQL and MariaDB keep a unique constraint as a unique index, so the constraint entity is missing and
            # an index is extra: a difference of representation the shared algorithm reports as such.
            ("constraint", "missing", ("users", "unique", "name", "users_email_key")): ("certain", ()),
            ("index", "extra", ("users", "name", "users_email_key")): ("certain", ()),
        }
        if not functional:  # MariaDB has no functional indexes.
            out["index", "missing", ("users", "name", "users_lower_email_idx")] = ("certain", ())
        return out

    def test_postgresql_against_mysql_and_mariadb(self):
        comparisons = compute(self.document, [spec("primary=mysql"), spec("primary=mariadb")])
        by_target = {c.target.connection: c for c in comparisons}
        for name, functional in (("mysql", True), ("mariadb", False)):
            with self.subTest(name):
                comparison = by_target[f"connection:{name}"]
                self.assertEqual({k: (d.certainty, tuple(f.field for f in d.fields))
                                  for k, d in found(comparison).items()}, self.expected(functional))
                created = found(comparison)["column", "changed", ("users", "created_at")]
                self.assertEqual([f.baseline for f in created.fields][1], "timestamp with time zone")

    def test_mysql_against_mariadb(self):
        [comparison] = compute(self.document, [spec("mysql=mariadb")])
        differences = found(comparison)
        # Different drivers: types compare normalized and equal; CURRENT_TIMESTAMP and current_timestamp() normalize
        # alike. MariaDB lacks the functional index, and reads the decimal default unquoted (likely).
        self.assertEqual(set(differences), {("index", "missing", ("users", "name", "users_lower_email_idx")),
                                            ("column", "changed", ("orders", "total"))})
        total = differences["column", "changed", ("orders", "total")]
        self.assertEqual((total.certainty, [(f.baseline.value, f.target.value) for f in total.fields]),
                         ("likely", [("'0.00'", "0.00")]))
        document = store_comparisons(self.document, compute(self.document, [spec("primary=mysql"), spec("mysql=mariadb")]))
        self.assertEqual(validate_document(document_to_json(document), strict=True)[1], [])


if __name__ == "__main__":
    unittest.main()
