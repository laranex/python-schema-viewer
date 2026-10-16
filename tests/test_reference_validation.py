import tempfile
import unittest
from pathlib import Path

from scripts.validate_reference import discover_declarations


class ReferenceValidationTests(unittest.TestCase):
    def test_nested_modules_and_package_initializers_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "app/models/nested"
            nested.mkdir(parents=True)
            (nested / "__init__.py").write_text("class AtPackage(SQLModel, table=True): pass\n")
            (nested / "model.py").write_text(
                "class Concrete(SQLModel, table=True): pass\n"
                "class NotTable(SQLModel, table=False): pass\n"
            )
            self.assertEqual(discover_declarations(root, "app.models"), [
                ("app.models.nested", "AtPackage"),
                ("app.models.nested.model", "Concrete"),
            ])
