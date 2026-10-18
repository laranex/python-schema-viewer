"""Check the built distribution: the shared UI assets of the tarball ui.lock.json pins (the viewer files its manifest
lists and the JSON Schemas, byte for byte as ui.stamp.json records them, and nothing else there), workers, type marker
and CLI, and nothing of the removed version 1 snapshot format."""

import configparser
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    wheels = [Path(a) for a in sys.argv[1:]] or list(Path("dist").glob("*.whl"))
    assert len(wheels) == 1, "Build exactly one wheel first, or pass its path"
    lock = json.loads((ROOT / "ui.lock.json").read_text())
    stamp = json.loads((ROOT / "ui.stamp.json").read_text())
    assert stamp["verified"] and stamp["integrity"] == lock["integrity"], "the UI assets are not the locked tarball's"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        # The shared viewer: every file its manifest lists, byte for byte, and nothing from the old viewer.
        viewer = "laranex_schema_viewer/static/viewer/"
        manifest = json.loads(wheel.read(viewer + "manifest.json"))
        assert (manifest["name"], manifest["version"]) == (lock["name"], lock["version"]), manifest["version"]
        assert manifest["contract"] == {"format": "laranex-schema", "major": 2}, manifest["contract"]
        for entry in manifest["files"]:
            data = wheel.read(viewer + entry["path"])
            assert len(data) == entry["size"] and hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
        for filename in ("LICENSE.txt", "NOTICE.txt"):
            assert viewer + filename in names
        # Exactly the fetched runtime assets, as the stamp records them.
        packaged = {f"src/{n}" for n in names if n.startswith(viewer) or n.endswith(".schema.json")
                    or n == "laranex_schema_viewer/contract/schema.json"}
        runtime = {n for n in stamp["files"] if n.startswith("src/")}
        assert packaged == runtime, sorted(packaged ^ runtime)
        for name in runtime:
            assert hashlib.sha256(wheel.read(name.removeprefix("src/"))).hexdigest() == stamp["files"][name], name
        for legacy in ("index.html", "app.js", "graph.js", "styles.css"):
            assert f"laranex_schema_viewer/static/{legacy}" not in names
        for module in ("isolation/worker.py", "isolation/guards.py", "introspection/worker.py",
                       "introspection/readonly.py", "reconcile.py", "model.py", "contract/serialize.py",
                       "contract/validate.py", "contract/shape.py", "contract/graph.py", "contract/migration.py",
                       "contract/comparison.py", "contract/gate.py", "compare.py", "check.py"):
            assert f"laranex_schema_viewer/{module}" in names, module
        schema = json.loads(wheel.read("laranex_schema_viewer/contract/schema.json"))
        assert schema["$id"].endswith("/laranex-schema/2.0.json"), schema["$id"]
        gate = json.loads(wheel.read("laranex_schema_viewer/contract/gate-report.schema.json"))
        assert gate["$id"].endswith("/laranex-schema-gate/1.0.json"), gate["$id"]
        assert {"requested", "comparisons"} <= set(gate["properties"]), sorted(gate["properties"])
        # The removed version 1 snapshot format: its JSON Schema, models and converter.
        for removed in ("schema.json", "document.py", "unified.py", "contract/models.py"):
            assert f"laranex_schema_viewer/{removed}" not in names, removed
        assert "laranex_schema_viewer/py.typed" in names
        entries = next(n for n in names if n.endswith("entry_points.txt"))
        parser = configparser.ConfigParser()
        parser.read_string(wheel.read(entries).decode())
        assert parser["console_scripts"]["laranex-schema-viewer"] == "laranex_schema_viewer.cli:main"
        assert not any("reference.json" in n or "tests/" in n or ".validation/" in n for n in names)
        with tempfile.TemporaryDirectory() as directory:
            wheel.extractall(directory)
            # Import from the extracted wheel in a clean child, without the source tree.
            result = subprocess.run([sys.executable, "-c", (
                "from importlib.resources import files; "
                "import laranex_schema_viewer; "
                "from laranex_schema_viewer.server import viewer_routes; "
                "routes = viewer_routes(); assert '/' in routes and '/app.js' in routes, routes; "
                "assert all(files('laranex_schema_viewer').joinpath('static/viewer', n).is_file() "
                "for n, _ in routes.values()); "
                "import json; from laranex_schema_viewer.contract.shape import json_schema; "
                "assert json.loads(files('laranex_schema_viewer').joinpath('contract/schema.json').read_text()) "
                "== json_schema(); "
                "from laranex_schema_viewer.contract.gate import gate_json_schema; "
                "assert json.loads(files('laranex_schema_viewer').joinpath('contract/gate-report.schema.json')"
                ".read_text()) == gate_json_schema(); "
                "from laranex_schema_viewer.cli import main; main(['--version'])"
            )], cwd=directory, env=dict(os.environ, PYTHONPATH=directory,
                                       PYTHONDONTWRITEBYTECODE="1"),
                capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
    print(f"{wheels[0].name}: viewer {manifest['version']} ({len(manifest['files'])} files, {len(runtime)} UI assets "
          "matching ui.stamp.json), laranex-schema 2.0 "
          "and gate report schemas, workers, CLI entry point, no version 1 format, and isolated import: passed")


if __name__ == "__main__":
    main()

