import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer import model as m
from laranex_schema_viewer.build import build_document
from laranex_schema_viewer.contract import document_bytes, document_to_json, validate_document

HAS_TORTOISE = importlib.util.find_spec("tortoise") is not None
MODELS = "tests.fixtures.tortoise_models"


@unittest.skipUnless(HAS_TORTOISE, "Tortoise ORM is not installed")
class TortoiseExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build_document([MODELS], project_root=Path.cwd())
        cls.tables = {t.name: t for t in cls.doc.tables()}
        names = {x.id: x.class_name.rsplit(".", 1)[1] for x in cls.doc.models}
        cls.relations = {(names[r.model], r.name): r for r in cls.doc.relationships}

    def test_detected_without_connecting(self):
        from tortoise import Tortoise

        self.assertFalse(Tortoise._inited)
        explicit = build_document([MODELS], project_root=Path.cwd(), orm="tortoise")
        self.assertEqual(document_bytes(explicit), document_bytes(self.doc))
        self.assertEqual(validate_document(document_to_json(self.doc), strict=True)[1], [])
        self.assertEqual(self.doc.generator.orm, "tortoise")

    def test_tables_columns_and_schema(self):
        authors = self.tables["authors"]
        connection = next(c for c in self.doc.connections if authors in c.tables)
        self.assertEqual((connection.name, authors.schema, authors.comment), ("models", None, "People who write books"))
        columns = {c.name: c for c in authors.columns}
        self.assertEqual([c.name for c in authors.columns],
                         ["id", "name", "email", "active", "token", "joined"])
        self.assertEqual(columns["name"].type, "VARCHAR(100)")
        self.assertEqual(columns["name"].comment, "Display name")
        self.assertTrue(columns["id"].auto_increment)
        self.assertEqual(columns["token"].orm_default.kind, "callable")
        self.assertEqual(self.tables["book"].schema, "library")

    def test_foreign_keys_and_relationships_stay_separate(self):
        book = self.tables["book"]
        self.assertEqual([(f.columns, f.on_delete) for f in book.foreign_keys], [(("author_id",), "SET NULL")])
        editor = self.relations[("Book", "editor")]
        self.assertEqual((editor.type, editor.inverse), ("MANYTOONE", None))
        books = self.relations[("Author", "books")]
        self.assertEqual((books.type, books.collection, books.inverse),
                         ("ONETOMANY", True, self.relations[("Book", "author")].id))
        profile = self.relations[("Author", "profile")]
        self.assertEqual((profile.collection, profile.cardinality), (False, "one-to-one"))

    def test_many_to_many_tables(self):
        book_tag = self.tables["book_tag"]
        self.assertEqual([c.name for c in book_tag.columns], ["book_id", "tag_id"])
        self.assertEqual(sorted(f.on_delete for f in book_tag.foreign_keys), ["CASCADE", "CASCADE"])
        tags = self.relations[("Book", "tags")]
        self.assertEqual(tags.pivot.table, book_tag.id)
        self.assertEqual(tags.join.secondary, "tag.id = book_tag.tag_id")
        reverse = self.relations[("Tag", "books")]
        self.assertEqual(reverse.pivot.table, book_tag.id)
        self.assertEqual(self.relations[("Book", "readers")].pivot.table, self.tables["reading"].id)
        self.assertEqual(sum(t.name == "reading" for t in self.doc.tables()), 1)

    def test_indexes_and_constraints(self):
        authors = self.tables["authors"]
        indexes = {i.name: i for i in authors.indexes}
        self.assertEqual(indexes["author_name_active"].parts, (m.IndexPart(column="name"), m.IndexPart(column="active")))
        self.assertEqual(indexes["author_active_email"].dialect_options,
                         {"tortoise": m.IndexOptions(predicate="active = true")})
        constraints = {(c.kind, c.name): c for c in authors.constraints}
        unnamed = sorted(c.columns for c in authors.constraints if c.kind == "unique" and c.name is None)
        self.assertEqual(unnamed, [("email",), ("name", "email")])
        if ("check", "author_name_present") in constraints:
            self.assertEqual(constraints[("check", "author_name_present")].expression, "length(name) > 0")

    def test_config_target_selects_dialect(self):
        doc = build_document([], project_root=Path.cwd(), orm="tortoise",
                             targets=["tests.fixtures.tortoise_config:TORTOISE_ORM"])
        authors = {t.name: t for t in doc.tables()}["authors"]
        self.assertEqual({c.name: c.type for c in authors.columns}["joined"], "TIMESTAMPTZ")
        # Tortoise's "postgres" dialect under the contract's name.
        self.assertEqual(set({i.name: i for i in authors.indexes}["author_active_email"].dialect_options), {"postgresql"})

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
