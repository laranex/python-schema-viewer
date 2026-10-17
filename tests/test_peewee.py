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

HAS_PEEWEE = importlib.util.find_spec("peewee") is not None
MODELS = "tests.fixtures.peewee_models"


@unittest.skipUnless(HAS_PEEWEE, "Peewee is not installed")
class PeeweeExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build_document([MODELS], project_root=Path.cwd())
        cls.tables = {t.name: t for t in cls.doc.tables()}
        names = {x.id: x.class_name.rsplit(".", 1)[1] for x in cls.doc.models}
        cls.relations = {(names[r.model], r.name): r for r in cls.doc.relationships}
        cls.diagnostics = {d.kind: d for d in cls.doc.diagnostics}

    def test_detected_without_connecting(self):
        from tests.fixtures.peewee_models import database

        self.assertTrue(database.is_closed())
        explicit = build_document([MODELS], project_root=Path.cwd(), orm="peewee")
        self.assertEqual(document_bytes(explicit), document_bytes(self.doc))
        self.assertEqual(validate_document(document_to_json(self.doc), strict=True)[1], [])
        self.assertEqual(self.doc.generator.orm, "peewee")

    def test_tables_columns_and_defaults(self):
        self.assertNotIn("basemodel", self.tables)
        skipped = self.diagnostics["skipped_base_model"].subject
        self.assertEqual((skipped.type, skipped.label), ("document", "tests.fixtures.peewee_models.BaseModel"))
        columns = {c.name: c for c in self.tables["authors"].columns}
        self.assertEqual(columns["name"].type, "VARCHAR(100)")
        self.assertTrue(columns["id"].auto_increment)
        self.assertEqual(columns["joined"].orm_default, m.OrmDefault(kind="callable", value="datetime.now"))
        self.assertEqual(columns["score"].default, m.DefaultValue(kind="literal", value="0"))
        self.assertEqual({c.name: c.type for c in self.tables["unbound"].columns}["code"], "VARCHAR(10)")
        self.assertEqual(self.tables["book"].schema, "library")

    def test_composite_primary_key(self):
        self.assertEqual(self.tables["orderline"].primary_key, ("order", "number"))

    def test_foreign_keys_and_relationships(self):
        keys = {f.columns[0]: f for f in self.tables["book"].foreign_keys}
        self.assertEqual((keys["author_id"].on_delete, keys["author_id"].on_update), ("CASCADE", "NO ACTION"))
        editor = keys["editor_id"]
        self.assertEqual((editor.name, editor.deferrable, editor.initially), ("book_editor_fk", True, "DEFERRED"))
        self.assertEqual(self.relations[("Book", "editor")].inverse, None)
        books = self.relations[("Author", "books")]
        self.assertEqual((books.type, books.collection, books.inverse),
                         ("ONETOMANY", True, self.relations[("Book", "author")].id))
        self.assertFalse(self.relations[("Author", "profile")].collection)

    def test_many_to_many_join_table(self):
        through = self.tables["book_tag_through"]
        self.assertEqual(len(through.foreign_keys), 2)
        self.assertNotIn("BookTagThrough", [x.class_name.rsplit(".", 1)[1] for x in self.doc.models])
        tags, books = self.relations[("Book", "tags")], self.relations[("Tag", "books")]
        self.assertEqual((tags.pivot.table, tags.inverse, books.inverse), (through.id, books.id, tags.id))
        self.assertEqual(tags.join.secondary, "tag.id = book_tag_through.tag_id")

    def test_indexes_and_constraints(self):
        authors = self.tables["authors"]
        indexes = {i.name: i for i in authors.indexes}
        self.assertTrue(indexes["author_email"].unique)
        self.assertEqual(indexes["author_lower_name"].parts, (m.IndexPart(expression='lower("name")'),))
        self.assertEqual(indexes["author_name_email"].columns, ("name", "email"))
        self.assertEqual(indexes["author_top_email"].dialect_options, {"sqlite": m.IndexOptions(predicate='("score" > 10)')})
        self.assertEqual({i.name: i for i in self.tables["book"].indexes}["book_title_desc"].parts,
                         (m.IndexPart(column="title", order="desc"),))
        constraints = {(c.kind, c.name): c for c in authors.constraints}
        self.assertEqual(constraints[("check", "name_present")].expression, "length(name) > 0")
        self.assertEqual(constraints[("check", None)].columns, ("score",))
        self.assertEqual(constraints[("unique", None)].columns, ("email", "name"))
        self.assertIn("author_score_raw", self.diagnostics["unsupported_index"].message)

    def test_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "--target applies only"):
            build_document([MODELS], project_root=Path.cwd(), orm="peewee", targets=["x:y"])


@unittest.skipUnless(HAS_PEEWEE, "Peewee is not installed")
class PeeweeCliTests(unittest.TestCase):
    def test_cli_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "schema.json"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join(filter(None, [str(Path("src").resolve()),
                                                                os.environ.get("PYTHONPATH")])))
            result = subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "build",
                                     "--module", MODELS, "--orm", "peewee", "--output", str(output)],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("7 tables, 6 models", result.stdout)
