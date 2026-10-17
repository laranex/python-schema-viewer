"""Unified serialization: adapters' declared metadata and version 1 snapshots as laranex-schema 1.0."""

import importlib.util
import unittest
from pathlib import Path

from laranex_schema_viewer.contract import canonical_json, validate_document
from laranex_schema_viewer.document import SchemaDocument
from laranex_schema_viewer.unified import action, database_default, from_v1
from laranex_schema_viewer.document import Default

ROOT = Path(__file__).resolve().parents[1]
V1 = SchemaDocument.model_validate_json((ROOT / "tests/fixtures/v1/shop.json").read_text())


def tables(doc):
    return {t["name"]: t for c in doc["connections"] for t in c["tables"]}


class VersionOneConversion(unittest.TestCase):
    def setUp(self):
        self.doc = from_v1(V1, orm="sqlalchemy")

    def test_is_a_strictly_valid_declared_document(self):
        self.assertEqual(validate_document(self.doc, strict=True)[1], [])
        self.assertEqual(self.doc["source"], "declared")
        self.assertEqual(self.doc["generator"], {"name": "python-schema-viewer", "orm": "sqlalchemy"})
        connection = self.doc["connections"][0]
        self.assertEqual(connection["coverage"]["database"], None)
        self.assertTrue(connection["coverage"]["declared"]["complete"])

    def test_keeps_ids_schemas_and_composite_keys(self):
        lines = tables(self.doc)["order_lines"]
        self.assertEqual(lines["id"], '["default","sales","order_lines"]')
        self.assertEqual(lines["schema"], "sales")
        self.assertEqual(lines["primaryKey"], ["order_id", "line_no"])
        fk = lines["foreignKeys"][0]
        self.assertEqual((fk["onDelete"], fk["deferrable"], fk["initially"]), ("CASCADE", True, "DEFERRED"))

    def test_splits_database_and_orm_defaults(self):
        active = next(c for c in tables(self.doc)["customers"]["columns"] if c["name"] == "active")
        self.assertEqual(active["default"], {"kind": "literal", "value": "true"})
        self.assertEqual(active["ormDefault"], {"kind": "scalar", "value": "True"})
        created = next(c for c in tables(self.doc)["customers"]["columns"] if c["name"] == "created_at")
        self.assertEqual(created["ormDefault"], {"kind": "callable", "value": "datetime.now"})

    def test_keeps_expression_parts_and_dialect_options(self):
        indexes = {i["name"]: i for i in tables(self.doc)["customers"]["indexes"]}
        lower = indexes["ix_customers_lower_email"]
        self.assertEqual([p["expression"] for p in lower["parts"]], ["lower(email)"])
        self.assertTrue(lower["expression"])
        self.assertEqual(lower["dialectOptions"], {"postgresql": {"method": "btree", "included": ["id"]}})
        partial = indexes["uq_customers_active_email"]
        self.assertEqual(partial["dialectOptions"], {"postgresql": {"predicate": "active = true"}})
        self.assertFalse(partial["partial"])

    def test_orients_keys_like_foreign_keys_and_finds_inverses(self):
        rels = {r["name"]: r for r in self.doc["relationships"]}
        self.assertEqual(rels["customer"]["keys"]["from"]["columns"], ["customer_id"])
        self.assertEqual(rels["orders"]["keys"], rels["customer"]["keys"])
        self.assertEqual(rels["orders"]["inverse"], rels["customer"]["id"])
        self.assertEqual((rels["customer"]["cardinality"], rels["orders"]["cardinality"]), ("many-to-one", "one-to-many"))
        self.assertTrue(rels["lines"]["readOnly"])

    def test_maps_secondary_tables_to_pivots_that_join_their_foreign_keys(self):
        tags = next(r for r in self.doc["relationships"] if r["name"] == "tags")
        self.assertEqual(tags["pivot"]["parentPivotColumns"], ["customer_id"])
        self.assertEqual(tags["pivot"]["relatedPivotColumns"], ["tag_id"])
        pivot_links = [link for link in self.doc["links"] if tags["id"] in link["relationships"]]
        self.assertEqual(sorted(link["state"] for link in pivot_links), ["both", "both"])

    def test_names_each_unresolved_foreign_key(self):
        [diagnostic] = self.doc["diagnostics"]
        self.assertEqual(diagnostic["kind"], "unresolved_foreign_key")
        self.assertEqual(diagnostic["subject"]["type"], "foreignKey")
        self.assertIn("legacy_ref", diagnostic["subject"]["columns"])

    def test_is_deterministic(self):
        self.assertEqual(canonical_json(from_v1(V1, orm="sqlalchemy")), canonical_json(self.doc))


class Mapping(unittest.TestCase):
    def test_referential_actions(self):
        self.assertEqual(action("cascade"), ("CASCADE", None))
        self.assertEqual(action("SET_NULL"), ("SET NULL", None))
        self.assertEqual(action("python_protect"), (None, "python_protect"))

    def test_server_defaults_never_invent_sql(self):
        self.assertEqual(database_default(Default(kind="generated", value="Identity")), ({"kind": "identity", "value": "Identity"}, None))
        self.assertEqual(database_default(Default(kind="generated", value="price * 2")), (None, {"kind": None, "expression": "price * 2"}))
        self.assertEqual(database_default(Default(kind="sql", value="now()")), ({"kind": "expression", "value": "now()"}, None))


@unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "SQLAlchemy is not installed")
class AdapterOutput(unittest.TestCase):
    def test_every_installed_orm_converts_to_a_valid_document(self):
        from laranex_schema_viewer.build import build_document

        cases = {"sqlalchemy": dict(modules=["tests.fixtures.sqlalchemy_models"]),
                 "peewee": dict(modules=["tests.fixtures.peewee_models"]),
                 "django": dict(modules=[], settings="tests.fixtures.django_settings"),
                 "tortoise": dict(modules=[], targets=["tests.fixtures.tortoise_config:TORTOISE_ORM"])}
        for orm, kwargs in cases.items():
            if importlib.util.find_spec(orm) is None:
                continue
            with self.subTest(orm):
                doc = from_v1(build_document(project_root=ROOT, orm=orm, **kwargs), orm=orm)
                self.assertEqual(validate_document(doc, strict=True)[1], [])
                self.assertTrue(doc["links"])


if __name__ == "__main__":
    unittest.main()
