import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer.build import build_document
from laranex_schema_viewer.snapshot import snapshot_bytes

HAS_TORTOISE = importlib.util.find_spec("tortoise") is not None
MODELS = "tests.fixtures.tortoise_models"


@unittest.skipUnless(HAS_TORTOISE, "Tortoise ORM is not installed")
class TortoiseExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build_document([MODELS], project_root=Path.cwd())
        cls.tables = {t.name: t for t in cls.doc.tables}
        names = {m.id: m.class_name.rsplit(".", 1)[1] for m in cls.doc.models}
        cls.relations = {(names[r.model], r.name): r for r in cls.doc.relationships}

    def test_detected_without_connecting(self):
        from tortoise import Tortoise

        self.assertFalse(Tortoise._inited)
        explicit = build_document([MODELS], project_root=Path.cwd(), orm="tortoise")
        self.assertEqual(snapshot_bytes(explicit), snapshot_bytes(self.doc))

    def test_tables_columns_and_schema(self):
        authors = self.tables["authors"]
        self.assertEqual((authors.namespace, authors.schema_name, authors.comment),
                         ("models", None, "People who write books"))
        columns = {c.name: c for c in authors.columns}
        self.assertEqual([c.name for c in authors.columns],
                         ["id", "name", "email", "active", "token", "joined"])
        self.assertEqual(columns["name"].type, "VARCHAR(100)")
        self.assertEqual(columns["name"].comment, "Display name")
        self.assertTrue(columns["id"].autoincrement)
        self.assertEqual(columns["token"].default.kind, "callable")
        self.assertEqual(self.tables["book"].schema_name, "library")

    def test_foreign_keys_and_relationships_stay_separate(self):
        book = self.tables["book"]
        self.assertEqual([(f.columns, f.on_delete) for f in book.foreign_keys], [(["author_id"], "SET NULL")])
        editor = self.relations[("Book", "editor")]
        self.assertEqual((editor.direction, editor.back_populates), ("MANYTOONE", None))
        books = self.relations[("Author", "books")]
        self.assertEqual((books.direction, books.uselist, books.back_populates), ("ONETOMANY", True, "author"))
        profile = self.relations[("Author", "profile")]
        self.assertFalse(profile.uselist)

    def test_many_to_many_tables(self):
        book_tag = self.tables["book_tag"]
        self.assertEqual([c.name for c in book_tag.columns], ["book_id", "tag_id"])
        self.assertEqual(sorted(f.on_delete for f in book_tag.foreign_keys), ["CASCADE", "CASCADE"])
        tags = self.relations[("Book", "tags")]
        self.assertEqual(tags.secondary, book_tag.id)
        self.assertEqual(tags.secondary_join, "tag.id = book_tag.tag_id")
        reverse = self.relations[("Tag", "books")]
        self.assertEqual(reverse.secondary, book_tag.id)
        self.assertEqual(self.relations[("Book", "readers")].secondary, self.tables["reading"].id)
        self.assertEqual(sum(t.name == "reading" for t in self.doc.tables), 1)

    def test_indexes_and_constraints(self):
        authors = self.tables["authors"]
        indexes = {i.name: i for i in authors.indexes}
        self.assertEqual(indexes["author_name_active"].columns, ["name", "active"])
        self.assertEqual(indexes["author_active_email"].predicates, {"tortoise": "active = true"})
        constraints = {(c.kind, c.name): c for c in authors.constraints}
        unnamed = sorted(c.columns for c in authors.constraints if c.kind == "unique" and c.name is None)
        self.assertEqual(unnamed, [["email"], ["name", "email"]])
        if ("check", "author_name_present") in constraints:
            self.assertEqual(constraints[("check", "author_name_present")].expression, "length(name) > 0")

    def test_config_target_selects_dialect(self):
        doc = build_document([], project_root=Path.cwd(), orm="tortoise",
                             targets=["tests.fixtures.tortoise_config:TORTOISE_ORM"])
        authors = {t.name: t for t in doc.tables}["authors"]
        self.assertEqual({c.name: c.type for c in authors.columns}["joined"], "TIMESTAMPTZ")
        self.assertIn("postgres", {i.name: i for i in authors.indexes}["author_active_email"].predicates)

    def test_invalid_targets(self):
        with self.assertRaisesRegex(ValueError, "not a Tortoise config"):
            build_document([], project_root=Path.cwd(), orm="tortoise", targets=["os:sep"])


@unittest.skipUnless(HAS_TORTOISE, "Tortoise ORM is not installed")
class TortoiseCliTests(unittest.TestCase):
    def test_cli_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "schema.json"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join(filter(None, [str(Path("src").resolve()),
                                                                os.environ.get("PYTHONPATH")])))
            result = subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "build",
                                     "--module", MODELS, "--output", str(output)],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("6 tables, 5 models", result.stdout)
