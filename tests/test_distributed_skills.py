"""Validate portable skill bundles and their examples against the real snapshot API."""

import inspect
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from laranex_schema_viewer.query import SnapshotQuery
from laranex_schema_viewer.snapshot import write_snapshot
from tests.test_extraction import example_document

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAMES = {
    "python-schema-viewer", "schema-discovery", "relationship-analysis",
    "schema-diagnostics", "schema-viewer-mcp",
}


def validate_bundle(directory):
    text = (directory / "SKILL.md").read_text()
    match = re.match(r"\A---\n(.*?)\n---\n(.+)\Z", text, re.S)
    if not match:
        raise ValueError("Missing frontmatter or body")
    metadata = yaml.safe_load(match[1])
    name = metadata["name"]
    if name != directory.name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("Invalid skill name")
    if len(name) > 64 or not isinstance(metadata["description"], str):
        raise ValueError("Invalid skill metadata")
    if not 1 <= len(metadata["description"]) <= 1024 or metadata.get("license") != "MIT":
        raise ValueError("Invalid description or license")
    for source in directory.rglob("*.md"):
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", source.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            path = (source.parent / target.split("#")[0]).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise ValueError(f"Missing or nonportable reference: {target}")
    return text


class DistributedSkillTests(unittest.TestCase):
    def test_catalog_and_isolated_agent_bundles(self):
        self.assertEqual({p.name for p in SKILLS.iterdir() if p.is_dir()}, NAMES)
        with tempfile.TemporaryDirectory() as tmp:
            # A portability check, not a substitute for Skills CLI installation.
            for agent in (".agents", ".claude"):
                for name in sorted(NAMES):
                    source = SKILLS / name
                    target = Path(tmp) / agent / "skills" / name
                    shutil.copytree(source, target)
                    self.assertEqual(validate_bundle(source), validate_bundle(target))
                    self.assertFalse((target / "AGENTS.md").exists())

    def test_missing_and_escaping_references_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "schema-discovery"
            shutil.copytree(SKILLS / target.name, target)
            (target / "references" / "snapshot.md").unlink()
            with self.assertRaisesRegex(ValueError, "reference"):
                validate_bundle(target)
            (target / "SKILL.md").write_text(
                (target / "SKILL.md").read_text().replace(
                    "references/snapshot.md", "../outside.md"
                )
            )
            with self.assertRaisesRegex(ValueError, "reference"):
                validate_bundle(target)

    def test_json_tool_examples_match_query_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema-viewer.json"
            write_snapshot(example_document(), path)
            query = SnapshotQuery(path)
            methods = {
                "schema_overview": query.overview,
                "schema_search": query.search,
                "schema_get_table": query.get_table,
                "schema_get_model": query.get_model,
                "schema_get_relationships": query.relationships,
            }
            exercised = set()
            for source in SKILLS.rglob("*.md"):
                for block in re.findall(r"```json\n(.*?)\n```", source.read_text(), re.S):
                    example = json.loads(block)
                    if "tool" not in example:
                        continue  # Client configuration is JSON too.
                    args = example["arguments"].copy()
                    if args.get("table") == "opaque-table-id":
                        args["table"] = next(iter(query.tables))
                    if args.get("model") == "opaque-model-id":
                        args["model"] = next(iter(query.models))
                    if "query_text" in args:
                        args["query"] = args.pop("query_text")
                    method = methods[example["tool"]]
                    inspect.signature(method).bind(**args)
                    result = method(**args)
                    self.assertIn("revision", result)
                    exercised.add(example["tool"])
            self.assertEqual(exercised, set(methods))

    def test_snapshot_fallback_example_runs_without_application_imports(self):
        source = (SKILLS / "schema-discovery/references/snapshot.md").read_text()
        code = re.search(r"```python\n(.*?)\n```", source, re.S)[1]
        with tempfile.TemporaryDirectory() as tmp:
            write_snapshot(example_document(), Path(tmp) / "schema-viewer.json")
            result = subprocess.run(
                [sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True,
                timeout=20, check=True,
            )
            self.assertIn("declared_metadata", result.stdout)
            self.assertIn("users", result.stdout)


if __name__ == "__main__":
    unittest.main()
