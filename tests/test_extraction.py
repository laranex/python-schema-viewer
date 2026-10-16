import tempfile
import unittest
from pathlib import Path

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, Table, Time
from sqlalchemy.orm import registry, relationship

from laranex_schema_viewer.adapters.sqlalchemy import SQLAlchemyAdapter
from laranex_schema_viewer.build import build_document
from laranex_schema_viewer.snapshot import snapshot_bytes
from tests.fixtures import sqlalchemy_models as fixture


def example_document():
    return SQLAlchemyAdapter({"default": fixture.Base.metadata},
                             [fixture.User, fixture.Team, fixture.Audit], Path.cwd()).extract()


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.doc = example_document()
        self.tables = {t.name: t for t in self.doc.tables}

    def test_metadata_includes_unmapped_tables(self):
        self.assertEqual(len(self.doc.tables), 6)
        self.assertEqual(len(self.doc.models), 3)
        self.assertIn("parent", self.tables)

    def test_fk_pairs_keep_order_and_actions(self):
        fk = self.tables["child"].foreign_keys[0]
        self.assertEqual(fk.columns, ["a", "b"])
        self.assertEqual(fk.target_columns, ["a", "b"])
        self.assertEqual(fk.target_table, self.tables["parent"].id)
        self.assertEqual(self.tables["users"].foreign_keys[0].on_delete, "SET NULL")

    def test_indexes_preserve_postgres_details(self):
        indexes = {i.name: i for i in self.tables["users"].indexes}
        self.assertEqual(indexes["ix_users_active"].predicates, {"postgresql": "active = true"})
        self.assertEqual(indexes["ix_users_active"].included_columns, {"postgresql": ["name"]})
        self.assertIn("lower(email)", indexes["ix_users_email_lower"].expressions)

    def test_callable_defaults_are_not_executed(self):
        columns = {c.name: c for c in self.tables["users"].columns}
        self.assertEqual(columns["name"].default.kind, "callable")
        self.assertTrue(columns["name"].default.value.endswith("never_call"))
        self.assertEqual(columns["active"].server_default.value, "1")

    def test_relationships_remain_separate(self):
        self.assertEqual(len(self.doc.relationships), 5)
        audit = next(r for r in self.doc.relationships if r.name == "user")
        self.assertTrue(audit.viewonly)
        self.assertEqual(audit.direction, "MANYTOONE")
        self.assertEqual(self.tables["audits"].foreign_keys, [])
        teams = next(r for r in self.doc.relationships if r.name == "teams")
        self.assertEqual(teams.secondary, self.tables["memberships"].id)
        self.assertEqual(teams.direction, "MANYTOMANY")

    def test_self_relationship_and_sources(self):
        manager = next(r for r in self.doc.relationships if r.name == "manager")
        self.assertEqual(manager.source_table, manager.target_table)
        self.assertEqual(manager.back_populates, "reports")
        model = next(m for m in self.doc.models if m.class_name.endswith(".User"))
        self.assertEqual(model.source.file, "tests/fixtures/sqlalchemy_models.py")

    def test_deterministic_output(self):
        self.assertEqual(snapshot_bytes(self.doc), snapshot_bytes(example_document()))

    def test_schema_and_namespace_identity(self):
        first, second = MetaData(), MetaData()
        Table("items", first, Column("id", Integer, primary_key=True))
        Table("items", first, Column("id", Integer, primary_key=True), schema="public")
        Table("items", second, Column("id", Integer, primary_key=True))
        doc = SQLAlchemyAdapter({"one": first, "two": second}).extract()
        self.assertEqual(len({t.id for t in doc.tables}), 3)

    def test_native_postgres_types_and_timezone_are_preserved(self):
        from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

        metadata = MetaData()
        Table("typed", metadata,
              Column("created", DateTime(timezone=True)),
              Column("clock", Time(timezone=False)),
              Column("payload", JSONB), Column("identifier", UUID),
              Column("values", ARRAY(Integer)))
        doc = SQLAlchemyAdapter({"default": metadata}).extract()
        columns = {column.name: column for column in doc.tables[0].columns}
        self.assertTrue(columns["created"].timezone)
        self.assertFalse(columns["clock"].timezone)
        self.assertEqual(columns["payload"].type, "JSONB")
        self.assertEqual(columns["identifier"].type, "UUID")
        self.assertIsNone(columns["payload"].timezone)

    def test_missing_fk_target_is_reported(self):
        metadata = MetaData()
        Table("child", metadata, Column("id", Integer, ForeignKey("absent.id")))
        doc = SQLAlchemyAdapter({"default": metadata}).extract()
        self.assertEqual(doc.diagnostics[0].kind, "unresolved_foreign_key")
        self.assertEqual(doc.tables[0].foreign_keys[0].target_columns, ["id"])

    def test_build_module_and_explicit_target(self):
        doc = build_document(["tests.fixtures.sqlalchemy_models"], project_root=Path.cwd())
        self.assertEqual(len(doc.tables), 6)
        doc = build_document([], project_root=Path.cwd(),
                             targets=["tests.fixtures.sqlalchemy_models:Base"])
        self.assertEqual(len(doc.relationships), 5)

    def test_recursive_build_and_import_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "isolated_fixture"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "model.py").write_text(
                'from sqlalchemy import MetaData, Table, Column, Integer\n'
                'metadata = MetaData()\nTable("loaded", metadata, Column("id", Integer))\n'
            )
            doc = build_document(["isolated_fixture"], project_root=root, recursive=True)
            self.assertEqual([t.name for t in doc.tables], ["loaded"])
            self.assertFalse(list(package.glob("__pycache__")))
            (package / "broken.py").write_text('raise RuntimeError("broken model")')
            with self.assertRaisesRegex(RuntimeError, "broken model"):
                build_document(["isolated_fixture"], project_root=root, recursive=True)

    def test_no_metadata_errors(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            build_document([], project_root=Path.cwd())
        with self.assertRaisesRegex(ValueError, "No SQLAlchemy"):
            build_document(["json"], project_root=Path.cwd())

    def test_imperative_mapping_with_multiple_foreign_key_paths(self):
        mappings = registry()
        person = Table("people", mappings.metadata, Column("id", Integer, primary_key=True))
        note = Table("notes", mappings.metadata, Column("id", Integer, primary_key=True),
                     Column("author_id", ForeignKey("people.id")),
                     Column("editor_id", ForeignKey("people.id")))

        class Person:
            pass

        class Note:
            pass

        mappings.map_imperatively(Person, person)
        mappings.map_imperatively(Note, note, properties={
            "author": relationship(Person, foreign_keys=[note.c.author_id]),
            "editor": relationship(Person, foreign_keys=[note.c.editor_id]),
        })
        doc = SQLAlchemyAdapter({"default": mappings.metadata}, [Note]).extract()
        self.assertEqual(len(doc.models), 2)
        self.assertEqual({r.join_pairs[0].local.column for r in doc.relationships},
                         {"author_id", "editor_id"})

    def test_mapper_configuration_failure_is_explicit(self):
        mappings = registry()
        table = Table("broken", mappings.metadata, Column("id", Integer, primary_key=True))

        class Broken:
            pass

        mappings.map_imperatively(Broken, table, properties={"missing": relationship("Missing")})
        with self.assertRaisesRegex(ValueError, "mapper configuration failed"):
            SQLAlchemyAdapter({"default": mappings.metadata}, [Broken]).extract()
        mappings.dispose()

    def test_sqlmodel_relationships(self):
        from tests.fixtures.sqlmodel_models import FixtureBase, Hero, Squad

        doc = SQLAlchemyAdapter({"default": FixtureBase.metadata}, [Hero, Squad]).extract()
        self.assertEqual(len(doc.tables), 2)
        self.assertEqual(len(doc.models), 2)
        self.assertEqual(len(doc.relationships), 2)
        self.assertEqual(sum(len(t.foreign_keys) for t in doc.tables), 1)
        self.assertEqual({r.direction for r in doc.relationships}, {"MANYTOONE", "ONETOMANY"})

