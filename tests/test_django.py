import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer.build import build_document
from laranex_schema_viewer.snapshot import snapshot_bytes

SETTINGS = "tests.fixtures.django_settings"
HAS_DJANGO = importlib.util.find_spec("django") is not None


def has_composite_primary_key():
    if not HAS_DJANGO:
        return False
    from django.db import models

    return hasattr(models, "CompositePrimaryKey")


def build(*modules):
    return build_document(list(modules), project_root=Path.cwd(), orm="django", settings=SETTINGS)


@unittest.skipUnless(HAS_DJANGO, "Django is not installed")
class DjangoExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build()
        cls.tables = {t.name: t for t in cls.doc.tables}
        cls.models = {m.class_name.rsplit(".", 1)[1]: m for m in cls.doc.models}
        cls.relations = {(cls.models_by_id()[r.model], r.name): r for r in cls.doc.relationships}

    @classmethod
    def models_by_id(cls):
        return {m.id: m.class_name.rsplit(".", 1)[1] for m in cls.doc.models}

    def test_tables_columns_and_comments(self):
        author = self.tables["django_app_author"]
        self.assertEqual(author.comment, "People who write books")
        self.assertEqual(author.primary_key, ["id"])
        columns = {c.name: c for c in author.columns}
        self.assertEqual(columns["name"].type, "varchar(100)")
        self.assertEqual(columns["name"].comment, "Display name")
        self.assertTrue(columns["id"].autoincrement)
        self.assertEqual(columns["active"].default.model_dump(), {"kind": "scalar", "value": "True"})
        self.assertEqual(columns["token"].default.kind, "callable")
        self.assertTrue(columns["token"].default.value.endswith("never_called"))

    def test_foreign_keys_stay_separate_from_orm_relationships(self):
        book = self.tables["django_app_book"]
        self.assertEqual([(f.columns, f.target_columns) for f in book.foreign_keys],
                         [(["author_id"], ["id"])])
        editor = self.relations[("Book", "editor")]
        self.assertEqual((editor.direction, editor.back_populates), ("MANYTOONE", None))
        author = self.relations[("Book", "author")]
        self.assertEqual(author.back_populates, "books")
        self.assertEqual(author.primary_join, "django_app_book.author_id = django_app_author.id")
        books = self.relations[("Author", "books")]
        self.assertEqual((books.direction, books.uselist, books.back_populates),
                         ("ONETOMANY", True, "author"))
        self.assertNotIn(("Author", "editor"), self.relations)

    def test_one_to_one_and_many_to_many(self):
        profile = self.relations[("Author", "profile")]
        self.assertEqual((profile.direction, profile.uselist), ("ONETOMANY", False))
        self.assertIn("author_id", [c.columns[0] for c in self.tables["django_app_profile"].constraints])
        tags = self.relations[("Book", "tags")]
        through = self.tables["django_app_book_tags"]
        self.assertEqual((tags.direction, tags.secondary), ("MANYTOMANY", through.id))
        self.assertEqual(tags.primary_join, "django_app_book.id = django_app_book_tags.book_id")
        self.assertEqual(tags.secondary_join, "django_app_tag.id = django_app_book_tags.tag_id")
        self.assertEqual(len(through.foreign_keys), 2)
        self.assertNotIn("Book_tags", self.models)
        readers = self.relations[("Author", "read_books")]
        self.assertEqual(readers.secondary, self.tables["django_app_reading"].id)
        self.assertEqual(readers.back_populates, "readers")

    def test_inheritance_proxy_and_unmanaged_models(self):
        restaurant = self.tables["django_app_restaurant"]
        self.assertEqual(restaurant.primary_key, ["place_ptr_id"])
        self.assertEqual(restaurant.foreign_keys[0].target_table, self.tables["django_app_place"].id)
        self.assertEqual(sorted(self.models["Restaurant"].table_ids),
                         sorted([restaurant.id, self.tables["django_app_place"].id]))
        self.assertEqual(self.models["FeaturedAuthor"].table_ids, [self.tables["django_app_author"].id])
        self.assertEqual(sum(t.name == "django_app_author" for t in self.doc.tables), 1)
        self.assertIn(("unmanaged_model", self.tables["legacy_codes"].id),
                      {(d.kind, d.subject) for d in self.doc.diagnostics})

    def test_indexes_and_constraints(self):
        author = self.tables["django_app_author"]
        indexes = {i.name: i for i in author.indexes}
        self.assertEqual(indexes["author_name_desc"].expressions, ["django_app_author.name DESC"])
        self.assertEqual(indexes["author_lower_name"].expressions, ['LOWER("name")'])
        self.assertIn("active", indexes["author_active_email"].predicates["sqlite"])
        self.assertTrue(indexes["author_active_name"].unique)
        constraints = {c.name: c for c in author.constraints}
        self.assertEqual(constraints["author_name_email"].columns, ["name", "email"])
        self.assertIn("name", constraints["author_name_present"].expression)
        self.assertIn(["email"], [c.columns for c in author.constraints if c.name is None])
        self.assertIn(["label"], [i.columns for i in self.tables["django_app_tag"].indexes])
        self.assertIn(["book_id", "reader_id"], [c.columns for c in self.tables["django_app_reading"].constraints])

    @unittest.skipUnless(has_composite_primary_key(), "Django < 5.2 has no composite primary keys")
    def test_composite_primary_key(self):
        line = self.tables["django_app_orderline"]
        self.assertEqual(line.primary_key, ["order_id", "line_no"])
        self.assertEqual(self.models["OrderLine"].primary_key, ["order_id", "line_no"])
        self.assertNotIn("pk", [c.name for c in line.columns])

    def test_no_connection_and_deterministic_output(self):
        from django.db import connection

        self.assertIsNone(connection.connection)
        self.assertEqual(snapshot_bytes(build()), snapshot_bytes(self.doc))

    def test_module_filter_includes_related_models(self):
        self.assertEqual(snapshot_bytes(build("tests.fixtures.django_app")), snapshot_bytes(self.doc))
        with self.assertRaisesRegex(ValueError, "No Django models"):
            build("missing_app")
        with self.assertRaisesRegex(ValueError, "--target"):
            build_document([], project_root=Path.cwd(), orm="django", settings=SETTINGS,
                           targets=["x:y"])


@unittest.skipUnless(HAS_DJANGO, "Django is not installed")
class DjangoCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.pathsep.join(filter(None, [str(Path("src").resolve()), str(Path.cwd()),
                                                             os.environ.get("PYTHONPATH")])))
        env.pop("DJANGO_SETTINGS_MODULE", None)
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args], cwd=cwd,
                              env=env, capture_output=True, text=True, timeout=60)

    def test_build_detects_settings_from_manage_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manage.py").write_text(
                f'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "{SETTINGS}")\n')
            result = self.run_cli("build", cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Detected Django settings {SETTINGS}", result.stdout)
            self.assertTrue((root / "schema-viewer.json").is_file())
            result = self.run_cli("init", "--yes", cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (root / "pyproject.toml").read_text()
            self.assertIn('orm = "django"', text)
            self.assertIn(f'settings = "{SETTINGS}"', text)

    def test_django_without_settings_explains_the_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("build", "--orm", "django", cwd=tmp)
            self.assertEqual(result.returncode, 1)
            self.assertIn("--settings", result.stderr)
