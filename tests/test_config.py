import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer.config import (
    DEFAULT_SNAPSHOT,
    add_config,
    config_section,
    detect_model_module,
    load_config,
    resolve_snapshot,
)

MODEL = (
    "from sqlalchemy import Column, Integer\n"
    "from sqlalchemy.orm import declarative_base\n"
    "Base = declarative_base()\n"
    "class Item(Base):\n"
    "    __tablename__ = 'items'\n"
    "    id = Column(Integer, primary_key=True)\n"
)


def package(root: Path, dotted: str, source: str = "") -> None:
    directory = root.joinpath(*dotted.split("."))
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text(source)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_pyproject(self, text):
        (self.root / "pyproject.toml").write_text(text)

    def run_cli(self, *args, input=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(Path("src").resolve()))
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args],
                              cwd=self.root, env=env, input=input, capture_output=True,
                              text=True, timeout=20)

    def test_missing_file_or_section_uses_defaults(self):
        self.assertEqual(load_config(self.root).modules, [])
        self.write_pyproject("[project]\nname = 'app'\n")
        self.assertEqual(resolve_snapshot(None, self.root), DEFAULT_SNAPSHOT)

    def test_section_is_read_and_output_is_project_relative(self):
        self.write_pyproject(
            "[tool.laranex-schema-viewer]\nmodules = ['app.models']\nrecursive = true\n"
            "output = 'build/schema.json'\n"
        )
        config = load_config(self.root)
        self.assertEqual((config.modules, config.recursive), (["app.models"], True))
        self.assertEqual(config.output, self.root / "build/schema.json")
        self.assertEqual(resolve_snapshot(None, self.root), self.root / "build/schema.json")
        self.assertEqual(resolve_snapshot(Path("x.json"), self.root), Path("x.json"))

    def test_invalid_section_is_rejected(self):
        for body in ["module = 'app'", "modules = 'app'", "recursive = 'yes'", "output = 1"]:
            with self.subTest(body=body):
                self.write_pyproject(f"[tool.laranex-schema-viewer]\n{body}\n")
                with self.assertRaises(ValueError):
                    load_config(self.root)

    def test_detects_single_package_or_module_and_skips_environments(self):
        package(self.root, "src.infrastructure.models")
        package(self.root, ".venv.lib.models")
        package(self.root, "tests.models")
        self.assertEqual(detect_model_module(self.root), "src.infrastructure.models")
        (self.root / "app").mkdir()
        (self.root / "app/models.py").write_text("")
        with self.assertRaisesRegex(ValueError, "Multiple"):
            detect_model_module(self.root)

    def test_detection_without_candidates_explains_options(self):
        with self.assertRaisesRegex(ValueError, "--module"):
            detect_model_module(self.root)

    def test_add_config_appends_once_and_preserves_content(self):
        self.write_pyproject("[project]\nname = 'app'\n")
        add_config(self.root, config_section(["app.models"], True, "schema.json"))
        text = (self.root / "pyproject.toml").read_text()
        self.assertTrue(text.startswith("[project]\nname = 'app'\n\n[tool.laranex-schema-viewer]"))
        self.assertEqual(load_config(self.root).modules, ["app.models"])
        with self.assertRaisesRegex(ValueError, "already contains"):
            add_config(self.root, config_section(["other"], False, "x.json"))
        with self.assertRaises(ValueError):
            config_section(["not a module"], False, "x.json")

    def test_init_accepts_detected_defaults(self):
        package(self.root, "app.models")
        result = self.run_cli("init", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = load_config(self.root)
        self.assertEqual((config.modules, config.recursive), (["app.models"], True))
        self.assertEqual(config.output, self.root / "schema-viewer.json")

    def test_init_prompts_for_values(self):
        result = self.run_cli("init", input="first.models, second\nn\nout/schema.json\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = load_config(self.root)
        self.assertEqual((config.modules, config.recursive), (["first.models", "second"], False))
        self.assertEqual(config.output, self.root / "out/schema.json")
        self.assertNotEqual(self.run_cli("init", "--yes").returncode, 0)

    def test_init_reports_incomplete_input(self):
        result = self.run_cli("init", input="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("input ended", result.stderr)
        self.assertFalse((self.root / "pyproject.toml").exists())

    def test_build_without_options_uses_config_then_detection(self):
        package(self.root, "app.models", MODEL)
        result = self.run_cli("build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Detected model module app.models", result.stdout)
        self.assertIn("1 tables", result.stdout)
        self.assertTrue((self.root / "schema-viewer.json").is_file())

        package(self.root, "other.models")
        self.write_pyproject(
            "[tool.laranex-schema-viewer]\nmodules = ['app.models']\noutput = 'out/schema.json'\n"
        )
        result = self.run_cli("build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Detected", result.stdout)
        self.assertTrue((self.root / "out/schema.json").is_file())

    def test_serve_reports_the_configured_snapshot(self):
        self.write_pyproject("[tool.laranex-schema-viewer]\noutput = 'out/missing.json'\n")
        result = self.run_cli("serve", "--port", "0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing.json", result.stderr)
