"""Check the built distribution includes assets, schema, type marker, and CLI."""

import configparser
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main():
    wheels = [Path(a) for a in sys.argv[1:]] or list(Path("dist").glob("*.whl"))
    assert len(wheels) == 1, "Build exactly one wheel first, or pass its path"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        # The vendored viewer: every file its manifest lists, byte for byte, and nothing from the old viewer.
        viewer = "laranex_schema_viewer/static/viewer/"
        manifest = json.loads(wheel.read(viewer + "manifest.json"))
        for entry in manifest["files"]:
            data = wheel.read(viewer + entry["path"])
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
        for filename in ("LICENSE.txt", "NOTICE.txt"):
            assert viewer + filename in names
        for legacy in ("index.html", "app.js", "graph.js", "styles.css"):
            assert f"laranex_schema_viewer/static/{legacy}" not in names
        for module in ("isolation/worker.py", "isolation/guards.py", "introspection/worker.py",
                       "introspection/readonly.py", "reconcile.py", "contract/models.py"):
            assert f"laranex_schema_viewer/{module}" in names, module
        assert "laranex_schema_viewer/schema.json" in names
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
                "from laranex_schema_viewer.cli import main; main(['--version'])"
            )], cwd=directory, env=dict(os.environ, PYTHONPATH=directory,
                                       PYTHONDONTWRITEBYTECODE="1"),
                capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
    print(f"{wheels[0].name}: viewer {manifest['version']} ({len(manifest['files'])} files), schema, "
          "workers, CLI entry point, and isolated import: passed")


if __name__ == "__main__":
    main()

