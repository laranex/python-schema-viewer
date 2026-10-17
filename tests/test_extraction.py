import datetime
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import Column, Computed, DateTime, ForeignKey, Identity, Index, Integer, MetaData, Table, Time, func, text
from sqlalchemy.orm import registry, relationship

from laranex_schema_viewer import model as m
from laranex_schema_viewer.adapters import referential_action
from laranex_schema_viewer.adapters.sqlalchemy import SQLAlchemyAdapter, server_default
from laranex_schema_viewer.build import build_document
from laranex_schema_viewer.contract import document_bytes, document_to_json, validate_document
from tests.fixtures import sqlalchemy_models as fixture


def example_document():
    return SQLAlchemyAdapter({"default": fixture.Base.metadata},
                             [fixture.User, fixture.Team, fixture.Audit], Path.cwd()).extract()


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.doc = example_document()
        self.tables = {t.name: t for t in self.doc.tables()}

    def test_is_a_strictly_valid_declared_document(self):
        self.assertEqual(validate_document(document_to_json(self.doc), strict=True)[1], [])
        self.assertEqual((self.doc.source, self.doc.generator), ("declared", m.Generator(
            name="python-schema-viewer", orm="sqlalchemy")))
        [connection] = self.doc.connections
        self.assertEqual((connection.name, connection.driver, connection.coverage.database), ("default", None, None))
        self.assertTrue(connection.coverage.declared.complete)

    def test_metadata_includes_unmapped_tables(self):
        self.assertEqual(len(self.doc.tables()), 6)
        self.assertEqual(len(self.doc.models), 3)
        self.assertIn("parent", self.tables)

    def test_fk_pairs_keep_order_and_actions(self):
        fk = self.tables["child"].foreign_keys[0]
        self.assertEqual(fk.columns, ("a", "b"))
        self.assertEqual(fk.foreign_columns, ("a", "b"))
        self.assertEqual(fk.foreign_table, self.tables["parent"].id)
        self.assertEqual(self.tables["users"].foreign_keys[0].on_delete, "SET NULL")
        self.assertEqual(fk.provenance, ("declared",))

    def test_indexes_keep_column_parts_and_dialect_options(self):
        indexes = {i.name: i for i in self.tables["users"].indexes}
        active = indexes["ix_users_active"]
        self.assertEqual((active.parts, active.expression, active.unique),
                         ((m.IndexPart(column="email"),), False, True))
        self.assertEqual(active.dialect_options,
                         {"postgresql": m.IndexOptions(predicate="active = true", included=("name",))})
        self.assertEqual((active.partial, active.predicate), (False, None))  # a PostgreSQL-only predicate
        lower = indexes["ix_users_email_lower"]
        self.assertEqual((lower.parts, lower.expression), ((m.IndexPart(expression="lower(email)"),), True))

    def test_index_parts_keep_order_and_expressions(self):
        metadata = MetaData()
        table = Table("t", metadata, Column("a", Integer), Column("b", Integer))
        Index("ix", table.c.a, table.c.b.desc().nulls_last(), func.lower(text("a")))
        [index] = SQLAlchemyAdapter({"default": metadata}).extract().tables()[0].indexes
        self.assertEqual(index.parts, (m.IndexPart(column="a"), m.IndexPart(column="b", order="desc", nulls="last"),
                                       m.IndexPart(expression="lower(a)")))
        self.assertEqual(index.id, '["default",null,"t"]:index:ix')

    def test_database_and_orm_defaults_stay_apart(self):
        columns = {c.name: c for c in self.tables["users"].columns}
        self.assertEqual(columns["name"].orm_default.kind, "callable")
        self.assertTrue(columns["name"].orm_default.value.endswith("never_call"))
        self.assertEqual(columns["active"].default, m.DefaultValue(kind="literal", value="1"))
        self.assertEqual(columns["active"].orm_default, m.OrmDefault(kind="scalar", value="1"))

    def test_server_defaults_never_invent_sql(self):
        self.assertEqual(server_default(Identity()), (m.DefaultValue(kind="identity", value="Identity"), None))
        self.assertEqual(server_default(Column("x", Integer, server_default=text("now()")).server_default),
                         (m.DefaultValue(kind="expression", value="now()"), None))
        self.assertEqual(server_default(Computed("price * 2")), (None, m.Generated(kind=None, expression="price * 2")))
        self.assertEqual(referential_action("set_null"), ("SET NULL", None))
        self.assertEqual(referential_action("python_protect"), (None, "python_protect"))

    def test_builtin_callable_defaults_are_named_without_a_module(self):
        metadata = MetaData()
        Table("events", metadata, Column("at", DateTime, default=datetime.datetime.now))
        column = SQLAlchemyAdapter({"default": metadata}).extract().tables()[0].columns[0]
        self.assertEqual(column.orm_default.value, "datetime.now")

    def test_relationships_remain_separate(self):
        relationships = {r.name: r for r in self.doc.relationships}
        self.assertEqual(len(relationships), 5)
        audit = relationships["user"]
        self.assertTrue(audit.read_only)
        self.assertEqual((audit.type, audit.cardinality), ("MANYTOONE", "many-to-one"))
        self.assertEqual(self.tables["audits"].foreign_keys, ())
        link = next(x for x in self.doc.links if audit.id in x.relationships)
        self.assertEqual((link.state, link.foreign_key), ("relationship", None))
        teams = relationships["teams"]
        self.assertEqual(teams.pivot.table, self.tables["memberships"].id)
        self.assertEqual((teams.pivot.parent_pivot_columns, teams.pivot.related_pivot_columns),
                         (("user_id",), ("team_id",)))
        self.assertEqual((teams.type, teams.cardinality, teams.keys), ("MANYTOMANY", "many-to-many", None))
        pivot_links = [x for x in self.doc.links if teams.id in x.relationships]
        self.assertEqual(sorted(x.state for x in pivot_links), ["both", "both"])

    def test_keys_run_like_foreign_keys_and_inverses_resolve(self):
        relationships = {r.name: r for r in self.doc.relationships}
        manager, reports = relationships["manager"], relationships["reports"]
        self.assertEqual(manager.table, manager.related_table)
        self.assertEqual(manager.keys, reports.keys)
        self.assertEqual(manager.keys.source.columns, ("manager_id",))
        self.assertEqual((manager.inverse, reports.inverse), (reports.id, manager.id))
        self.assertEqual((manager.cardinality, reports.cardinality), ("many-to-one", "one-to-many"))
        model = next(x for x in self.doc.models if x.class_name.endswith(".User"))
        self.assertEqual(model.source.file, "tests/fixtures/sqlalchemy_models.py")

    def test_deterministic_output(self):
        self.assertEqual(document_bytes(self.doc), document_bytes(example_document()))

    def test_schema_and_namespace_identity(self):
        first, second = MetaData(), MetaData()
        Table("items", first, Column("id", Integer, primary_key=True))
        Table("items", first, Column("id", Integer, primary_key=True), schema="public")
        Table("items", second, Column("id", Integer, primary_key=True))
        doc = SQLAlchemyAdapter({"one": first, "two": second}).extract()
        self.assertEqual(len({t.id for t in doc.tables()}), 3)
        self.assertEqual([c.name for c in doc.connections], ["one", "two"])

    def test_native_postgres_types_and_timezone_are_preserved(self):
        from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

        metadata = MetaData()
        Table("typed", metadata,
              Column("created", DateTime(timezone=True)),
              Column("clock", Time(timezone=False)),
              Column("payload", JSONB), Column("identifier", UUID),
              Column("values", ARRAY(Integer)))
        doc = SQLAlchemyAdapter({"default": metadata}).extract()
        columns = {column.name: column for column in doc.tables()[0].columns}
        self.assertTrue(columns["created"].timezone)
        self.assertFalse(columns["clock"].timezone)
        self.assertEqual(columns["payload"].type, "JSONB")
        self.assertEqual(columns["identifier"].type, "UUID")
        self.assertIsNone(columns["payload"].timezone)

    def test_missing_fk_target_is_reported(self):
        metadata = MetaData()
        Table("child", metadata, Column("id", Integer, ForeignKey("absent.id")),
              Column("other", Integer, ForeignKey("missing.id")))
        doc = SQLAlchemyAdapter({"default": metadata}).extract()
        foreign_keys = doc.tables()[0].foreign_keys
        self.assertEqual(foreign_keys[0].foreign_columns, ("id",))
        # One diagnostic per foreign key, naming it; the document stays valid without the targets.
        self.assertEqual([(d.kind, d.subject.type, d.subject.id) for d in doc.diagnostics],
                         [("unresolved_foreign_key", "foreignKey", fk.id) for fk in foreign_keys])
        self.assertEqual(validate_document(document_to_json(doc), strict=True)[1], [])

    def test_build_module_and_explicit_target(self):
        doc = build_document(["tests.fixtures.sqlalchemy_models"], project_root=Path.cwd())
        self.assertEqual(len(doc.tables()), 6)
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
            self.assertEqual([t.name for t in doc.tables()], ["loaded"])
            self.assertFalse(list(package.glob("__pycache__")))
            (package / "broken.py").write_text('raise RuntimeError("broken model")')
            with self.assertRaisesRegex(RuntimeError, "broken model"):
                build_document(["isolated_fixture"], project_root=root, recursive=True)

    def test_no_metadata_errors(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            build_document([], project_root=Path.cwd())
        with self.assertRaisesRegex(ValueError, "No SQLAlchemy"):
            build_document(["json"], project_root=Path.cwd())

    def test_orm_selection(self):
        explicit = build_document(["tests.fixtures.sqlalchemy_models"], project_root=Path.cwd(),
                                  orm="sqlalchemy")
        detected = build_document(["tests.fixtures.sqlalchemy_models"], project_root=Path.cwd())
        self.assertEqual(document_bytes(explicit), document_bytes(detected))
        with self.assertRaisesRegex(ValueError, "Unsupported ORM"):
            build_document(["tests.fixtures.sqlalchemy_models"], project_root=Path.cwd(), orm="nope")
        with self.assertRaisesRegex(ValueError, "No SQLAlchemy metadata or mapped classes"):
            build_document(["json"], project_root=Path.cwd(), orm="sqlalchemy")

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
        self.assertEqual({r.keys.source.columns for r in doc.relationships}, {("author_id",), ("editor_id",)})

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
        self.assertEqual(len(doc.tables()), 2)
        self.assertEqual(len(doc.models), 2)
        self.assertEqual(len(doc.relationships), 2)
        self.assertEqual(sum(len(t.foreign_keys) for t in doc.tables()), 1)
        self.assertEqual({r.type for r in doc.relationships}, {"MANYTOONE", "ONETOMANY"})

